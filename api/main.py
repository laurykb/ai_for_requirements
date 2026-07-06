"""API FastAPI (backend du nouveau front).

L'UI humaine cible est le front Next.js (`web/`) ; pendant la migration,
l'application fonctionnelle reste le Streamlit (`app/main.py`). Cette API
n'importe jamais streamlit : elle parle directement à Mongo et Ollama.

Lancer : `python serve.py --web` (ou `uvicorn api.main:app --port 8000`).
"""
from __future__ import annotations

import json
import socket

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pymongo import MongoClient

from env_config import MONGO_URI, MONGO_DB

app = FastAPI(title="AI for SSH — API")
# Front Next.js local (`web/`) : REST cross-origin depuis :3000.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    + historique de conversation (géré côté client pour l'instant)."""
    question: str
    source: str | None = None
    history: list[dict] = []


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
