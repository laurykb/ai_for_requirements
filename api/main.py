"""API FastAPI (backend du nouveau front).

L'UI humaine cible est le front Next.js (`web/`) ; pendant la migration,
l'application fonctionnelle reste le Streamlit (`app/main.py`). Cette API
n'importe jamais streamlit : elle parle directement à Mongo et Ollama.

Lancer : `python serve.py --web` (ou `uvicorn api.main:app --port 8000`).
"""
from __future__ import annotations

import json
import re
import socket
import time

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pymongo import MongoClient

from env_config import MONGO_URI, MONGO_DB
from core import ingest_queue

app = FastAPI(title="AI for SSH — API")
# Front Next.js local (`web/`) : REST cross-origin depuis :3000.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# LynX (AI for Requirements) : routeur dédié, importé paresseusement pour ne
# pas payer l'init de lynx/src au démarrage si on n'utilise que le RAG.
from api.lynx_api import router as lynx_router  # noqa: E402
app.include_router(lynx_router)

_client: MongoClient | None = None


def _chunks_col():
    """Collection `chunks` (client Mongo paresseux, timeout court : l'API doit
    répondre vite même si Mongo est éteint)."""
    global _client
    if _client is None:
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=1500)
    return _client[MONGO_DB]["chunks"]


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    s = socket.socket()
    s.settimeout(0.3)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


@app.get("/health")
def health() -> dict:
    """Sonde de vivacité + état des services locaux (affiché par le front)."""
    return {
        "status": "ok",
        "services": {
            "mongo": _port_open(27017),
            "ollama": _port_open(11434),
        },
    }


class AskBody(BaseModel):
    """Requête du chat : question + périmètre (un document, ou null = tous)
    + historique de conversation (géré côté client) + options de recherche
    par requête (None = défaut .env)."""
    question: str
    source: str | None = None
    history: list[dict] = []
    parent_child: bool | None = None
    self_rag: bool | None = None
    system_prompt: str | None = None


def _trim_chunk(c: dict) -> dict:
    """Réduit un chunk aux champs utiles à l'affichage (même logique que le
    panneau « Passages récupérés » du Streamlit : contenu intégral + méta)."""
    meta = c.get("meta", {})
    keep = ("source", "page_number", "heading", "breadcrumb", "section_idx",
            "chunk_type", "keywords_str", "questions_str", "entities_str",
            "summary_num_chunks")
    return {"doc": c.get("doc", ""), "ce_score": c.get("ce_score"),
            "meta": {k: meta.get(k) for k in keep if meta.get(k) is not None}}


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post("/api/ask")
def ask(body: AskBody) -> StreamingResponse:
    """Q&A RAG en SSE — boîte de verre : chaque étape du pipeline est un
    événement (`stage`, `retrieved`, `token`…, `done`), les erreurs une trame
    `error` (le front ne pend jamais).

    Import paresseux de core.ask : le premier appel charge les modèles
    (Chroma, reranker) ; l'API démarre vite.
    """
    def gen():
        try:
            from core.ask import process_query_stream

            yield _sse({"type": "stage", "stage": "retrieve"})
            token_gen, chunks, citations = process_query_stream(
                body.question,
                source_filter=body.source,
                conversation_history=body.history or [],
                parent_child_on=body.parent_child,
                self_rag_enabled=body.self_rag,
                system_prompt=body.system_prompt or None,
            )
            yield _sse({"type": "retrieved",
                        "chunks": [_trim_chunk(c) for c in (chunks or [])]})

            if token_gen is None:
                yield _sse({"type": "done", "found": False})
                return

            yield _sse({"type": "stage", "stage": "generate"})
            for token in token_gen:
                yield _sse({"type": "token", "text": token})
            yield _sse({"type": "sources", "citations": citations or []})
            yield _sse({"type": "done", "found": bool(chunks)})
        except Exception as e:  # Ollama/Mongo coupé, timeout… -> trame lisible
            yield _sse({"type": "error",
                        "message": f"{type(e).__name__}: {str(e)[:200]}"})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store"})


@app.get("/api/sources")
def sources() -> dict:
    """Documents ingérés : nom + nombre de chunks (depuis Mongo).

    Renvoie `available: false` si Mongo est injoignable — le front affiche
    alors un état dégradé au lieu d'une erreur.
    """
    try:
        rows = list(_chunks_col().aggregate([
            {"$match": {"source": {"$ne": None}}},
            {"$group": {"_id": "$source", "chunks": {"$sum": 1}}},
            {"$sort": {"_id": 1}},
        ]))
    except Exception:
        return {"available": False, "sources": []}
    return {
        "available": True,
        "sources": [{"name": r["_id"], "chunks": r["chunks"]} for r in rows],
    }


# ─────────────── Documents & ingestion ───────────────

@app.get("/api/ingest/defaults")
def ingest_defaults() -> dict:
    """Options d'ingestion par défaut (.env) + types de fichiers acceptés."""
    return {"params": ingest_queue.default_params(),
            "upload_types": list(ingest_queue.UPLOAD_TYPES)}


@app.post("/api/documents")
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


@app.get("/api/ingest/status")
def ingest_status() -> dict:
    """File d'ingestion : une entrée par document, dans l'ordre de lancement."""
    jobs = []
    for j in ingest_queue.snapshot():
        res = j.get("result") or {}
        jobs.append({
            "id": j["id"], "name": j["name"], "status": j["status"],
            "pct": j["pct"], "step": j["step"],
            "elapsed": (int(time.time() - j["t0"]) if j["status"] == "running" and j["t0"]
                        else int((j["t_end"] or 0) - (j["t0"] or 0)) or None),
            "num_chunks": res.get("num_chunks"),
            "message": res.get("message"),
        })
    return {"active": ingest_queue.active(), "jobs": jobs}


@app.post("/api/ingest/clear")
def ingest_clear() -> dict:
    ingest_queue.clear_finished()
    return {"ok": True}


@app.delete("/api/documents/{name}")
def delete_document(name: str) -> dict:
    """Supprime un document de TOUS les index : chunks Mongo, vecteurs Chroma,
    index BM25. (Le Streamlit ne purgeait que Mongo ; ici le retrait est complet.)"""
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=1500)
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


# ─────────────── Observabilité & paramètres (mode expert) ───────────────

@app.get("/api/perf")
def perf_stats() -> dict:
    """Performance d'inférence de CE process API : tokens/s, time-to-first-token,
    latence (stats exactes Ollama) + VRAM live."""
    from utils import perf
    rows = perf.snapshot()
    return {"gpu": [{"used": int(u), "total": int(t)} for u, t in (perf.gpu_memory() or [])],
            "aggregate": perf.aggregate(rows) if rows else None,
            "rows": [{"model": r["model"], "gen_tokens": r["gen_tokens"],
                      "tok_per_s": round(r["tok_per_s"], 1) if r["tok_per_s"] else None,
                      "ttft_s": round(r["ttft_s"], 2) if r["ttft_s"] else None,
                      "total_s": round(r["total_s"], 1)} for r in rows[:30]]}


@app.get("/api/traces")
def traces(limit: int = 20) -> dict:
    """Dernières traces de requêtes (spans chronométrés, persistés en Mongo)."""
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=1500)
        rows = list(client[MONGO_DB]["traces"].find().sort("_id", -1).limit(max(1, min(limit, 100))))
    except Exception:
        return {"available": False, "traces": []}

    def strip(s: dict) -> dict:
        return {"name": s.get("name"), "duration_ms": s.get("duration_ms"),
                "metadata": {k: v for k, v in (s.get("metadata") or {}).items()},
                "children": [strip(c) for c in s.get("children", [])]}

    return {"available": True,
            "traces": [{"timestamp": str(t.get("timestamp", "")),
                        "duration_ms": t.get("duration_ms"),
                        "query": (t.get("metadata") or {}).get("query", ""),
                        "hors_scope": bool((t.get("metadata") or {}).get("hors_scope")),
                        "spans": [strip(c) for c in t.get("children", [])]}
                       for t in rows]}


@app.get("/api/models")
def models() -> dict:
    """Modèles Ollama disponibles + routage par rôle + état du magasin vectoriel."""
    import requests
    try:
        from env_config import OLLAMA_HOST
        tags = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5).json()
        names = [m["name"] for m in tags.get("models", [])]
    except Exception:
        names = []
    routing = {}
    try:
        from core.model_router import routing_table
        routing = routing_table()
    except Exception:
        pass
    vectors = None
    try:
        from retrieval.vector_store import get_vector_store
        vectors = get_vector_store().count()
    except Exception:
        pass
    return {"models": names, "routing": routing, "vectors": vectors}


class ModelAction(BaseModel):
    model: str
    action: str  # "load" (épingle en VRAM + active à chaud) | "unload"


@app.post("/api/models/generate")
def set_generation_model(body: ModelAction) -> dict:
    """Changement à chaud du modèle de génération + gestion VRAM (comme les
    boutons Charger/Décharger du Streamlit)."""
    import requests
    from env_config import OLLAMA_HOST
    try:
        if body.action == "load":
            from core.model_router import set_generate_model
            set_generate_model(body.model)
        keep = -1 if body.action == "load" else 0
        requests.post(f"{OLLAMA_HOST}/api/generate",
                      json={"model": body.model, "keep_alive": keep}, timeout=30)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(502, f"Ollama : {e}")


@app.get("/api/settings")
def get_settings() -> dict:
    """Valeurs .env actuelles (celles que la vue Paramètres peut modifier)
    + le system prompt par défaut."""
    import env_config as cfg
    from core.llm_answer import DEFAULT_SYSTEM_PROMPT
    return {"values": {
        "NUM_CHUNKS": cfg.NUM_CHUNKS,
        "EMBED_MODEL": cfg.EMBED_MODEL,
        "GEN_MODEL": cfg.GEN_MODEL,
        "WEIGHT_SEMANTIC": cfg.WEIGHT_SEMANTIC,
        "WEIGHT_BM25": cfg.WEIGHT_BM25,
        "CE_RELEVANCE_THRESHOLD": cfg.CE_RELEVANCE_THRESHOLD,
        "AUTO_KEYWORDS": cfg.AUTO_KEYWORDS,
        "AUTO_QUESTIONS": cfg.AUTO_QUESTIONS,
        "CHUNKING_MODE": cfg.CHUNKING_MODE,
        "RAPTOR_SUMMARIES": cfg.RAPTOR_SUMMARIES,
        "SELF_RAG_ENABLED": cfg.SELF_RAG_ENABLED,
        "SELF_RAG_THRESHOLD": cfg.SELF_RAG_THRESHOLD,
        "SELF_RAG_MAX_RETRIES": cfg.SELF_RAG_MAX_RETRIES,
    }, "default_system_prompt": DEFAULT_SYSTEM_PROMPT}


class EnvUpdates(BaseModel):
    updates: dict[str, str]

_ENV_ALLOWED = {"NUM_CHUNKS", "EMBED_MODEL", "GEN_MODEL", "WEIGHT_SEMANTIC",
                "WEIGHT_BM25", "CE_RELEVANCE_THRESHOLD", "AUTO_KEYWORDS",
                "AUTO_QUESTIONS", "CHUNKING_MODE", "RAPTOR_SUMMARIES",
                "SELF_RAG_ENABLED", "SELF_RAG_THRESHOLD", "SELF_RAG_MAX_RETRIES"}


@app.post("/api/settings")
def save_settings(body: EnvUpdates) -> dict:
    """Écrit les réglages dans .env (liste blanche). Prend effet au prochain
    démarrage de l'API (comme dans le Streamlit)."""
    updates = {k: v for k, v in body.updates.items() if k in _ENV_ALLOWED}
    if not updates:
        raise HTTPException(400, "Aucune clé autorisée dans la demande.")
    from pathlib import Path
    env_path = Path(__file__).resolve().parent.parent / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    remaining = dict(updates)
    out = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            key = s.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)
    out.extend(f"{k}={v}" for k, v in remaining.items())
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return {"ok": True, "saved": sorted(updates)}


@app.post("/api/corpus/reset")
def corpus_reset() -> dict:
    """Vide l'index documentaire local (Chroma + chunks/BM25/graphe Mongo)
    SANS toucher aux sessions ni aux traces. Même geste que le Streamlit."""
    report = []
    try:
        db = MongoClient(MONGO_URI, serverSelectionTimeoutMS=1500)[MONGO_DB]
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


class VerifyBody(BaseModel):
    question: str
    answer: str
    chunks: list[dict]


@app.post("/api/verify")
def verify(body: VerifyBody) -> dict:
    """Vérification LLM-as-judge de la dernière réponse (à la demande) :
    fidélité aux sources, pertinence réponse/contexte + points à vérifier."""
    from core.evaluation import verify_answer
    return verify_answer(body.question, body.answer, body.chunks) or {}


@app.get("/api/documents/{name}/chunks")
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
        "keywords_str": r.get("keywords_str"), "questions_str": r.get("questions_str"),
        "entities_str": r.get("entities_str"),
    } for r in rows]
    return {"total": len(chunks), "chunks": chunks}
