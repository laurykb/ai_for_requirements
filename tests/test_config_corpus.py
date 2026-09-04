import env_config as ec


def test_corpus_knobs():
    assert isinstance(ec.CORPUS_MAP_CONCURRENCY, int) and ec.CORPUS_MAP_CONCURRENCY >= 1
    assert isinstance(ec.COVERAGE_REPAIR_ENABLED, bool)
    assert isinstance(ec.CORPUS_MAX_DOCS, int) and ec.CORPUS_MAX_DOCS >= 1
    assert isinstance(ec.CORPUS_PREFILTER_TOPN, int) and ec.CORPUS_PREFILTER_TOPN >= ec.NUM_CHUNKS
