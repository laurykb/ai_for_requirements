"""Périmètre de l'agent : `scope_arguments` n'injecte le document que si un
périmètre précis est choisi. En « Tous les documents » (source vide/None),
l'agent n'est jamais collé au dernier document. 100 % hors-ligne."""
from api.rag import scope_arguments


def test_no_scope_when_source_none():
    assert scope_arguments(None, {"query": "x"}) == {"query": "x"}


def test_no_scope_when_source_empty():
    out = scope_arguments("", {"query": "x"})
    assert "document" not in out


def test_scope_injected_when_source_set():
    out = scope_arguments("a.md", {"query": "x"})
    assert out["document"] == "a.md"


def test_explicit_document_not_overridden():
    out = scope_arguments("a.md", {"query": "x", "document": "b.md"})
    assert out["document"] == "b.md"


def test_original_arguments_not_mutated():
    args = {"query": "x"}
    scope_arguments("a.md", args)
    assert "document" not in args  # copie, pas mutation en place
