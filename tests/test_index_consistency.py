from core import index_consistency as consistency


class FakeCursor(list):
    pass


class FakeChunks:
    def aggregate(self, _pipeline):
        return FakeCursor([
            {"_id": "complete.md", "mongo": 3, "quarantined": 1},
            {"_id": "missing-vectors.md", "mongo": 2, "quarantined": 0},
        ])


class FakeBm25:
    def find(self, _query, _projection):
        return FakeCursor([
            {"source_doc": "complete.md"},
            {"source_doc": "missing-vectors.md"},
        ])


class FakeDb:
    def __getitem__(self, name):
        return {"chunks": FakeChunks(), "bm25_indexes": FakeBm25()}[name]


class FakeStore:
    def source_counts(self):
        return {"complete.md": 2, "missing-vectors.md": 1, "orphan.md": 4}


def test_rag_index_consistency_reports_partial_and_orphan_vectors(monkeypatch):
    monkeypatch.setattr(consistency, "get_db", lambda: FakeDb())
    monkeypatch.setattr(consistency, "get_vector_store", lambda: FakeStore())

    result = consistency.rag_index_consistency()

    assert result["available"] is True
    assert result["in_sync"] is False
    assert result["totals"] == {
        "mongo": 5,
        "indexable": 4,
        "vectors": 3,
        "orphan_vectors": 4,
    }
    by_name = {row["name"]: row for row in result["sources"]}
    assert by_name["complete.md"]["in_sync"] is True
    assert by_name["missing-vectors.md"]["in_sync"] is False
    assert by_name["missing-vectors.md"]["degraded_reasons"] == [
        "Index vectoriel incomplet (1/2)."
    ]
    assert result["orphans"][0]["name"] == "orphan.md"
