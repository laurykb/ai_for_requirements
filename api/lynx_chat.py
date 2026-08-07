"""Chat RAG sur la baseline d'exigences LynX.

Le chat de l'espace AI for Requirements réutilise TOUT le pipeline RAG
(routage, agent, synthèse, contrat de réponse, attribution) mais son
périmètre documentaire est verrouillé sur un document réservé : la matrice
d'exigences de travail (« l'arbre »), sérialisée en Markdown puis ingérée
par le pipeline standard (chunks + embeddings + BM25).

Ce module ne fait que la passerelle baseline -> index documentaire :
  - POST /api/lynx/chat/sync    (ré)indexe la baseline courante
  - GET  /api/lynx/chat/status  fraîcheur de l'index (pour l'indicateur UI)
Le front interroge ensuite /api/ask avec `source = BASELINE_SOURCE`.

Le document réservé est invisible du monde RAG (exclu de /api/sources) ;
les sessions de chat créées ici portent `source_filter = BASELINE_SOURCE`,
ce qui permet aux deux mondes de filtrer leurs conversations.
"""
from __future__ import annotations

import hashlib
import json
import time

from fastapi import APIRouter, HTTPException

from core import ingest_queue
from core.reserved_sources import LYNX_BASELINE_SOURCE as BASELINE_SOURCE
from utils.mongo import get_db

# Métadonnées de synchronisation (fingerprint de la baseline indexée).
_META_COLLECTION = "lynx_chat_meta"

router = APIRouter(prefix="/api/lynx/chat")


def _fingerprint(corpus: list[dict]) -> str:
    """Empreinte stable de la baseline : tout changement (texte, niveau,
    liens, ajout/suppression) invalide l'index et l'UI propose de resynchroniser."""
    canon = json.dumps(sorted(corpus, key=lambda r: str(r.get("id"))),
                       sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _req_hashes(corpus: list[dict]) -> dict[str, str]:
    """Empreinte PAR exigence — permet au status de dire QUOI a changé
    (ajoutées / modifiées / supprimées) depuis la dernière synchronisation."""
    return {str(r.get("id")): hashlib.sha256(
                json.dumps(r, sort_keys=True, ensure_ascii=False, default=str)
                .encode("utf-8")).hexdigest()[:16]
            for r in corpus}


def _baseline_diff(indexed: dict[str, str] | None, current: list[dict]) -> dict | None:
    """Diff baseline indexée -> baseline courante (listes d'ids, plafonnées)."""
    if not indexed:
        return None
    now = _req_hashes(current)
    added = sorted(i for i in now if i not in indexed)
    removed = sorted(i for i in indexed if i not in now)
    changed = sorted(i for i in now if i in indexed and now[i] != indexed[i])
    if not (added or removed or changed):
        return None
    cap = 20
    return {"added": added[:cap], "removed": removed[:cap], "changed": changed[:cap],
            "n_added": len(added), "n_removed": len(removed), "n_changed": len(changed)}


def _render_markdown(corpus: list[dict]) -> str:
    """Sérialise la baseline en Markdown pour le pipeline d'ingestion.

    Une section par domaine, un titre par exigence (ID + niveau) : le
    découpage sémantique conserve ces titres en fil d'Ariane, donc les
    citations du chat pointent vers des identifiants d'exigences."""
    by_domain: dict[str, list[dict]] = {}
    for req in corpus:
        by_domain.setdefault(str(req.get("domaine") or "Général"), []).append(req)

    lines = ["# Baseline d'exigences (LynX)", ""]
    for domain in sorted(by_domain):
        lines += [f"## Domaine : {domain}", ""]
        for req in sorted(by_domain[domain],
                          key=lambda r: (int(r.get("niveau") or 0), str(r.get("id")))):
            head = f"{req.get('id')} — {req.get('type') or 'Exigence'} · niveau L{req.get('niveau', 0)}"
            lines += [f"### {head}", ""]
            texte = str(req.get("texte") or "").strip()
            lines += [texte if texte else "(énoncé vide)", ""]
            details = []
            if req.get("parent_id"):
                details.append(f"Dérivée de : {req['parent_id']}")
            for link in req.get("links") or []:
                if isinstance(link, dict) and link.get("target_id"):
                    details.append(f"Lien {link.get('type', 'REFERENCE')} : {link['target_id']}")
            if req.get("verification"):
                details.append(f"Vérification (IADT) : {req['verification']}")
            if req.get("source"):
                details.append(f"Origine : {req['source']}")
            if req.get("rationale"):
                details.append(f"Justification : {req['rationale']}")
            if details:
                lines += ["- " + "\n- ".join(details), ""]
    return "\n".join(lines)


def _purge_baseline_index() -> None:
    """Retire l'ancienne baseline de tous les index (Mongo, vecteurs, caches)."""
    from api.documents import delete_document
    delete_document(BASELINE_SOURCE)


def _baseline_job() -> dict | None:
    """Dernière tâche d'ingestion de la baseline dans la file, s'il y en a une."""
    jobs = [j for j in ingest_queue.snapshot() if j.get("name") == BASELINE_SOURCE]
    return jobs[-1] if jobs else None


@router.post("/sync")
def sync_baseline() -> dict:
    """(Ré)indexe la baseline de travail courante pour le chat.

    Ingestion dédiée « requirement-aware » (core/lynx_baseline_ingest) :
    1 chunk = 1 exigence avec ses attributs en métadonnées, questions HyPE
    élastiques (couvre le décalage de vocabulaire questions ↔ énoncés
    contractuels). L'arbre est aussi sérialisé en Markdown pour la
    visionneuse de document."""
    from api.lynx_api import _get_corpus
    corpus = _get_corpus()
    if not corpus:
        raise HTTPException(400, "Baseline vide : chargez ou importez une matrice d'exigences.")

    job = _baseline_job()
    if job and job.get("status") in ("queued", "running"):
        raise HTTPException(409, "Synchronisation déjà en cours.")

    ingest_queue.DOCS_OUT.mkdir(parents=True, exist_ok=True)
    path = ingest_queue.DOCS_OUT / BASELINE_SOURCE
    path.write_text(_render_markdown(corpus), encoding="utf-8")

    _purge_baseline_index()
    from core.lynx_baseline_ingest import ingest_baseline
    snapshot = [dict(r) for r in corpus]  # figé : l'arbre peut bouger pendant l'ingestion
    ingest_queue.enqueue(
        [{"name": BASELINE_SOURCE, "path": str(path),
          "run": lambda cb: ingest_baseline(snapshot, cb)}],
        {"nkw": 0, "nq": 0, "mode": "technical", "raptor": False, "enh_model": ""})
    try:
        get_db()[_META_COLLECTION].replace_one(
            {"_id": "baseline"},
            {"_id": "baseline", "fingerprint": _fingerprint(snapshot),
             "req_hashes": _req_hashes(snapshot),
             "n_exigences": len(snapshot), "synced_at": time.time()},
            upsert=True)
    except Exception:
        pass  # métadonnées de fraîcheur en mode meilleur-effort
    return {"queued": True, "n_exigences": len(snapshot), "source": BASELINE_SOURCE}


@router.get("/examples")
def chat_examples(n: int = 3) -> dict:
    """Suggestions de questions VIVANTES : échantillon aléatoire des questions
    HyPE indexées — générées depuis la baseline elle-même, elles montrent ce
    que la baseline sait répondre. Vide si HyPE coupé (le front garde alors
    ses exemples statiques)."""
    import random
    n = max(1, min(n, 10))
    try:
        rows = list(get_db()["chunks"].aggregate([
            {"$match": {"source": BASELINE_SOURCE, "questions.0": {"$exists": True}}},
            {"$sample": {"size": n * 2}},   # marge pour la déduplication
            {"$project": {"questions": 1}},
        ]))
    except Exception:
        return {"examples": []}
    seen: set[str] = set()
    examples: list[str] = []
    for row in rows:
        options = [q.strip() for q in row.get("questions", []) if q and q.strip()]
        if not options:
            continue
        pick = random.choice(options)
        if pick not in seen:
            seen.add(pick)
            examples.append(pick)
        if len(examples) >= n:
            break
    return {"examples": examples}


@router.get("/coverage")
def baseline_coverage() -> dict:
    """Couverture de la baseline par les conversations : quelles exigences ont
    déjà fondé une réponse (citées dans les passages), lesquelles jamais —
    détecte les angles morts (de la baseline comme des questions posées)."""
    from api.lynx_api import _get_corpus
    corpus = _get_corpus()
    try:
        rows = get_db()["chat_sessions"].aggregate([
            {"$match": {"source_filter": BASELINE_SOURCE}},
            {"$unwind": "$messages"},
            {"$unwind": "$messages.chunks"},
            {"$group": {"_id": "$messages.chunks.meta.req_id", "n": {"$sum": 1}}},
        ])
        counts = {r["_id"]: r["n"] for r in rows if r.get("_id")}
    except Exception:
        return {"available": False, "n_exigences": len(corpus), "n_cited": 0,
                "domains": [], "never_cited": [], "top": []}

    domains: dict[str, dict] = {}
    never: list[dict] = []
    for req in sorted(corpus, key=lambda r: (str(r.get("domaine") or "Général"),
                                             int(r.get("niveau") or 0), str(r.get("id")))):
        rid = str(req.get("id"))
        dom = str(req.get("domaine") or "Général")
        entry = domains.setdefault(dom, {"domaine": dom, "total": 0, "cited": 0})
        entry["total"] += 1
        if counts.get(rid):
            entry["cited"] += 1
        else:
            never.append({"id": rid, "domaine": dom, "niveau": req.get("niveau", 0)})

    top = sorted(((rid, n) for rid, n in counts.items()), key=lambda t: -t[1])[:5]
    return {
        "available": True,
        "n_exigences": len(corpus),
        "n_cited": sum(d["cited"] for d in domains.values()),
        "domains": sorted(domains.values(), key=lambda d: d["domaine"]),
        "never_cited": never[:50],
        "n_never": len(never),
        "top": [{"id": rid, "n": n} for rid, n in top],
    }


@router.get("/status")
def baseline_status() -> dict:
    """Fraîcheur de l'index baseline, pour l'indicateur de l'UI :
    - `indexed_chunks` : passages réellement interrogeables
    - `in_sync` : l'index correspond-il à la baseline affichée dans l'arbre ?
    - `syncing` / `sync_error` : état de la file d'ingestion."""
    from api.lynx_api import _get_corpus
    corpus = _get_corpus()

    indexed_chunks = 0
    meta = None
    available = True
    try:
        db = get_db()
        indexed_chunks = db["chunks"].count_documents({"source": BASELINE_SOURCE})
        meta = db[_META_COLLECTION].find_one({"_id": "baseline"})
    except Exception:
        available = False

    job = _baseline_job()
    syncing = bool(job and job.get("status") in ("queued", "running"))
    sync_error = (job.get("result") or {}).get("message") if job and job.get("status") == "error" else None

    in_sync = bool(indexed_chunks) and bool(meta) \
        and meta.get("fingerprint") == _fingerprint(corpus)
    return {
        "available": available,
        "source": BASELINE_SOURCE,
        "n_exigences": len(corpus),
        "indexed_chunks": indexed_chunks,
        "indexed_n_exigences": (meta or {}).get("n_exigences"),
        "synced_at": (meta or {}).get("synced_at"),
        "in_sync": in_sync,
        # Quoi a bougé depuis la dernière synchronisation (bandeau UI).
        "diff": None if in_sync else _baseline_diff((meta or {}).get("req_hashes"), corpus),
        "syncing": syncing,
        "sync_pct": job.get("pct") if syncing and job else None,
        "sync_error": sync_error,
    }
