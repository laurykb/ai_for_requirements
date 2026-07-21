"""Tests du débat contradictoire (LLM mocké) : fail-safe, rétrogradation,
périmètre (les constats factuels ne sont jamais débattus), sérialisation.
"""

import pytest

from src import debate
from src.audit import MatrixFinding, audit_matrix
from src.models import Finding, Scope, Severity
from src.tree import RequirementTree

_CORPUS = [
    {"id": "P", "niveau": 0, "type": "x", "domaine": "d",
     "texte": "Le drone doit voler au moins 2 heures.", "parent_id": None, "test_status": "PENDING"},
    {"id": "C1", "niveau": 1, "type": "x", "domaine": "d",
     "texte": "L'autonomie est limitée à 30 minutes.", "parent_id": "P", "test_status": "PENDING"},
]


def _tree():
    return RequirementTree([dict(r) for r in _CORPUS])


def _mock_skills(monkeypatch, defense=None, juge=None):
    """call_skill mocké : renvoie les réponses fournies par skill."""
    reponses = {"defense_exigence": defense, "juge_verdict": juge}

    def fake(skill, payload, schema=None):
        r = reponses.get(skill)
        assert r is not None, f"appel inattendu au skill {skill}"
        return dict(r)
    monkeypatch.setattr(debate.llm, "call_skill", fake)


_DEFENSE_OK = {"plaidoyer": "La cible précise le parent sans le contredire.",
               "arguments": ["préciser n'est pas contredire"],
               "elements_contexte": ["au moins 2 heures"],
               "refutation_possible": True}


def test_bloquant_maintenu(monkeypatch):
    _mock_skills(monkeypatch, defense=_DEFENSE_OK,
                 juge={"verdict": "MAINTENU", "motivation": "Le plaidoyer n'apporte rien de concret."})
    out = debate.contest_blocking("Pertinence : contradiction.", debate.build_context(_tree(), "C1"))
    assert out["statut"] == "MAINTENU"
    assert out["plaidoyer"] and out["jugement"]


def test_retrograde_devient_warning(monkeypatch):
    _mock_skills(monkeypatch, defense=_DEFENSE_OK,
                 juge={"verdict": "RETROGRADE", "motivation": "Réfutation fondée sur l'extrait cité."})
    f = Finding(analyzer="pertinence", scope=Scope.AMONT, severity=Severity.BLOCKING,
                message="Pertinence : contradiction.")
    out = debate.contest(f, _tree(), "C1")
    assert out.severity == Severity.WARNING
    assert out.debate["statut"] == "RETROGRADE"
    assert out.debate["plaidoyer"] and out.debate["jugement"]


def test_matrix_finding_retrograde(monkeypatch):
    _mock_skills(monkeypatch, defense=_DEFENSE_OK,
                 juge={"verdict": "RETROGRADE", "motivation": "ok"})
    f = MatrixFinding("C1", "PERTINENCE", "BLOQUANT", "Pertinence : contradiction.")
    out = debate.contest(f, _tree(), "C1")
    assert out.severity == "WARNING"
    # Sérialisation API de l'audit : vars() emporte le champ debate.
    assert vars(out)["debate"]["statut"] == "RETROGRADE"


def test_erreur_avocat_maintenu(monkeypatch):
    _mock_skills(monkeypatch, defense={"error": "TIMEOUT"})
    out = debate.contest_blocking("msg", debate.build_context(_tree(), "C1"))
    assert out["statut"] == "MAINTENU"
    assert "avocat" in out["erreur"]


def test_erreur_juge_maintenu(monkeypatch):
    _mock_skills(monkeypatch, defense=_DEFENSE_OK, juge={"error": "TIMEOUT"})
    out = debate.contest_blocking("msg", debate.build_context(_tree(), "C1"))
    assert out["statut"] == "MAINTENU"
    assert "juge" in out["erreur"]


def test_avocat_sans_refutation_ne_saisit_pas_le_juge(monkeypatch):
    _mock_skills(monkeypatch,
                 defense={"plaidoyer": "Rien à opposer.", "arguments": [],
                          "elements_contexte": [], "refutation_possible": False},
                 juge=None)  # tout appel au juge ferait échouer le mock
    out = debate.contest_blocking("msg", debate.build_context(_tree(), "C1"))
    assert out["statut"] == "MAINTENU"


def test_warning_jamais_debattu(monkeypatch):
    monkeypatch.setattr(debate, "contest_blocking",
                        lambda *a, **k: pytest.fail("un WARNING ne se débat pas"))
    f = Finding(analyzer="pertinence", scope=Scope.AMONT, severity=Severity.WARNING, message="m")
    assert debate.contest(f, _tree(), "C1").debate is None


def test_structurels_jamais_debattus(monkeypatch):
    # Corpus avec un BLOQUANT structurel (parent inexistant) : l'audit
    # déterministe ne doit déclencher AUCUN débat.
    monkeypatch.setattr(debate, "contest_blocking",
                        lambda *a, **k: pytest.fail("un constat structurel ne se débat pas"))
    monkeypatch.setattr("src.audit.embeddings.embeddings_available", lambda: False)
    corpus = [dict(_CORPUS[0]), {"id": "ORPH", "niveau": 1, "type": "x", "domaine": "d",
                                 "texte": "x", "parent_id": "ABSENT", "test_status": "PENDING"}]
    rep = audit_matrix(corpus, deep=False)
    assert any(f.axis == "LIEN" and f.severity == "BLOQUANT" for f in rep.findings)
    assert all(f.debate is None for f in rep.findings)


def test_debat_desactive(monkeypatch):
    monkeypatch.setattr(debate, "DEBATE_ENABLED", False)
    monkeypatch.setattr(debate, "contest_blocking",
                        lambda *a, **k: pytest.fail("débat désactivé"))
    f = Finding(analyzer="pertinence", scope=Scope.AMONT, severity=Severity.BLOCKING, message="m")
    out = debate.contest(f, _tree(), "C1")
    assert out.severity == Severity.BLOCKING and out.debate is None
