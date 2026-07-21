"""Tests unitaires des IDs stables de chunks."""
from utils.text_utils import normalize_text, make_doc_id


def test_normalize_text_collapses_whitespace():
    assert normalize_text("  a   b\n c ") == "a b c"


def test_make_doc_id_is_stable_and_16_chars():
    a = make_doc_id("hello world", "src.md", 1)
    b = make_doc_id("hello world", "src.md", 1)
    assert a == b
    assert len(a) == 16


def test_make_doc_id_differs_on_index():
    assert make_doc_id("hello", "src.md", 1) != make_doc_id("hello", "src.md", 2)


def test_make_doc_id_differs_on_content():
    assert make_doc_id("hello", "src.md", 1) != make_doc_id("world", "src.md", 1)
