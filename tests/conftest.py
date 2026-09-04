"""Isolation des effets de bord de la suite principale."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_trace_persistence(monkeypatch):
    """Conserve les traces en mémoire sans écrire dans la base applicative."""
    import utils.tracing as tracing

    monkeypatch.setattr(
        tracing, "_persist", lambda trace: tracing._RECENT.appendleft(trace.to_dict())
    )
