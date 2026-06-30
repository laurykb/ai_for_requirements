"""Tests unitaires des métriques d'évaluation (déterministes, sans LLM)."""
from core.evaluation import (
    exact_match, f1_token, context_recall, context_precision, keyword_hit_rate,
    _parse_verify_json, verify_answer,
)


def test_exact_match():
    assert exact_match("La réponse est EAL3", "eal3") == 1.0
    assert exact_match("autre chose", "eal3") == 0.0


def test_f1_token_identical():
    assert f1_token("alpha beta", "alpha beta") == 1.0


def test_f1_token_disjoint():
    assert f1_token("alpha", "beta") == 0.0


def test_context_recall_full():
    chunks = [{"doc": "le niveau eal3 est retenu"}]
    assert context_recall(chunks, "eal3 niveau") == 1.0


def test_context_recall_partial():
    chunks = [{"doc": "le niveau seulement"}]
    assert context_recall(chunks, "eal3 niveau") == 0.5


def test_context_precision_topk():
    chunks = [{"doc": "eal3 niveau retenu"}, {"doc": "contenu sans rapport xyz"}]
    # 1 chunk pertinent sur 2 -> 0.5
    assert context_precision(chunks, "eal3 niveau", topk=2) == 0.5


def test_keyword_hit_rate():
    chunks = [{"doc": "EAL3 et ALC_FLR mentionnés"}]
    assert keyword_hit_rate(chunks, ["EAL3", "ALC_FLR"]) == 1.0
    assert keyword_hit_rate(chunks, ["EAL3", "absent"]) == 0.5


def test_keyword_hit_rate_edge_cases():
    assert keyword_hit_rate([{"doc": "x"}], []) is None
    assert keyword_hit_rate([], ["x"]) == 0.0


# -- Vérificateur fusionné (parsing déterministe, hors-ligne) ------------------
def test_parse_verify_json_clean():
    raw = '{"faithfulness": 0.9, "answer_relevance": 1.0, "context_relevance": 0.7, "issues": ["extrait douteux"]}'
    out = _parse_verify_json(raw)
    assert out["faithfulness"] == 0.9
    assert out["answer_relevance"] == 1.0
    assert out["context_relevance"] == 0.7
    assert out["issues"] == ["extrait douteux"]


def test_parse_verify_json_tolerates_surrounding_text_and_clamps():
    raw = 'Voici mon verdict : {"faithfulness": 1.5, "answer_relevance": -0.2, "context_relevance": 0.5, "issues": "manque la page"} fin.'
    out = _parse_verify_json(raw)
    assert out["faithfulness"] == 1.0        # borné à 1.0
    assert out["answer_relevance"] == 0.0    # borné à 0.0
    assert out["issues"] == ["manque la page"]  # str -> liste


def test_parse_verify_json_invalid_falls_back():
    out = _parse_verify_json("pas de json ici")
    assert out == {"faithfulness": 0.0, "answer_relevance": 0.0, "context_relevance": 0.0, "issues": []}


def test_verify_answer_uses_injected_llm_single_call():
    # LLM injecté -> 100 % hors-ligne ; on vérifie qu'UN seul appel est fait (vs 3 séparés).
    calls = {"n": 0}
    class _LLM:
        def invoke(self, prompt):
            calls["n"] += 1
            return '{"faithfulness": 0.8, "answer_relevance": 0.6, "context_relevance": 0.9, "issues": []}'
    out = verify_answer("q", "réponse", [{"doc": "contexte"}], llm=_LLM())
    assert calls["n"] == 1
    assert out["faithfulness"] == 0.8 and out["context_relevance"] == 0.9


def test_verify_answer_empty_inputs_short_circuit():
    # Pas de génération ou pas de chunks -> aucun appel LLM, scores nuls.
    class _Boom:
        def invoke(self, prompt):
            raise AssertionError("ne doit pas être appelé")
    assert verify_answer("q", "", [{"doc": "x"}], llm=_Boom())["faithfulness"] == 0.0
    assert verify_answer("q", "réponse", [], llm=_Boom())["issues"] == []
