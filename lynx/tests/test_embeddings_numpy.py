"""Parité numpy vs cosinus Python de référence, sans Ollama (vecteurs injectés)."""
import math

import numpy as np

from src import embeddings


def _ref_cosine(a, b):
    """Cosinus de référence (ancienne implémentation Python), pour la parité."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _rand_vecs(n, dim=16, seed=0):
    rng = np.random.default_rng(seed)
    return [list(map(float, rng.normal(size=dim))) for _ in range(n)]


def test_duplicate_pairs_matches_reference():
    vecs = _rand_vecs(12, seed=1)
    # Fabrique un quasi-doublon certain (indices 3 et 7).
    vecs[7] = [x * 1.0001 for x in vecs[3]]
    threshold = 0.95
    # Référence : double boucle Python O(N²).
    ref = []
    for i in range(len(vecs)):
        for j in range(i + 1, len(vecs)):
            s = _ref_cosine(vecs[i], vecs[j])
            if s >= threshold:
                ref.append((i, j))
    got = [(i, j) for i, j, _ in embeddings.duplicate_pairs(vecs, threshold)]
    assert got == ref
    # Les scores correspondent aussi (tolérance flottante).
    for i, j, s in embeddings.duplicate_pairs(vecs, threshold):
        assert abs(s - _ref_cosine(vecs[i], vecs[j])) < 1e-9


def test_duplicate_pairs_excludes_invalid():
    vecs = _rand_vecs(5, seed=2)
    vecs[2] = None
    vecs[4] = []  # invalide
    pairs = embeddings.duplicate_pairs(vecs, 0.0)  # seuil 0 : toutes les paires valides
    idx = {i for i, _, _ in pairs} | {j for _, j, _ in pairs}
    assert 2 not in idx and 4 not in idx


def test_similarities_to_matches_reference():
    vecs = _rand_vecs(8, seed=3)
    target, cands = vecs[0], vecs[1:]
    got = embeddings.similarities_to(target, cands)
    ref = [_ref_cosine(target, c) for c in cands]
    assert len(got) == len(ref)
    for g, r in zip(got, ref):
        assert abs(g - r) < 1e-9


def test_similarities_to_invalid_target_returns_zeros():
    cands = _rand_vecs(3, seed=4)
    assert embeddings.similarities_to(None, cands) == [0.0, 0.0, 0.0]


def test_most_similar_picks_reference_best(monkeypatch):
    vecs = _rand_vecs(6, seed=5)
    texts = [f"t{i}" for i in range(len(vecs))]
    monkeypatch.setattr(embeddings, "get_embeddings", lambda ts: vecs)
    candidates = [(f"ID{i}", texts[i + 1]) for i in range(len(vecs) - 1)]
    best_id, best_score = embeddings.most_similar(texts[0], candidates)
    ref_scores = [_ref_cosine(vecs[0], vecs[i + 1]) for i in range(len(candidates))]
    ref_best = int(np.argmax(ref_scores))
    assert best_id == candidates[ref_best][0]
    assert abs(best_score - ref_scores[ref_best]) < 1e-9
