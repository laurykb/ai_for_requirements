"""Tests unitaires de la fusion RRF (cœur du retrieval hybride)."""
from retrieval.rrf import normalize_scores, rrf, fuse_with_rrf


def test_normalize_scores_minmax():
    assert normalize_scores([0, 5, 10]) == [0.0, 0.5, 1.0]


def test_normalize_scores_all_equal():
    assert normalize_scores([3, 3, 3]) == [1.0, 1.0, 1.0]


def test_normalize_scores_empty():
    assert normalize_scores([]) == []


def test_rrf_shared_top_id_ranks_first():
    res = rrf([["a", "b", "c"], ["a", "c", "b"]], k=60)
    assert res[0][0] == "a"  # 1er dans les deux listes → meilleur score RRF


def test_fuse_empty_returns_empty():
    assert fuse_with_rrf(lists_a=[[]], lookups_a=[{}]) == []


def test_fuse_topk_limits():
    lists_a = [["a", "b", "c", "d"]]
    lookups_a = [{x: {"sim_est": v, "doc": x}
                  for x, v in [("a", 0.9), ("b", 0.8), ("c", 0.7), ("d", 0.6)]}]
    fused = fuse_with_rrf(lists_a, lookups_a, topk_final=2)
    assert len(fused) == 2


def test_fuse_orders_by_semantic_weight():
    lists_a = [["a", "b"]]
    lookups_a = [{"a": {"sim_est": 0.2, "doc": "a"},
                  "b": {"sim_est": 0.9, "doc": "b"}}]
    fused = fuse_with_rrf(lists_a, lookups_a,
                          weight_semantic=1.0, weight_bm25=0.0, topk_final=5)
    assert fused[0]["id"] == "b"  # sim_est plus élevé → score_global plus élevé
