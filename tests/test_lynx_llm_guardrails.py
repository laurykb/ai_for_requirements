"""Garde-fous multi-agent LynX : routage par rôle + budget d'appels LLM."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lynx"))
from src import llm  # noqa: E402


def test_model_for_routing(monkeypatch):
    monkeypatch.delenv("LYNX_MODEL_DEFAULT", raising=False)
    assert llm.model_for("coherence") == llm.current_model()
    monkeypatch.setenv("LYNX_MODEL_COHERENCE", "qwen3.5:latest")
    assert llm.model_for("coherence") == "qwen3.5:latest"
    assert llm.model_for("autre-skill") == llm.current_model()
    monkeypatch.setenv("LYNX_MODEL_DEFAULT", "llama3.1:latest")
    assert llm.model_for("autre-skill") == "llama3.1:latest"
    assert llm.model_for("coherence") == "qwen3.5:latest"  # spécifique > défaut


def test_llm_call_budget(monkeypatch):
    import pytest
    monkeypatch.setenv("LYNX_MAX_LLM_CALLS", "2")
    llm.start_trace()
    llm._count_llm_call()
    llm._count_llm_call()
    assert llm.llm_calls_in_trace() == 2
    with pytest.raises(llm.LynxBudgetExceeded):
        llm._count_llm_call()
    llm.stop_trace()
    monkeypatch.setenv("LYNX_MAX_LLM_CALLS", "0")  # désactivé
    llm.start_trace()
    for _ in range(5):
        llm._count_llm_call()
    assert llm.llm_calls_in_trace() == 5
    llm.stop_trace()
