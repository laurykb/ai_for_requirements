"""Tests unitaires du parsing d'enrichissement (sans appel LLM)."""
from nlp.chunk_enhancer import _parse_kwq_json, _clean_generated_lines


def test_parse_kwq_json_clean():
    kws, qs = _parse_kwq_json('{"keywords": ["a", "b"], "questions": ["q1 ?"]}')
    assert kws == ["a", "b"]
    assert qs == ["q1 ?"]


def test_parse_kwq_json_with_surrounding_text():
    kws, qs = _parse_kwq_json('Voici le JSON : {"keywords":["x"],"questions":[]} (fin)')
    assert kws == ["x"]
    assert qs == []


def test_parse_kwq_json_invalid():
    assert _parse_kwq_json("pas de json ici") == ([], [])
    assert _parse_kwq_json("") == ([], [])


def test_clean_generated_lines_strips_bullets_and_numbering():
    out = _clean_generated_lines("- alpha\n2. beta\n* gamma", separator="\n")
    assert out == ["alpha", "beta", "gamma"]
