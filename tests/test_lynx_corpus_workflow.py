"""Brouillon, santé et diff de la baseline LynX."""
import json

import pytest
from fastapi import HTTPException

from fastapi.testclient import TestClient

import api.lynx_api as lynx_api
from api.main import app
from api.lynx_api import (ActionBody, _candidate, _corpus_diff, _corpus_facets, _corpus_health, _corpus_issues, _query_requirements, _requirement_relations)
from api.lynx_corpus import prepare_activation


def req(ident, text="texte", parent=None, links=None, verification=None):
    return {"id": ident, "niveau": 0, "texte": text, "parent_id": parent,
            "links": links or [], "verification": verification}


def test_diff_identifie_changements_et_voisinage():
    before = [req("SYS-REQ-001"), req("SUB-REQ-002", parent="SYS-REQ-001")]
    after = [req("SYS-REQ-001", "modifié"), req("SUB-REQ-002", parent="SYS-REQ-001"),
             req("NEW-REQ-003")]
    diff = _corpus_diff(before, after)
    assert diff["added"] == ["NEW-REQ-003"]
    assert diff["modified"] == ["SYS-REQ-001"]
    assert set(diff["impacted"]) >= {"SYS-REQ-001", "SUB-REQ-002", "NEW-REQ-003"}


def test_sante_detecte_liens_casses_cycles_et_champs_vides():
    corpus = [
        req("AAA-REQ-001", "", links=[{"type": "SATISFIES", "target": "BBB-REQ-002"}]),
        req("BBB-REQ-002", links=[{"type": "SATISFIES", "target": "AAA-REQ-001"}]),
        req("CCC-REQ-003", links=[{"type": "SATISFIES", "target": "ABS-REQ-999"}]),
    ]
    health = _corpus_health(corpus)
    assert health["broken_links_count"] == 1
    assert health["cycles_count"] == 1
    assert health["empty_text_count"] == 1
    assert health["score"] < 100


def test_file_qualite_priorise_et_regroupe_les_actions():
    corpus = [
        req("EMPTY", ""),
        req("BROKEN", links=[{"type": "DERIVE", "target": "ABSENT"}]),
        req("OK", parent="BROKEN", verification="T"),
    ]
    queue = _corpus_issues(corpus, ["OK"])
    assert queue["items"][0]["req_id"] == "EMPTY"
    assert queue["items"][0]["priority"] == "critical"
    assert queue["by_code"]["BROKEN_LINK"] == 1
    assert "NO_VERIFICATION" not in queue["by_code"]
    ok = next(item for item in queue["items"] if item["req_id"] == "OK")
    assert [issue["code"] for issue in ok["issues"]] == ["RECENT_IMPACT"]


def test_racine_legitime_et_orpheline_sont_distinguees():
    corpus = [req("ROOT", parent=None), {**req("ORPHAN", parent=None), "niveau": 1}]
    health = _corpus_health(corpus)
    assert health["roots_count"] == 2
    assert health["orphans"] == ["ORPHAN"]
    declared = [{**corpus[1], "root_declared": True}, corpus[0]]
    assert _corpus_health(declared)["orphans_count"] == 0


def test_activation_refuse_une_revision_obsolete(monkeypatch):
    draft = {"draft_id": "d", "base_revision": "ancienne", "exigences": [req("ROOT")]}
    monkeypatch.setattr(lynx_api, "_drafts", {"d": draft})
    monkeypatch.setattr(lynx_api, "_corpus", [req("ACTIVE")])
    monkeypatch.setattr(lynx_api.store, "load_activation_receipt", lambda value: None)
    with pytest.raises(HTTPException) as caught:
        lynx_api.activate_draft("d", lynx_api.ActivateDraftBody(included_ids=["ROOT"], expected_revision="ancienne", activation_id="a"))
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "BASELINE_CONFLICT"


def test_activation_idempotente_rejoue_le_recu(monkeypatch):
    monkeypatch.setattr(lynx_api.store, "load_activation_receipt", lambda value: {"activation_id": value, "n": 2})
    result = lynx_api.activate_draft("brouillon-supprime", lynx_api.ActivateDraftBody(activation_id="same"))
    assert result == {"activation_id": "same", "n": 2, "idempotent_replay": True}


def test_prepare_activation_filtre_et_nettoie_les_liens_sans_muter():
    source = [req("ROOT", links=[]), req("CHILD", parent="ROOT", links=[{"type": "DERIVE", "target": "ROOT"}])]
    selected = prepare_activation(source, ["CHILD"])
    assert selected[0]["parent_id"] is None
    assert selected[0]["links"] == []
    assert source[1]["parent_id"] == "ROOT"


def test_update_candidate_modifie_uniquement_le_texte():
    corpus = [req("REQ-1", "avant")]
    action = ActionBody(action_type="UPDATE", target_id="REQ-1", new_text="après")
    updated = _candidate(corpus, action)
    assert updated[0]["texte"] == "après"
    assert updated[0]["verification"] is None
    assert corpus[0]["texte"] == "avant"


def test_consultation_paginee_facettes_et_relations():
    corpus = [
        {**req("SYS-1", "Besoin racine", verification="I"), "source": "a.xlsx", "domaine": "Système"},
        {**req("SUB-2", "Contrôle réseau", parent="SYS-1"), "niveau": 1, "source": "b.xlsx", "domaine": "Réseau"},
        {**req("SUB-3", "Journalisation", parent="SYS-1"), "niveau": 1, "source": "b.xlsx", "domaine": "Réseau"},
    ]
    facets = _corpus_facets(corpus)
    assert facets["sources"] == {"a.xlsx": 1, "b.xlsx": 2}
    page = _query_requirements(corpus, query="sub", level=1, page=1, page_size=1)
    assert page["total"] == 2 and len(page["items"]) == 1
    assert _query_requirements(corpus, domain="Réseau")["total"] == 2
    assert _query_requirements(corpus, status="attention")["total"] == 0
    impacted = _query_requirements(corpus, status="impacted", impacted_ids=["SUB-2"])
    assert [item["id"] for item in impacted["items"]] == ["SUB-2"]
    assert facets["views"] == {"attention": 0, "roots": 1}
    relations = _requirement_relations(corpus, "SYS-1")
    assert {req["id"] for req in relations["downstream"]} == {"SUB-2", "SUB-3"}
    assert _requirement_relations(corpus, "UNKNOWN") is None


def test_sante_corpus_propre():
    health = _corpus_health([req("AAA-REQ-001", verification="T")])
    assert health["score"] == 100
    assert health["broken_links_count"] == 0


def test_preview_corpus_stream_accepte_le_signal_annulation(monkeypatch):
    corpus = [req("REC-SYS-001", verification="T")]
    monkeypatch.setattr(lynx_api, "_parse_corpus_payloads", lambda payloads: (corpus, []))
    monkeypatch.setattr(
        lynx_api,
        "_make_draft",
        lambda parsed, warnings, names, *args, **kwargs: {
            "draft_id": "draft-test",
            "exigences": parsed,
            "warnings": warnings,
            "source_names": names,
            "health": _corpus_health(parsed),
            "diff": _corpus_diff([], parsed),
        },
    )

    response = TestClient(app).post(
        "/api/lynx/corpus/preview/stream",
        files={"files": ("baseline.json", json.dumps({"exigences": corpus}), "application/json")},
    )

    assert response.status_code == 200
    assert "Event object is not callable" not in response.text
    assert "\"type\": \"result\"" in response.text
    assert "\"type\": \"done\"" in response.text


def test_arbitrage_collision_conserve_la_formulation_choisie(monkeypatch):
    requirement = {
        **req("REQ-COLLISION", "version A"),
        "source": "a.doc",
        "collision_variants": [
            {"source": "a.doc", "texte": "version A"},
            {"source": "b.doc", "texte": "version B"},
        ],
    }
    draft = {"draft_id": "draft-collision", "exigences": [requirement]}
    monkeypatch.setattr(lynx_api, "_drafts", {"draft-collision": draft})
    monkeypatch.setattr(lynx_api.store, "save_draft", lambda value: None)
    monkeypatch.setattr(lynx_api, "_get_corpus", lambda: [])

    resolved = lynx_api.resolve_corpus_collision(
        "draft-collision", "REQ-COLLISION",
        lynx_api.ResolveCollisionBody(texte="version B", source="b.doc"),
    )

    selected = resolved["exigences"][0]
    assert selected["texte"] == "version B"
    assert selected["source"] == "b.doc"
    assert selected["collision_variants"] == []


def test_suppression_baseline_sauvegarde_avant_de_vider(monkeypatch):
    lynx_api._corpus = [req("ACTIVE")]
    monkeypatch.setattr(lynx_api.store, "save_version", lambda corpus, reason: "v-backup")
    monkeypatch.setattr(lynx_api.store, "save_working", lambda corpus: None)
    monkeypatch.setattr("api.lynx_chat._purge_baseline_index", lambda: None)

    result = lynx_api.delete_active_corpus()

    assert result["previous_version_id"] == "v-backup"
    assert lynx_api._corpus == []


def test_suppression_dun_depot_recalcule_le_brouillon(monkeypatch):
    first = {"batch_id": "b1", "source_names": ["a.json"], "exigences": [req("A")], "warnings": []}
    second = {"batch_id": "b2", "source_names": ["b.json"], "exigences": [req("B")], "warnings": []}
    lynx_api._drafts = {"d": {"draft_id": "d", "source_batches": [first, second],
                              "source_names": ["a.json", "b.json"],
                              "exigences": [req("A"), req("B")], "warnings": []}}
    monkeypatch.setattr(lynx_api.store, "save_draft", lambda value: None)

    result = lynx_api.delete_draft_source("d", "b1")

    assert result["draft"]["source_names"] == ["b.json"]
    assert [item["id"] for item in result["draft"]["exigences"]] == ["B"]


def test_changement_baseline_programme_la_synchronisation_du_chat(monkeypatch):
    import api.lynx_chat as lynx_chat

    monkeypatch.setattr(lynx_chat, "sync_baseline", lambda: {"job_id": "sync-1"})

    result = lynx_api._sync_chat_after_baseline_change()

    assert result == {"scheduled": True, "job_id": "sync-1"}
