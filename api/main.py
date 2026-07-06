"""API FastAPI (backend du nouveau front).

L'UI humaine cible est le front Next.js (`web/`) ; pendant la migration,
l'application fonctionnelle reste le Streamlit (`app/main.py`). Cette API
n'importe jamais streamlit : elle parle directement à Mongo et Ollama.

Lancer : `python serve.py --web` (ou `uvicorn api.main:app --port 8000`).
"""
from __future__ import annotations

import socket

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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
