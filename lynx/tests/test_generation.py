"""Tests de la génération descendante L(n)->L(n+1) (LLM et audit mockés)."""

import pytest

from src import generation
from src.audit import MatrixFinding, MatrixReport

_CORPUS = [
    {"id": "REQ-L1-SYS-001", "niveau": 1, "type": "x", "domaine": "d",
     "texte": "Le drone doit assurer une liaison de données sécurisée.",
     "parent_id": None, "test_status": "PENDING"},
    {"id": "REQ-L2-LDT-001", "niveau": 2, "type": "x", "domaine": "d",
     "texte": "La liaison doit être chiffrée de bout en bout.",
     "parent_id": "REQ-L1-SYS-001", "test_status": "PENDING"},
    {"id": "REQ-L5-TST-001", "niveau": 5, "type": "x", "domaine": "d",
     "texte": "L'essai en vol valide la liaison.", "parent_id": "REQ-L2-LDT-001",
     "test_status": "PENDING"},
]


def _corpus():
    return [dict(r) for r in _CORPUS]


def _filles(n):
    return {"filles": [{"texte": f"La liaison doit satisfaire l'aspect {i}.",
                        "justification": f"j{i}", "aspect_couvert": f"a{i}"}
                       for i in range(n)],
            "aspects_non_couverts": ["résilience au brouillage"]}


def _audit_propre(monkeypatch):
    monkeypatch.setattr(generation, "run_batch_fix",
                        lambda *a, **k: pytest.fail("rien à corriger"))
    import src.audit as audit_mod
    monkeypatch.setattr(audit_mod, "audit_matrix",
                        lambda corpus, deep=True, on_event=None:
                        MatrixReport(n=len(corpus), score=100, findings=[]))


def test_generation_nominale(monkeypatch):
    vus = {}

    def fake_call(prompt, payload, label=None, schema=None):
        vus.update(payload)
        return _filles(3)
    monkeypatch.setattr(generation.llm, "call_agent", fake_call)
    _audit_propre(monkeypatch)

    corpus = _corpus()
    out = generation.generate_children(corpus, "REQ-L1-SYS-001")
    assert "error" not in out
    assert out["niveau_filles"] == 2 and len(out["filles"]) == 3
    for f in out["filles"]:
        assert f["statut"] == "conforme" and f["findings_restants"] == []
        # Convention d'ids du corpus : préfixe/format repris, niveau incrémenté.
        assert f["id_propose"].startswith("REQ-L2-SYS-")
    # Redondance : les filles existantes sont passées au prompt.
    assert [c["id"] for c in vus["filles_existantes"]] == ["REQ-L2-LDT-001"]
    # Le corpus d'entrée n'est pas muté.
    assert corpus == _CORPUS


def test_fille_flaggee_reecrite(monkeypatch):
    monkeypatch.setattr(generation.llm, "call_agent",
                        lambda *a, **k: _filles(2))
    import src.audit as audit_mod

    def fake_audit(corpus, deep=True, on_event=None):
        return MatrixReport(n=len(corpus), score=90, findings=[
            MatrixFinding("REQ-L2-SYS-001", "REDACTION", "WARNING", "vague")])
    monkeypatch.setattr(audit_mod, "audit_matrix", fake_audit)

    def fake_fix(copie, findings, max_passes, deep, on_progress=None, cancelled=None):
        assert max_passes == 2
        assert [f.req_id for f in findings] == ["REQ-L2-SYS-001"]
        return {"recap": [{"req_id": "REQ-L2-SYS-001",
                           "texte_avant": "La liaison doit satisfaire l'aspect 0.",
                           "texte_apres": "La liaison doit satisfaire l'aspect 0 en 10 ms.",
                           "findings_avant": [], "findings_apres": [],
                           "justification": "quantifiée", "erreur": "",
                           "statut": "corrigee", "passes": 1}],
                "compteurs": {}, "passes": 1, "score_apres": 100}
    monkeypatch.setattr(generation, "run_batch_fix", fake_fix)

    out = generation.generate_children(_corpus(), "REQ-L1-SYS-001")
    par_id = {f["id_propose"]: f for f in out["filles"]}
    corrigee = par_id["REQ-L2-SYS-001"]
    assert corrigee["statut"] == "corrigee" and "10 ms" in corrigee["texte"]
    assert par_id["REQ-L2-SYS-002"]["statut"] == "conforme"


def test_niveau_5_refuse(monkeypatch):
    monkeypatch.setattr(generation.llm, "call_agent",
                        lambda *a, **k: pytest.fail("pas d'appel LLM à L5"))
    out = generation.generate_children(_corpus(), "REQ-L5-TST-001")
    assert "error" in out and "L5" in out["error"]


def test_bornage_nombre_de_filles(monkeypatch):
    monkeypatch.setattr(generation.llm, "call_agent", lambda *a, **k: _filles(12))
    _audit_propre(monkeypatch)
    out = generation.generate_children(_corpus(), "REQ-L1-SYS-001")
    assert len(out["filles"]) == generation.MAX_FILLES

    monkeypatch.setattr(generation.llm, "call_agent", lambda *a, **k: _filles(1))
    out = generation.generate_children(_corpus(), "REQ-L1-SYS-001")
    assert "error" in out


def test_mere_introuvable(monkeypatch):
    monkeypatch.setattr(generation.llm, "call_agent",
                        lambda *a, **k: pytest.fail("pas d'appel LLM"))
    out = generation.generate_children(_corpus(), "ABSENT")
    assert "error" in out
