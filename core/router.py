"""
Routeur de requêtes - aiguille chaque question vers le bon traitement, SANS appel LLM.

Le « mode Agent » manuel devient un cas particulier : en mode Auto, ce routeur décide
- s'il faut lancer l'agent ReAct (questions complexes : comparaison, multi-sauts,
  multi-documents, plusieurs sous-questions) ou un RAG classique (question factuelle directe) ;
- si la réponse mérite une VÉRIFICATION (questions « à enjeu » : niveau d'assurance,
  exigences, versions... où une hallucination coûte cher).

Choix déterminant pour la LATENCE : le routage est 100 % HEURISTIQUE (regex/signaux,
~microsecondes). Un routeur LLM ajouterait un appel à CHAQUE question - y compris les
plus simples - ce qui ferait exploser la latence sur un petit GPU. Ici, on ne dépense
des appels (agent, vérificateur) que lorsqu'un signal gratuit indique que ça en vaut la
peine. L'utilisateur garde la main : modes « RAG » et « Agent » forcent le traitement.

Tout est testable hors-ligne (aucun service requis).

NB (évolution) : la décision de vérification gagnerait à s'appuyer AUSSI sur le score
cross-encoder du retrieval (confiance), mais celui-ci n'est pas encore propagé hors de
ask._prepare_retrieval. Pour l'instant on se fonde sur des signaux de la question.
"""
from __future__ import annotations

import re
import unicodedata

from utils.logging_config import get_logger

logger = get_logger("rag.router")


def _norm(text: str) -> str:
    """Minuscule + sans accents, pour comparer des motifs (différence ~ difference)."""
    t = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


# -- Signaux de COMPLEXITÉ -> agent ReAct (motifs sur texte normalisé sans accents) --
_AGENT_SIGNALS = {
    "comparaison": re.compile(
        r"\b(compar|differen|differe|versus|\bvs\b|par rapport a|contrairement|"
        r"plutot que|distingu|oppos|difference entre|ecart entre)"),
    "relationnel": re.compile(
        r"\b(relation entre|lien entre|liens entre|depend|entrain|consequence|"
        r"consequences? (de|des|sur)|impact de|influen|en quoi .* (diff|affect)|"
        r"comment .* affect)"),
    "multi_documents": re.compile(
        r"\b(les deux|chaque (document|cible|target)|pour chaque|entre les "
        r"(cibles|documents|targets)|dans tous les|a travers (les|tous|toutes)|"
        r"d'un document a l'autre|sur l'ensemble des)"),
}


def _count_signals(qn: str) -> dict:
    """Signaux de complexité présents dans la question normalisée."""
    found = {name: bool(rx.search(qn)) for name, rx in _AGENT_SIGNALS.items()}
    found["multi_questions"] = qn.count("?") >= 2
    return {k: v for k, v in found.items() if v}


def route_query(question: str) -> dict:
    """Décide du traitement d'une question (mode Auto).

    Retourne {"mode": "agent"|"rag", "reason": str, "signals": [..]}.
    Conservateur : on n'envoie vers l'agent (plus lent) que sur un signal clair de
    complexité ; sinon RAG classique (le cas courant, rapide).
    """
    qn = _norm(question)
    signals = _count_signals(qn)
    if signals:
        reason = "complexité détectée : " + ", ".join(signals)
        decision = {"mode": "agent", "reason": reason, "signals": sorted(signals)}
    else:
        decision = {"mode": "rag", "reason": "question factuelle directe", "signals": []}
    logger.debug("[router] %s -> %s (%s)", question[:60], decision["mode"], decision["reason"])
    return decision


# -- Questions « à ENJEU » -> vérification de la réponse -------------------------
# Termes où une réponse non fondée coûte cher (extraction critique normative).
_STAKE = re.compile(
    r"\b(eal\d?|niveau d'?assurance|exigenc|conform|certifi|version|augment|"
    r"alc_|ava_|adv_|ase_|agd_|referentiel|obligatoire|garanti|valeur exacte|"
    r"combien|quelle valeur|quel niveau|quelle version)")


def should_verify(question: str) -> dict:
    """Décide si la réponse à cette question mérite une vérification (mode Auto).

    Retourne {"verify": bool, "reason": str}. Conservateur : on ne paie le coût du
    vérificateur que pour les questions à enjeu (extraction critique), pas pour les
    questions générales/exploratoires.
    """
    if _STAKE.search(_norm(question)):
        return {"verify": True, "reason": "question à enjeu (extraction critique)"}
    return {"verify": False, "reason": "question générale - vérification non nécessaire"}


POLICY_VERSION = "adaptive-v1"

_STRUCTURED_AGGREGATE = re.compile(
    r"\b(categor|class|table|typologie|categories?).*"
    r"(sources? de risques?|objectifs?|target objectives?|attaquants?)|"
    r"(sources? de risques?|objectifs?|target objectives?|attaquants?).*"
    r"(categor|class|table|typologie|categories?)")

def select_query_strategy(question: str, requested_mode: str = "auto",
                          parent_child: bool | None = None,
                          self_rag: bool | None = None) -> dict:
    """Décision automatique; les valeurs non-None sont des overrides Expert."""
    from retrieval.intent import classify_intent
    intent = classify_intent(question)
    routed = route_query(question)
    if requested_mode == "deep":
        mode = "synth"
        mode_reason = "mode ANALYSE PROFONDE forcé par l utilisateur"
        mode_source = "expert_override"
    elif requested_mode in ("rag", "agent", "synth"):
        mode = requested_mode
        mode_reason = f"mode {requested_mode.upper()} forcé par l’utilisateur"
        mode_source = "expert_override"
    elif intent == "aggregate":
        mode, mode_reason, mode_source = (
            "synth", "agrégation exhaustive détectée sur le corpus", "automatic")
    else:
        mode, mode_reason, mode_source = routed["mode"], routed["reason"], "automatic"
    structured = bool(_STRUCTURED_AGGREGATE.search(_norm(question)))
    query_type = ("structured_aggregate" if intent == "aggregate" and structured else
                  "aggregate" if intent == "aggregate" else
                  "multi_hop" if routed["signals"] else intent)
    pc_source = "expert_override" if parent_child is not None else "automatic"
    sr_source = "expert_override" if self_rag is not None else "automatic"
    # Politique v1 prudente: aucune technique non validée n’est auto-activée.
    pc_effective = bool(parent_child) if parent_child is not None else False
    sr_effective = bool(self_rag) if self_rag is not None else False
    verification = should_verify(question)
    rationale = [mode_reason]
    if parent_child is not None:
        rationale.append("Parent-Child imposé par le mode Expert")
    if self_rag is not None:
        rationale.append("Self-RAG imposé par le mode Expert")
    if verification["verify"]:
        rationale.append(verification["reason"])
    return {
        "policy_version": POLICY_VERSION, "query_type": query_type,
        "intent": intent, "mode": mode, "requested_mode": requested_mode,
        "mode_source": mode_source, "signals": routed["signals"],
        "retrieval": {
            "profile": ("deep_research" if requested_mode == "deep" else
                        "corpus_coverage" if mode == "synth" else
                        "multi_hop" if mode == "agent" else "hybrid_precise"),
            "parent_child": pc_effective, "parent_child_source": pc_source,
            "self_rag": sr_effective, "self_rag_source": sr_source,
        },
        "verify": verification["verify"], "rationale": rationale,
    }
