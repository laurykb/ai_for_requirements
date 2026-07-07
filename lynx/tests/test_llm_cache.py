"""Tests du cache disque des appels LLM (survit au process, clé = modèle+prompt+entrée)."""

from src import llm


def _fake_chat(calls):
    def fake(system_prompt, user_data, temperature=0, label=None, schema=None,
             grammar=True, validate_retry=True):
        calls.append(label)
        return {"verdict": "MAINTENU", "motivation": "ok"}
    return fake


def _actif(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "LLM_CACHE_DISK", True)
    monkeypatch.setattr(llm, "LLM_CACHE_DIR", tmp_path)
    llm.clear_cache()


def test_cache_disque_survit_au_process(monkeypatch, tmp_path):
    _actif(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(llm, "_chat", _fake_chat(calls))

    r1 = llm.call_agent("prompt du juge", {"q": 1}, label="juge_verdict")
    assert r1["verdict"] == "MAINTENU" and len(calls) == 1
    assert list(tmp_path.glob("*.json"))  # écrit sur disque

    # « Nouveau process » : cache mémoire vidé -> le disque répond, pas le LLM.
    llm.clear_cache()
    r2 = llm.call_agent("prompt du juge", {"q": 1}, label="juge_verdict")
    assert r2 == r1 and len(calls) == 1


def test_prompt_modifie_invalide_le_cache(monkeypatch, tmp_path):
    _actif(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(llm, "_chat", _fake_chat(calls))

    llm.call_agent("prompt v1", {"q": 1}, label="juge_verdict")
    llm.clear_cache()
    # Prompt modifié : la clé change, l'appel repart au LLM (pas de verdict périmé).
    llm.call_agent("prompt v2 durci", {"q": 1}, label="juge_verdict")
    assert len(calls) == 2


def test_erreur_jamais_mise_en_cache(monkeypatch, tmp_path):
    _actif(monkeypatch, tmp_path)

    def fail(*a, **k):
        return {"error": "TIMEOUT"}
    monkeypatch.setattr(llm, "_chat", fail)
    llm.call_agent("p", {"q": 1}, label="juge_verdict")
    assert not list(tmp_path.glob("*.json"))


def test_cache_disque_desactivable(monkeypatch, tmp_path):
    _actif(monkeypatch, tmp_path)
    monkeypatch.setattr(llm, "LLM_CACHE_DISK", False)
    calls = []
    monkeypatch.setattr(llm, "_chat", _fake_chat(calls))
    llm.call_agent("p", {"q": 1}, label="juge_verdict")
    assert not list(tmp_path.glob("*.json"))
