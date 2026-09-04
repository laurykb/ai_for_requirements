"""
Tests unitaires du routeur de requêtes (core/router) - 100 % hors-ligne, déterministe.

On vérifie que les questions complexes (comparaison, relationnel, multi-documents,
multi-questions) partent vers l'agent, que les questions factuelles directes restent
en RAG classique, et que seules les questions « à enjeu » déclenchent la vérification.
"""
from core.router import route_query, should_verify, select_query_strategy, _norm


def test_norm_strips_accents_and_case():
    assert _norm("Différence ÉVALuée") == "difference evaluee"


# -- route_query -> agent (complexité) ------------------------------------------
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


def test_natural_comparison_routes_to_agent():
    for q in ["Compare les menaces des deux cibles.",
              "Quelle est la différence entre les exigences de A et de B ?",
              "En quoi la cible X diffère-t-elle de la cible Y ?"]:
        assert route_query(q)["mode"] == "agent", q


def test_relational_multi_hop_routes_to_agent():
    for q in ["Quels liens entre les hypothèses et les objectifs de sécurité ?",
              "Quelles conséquences des menaces sur les exigences ?"]:
        assert route_query(q)["mode"] == "agent", q


def test_multi_document_natural_phrasings_route_to_agent():
    for q in ["Pour chaque cible, donne le niveau EAL.",
              "Recense les exigences à travers les documents."]:
        assert route_query(q)["mode"] == "agent", q


# -- route_query -> rag (cas courant) -------------------------------------------
def test_direct_factual_routes_to_rag():
    d = route_query("Quel est le niveau EAL de la TOE ?")
    assert d["mode"] == "rag"
    assert d["signals"] == []


def test_simple_lookup_routes_to_rag():
    d = route_query("Donne la définition de la TOE.")
    assert d["mode"] == "rag"


def test_simple_factual_still_rag():
    for q in ["Quel est le niveau EAL de la TOE ?",
              "Donne la définition de la TOE.",
              "C'est quoi un IDS ?"]:
        assert route_query(q)["mode"] == "rag", q


# -- should_verify -------------------------------------------------------------
def test_high_stake_question_triggers_verification():
    assert should_verify("Quel niveau d'assurance EAL est visé ?")["verify"] is True
    assert should_verify("Quelle version exacte est certifiée ?")["verify"] is True


def test_general_question_skips_verification():
    assert should_verify("De quoi parle le document ?")["verify"] is False
    assert should_verify("Présente brièvement la cible.")["verify"] is False


# -- stratégie adaptative ------------------------------------------------------
def test_strategy_auto_routes_aggregate_to_synthesis():
    from core.router import select_query_strategy
    s = select_query_strategy("Recense toutes les menaces dans le corpus.")
    assert (s["mode"], s["query_type"]) == ("synth", "aggregate")
    assert s["mode_source"] == "automatic"

def test_strategy_auto_routes_comparison_to_agent():
    from core.router import select_query_strategy
    s = select_query_strategy("Compare les menaces des deux documents.")
    assert (s["mode"], s["query_type"]) == ("agent", "multi_hop")

def test_strategy_expert_overrides_win():
    from core.router import select_query_strategy
    s = select_query_strategy("Recense toutes les menaces.", requested_mode="rag",
                              parent_child=True, self_rag=False)
    assert s["mode"] == "rag" and s["mode_source"] == "expert_override"
    assert s["retrieval"]["parent_child"] is True
    assert s["retrieval"]["self_rag"] is False

def test_strategy_unvalidated_techniques_are_auto_off():
    from core.router import select_query_strategy
    s = select_query_strategy("Quel est le niveau EAL ?")
    assert s["retrieval"]["parent_child"] is False
    assert s["retrieval"]["self_rag"] is False
    assert s["verify"] is True


def test_representative_exhaustive_prompts_route_to_structured_synthesis():
    prompts = [
        "Sur tous les documents que tu as en mémoire, sur Galileo, identifie et caractérise les catégories de sources de risque, les sources de risque, les catégories d objectif visé et les objectifs visés. Sois exhaustif, précis et rigoureux.",
        "Catégorise moi tout les source objectif, tous les target objectives, et sort moi une table de tous les types d attaquant",
    ]
    for prompt in prompts:
        s = select_query_strategy(prompt)
        assert s["mode"] == "synth"
        assert s["query_type"] == "structured_aggregate"
        assert s["retrieval"]["profile"] == "corpus_coverage"


def test_deep_research_is_explicit_and_never_selected_silently():
    from core.router import select_query_strategy
    deep = select_query_strategy("Analyse exhaustive du corpus", requested_mode="deep")
    assert deep["mode"] == "synth"
    assert deep["requested_mode"] == "deep"
    assert deep["retrieval"]["profile"] == "deep_research"
    assert deep["mode_source"] == "expert_override"
