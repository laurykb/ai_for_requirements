"""Tests du détecteur d'intention — 100 % hors-ligne, déterministe."""
from retrieval.intent import classify_intent, is_exploratory, is_aggregate


def test_aggregate_intent():
    assert classify_intent("Catégorise toutes les attaques connues de ce corpus") == "aggregate"
    assert classify_intent("Liste-moi l'ensemble des exigences du corpus") == "aggregate"
    assert is_aggregate("Recense toutes les menaces dans tous les documents") is True
    # "liste-moi" (tiret) doit matcher indépendamment de "l'ensemble des".
    assert is_aggregate("Liste-moi les exigences de chiffrement") is True


def test_exploratory_intent():
    assert classify_intent("C'est quoi un IDS ?") == "exploratory"
    assert classify_intent("De quoi parle ce document ?") == "exploratory"
    assert is_exploratory("Résume la cible de sécurité") is True


def test_pointed_intent():
    assert classify_intent("Quelles sont les exigences de chiffrement ?") == "pointed"
    assert classify_intent("Quel est le niveau EAL de la TOE ?") == "pointed"
    assert is_exploratory("Quel est le niveau EAL de la TOE ?") is False
    assert is_aggregate("Quelles sont les exigences de chiffrement ?") is False


def test_aggregate_wins_over_exploratory():
    # "résume TOUTES les attaques" est agrégatif, pas juste exploratoire
    assert classify_intent("Résume toutes les attaques du corpus") == "aggregate"
