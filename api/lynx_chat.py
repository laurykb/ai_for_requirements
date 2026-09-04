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
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from core import ingest_queue
from core.reserved_sources import LYNX_BASELINE_SOURCE as BASELINE_SOURCE
from utils.mongo import get_db

# Métadonnées de synchronisation (fingerprint de la baseline indexée).
_META_COLLECTION = "lynx_chat_meta"
_VERSIONS_COLLECTION = "lynx_chat_versions"

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
                if isinstance(link, dict) and (link.get("target") or link.get("target_id")):
                    target = link.get("target") or link.get("target_id")
                    details.append(f"Lien {link.get('type', 'REFERENCE')} : {target}")
            if req.get("verification"):
                details.append(f"Méthode déclarée : {req['verification']}")
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
    try:
        db = get_db()
        db[_META_COLLECTION].delete_many({})
        db[_VERSIONS_COLLECTION].delete_many({})
    except Exception as exc:
        raise HTTPException(503, f"Index supprimé mais registre LynX non nettoyé : {exc}") from exc


def _baseline_job() -> dict | None:
    """Dernière tâche d'ingestion de la baseline dans la file, s'il y en a une."""
    jobs = [j for j in ingest_queue.snapshot()
            if j.get("name") == BASELINE_SOURCE
            and j.get("job_type") in {None, "lynx_baseline"}]
    return jobs[-1] if jobs else None


def fail_baseline_version(job: dict, message: str) -> None:
    """Rend un échec observable sans modifier la version active."""
    get_db()[_VERSIONS_COLLECTION].update_one(
        {"version_id": job.get("version_id")},
        {"$set": {"status": "failed", "error": str(message)[:1000],
                  "failed_at": time.time()}},
        upsert=True,
    )


def _cleanup_inactive_version(version: str) -> None:
    """Nettoie uniquement une préparation inactive avant une nouvelle tentative."""
    meta = get_db()[_META_COLLECTION].find_one({"_id": "baseline"}) or {}
    if meta.get("active_version") == version:
        raise RuntimeError("Refus de nettoyer la version actuellement active.")
    from retrieval.vector_store import get_vector_store
    from indexing.store_mongo import delete_source_version
    from indexing.keyword_index import delete_bm25_version
    get_vector_store().delete_version(BASELINE_SOURCE, version)
    delete_source_version(BASELINE_SOURCE, version)
    delete_bm25_version(BASELINE_SOURCE, version)


def activate_baseline_version(job: dict, stats: dict) -> None:
    """Point de commit unique après préparation réussie des trois index."""
    version = job.get("version_id")
    expected = int(stats.get("num_chunks") or 0)
    if not version or expected <= 0:
        raise RuntimeError("Version ou nombre de chunks invalide.")
    if int(stats.get("mongo_chunks") or 0) != expected:
        raise RuntimeError("MongoDB incomplet : publication refusée.")
    if int(stats.get("bm25_chunks") or 0) != expected:
        raise RuntimeError("Index BM25 incomplet : publication refusée.")
    if int(stats.get("vector_units") or 0) < expected:
        raise RuntimeError(
            "Index vectoriel incomplet : "
            f"{int(stats.get('vector_units') or 0)}/{expected} unités, publication refusée."
        )

    # Relecture indépendante : les compteurs déclarés par le pipeline ne
    # suffisent pas pour autoriser la bascule.
    db = get_db()
    mongo_count = db["chunks"].count_documents(
        {"source": BASELINE_SOURCE, "ingest_version": version}
    )
    bm25_ready = db["bm25_indexes"].find_one(
        {"source_doc": BASELINE_SOURCE, "ingest_version": version}, {"_id": 1}
    ) is not None
    from retrieval.vector_store import get_vector_store
    vector_count = get_vector_store().count_version(BASELINE_SOURCE, version)
    if mongo_count != expected or not bm25_ready or vector_count is None or vector_count < expected:
        raise RuntimeError(
            f"Contrôle des index refusé (Mongo={mongo_count}/{expected}, "
            f"BM25={'ok' if bm25_ready else 'absent'}, vecteurs={vector_count})."
        )

    markdown_path = Path(job.get("markdown_path") or "")
    if not markdown_path.is_file():
        raise RuntimeError("Markdown préparé absent : publication refusée.")
    ingest_queue.DOCS_OUT.mkdir(parents=True, exist_ok=True)
    shutil.copy2(markdown_path, ingest_queue.DOCS_OUT / BASELINE_SOURCE)

    version_row = db[_VERSIONS_COLLECTION].find_one({"version_id": version}) or {}
    now = time.time()
    # Ce document constitue le pointeur atomique lu par le retrieval.
    db[_META_COLLECTION].replace_one(
        {"_id": "baseline"},
        {"_id": "baseline", "active_version": version,
         "fingerprint": version_row.get("fingerprint"),
         "req_hashes": version_row.get("req_hashes", {}),
         "n_exigences": version_row.get("n_exigences", expected),
         "synced_at": now, "last_success": now,
         "index_counts": {"mongo": mongo_count,
                          "bm25": stats["bm25_chunks"],
                          "vectors": vector_count}},
        upsert=True,
    )
    db[_VERSIONS_COLLECTION].update_many(
        {"status": "active", "version_id": {"$ne": version}},
        {"$set": {"status": "ready"}},
    )
    db[_VERSIONS_COLLECTION].update_one(
        {"version_id": version},
        {"$set": {"status": "active", "activated_at": now,
                  "index_counts": {"mongo": mongo_count,
                                   "bm25": stats["bm25_chunks"],
                                   "vectors": vector_count},
                  "error": None}},
    )


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

    snapshot = [dict(r) for r in corpus]  # figé : l'arbre peut bouger pendant l'ingestion
    fingerprint = _fingerprint(snapshot)
    from core.lynx_baseline_ingest import hype_policy
    from env_config import EMBED_MODEL
    hype_on, hype_nq = hype_policy(len(snapshot))
    index_config = {"embed_model": EMBED_MODEL, "hype": hype_on, "hype_nq": hype_nq,
                    "schema": 2}
    version = hashlib.sha256((fingerprint + json.dumps(index_config, sort_keys=True))
                             .encode("utf-8")).hexdigest()[:20]
    try:
        active = get_db()[_META_COLLECTION].find_one({"_id": "baseline"}) or {}
        if active.get("active_version") == version:
            return {"queued": False, "already_active": True,
                    "version_id": version, "n_exigences": len(snapshot),
                    "source": BASELINE_SOURCE}
        previous = get_db()[_VERSIONS_COLLECTION].find_one({"version_id": version})
        if previous and previous.get("status") in {"failed", "preparing"}:
            _cleanup_inactive_version(version)
    except Exception as exc:
        raise HTTPException(503, f"Stockage des versions indisponible : {exc}") from exc

    version_dir = ingest_queue.DOCS_STAGING / f"lynx-{version}"
    version_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = version_dir / "baseline.json"
    markdown_path = version_dir / BASELINE_SOURCE
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    markdown_path.write_text(_render_markdown(snapshot), encoding="utf-8")
    try:
        get_db()[_VERSIONS_COLLECTION].replace_one(
            {"version_id": version},
            {"version_id": version, "fingerprint": fingerprint,
             "req_hashes": _req_hashes(snapshot), "n_exigences": len(snapshot),
             "status": "preparing", "created_at": time.time(),
             "index_config": index_config,
             "snapshot_path": str(snapshot_path), "markdown_path": str(markdown_path)},
            upsert=True)
    except Exception as exc:
        raise HTTPException(503, f"Impossible d'enregistrer la version LynX : {exc}") from exc
    try:
        ingest_queue.enqueue(
            [{"name": BASELINE_SOURCE, "path": str(snapshot_path),
              "markdown_path": str(markdown_path), "job_type": "lynx_baseline",
              "version_id": version, "content_hash": fingerprint}],
            {"nkw": 0, "nq": 0, "mode": "technical", "raptor": False, "enh_model": ""})
    except Exception as exc:
        fail_baseline_version({"version_id": version}, str(exc))
        raise HTTPException(503, str(exc)) from exc
    return {"queued": True, "version_id": version,
            "n_exigences": len(snapshot), "source": BASELINE_SOURCE}


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
    versions = []
    available = True
    try:
        db = get_db()
        meta = db[_META_COLLECTION].find_one({"_id": "baseline"})
        active_version = (meta or {}).get("active_version")
        chunk_query = {"source": BASELINE_SOURCE}
        if active_version:
            chunk_query["ingest_version"] = active_version
        indexed_chunks = db["chunks"].count_documents(chunk_query)
        versions = list(db[_VERSIONS_COLLECTION].find(
            {}, {"_id": 0, "req_hashes": 0, "snapshot_path": 0, "markdown_path": 0}
        ).sort("created_at", -1).limit(10))
    except Exception:
        available = False

    job = _baseline_job()
    syncing = bool(job and job.get("status") in ("queued", "running"))
    sync_error = (job.get("result") or {}).get("message") if job and job.get("status") == "error" else None
    if job and job.get("persistence_error"):
        sync_error = "Persistance du job indisponible : " + job["persistence_error"]
    if not sync_error:
        failed = versions[0] if versions and versions[0].get("status") == "failed" else None
        sync_error = (failed or {}).get("error")

    in_sync = bool(indexed_chunks) and bool(meta) \
        and meta.get("fingerprint") == _fingerprint(corpus)
    degraded_reasons = []
    if not available:
        degraded_reasons.append("Stockage documentaire indisponible.")
    if sync_error:
        degraded_reasons.append(f"Échec de synchronisation : {sync_error}")
    if available and not indexed_chunks:
        degraded_reasons.append("Aucun passage de baseline indexé.")
    if indexed_chunks and not in_sync:
        degraded_reasons.append("Index obsolète par rapport à la baseline active.")
    retrieval_mode = "hybrid" if available and in_sync else ("stale" if indexed_chunks else "unavailable")
    return {
        "available": available,
        "retrieval_mode": retrieval_mode,
        "degraded_reasons": degraded_reasons,
        "source": BASELINE_SOURCE,
        "n_exigences": len(corpus),
        "indexed_chunks": indexed_chunks,
        "indexed_n_exigences": (meta or {}).get("n_exigences"),
        "synced_at": (meta or {}).get("synced_at"),
        "active_version": (meta or {}).get("active_version"),
        "preparing_version": job.get("version_id") if syncing and job else None,
        "last_success": (meta or {}).get("last_success"),
        "index_counts": (meta or {}).get("index_counts"),
        "versions": versions,
        "in_sync": in_sync,
        # Quoi a bougé depuis la dernière synchronisation (bandeau UI).
        "diff": None if in_sync else _baseline_diff((meta or {}).get("req_hashes"), corpus),
        "syncing": syncing,
        "sync_pct": job.get("pct") if syncing and job else None,
        "sync_error": sync_error,
    }


@router.get("/versions")
def baseline_versions() -> dict:
    try:
        rows = list(get_db()[_VERSIONS_COLLECTION].find(
            {}, {"_id": 0, "req_hashes": 0}
        ).sort("created_at", -1).limit(20))
    except Exception as exc:
        raise HTTPException(503, f"Historique indisponible : {exc}") from exc
    return {"versions": rows}


class RestoreVersionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: str = Field(min_length=8, max_length=64, pattern=r"^[a-f0-9]+$")


@router.post("/versions/restore")
def restore_baseline_version(body: RestoreVersionBody) -> dict:
    """Rebascule atomiquement le chat sur une version précédemment validée."""
    db = get_db()
    row = db[_VERSIONS_COLLECTION].find_one({"version_id": body.version_id})
    if not row or row.get("status") not in {"active", "ready"}:
        raise HTTPException(404, "Version LynX validée introuvable.")
    expected = int(row.get("n_exigences") or 0)
    mongo_count = db["chunks"].count_documents(
        {"source": BASELINE_SOURCE, "ingest_version": body.version_id}
    )
    bm25_ready = db["bm25_indexes"].find_one(
        {"source_doc": BASELINE_SOURCE, "ingest_version": body.version_id}, {"_id": 1}
    ) is not None
    from retrieval.vector_store import get_vector_store
    vector_count = get_vector_store().count_version(BASELINE_SOURCE, body.version_id)
    if expected <= 0 or mongo_count != expected or not bm25_ready \
            or vector_count is None or vector_count < expected:
        raise HTTPException(409, "Cette version n'est plus complète dans les trois index.")
    markdown_path = Path(row.get("markdown_path") or "")
    if not markdown_path.is_file():
        raise HTTPException(409, "Le Markdown de cette version n'est plus disponible.")
    ingest_queue.DOCS_OUT.mkdir(parents=True, exist_ok=True)
    shutil.copy2(markdown_path, ingest_queue.DOCS_OUT / BASELINE_SOURCE)
    now = time.time()
    db[_META_COLLECTION].replace_one(
        {"_id": "baseline"},
        {"_id": "baseline", "active_version": body.version_id,
         "fingerprint": row.get("fingerprint"), "req_hashes": row.get("req_hashes", {}),
         "n_exigences": row.get("n_exigences"), "synced_at": now,
         "last_success": now, "index_counts": row.get("index_counts", {})},
        upsert=True,
    )
    db[_VERSIONS_COLLECTION].update_many(
        {"status": "active", "version_id": {"$ne": body.version_id}},
        {"$set": {"status": "ready"}},
    )
    db[_VERSIONS_COLLECTION].update_one(
        {"version_id": body.version_id}, {"$set": {"status": "active", "restored_at": now}}
    )
    from core.ask import clear_retrieval_caches
    clear_retrieval_caches()
    return {"ok": True, "active_version": body.version_id}
