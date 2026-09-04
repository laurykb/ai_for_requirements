"""Isolation des sources réservées (baseline LynX) vis-à-vis du monde RAG.

Garde-fous : une source réservée est interrogeable uniquement en la nommant
explicitement ; jamais via la recherche « Tous les documents », la liste des
sources ni la synthèse corpus.
"""
from __future__ import annotations

from core.reserved_sources import LYNX_BASELINE_SOURCE, RESERVED_SOURCES
from indexing.keyword_index import bm25_search, build_bm25_index


class _Doc:
    def __init__(self, content, source):
        self.page_content = content
        self.metadata = {"source": source, "keywords_str": "", "questions_str": "",
                         "entities_str": "", "breadcrumb": "", "heading": ""}


def _index():
    docs = [_Doc("exigence chiffrement liaison satellite", LYNX_BASELINE_SOURCE),
            _Doc("chiffrement des liaisons dans le rapport menace", "rapport.md")]
    return build_bm25_index(docs)


def test_bm25_unscoped_excludes_reserved_source():
    bm25, ids, texts, metas = _index()
    res = bm25_search(bm25, ids, texts, metas, "chiffrement liaison", topn=10)
    sources = {m["source"] for m in res["metadatas"][0]}
    assert LYNX_BASELINE_SOURCE not in sources
    assert "rapport.md" in sources


def test_bm25_explicit_scope_still_reaches_reserved_source():
    bm25, ids, texts, metas = _index()
    res = bm25_search(bm25, ids, texts, metas, "chiffrement liaison", topn=10,
                      source_filter=LYNX_BASELINE_SOURCE)
    sources = {m["source"] for m in res["metadatas"][0]}
    assert sources == {LYNX_BASELINE_SOURCE}


def test_list_indexed_sources_excludes_reserved(monkeypatch):
    from core.corpus import list_indexed_sources

    captured: dict = {}

    class _Col:
        def aggregate(self, pipeline):
            captured["match"] = pipeline[0]["$match"]
            return [{"_id": "rapport.md"}]

    class _Db:
        def __getitem__(self, name):
            return _Col()

    assert list_indexed_sources(db=_Db()) == ["rapport.md"]
    nin = captured["match"]["source"]["$nin"]
    assert None in nin
    for reserved in RESERVED_SOURCES:
        assert reserved in nin


def test_vector_store_unscoped_query_filters_reserved(monkeypatch):
    from retrieval import vector_store as vs
    monkeypatch.setattr("core.source_versions.active_version", lambda _source: None)

    captured: dict = {}

    class _Coll:
        def query(self, **kwargs):
            captured.update(kwargs)
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

    store = vs.ChromaVectorStore.__new__(vs.ChromaVectorStore)
    monkeypatch.setattr(store, "_coll", lambda **_kw: _Coll(), raising=False)

    store.query([0.0] * 4, n_results=3)
    assert captured["where"] == {"source": {"$nin": list(RESERVED_SOURCES)}}

    captured.clear()
    store.query([0.0] * 4, n_results=3, source_filter=LYNX_BASELINE_SOURCE)
    assert captured["where"] == {"source": {"$in": [LYNX_BASELINE_SOURCE]}}
