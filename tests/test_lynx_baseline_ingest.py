"""Ingestion requirement-aware de la baseline : 1 chunk = 1 exigence."""
from __future__ import annotations

from core.lynx_baseline_ingest import (build_requirement_documents, hype_policy,
                                       _requirement_text)
from core.reserved_sources import LYNX_BASELINE_SOURCE

REQ = {"id": "REQ-001", "niveau": 1, "type": "Exigence", "domaine": "Cyber",
       "texte": "Le système doit chiffrer les liaisons.", "parent_id": None,
       "links": [], "verification": "T", "source": "SPEC-A", "rationale": None}
REQ2 = {"id": "REQ-002", "niveau": 2, "type": "Exigence", "domaine": "Général",
        "texte": "Dérivée de chiffrement.", "parent_id": "REQ-001",
        "links": [{"type": "REFERENCE", "target_id": "REQ-001"}]}


def test_one_document_per_requirement_with_identity_metadata():
    docs = build_requirement_documents([REQ, REQ2])
    assert len(docs) == 2  # aucun chunk d'en-tête ou de domaine
    by_id = {d.metadata["req_id"]: d for d in docs}
    d1 = by_id["REQ-001"]
    assert d1.metadata["source"] == LYNX_BASELINE_SOURCE
    assert d1.metadata["chunk_type"] == "requirement"
    assert d1.metadata["req_niveau"] == 1 and d1.metadata["req_domaine"] == "Cyber"
    assert d1.metadata["id"] == f"{LYNX_BASELINE_SOURCE}::REQ-001"
    assert "chiffrer les liaisons" in d1.page_content
    assert by_id["REQ-002"].metadata["req_parent_id"] == "REQ-001"


def test_requirement_text_carries_traceability():
    txt = _requirement_text(REQ2)
    assert txt.startswith("REQ-002 (Exigence, niveau L2, domaine Général)")
    assert "Dérivée de : REQ-001" in txt
    assert "Lien REFERENCE : REQ-001" in txt


def test_requirement_text_never_empty():
    txt = _requirement_text({"id": "X", "texte": "  "})
    assert "(énoncé vide)" in txt


def test_documents_sorted_stable_by_domain_level_id():
    docs = build_requirement_documents([REQ2, REQ])
    assert [d.metadata["req_id"] for d in docs] == ["REQ-001", "REQ-002"]
    assert docs[0].metadata["section_idx"] == 0  # Cyber avant Général
    assert docs[1].metadata["section_idx"] == 1


def test_hype_policy_elastic(monkeypatch):
    monkeypatch.delenv("LYNX_CHAT_HYPE", raising=False)
    monkeypatch.delenv("LYNX_CHAT_HYPE_NQ", raising=False)
    monkeypatch.delenv("LYNX_CHAT_HYPE_MAX_REQS", raising=False)
    assert hype_policy(50) == (True, 2)          # petit corpus : HyPE actif
    assert hype_policy(301) == (False, 0)        # gros corpus : coupé (coût LLM)
    monkeypatch.setenv("LYNX_CHAT_HYPE", "false")
    assert hype_policy(50) == (False, 0)         # désactivable
    monkeypatch.setenv("LYNX_CHAT_HYPE", "true")
    monkeypatch.setenv("LYNX_CHAT_HYPE_NQ", "4")
    monkeypatch.setenv("LYNX_CHAT_HYPE_MAX_REQS", "1000")
    assert hype_policy(500) == (True, 4)         # knobs élastiques


def test_build_embedding_units_local_hype_override():
    from indexing.embedding import build_embedding_units
    docs = build_requirement_documents([REQ])
    docs[0].metadata["questions"] = ["Comment chiffrer ?", "Quel algorithme ?"]
    off = build_embedding_units(docs, hype_enabled=False)
    on = build_embedding_units(docs, hype_enabled=True, hype_max_questions=2)
    assert len(off) == 1
    assert len(on) == 3  # contenu + 2 questions HyPE
    hype_units = [u for u in on if u["metadata"].get("chunk_type") == "hype_question"]
    assert all(u["metadata"]["id"] == docs[0].metadata["id"] for u in hype_units)
