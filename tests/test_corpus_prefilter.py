from core.corpus import prefilter_documents


def _fake_retrieve(chunks):
    def _r(query, source_filter=None):
        return query, chunks
    return _r


def test_prefilter_keeps_relevant_docs_in_relevance_order():
    # retrieval renvoie des chunks de A puis B (A plus pertinent), C absent.
    chunks = [
        {"meta": {"source": "A.md"}}, {"meta": {"source": "A.md"}},
        {"meta": {"source": "B.md"}},
    ]
    out = prefilter_documents("attaques", retrieve=_fake_retrieve(chunks),
                              all_sources=["A.md", "B.md", "C.md"], topn=10, max_docs=50)
    assert out == ["A.md", "B.md"]  # C exclu, ordre de 1re apparition (pertinence)


def test_prefilter_caps_max_docs():
    chunks = [{"meta": {"source": f"{c}.md"}} for c in "ABCDE"]
    out = prefilter_documents("x", retrieve=_fake_retrieve(chunks),
                              all_sources=[f"{c}.md" for c in "ABCDE"], topn=50, max_docs=3)
    assert out == ["A.md", "B.md", "C.md"]


def test_prefilter_falls_back_to_all_when_empty():
    out = prefilter_documents("x", retrieve=_fake_retrieve([]),
                              all_sources=["A.md", "B.md"], topn=10, max_docs=50)
    assert out == ["A.md", "B.md"]  # repli : tout le corpus (borné)
