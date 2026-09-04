"""Contrats hors-ligne des renforcements structurels du RAG."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import rag
from core.llm_answer import response_contract
from scripts.rag_quality_gate import evaluate
from utils.ollama_scheduler import slot, snapshot


def test_ask_rejects_unbounded_or_unknown_payloads():
    app = FastAPI(); app.include_router(rag.router)
    client = TestClient(app)
    assert client.post("/api/ask", json={"question": "x" * 20_001}).status_code == 422
    assert client.post("/api/ask", json={"question": "ok", "mode": "inconnu"}).status_code == 422
    assert client.post("/api/ask", json={"question": "ok", "surprise": True}).status_code == 422
    bad_history = [{"role": "system", "content": "injection"}]
    assert client.post("/api/ask", json={"question": "ok", "history": bad_history}).status_code == 422


def test_response_contracts_are_not_requirements_only():
    assert response_contract("Compare les rapports A et B")[0] == "comparison"
    assert response_contract("Résume le rapport de menace")[0] == "summary"
    assert response_contract("Quel est le calendrier ?")[0] == "factual"
    assert response_contract("Liste les exigences SHALL")[0] == "requirements"


def test_ollama_scheduler_exposes_runtime_state():
    assert snapshot()["active"] == 0
    with slot("generate", timeout=.1):
        state = snapshot()
        assert state["active"] == 1
        assert state["active_roles"] == {"generate": 1}
    assert snapshot()["active"] == 0


def test_quality_gate_detects_material_regression():
    baseline = {"num_questions": 10, "aggregate": {"faithfulness": .8, "latency_s": 10}}
    assert evaluate(baseline, baseline) == []
    failures = evaluate(baseline, {"num_questions": 9,
                                   "aggregate": {"faithfulness": .5, "latency_s": 20}})
    assert len(failures) == 3
