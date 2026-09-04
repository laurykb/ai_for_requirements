"""La garde d'abstention respecte le seuil MAIS ne s'abstient pas sur les
questions exploratoires (« c'est quoi », « de quoi parle »)."""
import core.ask as ask


def test_abstains_on_pointed_below_threshold(monkeypatch):
    monkeypatch.setattr(ask, "USE_CROSS_ENCODER", True)
    monkeypatch.setattr(ask, "CE_RELEVANCE_THRESHOLD", 0.48)
    assert ask._should_abstain(0.40, "Quelle est la valeur exacte de la clé RSA ?") is True


def test_does_not_abstain_on_exploratory(monkeypatch):
    monkeypatch.setattr(ask, "USE_CROSS_ENCODER", True)
    monkeypatch.setattr(ask, "CE_RELEVANCE_THRESHOLD", 0.48)
    assert ask._should_abstain(0.40, "C'est quoi un IDS ?") is False
    assert ask._should_abstain(0.40, "De quoi parle ce document ?") is False


def test_no_abstention_above_threshold(monkeypatch):
    monkeypatch.setattr(ask, "USE_CROSS_ENCODER", True)
    monkeypatch.setattr(ask, "CE_RELEVANCE_THRESHOLD", 0.48)
    assert ask._should_abstain(0.60, "Quelle est la valeur exacte ?") is False


def test_no_abstention_when_ce_off(monkeypatch):
    monkeypatch.setattr(ask, "USE_CROSS_ENCODER", False)
    assert ask._should_abstain(None, "Quelle valeur ?") is False


def test_reserved_scope_never_abstains_at_retrieval_level():
    """Périmètre épinglé sur une source réservée (baseline LynX) : la porte
    hors-scope ne coupe jamais — le refus appartient à la génération."""
    from core.ask import _should_abstain
    from core.reserved_sources import LYNX_BASELINE_SOURCE
    assert _should_abstain(0.4, "Question quelconque très pointue ?") is True
    assert _should_abstain(0.4, "Question quelconque très pointue ?",
                           source_filter=LYNX_BASELINE_SOURCE) is False
    assert _should_abstain(0.4, "Question quelconque très pointue ?",
                           source_filter="rapport.md") is True
