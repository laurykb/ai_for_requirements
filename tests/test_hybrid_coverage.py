"""hybrid_retrieve applique le plancher de couverture après rerank (mode « Tous »).
On patch les étages coûteux (retrievers + cross-encoder) pour rester offline."""
import retrieval.retrieve as R


def _mk(src, ce, i):
    return {"doc": f"{src}-{i}", "meta": {"source": src}, "ce_score": ce}


def test_small_doc_surfaces_via_floor(monkeypatch):
    # 15 chunks BIG + 1 SMALL en dernier (hors top-15) après "rerank".
    canned = [_mk("BIG", 0.90 - i * 0.01, i) for i in range(15)] + [_mk("SMALL", 0.55, 99)]

    monkeypatch.setattr(R, "_run_parallel_retrievers",
                        lambda **kw: (["s0"], {"s0": {"doc": "x", "meta": {"source": "BIG"}, "distance": 0.1}}, [], {}))
    monkeypatch.setattr(R, "_maybe_apply_cross_encoder", lambda **kw: (canned, 0.90))

    out, max_ce = R.hybrid_retrieve(
        collection=None, query="peu importe", bm25_tuple=None,
        topk_chunks=15, source_filter=None, parent_child_on=False, debug=False,
    )
    assert "SMALL" in {c["meta"]["source"] for c in out}
    assert max_ce == 0.90
    assert len(out) <= R.MAX_CHUNKS


def test_candidate_pool_is_elastic(monkeypatch):
    # Capture le pool réellement demandé aux retrievers ; il doit croître avec le
    # nombre de documents du bm25_tuple (ici 2 sources -> proche de la borne basse).
    captured = {}

    def _fake_retrievers(**kw):
        captured["pool"] = kw["topk_chunks"]
        return ["s0"], {"s0": {"doc": "x", "meta": {"source": "A"}, "distance": 0.1}}, [], {}

    monkeypatch.setattr(R, "_run_parallel_retrievers", _fake_retrievers)
    monkeypatch.setattr(R, "_maybe_apply_cross_encoder", lambda **kw: ([_mk("A", 0.7, 0)], 0.7))

    # bm25_tuple = (bm25, ids, texts, metas) avec 2 sources distinctes.
    bm25_tuple = (None, ["a", "b"], ["t1", "t2"],
                  [{"source": "A.md"}, {"source": "B.md"}])
    R.hybrid_retrieve(collection=None, query="q", bm25_tuple=bm25_tuple,
                      topk_chunks=15, source_filter=None, parent_child_on=False, debug=False)
    # clamp(40, 40 + 3*2, 120) = 46
    assert captured["pool"] == 46


def test_no_floor_when_source_scoped(monkeypatch):
    # Périmètre = un seul document -> pas de plancher (comportement inchangé).
    canned = [_mk("BIG", 0.90 - i * 0.01, i) for i in range(15)] + [_mk("SMALL", 0.55, 99)]
    monkeypatch.setattr(R, "_run_parallel_retrievers",
                        lambda **kw: (["s0"], {"s0": {"doc": "x", "meta": {"source": "BIG"}, "distance": 0.1}}, [], {}))
    monkeypatch.setattr(R, "_maybe_apply_cross_encoder", lambda **kw: (canned, 0.90))
    out, _ = R.hybrid_retrieve(
        collection=None, query="q", bm25_tuple=None,
        topk_chunks=15, source_filter="BIG", parent_child_on=False, debug=False,
    )
    assert len(out) == 15  # top-15 brut, pas de SMALL forcé
