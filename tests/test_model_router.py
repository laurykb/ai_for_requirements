"""
Tests unitaires du routage de modèles.

Hors-ligne : on teste `llm_kwargs` (pur, ne construit aucun client Ollama) et la table
de routage. On vérifie surtout la NON-RÉGRESSION : les hyperparamètres par rôle doivent
reproduire à l'identique ceux qui étaient dispersés dans le code.
"""
import pytest

from core.model_router import model_for, routing_table, llm_kwargs, ollama_options
from env_config import (
    REWRITER_MODEL, GEN_MODEL, AGENT_MODEL, PLANNER_MODEL, SYNTHESIS_MODEL,
    EXTRACTION_MODEL, JUDGE_MODEL, ENHANCEMENT_MODEL,
    NUM_CHUNKS, LLM_NUM_CTX, ENHANCE_NUM_CTX,
)


def test_routing_table_covers_all_roles():
    table = routing_table()
    assert set(table) == {"rewrite", "agent", "planner", "extract", "synthesize", "generate", "judge", "enhance"}
    assert all(isinstance(m, str) and m for m in table.values())


def test_model_for_maps_roles_to_configured_models():
    assert model_for("rewrite") == REWRITER_MODEL
    assert model_for("agent") == AGENT_MODEL
    # planner : override explicite PLANNER_MODEL, sinon repli sur le modèle de l'agent.
    assert model_for("planner") == PLANNER_MODEL
    assert PLANNER_MODEL  # jamais vide : AGENT_MODEL (lui-même GEN_MODEL) en défaut
    assert model_for("extract") == EXTRACTION_MODEL
    assert model_for("synthesize") == SYNTHESIS_MODEL
    assert model_for("generate") == GEN_MODEL
    assert model_for("judge") == JUDGE_MODEL
    # enhance : override explicite ENHANCEMENT_MODEL, sinon repli sur REWRITER_MODEL.
    assert model_for("enhance") == (ENHANCEMENT_MODEL or REWRITER_MODEL)


def test_generate_model_runtime_override():
    """Le bouton « Charger le modèle » (UI) change le modèle de génération à chaud."""
    from core.model_router import set_generate_model, get_generate_model
    try:
        assert get_generate_model() == GEN_MODEL          # défaut = .env
        set_generate_model("mon-modele:latest")
        assert get_generate_model() == "mon-modele:latest"
        assert model_for("generate") == "mon-modele:latest"  # effet immédiat sur le rôle
        # Les autres rôles ne sont pas affectés.
        assert model_for("rewrite") == REWRITER_MODEL
        set_generate_model(None)                          # reset -> retombe sur .env
        assert get_generate_model() == GEN_MODEL
    finally:
        set_generate_model(None)                          # garde-fou : pas de fuite d'état


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
    # num_ctx est plafonné par rôle (anti-wedge KV-cache) : rewrite=8192, agent=LLM_NUM_CTX.
    rw = llm_kwargs("rewrite")
    assert rw == {"temperature": 0.2, "num_ctx": 8192, "model": REWRITER_MODEL}
    ag = llm_kwargs("agent")
    assert ag == {"temperature": 0.1, "num_ctx": LLM_NUM_CTX, "model": AGENT_MODEL}


def test_enhance_options_match_legacy_tuning():
    # Non-régression : ollama_options('enhance') reproduit les options qui étaient
    # codées en dur dans nlp/chunk_enhancer._call_ollama, plus le num_ctx plafonné
    # (ENHANCE_NUM_CTX), et n'expose pas le nom du modèle (le transport HTTP le pose).
    opts = ollama_options("enhance")
    assert opts == {"temperature": 0.1, "top_p": 0.9, "num_predict": 300,
                    "num_ctx": ENHANCE_NUM_CTX}
    assert "model" not in opts


def test_no_role_forces_keep_alive_forever():
    # Garde-fou de non-régression : aucun rôle ne doit re-pinner un modèle « Forever ».
    for role in ("rewrite", "agent", "planner", "extract", "synthesize", "generate", "judge", "enhance"):
        assert "keep_alive" not in llm_kwargs(role)


def test_model_override_forces_specific_model():
    # Le juge peut être forcé sur un modèle dédié (routage prod).
    assert llm_kwargs("judge", model="qwen2.5:14b")["model"] == "qwen2.5:14b"


def test_ollama_num_gpu_injecte_dans_tous_les_roles(monkeypatch):
    # OLLAMA_NUM_GPU (env) force l'offload GPU (0 = tout CPU quand le GPU est
    # occupé) : injecté dans les options de TOUS les rôles, sans écraser un
    # override explicite de l'appelant.
    import core.model_router as mr
    monkeypatch.setattr(mr, "OLLAMA_NUM_GPU", 0)
    for role in ("rewrite", "agent", "planner", "extract", "synthesize", "generate", "judge", "enhance"):
        assert llm_kwargs(role)["num_gpu"] == 0
    assert llm_kwargs("generate", num_gpu=20)["num_gpu"] == 20
    # Non défini (défaut) : on laisse Ollama décider.
    monkeypatch.setattr(mr, "OLLAMA_NUM_GPU", None)
    assert "num_gpu" not in llm_kwargs("generate")
