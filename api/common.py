"""Utilitaires partagés par les routeurs de l'API (aucune route ici).

Ce module ne doit JAMAIS importer les routeurs (api.rag, api.documents…)
sous peine d'import circulaire : il ne dépend que de la config et de Mongo.
"""
from __future__ import annotations

import json

from pymongo import MongoClient

from env_config import MONGO_URI, MONGO_DB

_client: MongoClient | None = None


def _chunks_col():
    """Collection `chunks` (client Mongo paresseux, timeout court : l'API doit
    répondre vite même si Mongo est éteint)."""
    global _client
    if _client is None:
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=1500)
    return _client[MONGO_DB]["chunks"]


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
    """Encode une trame Server-Sent Events (une ligne `data:` + ligne vide)."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
