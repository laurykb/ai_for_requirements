"""Contrat de qualité temps réel : SSE + persistance, entièrement offline."""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.rag as rag


def _events(text):
    return [json.loads(line[6:]) for line in text.splitlines()
            if line.startswith("data: ")]


def test_rag_emits_deterministic_metrics_without_automatic_judge(monkeypatch):
    import core.ask
    chunk = {"doc": "preuve", "meta": {"source": "d.md"}}
    monkeypatch.setattr(core.ask, "process_query_stream",
                        lambda *a, **k: (iter(["réponse"]), [chunk], [{"idx": 1, "source": "d.md"}]))
    monkeypatch.setattr(rag, "_persist_exchange", lambda *a, **k: None)
    monkeypatch.setattr(rag, "_run_attribution", lambda *a, **k: {"ok": False})
    app = FastAPI(); app.include_router(rag.router)
    evs = _events(TestClient(app).post("/api/ask", json={
        "question": "q", "mode": "rag", "session_id": "s"}).text)
    kinds = [e["type"] for e in evs]
    assert "eval" not in kinds
    assert kinds.index("done") < kinds.index("task_metrics")
    metrics = next(e["metrics"] for e in evs if e["type"] == "task_metrics")
    assert metrics["quality"]["semantic_judge"] == "not_run"
