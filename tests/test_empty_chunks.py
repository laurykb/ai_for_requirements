"""build_bm25_index ignore les chunks vides (page_content vide/blanc)."""
from dataclasses import dataclass
from indexing.keyword_index import build_bm25_index


@dataclass
class _Doc:
    page_content: str
    metadata: dict


def test_build_bm25_skips_empty_chunks():
    docs = [
        _Doc("Le chiffrement protege les donnees", {"id": "a1", "source": "A.md"}),
        _Doc("", {"id": "empty", "source": "A.md"}),
        _Doc("   ", {"id": "blank", "source": "A.md"}),
        _Doc("Le parefeu filtre le trafic", {"id": "b1", "source": "B.md"}),
    ]
    _, ids, texts, metas = build_bm25_index(docs)
    assert "empty" not in ids and "blank" not in ids
    assert set(ids) == {"a1", "b1"}
    assert all(t.strip() for t in texts)
