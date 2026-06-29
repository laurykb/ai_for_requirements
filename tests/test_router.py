"""
Tests unitaires du routeur de requêtes (core/router) — 100 % hors-ligne, déterministe.

On vérifie que les questions complexes (comparaison, relationnel, multi-documents,
multi-questions) partent vers l'agent, que les questions factuelles directes restent
en RAG classique, et que seules les questions « à enjeu » déclenchent la vérification.
"""
from core.router import route_query, should_verify, _norm


def test_norm_strips_accents_and_case():
    assert _norm("Différence ÉVALuée") == "difference evaluee"


# ── route_query → agent (complexité) ──────────────────────────────────────────
def test_comparison_routes_to_agent():
    d = route_query("Quelle est la différence entre EAL3 et EAL4 ?")
    assert d["mode"] == "agent"
    assert "comparaison" in d["signals"]


def test_relational_routes_to_agent():
    d = route_query("Quelle relation entre les menaces et les objectifs de sécurité ?")
    assert d["mode"] == "agent"
    assert "relationnel" in d["signals"]


def test_multi_documents_routes_to_agent():
    d = route_query("Compare les hypothèses dans les deux documents.")
    assert d["mode"] == "agent"


def test_multi_questions_routes_to_agent():
    d = route_query("Quel est le niveau EAL ? Et quelles sont les hypothèses ?")
    assert d["mode"] == "agent"
    assert "multi_questions" in d["signals"]


# ── route_query → rag (cas courant) ───────────────────────────────────────────
def test_direct_factual_routes_to_rag():
    d = route_query("Quel est le niveau EAL de la TOE ?")
    assert d["mode"] == "rag"
    assert d["signals"] == []


def test_simple_lookup_routes_to_rag():
    d = route_query("Donne la définition de la TOE.")
    assert d["mode"] == "rag"


# ── should_verify ─────────────────────────────────────────────────────────────
def test_high_stake_question_triggers_verification():
    assert should_verify("Quel niveau d'assurance EAL est visé ?")["verify"] is True
    assert should_verify("Quelle version exacte est certifiée ?")["verify"] is True


def test_general_question_skips_verification():
    assert should_verify("De quoi parle le document ?")["verify"] is False
    assert should_verify("Présente brièvement la cible.")["verify"] is False
