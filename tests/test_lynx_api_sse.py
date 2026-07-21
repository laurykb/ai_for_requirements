"""Smoke tests de l'ossature SSE partagée des endpoints LynX (`_sse_stream`).

Cette couche n'avait aucune couverture : on vérifie ici le chemin nominal et le
chemin d'erreur du helper, hors LLM/Mongo (le `run` est un faux travail). C'est
le filet ajouté avec la factorisation des 5 endpoints (analyze, audit,
audit/fix, generate, eval) qui partageaient tous le même échafaudage.
"""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.lynx_api import _sse_stream


def _frames(text: str) -> list[dict]:
    """Décode les trames `data: {json}` d'un flux SSE."""
    return [json.loads(line[len("data: "):])
            for line in text.splitlines() if line.startswith("data: ")]


def _client(run, **kw) -> TestClient:
    app = FastAPI()

    @app.post("/t")
    def _t():
        return _sse_stream(run, **kw)

    return TestClient(app)


def test_sse_stream_nominal_emits_events_then_terminates():
    def run(emit, cancelled):
        emit({"type": "progress", "done": 1, "total": 2})
        emit({"type": "result", "ok": True})
        emit({"type": "done"})

    r = _client(run).post("/t")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert _frames(r.text) == [
        {"type": "progress", "done": 1, "total": 2},
        {"type": "result", "ok": True},
        {"type": "done"},
    ]


def test_sse_stream_exception_becomes_error_frame():
    def run(emit, cancelled):
        emit({"type": "progress", "done": 0, "total": 1})
        raise ValueError("boom")

    frames = _frames(_client(run).post("/t").text)
    assert frames[0] == {"type": "progress", "done": 0, "total": 1}
    assert frames[-1]["type"] == "error"
    assert "ValueError" in frames[-1]["message"]
    assert "boom" in frames[-1]["message"]


def test_sse_stream_custom_cancel_exception_is_swallowed():
    """Une exception d'annulation déclarée (comme BatchCancelled) ne produit PAS
    de trame `error` — le flux se termine proprement."""
    class _Stop(Exception):
        pass

    def run(emit, cancelled):
        emit({"type": "progress", "done": 0, "total": 1})
        raise _Stop()

    frames = _frames(_client(run, cancel_excs=(_Stop,)).post("/t").text)
    assert frames == [{"type": "progress", "done": 0, "total": 1}]
    assert all(f["type"] != "error" for f in frames)
