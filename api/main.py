"""API FastAPI (backend du nouveau front) — point d'entrée.

L'UI humaine est le front Next.js (`web/`), servi par cette API. Elle parle
directement à Mongo et Ollama.

Ce module ne fait QUE l'assemblage ; chaque domaine vit dans son routeur :
  - api/rag.py        couche Q&A : RAG direct + agent ReAct, streaming SSE
  - api/sessions.py   sessions de conversation persistées
  - api/documents.py  documents & ingestion (upload, chunks, purge)
  - api/system.py     observabilité & paramètres (perf, traces, modèles, .env)
  - api/lynx_api.py   LynX (AI for Requirements)
  - api/common.py     utilitaires partagés (Mongo, SSE, mise en forme chunk)

Lancer : `python serve.py` (ou `uvicorn api.main:app --port 8000`).
"""
from __future__ import annotations

import os
import socket

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="AI for SSH — API")
# Front Next.js local (`web/`) : REST cross-origin depuis :3000 (défaut).
# WEB_ORIGINS (env, séparées par des virgules) autorise d'autres origines —
# ex. un front de worktree sur :3001 quand deux lanes tournent en parallèle.
_ORIGINS = os.environ.get(
    "WEB_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _ORIGINS.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

# LynX (AI for Requirements) : routeur dédié, importé paresseusement pour ne
# pas payer l'init de lynx/src au démarrage si on n'utilise que le RAG.
from api.lynx_api import router as lynx_router  # noqa: E402
from api.rag import router as rag_router        # noqa: E402
from api.sessions import router as sessions_router  # noqa: E402
from api.documents import router as documents_router  # noqa: E402
from api.system import router as system_router  # noqa: E402
from api.prompts import router as prompts_router  # noqa: E402
from api.lynx_chat import router as lynx_chat_router  # noqa: E402

app.include_router(lynx_router)
app.include_router(lynx_chat_router)
app.include_router(rag_router)
app.include_router(sessions_router)
app.include_router(documents_router)
app.include_router(system_router)
app.include_router(prompts_router)


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
