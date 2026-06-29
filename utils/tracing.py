"""
Tracing léger pour le pipeline RAG (observabilité, 100 % local).

Une trace (une requête de bout en bout) se décompose en spans imbriqués et
chronométrés (retrieval, rerank, génération...), chacun portant une durée et des
métadonnées (nb de chunks, etc.). Les traces sont persistées dans MongoDB (collection
`traces`) et gardées en mémoire (tampon récent) pour l'affichage.

Usage :
    from utils.tracing import start_trace, span
    with start_trace("rag.query", query=q) as tr:
        with span("retrieval") as s:
            ...
            s.set("num_chunks", 8)
        tr.set("answer_len", len(answer))

Désactivable via RAG_TRACING=false. Toujours sans effet de bord si Mongo est down.
"""
from __future__ import annotations

import os
import time
import contextvars
from collections import deque
from contextlib import contextmanager

from utils.logging_config import get_logger

logger = get_logger("rag.tracing")

_ENABLED = os.environ.get("RAG_TRACING", "true").lower() in ("true", "1", "yes")
_current_trace: contextvars.ContextVar = contextvars.ContextVar("rag_trace", default=None)

# Tampon mémoire des dernières traces (pour l'UI sans aller-retour Mongo).
_RECENT: deque = deque(maxlen=50)


class _Span:
    __slots__ = ("name", "metadata", "_start", "duration_ms", "children")

    def __init__(self, name: str, metadata: dict):
        self.name = name
        self.metadata = dict(metadata)
        self._start = time.perf_counter()
        self.duration_ms = None
        self.children = []

    def set(self, key: str, value):
        """Attache une métadonnée au span (ex: tokens, num_chunks)."""
        self.metadata[key] = value

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
            "children": [c.to_dict() for c in self.children],
        }


class Trace:
    def __init__(self, name: str, metadata: dict):
        self._start = time.perf_counter()
        self.timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.root = _Span(name, metadata)
        self._stack = [self.root]

    @property
    def name(self) -> str:
        return self.root.name

    def set(self, key: str, value):
        """Attache une métadonnée à la trace (sur le span racine)."""
        self.root.metadata[key] = value

    def to_dict(self) -> dict:
        d = self.root.to_dict()
        d["timestamp"] = self.timestamp
        return d


class _NullTrace:
    """Trace inerte (tracing désactivé) : mêmes méthodes, aucun effet."""
    def set(self, *_a, **_k):
        pass


def _persist(trace: Trace):
    doc = trace.to_dict()
    _RECENT.appendleft(doc)
    try:
        from pymongo import MongoClient
        from env_config import MONGO_URI, MONGO_DB
        MongoClient(MONGO_URI, serverSelectionTimeoutMS=500)[MONGO_DB]["traces"].insert_one(dict(doc))
    except Exception as e:  # Mongo down / indisponible : on ne casse jamais une requête
        logger.debug("Trace non persistée (%s)", e)


@contextmanager
def start_trace(name: str, **metadata):
    """Ouvre une trace de bout en bout. Persiste à la fermeture."""
    if not _ENABLED:
        yield _NullTrace()
        return
    tr = Trace(name, metadata)
    token = _current_trace.set(tr)
    try:
        yield tr
    finally:
        tr.root.duration_ms = round((time.perf_counter() - tr._start) * 1000, 1)
        _current_trace.reset(token)
        _persist(tr)


@contextmanager
def span(name: str, **metadata):
    """Ouvre un span imbriqué sous la trace courante. No-op si aucune trace active."""
    tr = _current_trace.get()
    if tr is None:
        yield None
        return
    sp = _Span(name, metadata)
    tr._stack[-1].children.append(sp)
    tr._stack.append(sp)
    try:
        yield sp
    finally:
        sp.duration_ms = round((time.perf_counter() - sp._start) * 1000, 1)
        tr._stack.pop()


def recent_traces(limit: int = 20) -> list[dict]:
    """Dernières traces en mémoire (pour l'UI)."""
    return list(_RECENT)[:limit]
