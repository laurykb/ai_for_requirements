"""Parité de _embedding_duplicates : mêmes constats que la référence Python O(N²)."""
import math

import numpy as np

from src import audit
from src.config import EMBED_DUP_THRESHOLD


def _ref_cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _ref_findings(corpus, vecs):
    """Ancienne logique : double boucle Python, message identique."""
    items = [(r["id"], r.get("texte", "")) for r in corpus if r.get("texte")]
    vmap = {items[i][0]: vecs[i] for i in range(min(len(items), len(vecs)))}
    out = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            id1, id2 = items[i][0], items[j][0]
            v1, v2 = vmap.get(id1), vmap.get(id2)
            if not v1 or not v2:
                continue
            s = _ref_cosine(v1, v2)
            if s >= EMBED_DUP_THRESHOLD:
                out.append((id1, f"Doublon probable : {id1} ≈ {id2} (similarité {s:.2f})."))
    return out


def test_embedding_duplicates_parity(monkeypatch):
    rng = np.random.default_rng(7)
    corpus = [{"id": f"REQ-{i}", "texte": f"exigence {i}"} for i in range(10)]
    vecs = [list(map(float, rng.normal(size=16))) for _ in corpus]
    vecs[5] = [x * 1.00005 for x in vecs[2]]  # quasi-doublon 2≈5

    monkeypatch.setattr(audit.embeddings, "embeddings_available", lambda: True)
    monkeypatch.setattr(audit.embeddings, "get_embeddings", lambda texts: vecs)

    findings = audit._embedding_duplicates(corpus)
    got = [(f.req_id, f.message) for f in findings]
    assert got == _ref_findings(corpus, vecs)
    # Le doublon injecté est bien détecté.
    assert any("REQ-2" == f.req_id and "REQ-5" in f.message for f in findings)
