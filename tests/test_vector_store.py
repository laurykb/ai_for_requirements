"""
Tests unitaires de l'abstraction du magasin vectoriel.

Hors-ligne : aucune vraie base Chroma. On injecte une collection factice pour vérifier
(1) la NORMALISATION de la réponse Chroma `[0]`-imbriquée vers `list[VectorHit]`,
(2) la traduction du `source_filter` en clause `where`, (3) la fabrique de backend.
"""
import pytest

from retrieval.vector_store import (
    VectorHit, ChromaVectorStore, get_vector_store, _hits_from_chroma,
)
from retrieval.semantic_search import hits_to_lookup


class FakeChromaCollection:
    """Collection Chroma factice : enregistre les kwargs et renvoie une réponse canon."""
    def __init__(self, response):
        self.response = response
        self.last_kwargs = None

    def query(self, **kwargs):
        self.last_kwargs = kwargs
        return self.response


_CHROMA_RES = {
    "ids": [["a", "b"]],
    "documents": [["texte A", "texte B"]],
    "metadatas": [[{"source": "x.md"}, {"source": "y.md"}]],
    "distances": [[0.1, 0.4]],
}


def test_hits_from_chroma_normalizes_nested_shape():
    hits = _hits_from_chroma(_CHROMA_RES)
    assert [h.id for h in hits] == ["a", "b"]
    assert hits[0].document == "texte A"
    assert hits[0].metadata == {"source": "x.md"}
    assert hits[0].distance == pytest.approx(0.1)


def test_hits_from_chroma_empty_is_empty_list():
    assert _hits_from_chroma({"ids": [[]]}) == []
    assert _hits_from_chroma(None) == []


def test_query_returns_vector_hits_and_builds_source_filter():
    fake = FakeChromaCollection(_CHROMA_RES)
    store = ChromaVectorStore(collection_name="t", collection=fake)
    hits = store.query([0.0, 1.0], n_results=2, source_filter="x.md")

    assert all(isinstance(h, VectorHit) for h in hits)
    assert fake.last_kwargs["n_results"] == 2
    assert fake.last_kwargs["query_embeddings"] == [[0.0, 1.0]]
    # source_filter -> clause where d'égalité (traduite par l'adaptateur Chroma).
    assert fake.last_kwargs["where"] == {"source": {"$eq": "x.md"}}


def test_query_without_source_filter_has_no_where():
    fake = FakeChromaCollection(_CHROMA_RES)
    ChromaVectorStore(collection_name="t", collection=fake).query([0.1], n_results=5)
    assert "where" not in fake.last_kwargs


def test_hits_to_lookup_shape():
    hits = [VectorHit(id="a", document="d", metadata={"source": "s"}, distance=0.2)]
    lookup = hits_to_lookup(hits)
    assert lookup == {"a": {"doc": "d", "meta": {"source": "s"}, "distance": 0.2}}


def test_factory_default_is_chroma():
    store = get_vector_store("some_collection")
    assert isinstance(store, ChromaVectorStore)
    assert store.collection_name == "some_collection"
    assert store.backend == "chroma"


def test_factory_unknown_backend_raises(monkeypatch):
    monkeypatch.setattr("retrieval.vector_store.VECTOR_STORE_BACKEND", "pinecone")
    with pytest.raises(ValueError) as e:
        get_vector_store()
    assert "pinecone" in str(e.value)


def test_factory_knows_qdrant():
    from retrieval.vector_store import QdrantVectorStore
    assert "qdrant" in __import__("retrieval.vector_store", fromlist=["_BACKENDS"])._BACKENDS


def test_qdrant_roundtrip_in_memory():
    """Vérifie l'adaptateur Qdrant de bout en bout contre un Qdrant ':memory:'
    (moteur réel embarqué, AUCUN serveur/Docker requis) — prouve le swap de backend."""
    pytest.importorskip("qdrant_client")
    from retrieval.vector_store import QdrantVectorStore

    store = QdrantVectorStore(collection_name="test_qdrant_rt", location=":memory:")
    store.reset()
    store.add(
        ids=["a", "b", "c"],
        documents=["alpha doc", "beta doc", "gamma doc"],
        metadatas=[{"source": "x.md"}, {"source": "y.md"}, {"source": "x.md"}],
        embeddings=[[1.0, 0.0], [0.0, 1.0], [0.9, 0.1]],
    )
    assert store.count() == 3

    # Plus proche voisin de [1,0] → 'a' ou 'c' (vecteurs alignés sur x).
    hits = store.query([1.0, 0.0], n_results=2)
    assert hits and all(isinstance(h, VectorHit) for h in hits)
    assert hits[0].id in ("a", "c")
    assert hits[0].document  # le texte est restitué depuis le payload
    assert hits[0].distance is not None

    # Filtre par source → seulement 'b'.
    only_y = store.query([1.0, 0.0], n_results=5, source_filter="y.md")
    assert [h.id for h in only_y] == ["b"]

    store.reset()
    assert store.count() == 0
