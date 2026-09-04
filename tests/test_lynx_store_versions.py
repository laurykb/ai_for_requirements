"""Persistance des brouillons et versions de baseline LynX."""

from lynx.src import store


def _configure_store(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DRAFTS_DIR", tmp_path / "drafts")
    monkeypatch.setattr(store, "VERSIONS_DIR", tmp_path / "versions")
    monkeypatch.setattr(store, "_LOCK", tmp_path / ".lock")


def test_brouillon_survit_au_cache_memoire(monkeypatch, tmp_path):
    _configure_store(monkeypatch, tmp_path)
    draft = {
        "draft_id": "draft-test",
        "created_at": 123.0,
        "source_names": ["matrice.xlsx"],
        "exigences": [{"id": "REQ-1"}],
        "health": {"score": 100},
        "diff": {"added": ["REQ-1"]},
    }

    store.save_draft(draft)

    assert store.load_draft("draft-test") == draft
    assert store.list_drafts() == [{
        "draft_id": "draft-test",
        "created_at": 123.0,
        "source_names": ["matrice.xlsx"],
        "health": {"score": 100},
        "diff": {"added": ["REQ-1"]},
    }]
    store.delete_draft("draft-test")
    assert store.load_draft("draft-test") is None


def test_version_est_listee_et_rechargeable(monkeypatch, tmp_path):
    _configure_store(monkeypatch, tmp_path)
    corpus = [{"id": "REQ-1", "texte": "Baseline précédente"}]

    version_id = store.save_version(corpus, "Avant activation")
    versions = store.list_versions()

    assert store.load_version(version_id) == corpus
    assert len(versions) == 1
    assert versions[0]["version_id"] == version_id
    assert versions[0]["reason"] == "Avant activation"
    assert versions[0]["n"] == 1
