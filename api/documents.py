"""Documents & ingestion : sources indexées, upload + file d'ingestion,
exploration des chunks, résumé/markdown d'un document, purge du corpus."""
from __future__ import annotations

import re
import time

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from env_config import MONGO_DB
from core import ingest_queue
from api.common import _chunks_col
from utils.mongo import get_client, get_db

router = APIRouter()


@router.get("/api/sources")
def sources() -> dict:
    """Documents ingérés : nom + nombre de chunks (depuis Mongo).

    Renvoie `available: false` si Mongo est injoignable — le front affiche
    alors un état dégradé au lieu d'une erreur.
    """
    # La baseline LynX est un document réservé : interrogeable uniquement
    # depuis le chat AI for Requirements, invisible du monde RAG.
    from api.lynx_chat import BASELINE_SOURCE
    try:
        rows = list(_chunks_col().aggregate([
            {"$match": {"source": {"$nin": [None, BASELINE_SOURCE]}}},
            {"$group": {"_id": "$source", "chunks": {"$sum": 1},
                    "accepted": {"$sum": {"$cond": [{"$eq": [{"$ifNull": ["$quality_status", "accepted"]}, "accepted"]}, 1, 0]}},
                    "degraded": {"$sum": {"$cond": [{"$eq": ["$quality_status", "degraded"]}, 1, 0]}},
                    "quarantined": {"$sum": {"$cond": [{"$eq": ["$quality_status", "quarantined"]}, 1, 0]}}}},
            {"$sort": {"_id": 1}},
        ]))
    except Exception:
        return {"available": False, "sources": []}
    return {
        "available": True,
        "sources": [{"name": r["_id"], "chunks": r["chunks"], "quality": {"accepted": r.get("accepted", 0), "degraded": r.get("degraded", 0), "quarantined": r.get("quarantined", 0)}} for r in rows],
    }


@router.post("/api/documents/{name}/summary")
def document_summary(name: str) -> dict:
    """Résumé global d'un document (réutilise les résumés de section RAPTOR)."""
    from core.summarize import summarize_document
    return summarize_document(name)


@router.get("/api/documents/{name}/markdown")
def document_markdown(name: str) -> dict:
    """Texte markdown source du document (visionneuse page blanche).
    Cherche dans docs/out ET dans le dossier des markdown nettoyés (les PDF
    convertis y vivent sous <nom>-clean.md)."""
    safe = name.replace("/", "_").replace("\\", "_")
    candidates = [ingest_queue.DOCS_OUT / safe,
                  ingest_queue.DOCS_OUT.parent / "out_clean_md" / safe]
    for path in candidates:
        if path.exists():
            return {"name": safe, "markdown": path.read_text(encoding="utf-8")}
    raise HTTPException(404, "Markdown source introuvable.")

@router.get("/api/ingest/defaults")
def ingest_defaults() -> dict:
    """Options d'ingestion par défaut (.env) + types de fichiers acceptés."""
    return {"params": ingest_queue.default_params(),
            "upload_types": list(ingest_queue.UPLOAD_TYPES)}


@router.post("/api/documents")
async def upload_documents(
    files: list[UploadFile] = File(...),
    nkw: int = Form(...),
    nq: int = Form(...),
    mode: str = Form(...),
    raptor: bool = Form(...),
    enh_model: str = Form(""),
) -> dict:
    """Dépose un LOT de documents et le met en file d'ingestion séquentielle.
    Les options sont choisies AU MOMENT de l'upload (règle produit) et
    partagées par le lot."""
    ingest_queue.DOCS_OUT.mkdir(parents=True, exist_ok=True)
    ingest_queue.DOCS_PDF.mkdir(parents=True, exist_ok=True)
    items = []
    for up in files:
        name = (up.filename or "document").replace("/", "_").replace("\\", "_")
        if name.split(".")[-1].lower() not in ingest_queue.UPLOAD_TYPES:
            raise HTTPException(400, f"Type non accepté : {name}")
        # Documents source -> docs/PDF ; markdown déjà converti -> docs/out.
        target = (ingest_queue.DOCS_OUT / name if name.lower().endswith(".md")
                  else ingest_queue.DOCS_PDF / name)
        target.write_bytes(await up.read())
        items.append({"name": name, "path": str(target)})
    params = {"nkw": nkw, "nq": nq,
              "mode": mode if mode in ("technical", "naive") else "technical",
              "raptor": raptor, "enh_model": enh_model.strip()}
    return {"added": ingest_queue.enqueue(items, params)}


@router.get("/api/ingest/status")
def ingest_status() -> dict:
    """File d'ingestion : une entrée par document, dans l'ordre de lancement."""
    jobs = []
    for j in ingest_queue.snapshot():
        res = j.get("result") or {}
        if j["status"] == "running" and j["t0"]:
            elapsed = int(time.time() - j["t0"])
        elif j["t_end"] and j["t0"]:
            elapsed = int(j["t_end"] - j["t0"])
        else:
            elapsed = None
        jobs.append({
            "id": j["id"], "name": j["name"], "status": j["status"],
            "pct": j["pct"], "step": j["step"],
            "elapsed": elapsed,
            # Nom de source RÉEL en base (un PDF devient <nom>-clean.md) :
            # c'est lui que l'UI doit cibler pour interroger le document.
            "source_name": j.get("source_name"),
            "num_chunks": res.get("num_chunks"),
            "quality": res.get("quality"),
            "message": res.get("message"),
        })
    return {"active": ingest_queue.active(), "jobs": jobs}


@router.post("/api/ingest/clear")
def ingest_clear() -> dict:
    ingest_queue.clear_finished()
    return {"ok": True}


@router.delete("/api/documents/{name}")
def delete_document(name: str) -> dict:
    """Supprime un document de TOUS les index : chunks Mongo, vecteurs Chroma,
    index BM25. (Le Streamlit ne purgeait que Mongo ; ici le retrait est complet.)"""
    try:
        client = get_client()
        n = client[MONGO_DB]["chunks"].delete_many({"source": name}).deleted_count
        client[MONGO_DB]["bm25_indexes"].delete_many({"source_doc": name})
    except Exception as e:
        raise HTTPException(503, f"Mongo injoignable : {e}")
    try:
        from retrieval.vector_store import get_vector_store
        get_vector_store().delete_source(name)
    except Exception:
        pass  # Chroma indisponible : les vecteurs orphelins partiront à la réingestion
    try:
        from core.ask import clear_retrieval_caches
        clear_retrieval_caches()
    except Exception:
        pass
    return {"deleted": n}


@router.post("/api/corpus/reset")
def corpus_reset() -> dict:
    """Vide l'index documentaire local (Chroma + chunks/BM25/graphe Mongo)
    SANS toucher aux sessions ni aux traces. Même geste que le Streamlit."""
    report = []
    try:
        db = get_db()
        for cn in ("chunks", "bm25_indexes", "entity_graph"):
            try:
                report.append(f"{cn}: -{db[cn].delete_many({}).deleted_count}")
            except Exception as e:
                report.append(f"{cn}: erreur ({e})")
    except Exception as e:
        raise HTTPException(503, f"Mongo injoignable : {e}")
    try:
        from retrieval.vector_store import get_vector_store
        get_vector_store().reset()
        report.append("vecteurs réinitialisés")
    except Exception as e:
        report.append(f"vecteurs: erreur ({e})")
    try:
        from core.ask import clear_retrieval_caches
        clear_retrieval_caches()
    except Exception:
        pass
    return {"ok": True, "report": " - ".join(report)}


@router.get("/api/documents/{name}/chunks")
def document_chunks(name: str, search: str = "", chunk_type: str = "tous",
                    limit: int = 200) -> dict:
    """Exploration d'un document : ses passages indexés, filtrables (boîte de
    verre de l'indexation — ce que la base contient réellement)."""
    q: dict = {"source": name}
    if chunk_type == "texte":
        q["chunk_type"] = {"$nin": ["summary", "table", "figure", "mixed"]}
    elif chunk_type == "resumes":
        q["chunk_type"] = "summary"
    elif chunk_type == "tabfig":
        q["chunk_type"] = {"$in": ["table", "figure", "mixed"]}
    if search:
        q["content"] = {"$regex": re.escape(search), "$options": "i"}
    try:
        rows = list(_chunks_col().find(q).sort([("section_idx", 1), ("chunk_idx", 1)])
                    .limit(max(1, min(limit, 500))))
    except Exception as e:
        raise HTTPException(503, f"Mongo injoignable : {e}")
    chunks = [{
        "content": r.get("content", ""),
        "heading": r.get("heading"), "breadcrumb": r.get("breadcrumb"),
        "section_idx": r.get("section_idx"), "page_number": r.get("page_number"),
        "chunk_type": r.get("chunk_type"),
        "quality_status": r.get("quality_status", "accepted"),
        "quality_reasons": r.get("quality_reasons", []),
        "content_provenance": r.get("content_provenance", "raw"),
        "keywords_str": r.get("keywords_str"), "questions_str": r.get("questions_str"),
        "entities_str": r.get("entities_str"),
    } for r in rows]
    return {"total": len(chunks), "chunks": chunks}
