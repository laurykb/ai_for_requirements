from retrieval.hype import resolve_hype_hits


def _lk(doc, meta, dist):
    return {"doc": doc, "meta": meta, "distance": dist}


def test_hype_hit_collapses_to_parent():
    ranked = ["p1::hype::0", "p2"]
    lookup = {
        "p1::hype::0": _lk("contenu p1", {"id": "p1", "parent_id": "p1", "chunk_type": "hype_question"}, 0.1),
        "p2": _lk("contenu p2", {"id": "p2", "chunk_type": "chunk"}, 0.3),
    }
    ids, lk = resolve_hype_hits(ranked, lookup)
    assert ids == ["p1", "p2"]
    assert lk["p1"]["doc"] == "contenu p1"
    assert lk["p1"]["meta"]["chunk_type"] == "chunk"  # normalised away from hype marker


def test_dedup_keeps_best_ranked_parent():
    ranked = ["p1::hype::0", "p1", "p1::hype::1"]
    lookup = {
        "p1::hype::0": _lk("c", {"id": "p1", "parent_id": "p1", "chunk_type": "hype_question"}, 0.1),
        "p1": _lk("c", {"id": "p1", "chunk_type": "chunk"}, 0.2),
        "p1::hype::1": _lk("c", {"id": "p1", "parent_id": "p1", "chunk_type": "hype_question"}, 0.4),
    }
    ids, lk = resolve_hype_hits(ranked, lookup)
    assert ids == ["p1"]                 # one entry, first (best) occurrence wins order
    assert lk["p1"]["distance"] == 0.1   # best distance retained
