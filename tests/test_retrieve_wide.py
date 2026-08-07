# tests/test_retrieve_wide.py
"""Tests offline de core.ask.retrieve_wide (retrieval large pour le pré-filtre
corpus-large) - aucune dépendance Mongo/Ollama réelle : tout est monkeypatché."""
import core.ask as ask_mod


def _fake_bm25_search(bm25, ids, texts, metas, query, topn=10, source_filter=None):
    # Simule un résultat BM25 issu du document A (grand document dominant).
    n = min(topn, 2)
    return {
        "ids": [["a1", "a2"][:n]],
        "documents": [["texte A1", "texte A2"][:n]],
        "metadatas": [[{"source": "A.md"}, {"source": "A.md"}][:n]],
        "scores": [[9.0, 8.0][:n]],
    }


def _fake_run_semantic_for_query(store, query, topn=10, source_filter=None, embedding_model=None):
    # Simule un résultat sémantique issu du petit document B, absent du BM25.
    n = min(topn, 1)
    ids = ["b1"][:n]
    lookup = {i: {"doc": "texte B1", "meta": {"source": "B.md"}, "distance": 0.1} for i in ids}
    return ids, lookup


def test_retrieve_wide_merges_both_sources(monkeypatch):
    monkeypatch.setattr(ask_mod, "_get_vector_store", lambda: object())
    monkeypatch.setattr(ask_mod, "_load_bm25", lambda source_filter=None: (object(), [], [], []))
    monkeypatch.setattr("indexing.keyword_index.bm25_search", _fake_bm25_search)
    monkeypatch.setattr("retrieval.semantic_search.run_semantic_for_query", _fake_run_semantic_for_query)

    query, chunks = ask_mod.retrieve_wide("attaques et menaces", topn=10)

    assert query == "attaques et menaces"
    sources = {(c.get("meta") or {}).get("source") for c in chunks}
    assert "A.md" in sources
    assert "B.md" in sources  # le petit document n'est PAS écrasé par le gros


def test_retrieve_wide_honors_topn(monkeypatch):
    monkeypatch.setattr(ask_mod, "_get_vector_store", lambda: object())
    monkeypatch.setattr(ask_mod, "_load_bm25", lambda source_filter=None: (object(), [], [], []))
    monkeypatch.setattr("indexing.keyword_index.bm25_search", _fake_bm25_search)
    monkeypatch.setattr("retrieval.semantic_search.run_semantic_for_query", _fake_run_semantic_for_query)

    _q, chunks = ask_mod.retrieve_wide("x", topn=1)
    assert len(chunks) == 1


def test_retrieve_wide_never_raises_when_everything_fails(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("indisponible")
    monkeypatch.setattr(ask_mod, "_get_vector_store", _boom)
    monkeypatch.setattr(ask_mod, "_load_bm25", _boom)

    query, chunks = ask_mod.retrieve_wide("q", topn=5)
    assert query == "q"
    assert chunks == []
