"""Ordonnanceur process-wide des générations Ollama.

Évite que génération, agents, juges et enrichissement se disputent la VRAM.
Les priorités basses correspondent aux interactions utilisateur.
"""
from __future__ import annotations

from contextlib import contextmanager
import heapq
import itertools
import os
import threading
import time

_LIMIT = max(1, min(int(os.environ.get("OLLAMA_MAX_CONCURRENT", "1")), 8))
_CV = threading.Condition()
_WAITING: list[tuple[int, int, object]] = []
_SEQ = itertools.count()
_ACTIVE = 0
_ACTIVE_ROLES: dict[str, int] = {}

_PRIORITY = {"generate": 0, "embed": 0, "agent": 1, "planner": 1, "rewrite": 2,
             "judge": 3, "synthesize": 4, "extract": 4, "enhance": 5}
_PRIORITY["embed_ingest"] = 5


@contextmanager
def slot(role: str | None = None, timeout: float = 600):
    global _ACTIVE
    role = role or "unknown"
    ticket = object()
    entry = (_PRIORITY.get(role, 3), next(_SEQ), ticket)
    deadline = time.monotonic() + timeout
    with _CV:
        heapq.heappush(_WAITING, entry)
        while _ACTIVE >= _LIMIT or not _WAITING or _WAITING[0][2] is not ticket:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _WAITING[:] = [item for item in _WAITING if item[2] is not ticket]
                heapq.heapify(_WAITING)
                raise TimeoutError("Délai d attente Ollama dépassé.")
            _CV.wait(min(remaining, 1.0))
        heapq.heappop(_WAITING)
        _ACTIVE += 1
        _ACTIVE_ROLES[role] = _ACTIVE_ROLES.get(role, 0) + 1
    try:
        yield
    finally:
        with _CV:
            _ACTIVE -= 1
            _ACTIVE_ROLES[role] -= 1
            if not _ACTIVE_ROLES[role]:
                del _ACTIVE_ROLES[role]
            _CV.notify_all()


def snapshot() -> dict:
    with _CV:
        return {"limit": _LIMIT, "active": _ACTIVE, "waiting": len(_WAITING),
                "active_roles": dict(_ACTIVE_ROLES)}
