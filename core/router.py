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
    """Minuscule + sans accents, pour des motifs robustes (différence ~ difference)."""
    t = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


# -- Signaux de COMPLEXITÉ -> agent ReAct (motifs sur texte normalisé sans accents) --
_AGENT_SIGNALS = {
    "comparaison": re.compile(
        r"\b(compar|differen|versus|\bvs\b|par rapport a|contrairement|"
        r"plutot que|distingu|oppos)"),
    "relationnel": re.compile(
        r"\b(relation entre|lien entre|depend|entrain|consequence|impact de|"
        r"influen|en quoi .* affect|comment .* affect)"),
    "multi_documents": re.compile(
        r"\b(les deux|chaque document|entre les (cibles|documents|targets)|"
        r"dans tous les|d'un document a l'autre)"),
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
