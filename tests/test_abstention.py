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
