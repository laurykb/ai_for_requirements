"""Chat LynX : sérialisation de la baseline et fraîcheur de l'index."""
from __future__ import annotations

import pytest
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
    assert "Vérification (IADT) : T" in md


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
