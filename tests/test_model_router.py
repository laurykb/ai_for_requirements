"""
Tests unitaires du routage de modèles.

Hors-ligne : on teste `llm_kwargs` (pur, ne construit aucun client Ollama) et la table
de routage. On vérifie surtout la NON-RÉGRESSION : les hyperparamètres par rôle doivent
reproduire à l'identique ceux qui étaient dispersés dans le code.
"""
import pytest

from core.model_router import model_for, routing_table, llm_kwargs, ollama_options
from env_config import (
    REWRITER_MODEL, GEN_MODEL, AGENT_MODEL, ENHANCEMENT_MODEL,
    NUM_CHUNKS, LLM_NUM_CTX,
)


def test_routing_table_covers_all_roles():
    table = routing_table()
    assert set(table) == {"rewrite", "agent", "generate", "judge", "enhance"}
    assert all(isinstance(m, str) and m for m in table.values())


def test_model_for_maps_roles_to_configured_models():
    assert model_for("rewrite") == REWRITER_MODEL
    assert model_for("agent") == AGENT_MODEL
    assert model_for("generate") == GEN_MODEL
    assert model_for("judge") == REWRITER_MODEL  # défaut historique du juge
    # enhance : override explicite ENHANCEMENT_MODEL, sinon repli sur REWRITER_MODEL.
    assert model_for("enhance") == (ENHANCEMENT_MODEL or REWRITER_MODEL)


def test_model_for_unknown_role_raises():
    with pytest.raises(ValueError):
        model_for("does_not_exist")


def test_generate_params_match_legacy_tuning():
    kw = llm_kwargs("generate")
    assert kw["model"] == GEN_MODEL
    assert kw["temperature"] == 0.3
    assert kw["top_k"] == NUM_CHUNKS
    assert kw["top_p"] == 0.8
    assert kw["repeat_penalty"] == 1.5
    assert kw["num_ctx"] == LLM_NUM_CTX
    # Non-streaming : pas de keep_alive (= défaut Ollama), comme avant.
    assert "keep_alive" not in kw


def test_generate_keep_alive_override_for_streaming():
    assert llm_kwargs("generate", keep_alive=-1)["keep_alive"] == -1
    # None est ignoré (= garder le défaut du rôle), pas de clé parasite.
    assert "keep_alive" not in llm_kwargs("generate", keep_alive=None)


def test_rewrite_and_agent_params():
    # keep_alive n'est PLUS forcé (anti-wedge VRAM) : le cycle de vie est au serveur.
    rw = llm_kwargs("rewrite")
    assert rw == {"temperature": 0.2, "model": REWRITER_MODEL}
    ag = llm_kwargs("agent")
    assert ag == {"temperature": 0.1, "model": AGENT_MODEL}


def test_enhance_options_match_legacy_tuning():
    # Non-régression : ollama_options('enhance') reproduit EXACTEMENT les options
    # qui étaient codées en dur dans nlp/chunk_enhancer._call_ollama, et n'expose
    # pas le nom du modèle (le transport HTTP le pose lui-même).
    opts = ollama_options("enhance")
    assert opts == {"temperature": 0.1, "top_p": 0.9, "num_predict": 300}
    assert "model" not in opts


def test_no_role_forces_keep_alive_forever():
    # Garde-fou de non-régression : aucun rôle ne doit re-pinner un modèle « Forever ».
    for role in ("rewrite", "agent", "generate", "judge", "enhance"):
        assert "keep_alive" not in llm_kwargs(role)


def test_model_override_forces_specific_model():
    # Le juge peut être forcé sur un modèle dédié (routage prod).
    assert llm_kwargs("judge", model="qwen2.5:14b")["model"] == "qwen2.5:14b"
