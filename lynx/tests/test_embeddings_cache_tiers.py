"""Cache 3 niveaux : L2 évite l'appel API, le cold-start persiste, parité des vecteurs."""
import numpy as np
import pytest

from src import embeddings, embed_store


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    # L1 vide, L2 sur tmp (le conftest isole déjà EMBED_CACHE_DB, on force le reset).
    embeddings._cache.clear()
    monkeypatch.setattr(embed_store, "EMBED_CACHE_DISK", True)
    embed_store.reset()
    yield
    embeddings._cache.clear()
    embed_store.reset()


def _fake_api(vectors_by_text):
    """Renvoie un faux httpx.post qui répond des embeddings pour les textes demandés."""
    class _Resp:
        def __init__(self, texts):
            self._texts = texts
        def raise_for_status(self):
            pass
        def json(self):
            return {"data": [{"index": i, "embedding": vectors_by_text[t]}
                             for i, t in enumerate(self._texts)]}
    calls = {"n": 0, "texts": []}
    def fake_post(url, headers=None, json=None, timeout=None):
        texts = json["input"]
        calls["n"] += 1
        calls["texts"].append(list(texts))
        return _Resp(texts)
    return fake_post, calls


def test_cold_start_calls_api_and_persists(monkeypatch):
    vecs = {"a": [1.0, 2.0, 3.0], "b": [4.0, 5.0, 6.0]}
    fake_post, calls = _fake_api(vecs)
    monkeypatch.setattr(embeddings.httpx, "post", fake_post)
    out = embeddings.get_embeddings(["a", "b"])
    assert out == [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
    assert calls["n"] == 1  # un appel API
    # Persisté en L2 : présent par clé.
    disk = embed_store.get_many([embeddings._key("a"), embeddings._key("b")])
    assert disk[embeddings._key("a")] == [1.0, 2.0, 3.0]


def test_warm_l2_skips_api(monkeypatch):
    vecs = {"a": [1.0, 2.0, 3.0]}
    # Pré-remplit le L2 puis vide le L1 : l'appel suivant NE doit PAS toucher l'API.
    embed_store.put_many({embeddings._key("a"): vecs["a"]})
    embeddings._cache.clear()
    def boom(*args, **kwargs):
        raise AssertionError("l'API ne doit pas être appelée quand le L2 a le vecteur")
    monkeypatch.setattr(embeddings.httpx, "post", boom)
    out = embeddings.get_embeddings(["a"])
    assert out == [[1.0, 2.0, 3.0]]  # parité : vecteur identique au disque


def test_partial_l2_hit_only_calls_api_for_misses(monkeypatch):
    embed_store.put_many({embeddings._key("known"): [9.0, 9.0]})
    embeddings._cache.clear()
    fake_post, calls = _fake_api({"newone": [1.0, 1.0]})
    monkeypatch.setattr(embeddings.httpx, "post", fake_post)
    out = embeddings.get_embeddings(["known", "newone"])
    assert out == [[9.0, 9.0], [1.0, 1.0]]
    assert calls["texts"] == [["newone"]]  # l'API n'a reçu que le manquant
