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


# Contrat d export : toute méthode appelée par le front doit rester déclarée.
FRONTEND_ROUTE_CONTRACT = {
    ("GET", "/health"), ("GET", "/api/sources"), ("POST", "/api/documents"),
    ("DELETE", "/api/documents/{name}"), ("POST", "/api/documents/{name}/summary"),
    ("GET", "/api/documents/{name}/markdown"), ("GET", "/api/documents/{name}/chunks"),
    ("GET", "/api/ingest/defaults"), ("GET", "/api/ingest/status"), ("POST", "/api/ingest/clear"),
    ("GET", "/api/sessions"), ("GET", "/api/sessions/{sid}/messages"),
    ("PATCH", "/api/sessions/{sid}"), ("DELETE", "/api/sessions/{sid}"),
    ("DELETE", "/api/sessions/{sid}/last-exchange"), ("POST", "/api/ask"),
    ("POST", "/api/regenerate"), ("GET", "/api/models"), ("POST", "/api/models/generate"),
    ("POST", "/api/models/routing"),
    ("POST", "/api/models/arsenal"),
    ("GET", "/api/settings"), ("POST", "/api/settings"),
    ("POST", "/api/corpus/reset"), ("GET", "/api/task-metrics"),
    ("GET", "/api/perf"), ("GET", "/api/traces"), ("POST", "/api/verify"),
    ("GET", "/api/prompts"), ("PUT", "/api/prompts/{key}"),
    ("DELETE", "/api/prompts/{key}"),
    ("GET", "/api/lynx/corpus"), ("DELETE", "/api/lynx/corpus"),
    ("GET", "/api/lynx/corpus/status"), ("GET", "/api/lynx/corpus/health"),
    ("GET", "/api/lynx/corpus/impact"), ("GET", "/api/lynx/corpus/issues"),
    ("GET", "/api/lynx/corpus/drafts"), ("GET", "/api/lynx/corpus/drafts/{draft_id}"),
    ("DELETE", "/api/lynx/corpus/drafts/{draft_id}"),
    ("DELETE", "/api/lynx/corpus/drafts/{draft_id}/sources/{batch_id}"),
    ("POST", "/api/lynx/corpus/preview/stream"),
    ("POST", "/api/lynx/corpus/drafts/{draft_id}/collisions/{req_id}"),
    ("POST", "/api/lynx/corpus/drafts/{draft_id}/activate"),
    ("GET", "/api/lynx/corpus/versions"), ("POST", "/api/lynx/corpus/versions/{version_id}/restore"),
    ("GET", "/api/lynx/requirements"), ("GET", "/api/lynx/requirements/facets"),
    ("GET", "/api/lynx/requirements/{req_id}"),
    ("GET", "/api/lynx/requirements/{req_id}/relations"),
    ("POST", "/api/lynx/requirements/{req_id}/root-status"),
    ("POST", "/api/lynx/analyze"), ("POST", "/api/lynx/apply"),
    ("POST", "/api/lynx/audit"), ("POST", "/api/lynx/audit/fix"),
    ("POST", "/api/lynx/audit/fix/apply"), ("POST", "/api/lynx/correct"),
    ("POST", "/api/lynx/feedback"), ("POST", "/api/lynx/model"),
    ("GET", "/api/lynx/runs"), ("GET", "/api/lynx/runs/{run_id}"),
    ("GET", "/api/lynx/skills"), ("PUT", "/api/lynx/skills/{name}"),
    ("GET", "/api/lynx/orchestration"), ("PUT", "/api/lynx/orchestration"),
    ("POST", "/api/lynx/chat/sync"), ("GET", "/api/lynx/chat/status"),
    ("GET", "/api/lynx/chat/examples"), ("GET", "/api/lynx/chat/coverage"),
    ("GET", "/api/lynx/chat/versions"), ("POST", "/api/lynx/chat/versions/restore"),
}

def test_frontend_route_contract_is_exposed_by_openapi():
    from api.main import app
    paths = app.openapi()["paths"]
    exposed = {(method.upper(), path) for path, methods in paths.items() for method in methods}
    assert FRONTEND_ROUTE_CONTRACT <= exposed
