"""Store d'embeddings SQLite : round-trip float64 exact, persistance, désactivation."""
import numpy as np
import pytest

from src import embed_store


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch, tmp_path):
    monkeypatch.setattr(embed_store, "EMBED_CACHE_DISK", True)
    monkeypatch.setattr(embed_store, "EMBED_CACHE_DB", tmp_path / "s.sqlite")
    embed_store.reset()
    yield
    embed_store.reset()


def test_put_then_get_roundtrip_is_exact():
    v = list(np.random.default_rng(0).normal(size=8))
    embed_store.put_many({"k1": v})
    got = embed_store.get_many(["k1"])
    assert "k1" in got
    # Round-trip float64 : valeurs strictement identiques (pas de perte de précision).
    assert got["k1"] == v


def test_get_missing_key_absent():
    embed_store.put_many({"a": [1.0, 2.0]})
    got = embed_store.get_many(["a", "absente"])
    assert set(got.keys()) == {"a"}


def test_persistence_across_reset(monkeypatch, tmp_path):
    # Même fichier, connexion recréée = simulate un redémarrage de process.
    db = tmp_path / "persist.sqlite"
    monkeypatch.setattr(embed_store, "EMBED_CACHE_DB", db)
    embed_store.reset()
    embed_store.put_many({"kp": [3.0, 4.0, 5.0]})
    embed_store.reset()  # ferme la connexion
    got = embed_store.get_many(["kp"])  # rouvre depuis le disque
    assert got["kp"] == [3.0, 4.0, 5.0]


def test_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(embed_store, "EMBED_CACHE_DISK", False)
    embed_store.reset()
    embed_store.put_many({"x": [1.0]})
    assert embed_store.get_many(["x"]) == {}


def test_large_batch_over_sqlite_param_limit():
    items = {f"k{i}": [float(i)] for i in range(2000)}
    embed_store.put_many(items)
    got = embed_store.get_many([f"k{i}" for i in range(2000)])
    assert len(got) == 2000
    assert got["k1999"] == [1999.0]
