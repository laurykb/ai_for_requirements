"""list_indexed_sources agrège les noms de documents distincts. Mongo factice injecté."""
from core.corpus import list_indexed_sources


class _FakeCol:
    def aggregate(self, pipeline):
        return iter([{"_id": "b.md"}, {"_id": "a.md"}])


class _FakeDB:
    def __getitem__(self, name):
        assert name == "chunks"
        return _FakeCol()


def test_list_indexed_sources_sorted_distinct():
    out = list_indexed_sources(db=_FakeDB())
    assert out == ["a.md", "b.md"]  # triés


def test_list_indexed_sources_empty():
    class _EmptyCol:
        def aggregate(self, pipeline):
            return iter([])
    class _EmptyDB:
        def __getitem__(self, n):
            return _EmptyCol()
    assert list_indexed_sources(db=_EmptyDB()) == []
