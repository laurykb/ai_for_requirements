"""clear_retrieval_caches vide aussi le cache BM25 fusionné de keyword_index,
sinon un nouveau document reste invisible jusqu'au redémarrage du process."""
import indexing.keyword_index as ki
import core.ask as ask


def test_clear_retrieval_caches_invalidates_keyword_index_cache():
    # Simule un cache BM25 fusionné chaud.
    ki._bm25_cache["__all__"] = ("fake_bm25", ["id1"], ["texte"], [{"source": "A.md"}])
    assert "__all__" in ki._bm25_cache

    ask.clear_retrieval_caches()

    assert "__all__" not in ki._bm25_cache
    assert ki._bm25_cache == {}
