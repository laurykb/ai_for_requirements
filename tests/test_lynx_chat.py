"""Chat LynX : sérialisation de la baseline et fraîcheur de l'index."""
from __future__ import annotations

import pytest
import json
from fastapi.testclient import TestClient

import api.lynx_api
import api.lynx_chat as lc
import api.main


REQ = {"id": "REQ-001", "niveau": 1, "type": "Exigence", "domaine": "Cyber",
       "texte": "Le système doit chiffrer les liaisons.", "parent_id": None,
       "links": [], "verification": "T", "source": "SPEC-A", "rationale": None}
REQ2 = {"id": "REQ-002", "niveau": 2, "type": "Exigence", "domaine": "Général",
        "texte": "Dérivée de chiffrement.", "parent_id": "REQ-001",
        "links": [{"type": "REFERENCE", "target_id": "REQ-001"}]}


def test_render_markdown_grouped_by_domain_with_ids_and_links():
    md = lc._render_markdown([REQ, REQ2])
    assert "# Baseline d'exigences (LynX)" in md
    assert "## Domaine : Cyber" in md and "## Domaine : Général" in md
    assert "### REQ-001 — Exigence · niveau L1" in md
    assert "Le système doit chiffrer les liaisons." in md
    assert "Dérivée de : REQ-001" in md
    assert "Lien REFERENCE : REQ-001" in md
    assert "IADT" not in md


def test_render_markdown_handles_empty_text():
    md = lc._render_markdown([{**REQ, "texte": "  "}])
    assert "(énoncé vide)" in md


def test_fingerprint_stable_and_sensitive():
    base = lc._fingerprint([REQ, REQ2])
    assert base == lc._fingerprint([REQ2, REQ])  # insensible à l'ordre
    assert base != lc._fingerprint([{**REQ, "texte": "Autre énoncé."}, REQ2])
    assert base != lc._fingerprint([REQ])


def test_sync_refuses_empty_baseline(monkeypatch):
    monkeypatch.setattr(api.lynx_api, "_corpus", [])
    client = TestClient(api.main.app)
    r = client.post("/api/lynx/chat/sync")
    assert r.status_code == 400
    assert "Baseline vide" in r.json()["detail"]


def test_baseline_source_is_reserved_markdown_name():
    # Le nom doit rester un .md stable : il est dupliqué côté front
    # (web/src/lib/api.ts) et sert de clé de périmètre aux sessions.
    assert lc.BASELINE_SOURCE.endswith(".md")
    assert "/" not in lc.BASELINE_SOURCE


@pytest.mark.parametrize("payload", [[], [{"id": "X"}]])
def test_fingerprint_never_crashes_on_minimal_rows(payload):
    assert isinstance(lc._fingerprint(payload), str)


class _Collection:
    def __init__(self, row=None, count=0):
        self.row, self.count, self.replaced = row, count, None

    def find_one(self, query, projection=None):
        if self.row and all(self.row.get(k) == v for k, v in query.items()):
            return dict(self.row)
        return None

    def count_documents(self, query):
        return self.count

    def replace_one(self, query, row, upsert=False):
        self.row = dict(row)
        self.replaced = dict(row)

    def update_many(self, *args, **kwargs):
        return None

    def update_one(self, query, update, upsert=False):
        if self.row is not None:
            self.row.update(update.get("$set", {}))


class _DB(dict):
    def __getitem__(self, name):
        return super().__getitem__(name)


def test_activation_switches_pointer_only_after_independent_checks(monkeypatch, tmp_path):
    version = "a" * 20
    markdown = tmp_path / "baseline.md"
    markdown.write_text("# baseline", encoding="utf-8")
    db = _DB({
        "chunks": _Collection(count=1),
        "bm25_indexes": _Collection(row={"source_doc": lc.BASELINE_SOURCE,
                                           "ingest_version": version}),
        "lynx_chat_versions": _Collection(row={"version_id": version,
                                                  "fingerprint": "fp", "n_exigences": 1}),
        "lynx_chat_meta": _Collection(),
    })
    monkeypatch.setattr(lc, "get_db", lambda: db)
    monkeypatch.setattr(lc.ingest_queue, "DOCS_OUT", tmp_path / "out")

    class _Vectors:
        def count_version(self, source, selected_version):
            assert source == lc.BASELINE_SOURCE and selected_version == version
            return 2

    monkeypatch.setattr("retrieval.vector_store.get_vector_store", lambda: _Vectors())
    lc.activate_baseline_version(
        {"version_id": version, "markdown_path": str(markdown)},
        {"num_chunks": 1, "mongo_chunks": 1, "bm25_chunks": 1, "vector_units": 2},
    )
    assert db["lynx_chat_meta"].replaced["active_version"] == version
    assert (tmp_path / "out" / lc.BASELINE_SOURCE).is_file()


def test_activation_refuses_incomplete_preparation(monkeypatch, tmp_path):
    version = "b" * 20
    db = _DB({
        "chunks": _Collection(count=0),
        "bm25_indexes": _Collection(row={"source_doc": lc.BASELINE_SOURCE,
                                           "ingest_version": version}),
        "lynx_chat_versions": _Collection(row={"version_id": version}),
        "lynx_chat_meta": _Collection(),
    })
    monkeypatch.setattr(lc, "get_db", lambda: db)
    monkeypatch.setattr("retrieval.vector_store.get_vector_store",
                        lambda: type("V", (), {"count_version": lambda self, s, v: 1})())
    with pytest.raises(RuntimeError, match="Contrôle des index refusé"):
        lc.activate_baseline_version(
            {"version_id": version, "markdown_path": str(tmp_path / "absent.md")},
            {"num_chunks": 1, "mongo_chunks": 1, "bm25_chunks": 1, "vector_units": 1},
        )
    assert db["lynx_chat_meta"].replaced is None


def test_typed_lynx_job_is_reconstructible_without_callable(monkeypatch, tmp_path):
    from core import ingest_queue
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps([REQ]), encoding="utf-8")
    seen = {}

    def fake_ingest(corpus, cb, source, version):
        seen.update(corpus=corpus, source=source, version=version)
        return {"status": "success", "num_chunks": 1}

    monkeypatch.setattr("core.lynx_baseline_ingest.ingest_baseline", fake_ingest)
    monkeypatch.setattr(lc, "activate_baseline_version", lambda job, stats: seen.update(activated=True))
    monkeypatch.setattr("core.ask.clear_retrieval_caches", lambda: None)
    job = {"name": lc.BASELINE_SOURCE, "path": str(path), "params": {},
           "job_type": "lynx_baseline", "version_id": "c" * 20,
           "run": None, "pct": 0, "step": "", "result": None}
    ingest_queue._process_job(job)
    assert seen["source"] == lc.BASELINE_SOURCE
    assert seen["version"] == "c" * 20
    assert seen["activated"] is True
    assert job["result"]["status"] == "success"
