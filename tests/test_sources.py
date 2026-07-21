"""Tests du helper de normalisation du périmètre documentaire."""
from utils.sources import normalize_sources


def test_none_and_empty_mean_all_index():
    assert normalize_sources(None) is None
    assert normalize_sources("") is None
    assert normalize_sources([]) is None
    assert normalize_sources(["", None]) is None


def test_str_becomes_singleton_list():
    assert normalize_sources("doc.md") == ["doc.md"]


def test_list_preserved_dedup_order():
    assert normalize_sources(["a.md", "b.md"]) == ["a.md", "b.md"]
    # Doublons retires, ordre preserve, valeurs vides ignorees.
    assert normalize_sources(["a.md", "a.md", "", "b.md"]) == ["a.md", "b.md"]


def test_tuple_and_set_accepted():
    assert normalize_sources(("a.md",)) == ["a.md"]
    assert set(normalize_sources({"a.md", "b.md"})) == {"a.md", "b.md"}
