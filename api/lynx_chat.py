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

    Sérialise l'arbre en Markdown, purge l'index précédent puis met le
    document réservé en file d'ingestion standard. Ingestion légère par
    défaut (pas d'enrichissement LLM : les exigences sont courtes et
    atomiques) — la synchronisation reste rapide après chaque évolution
    de la baseline."""
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
    ingest_queue.enqueue([{"name": BASELINE_SOURCE, "path": str(path)}],
                         {"nkw": 0, "nq": 0, "mode": "technical",
                          "raptor": False, "enh_model": ""})
    try:
        get_db()[_META_COLLECTION].replace_one(
            {"_id": "baseline"},
            {"_id": "baseline", "fingerprint": _fingerprint(corpus),
             "n_exigences": len(corpus), "synced_at": time.time()},
            upsert=True)
    except Exception:
        pass  # métadonnées de fraîcheur en mode meilleur-effort
    return {"queued": True, "n_exigences": len(corpus), "source": BASELINE_SOURCE}


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

    return {
        "available": available,
        "source": BASELINE_SOURCE,
        "n_exigences": len(corpus),
        "indexed_chunks": indexed_chunks,
        "indexed_n_exigences": (meta or {}).get("n_exigences"),
        "synced_at": (meta or {}).get("synced_at"),
        "in_sync": bool(indexed_chunks) and bool(meta)
                   and meta.get("fingerprint") == _fingerprint(corpus),
        "syncing": syncing,
        "sync_pct": job.get("pct") if syncing and job else None,
        "sync_error": sync_error,
    }
