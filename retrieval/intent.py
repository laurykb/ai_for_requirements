"""Détecteur d'intention de requête — aiguille RAG pointu / exploratoire / synthèse
corpus, SANS appel LLM (regex sur texte normalisé). Source unique d'aiguillage,
partagée avec l'audit du routeur (Chantier 2)."""
from __future__ import annotations

import re
import unicodedata


def _norm(text: str) -> str:
    """Minuscule + sans accents (comme core.router._norm)."""
    t = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


# Agrégation corpus-large : « toutes les X », « catégorise », « du corpus »...
_AGGREGATE = re.compile(
    r"\b(tous les|tout les|toutes les|toute les|l'ensemble des|chaque (document|attaque|menace|exigence|"
    r"categorie)|recense|inventaire|categorise|categoriser|enumere|enumerer|"
    r"classe (les|toutes|tous)|liste[- ](moi|nous|les|toutes|tous|l')|"
    r"exhaustif|exhaustive|exhaustivement|sources? de risques?.*objectifs?|target objectives?|types? d.attaquants?|"
    r"dans tout le corpus|sur tout le corpus|(de|du|dans le) corpus|"
    r"a travers (les|tous|toutes))")

# Générique / définitionnel : on ne doit PAS abstenir dessus.
_EXPLORATORY = re.compile(
    r"\b(de quoi (parle|traite|s'agit)|c'est quoi|qu'est ce que|qu'est-ce que|"
    r"quel est le sujet|que dit (le|ce) document|resume|resumer|synthese|"
    r"presente|presentation|explique|explication|vue d'ensemble|apercu|generalites)")


def is_aggregate(question: str) -> bool:
    return bool(_AGGREGATE.search(_norm(question)))


def is_exploratory(question: str) -> bool:
    return bool(_EXPLORATORY.search(_norm(question)))


def classify_intent(question: str) -> str:
    """Renvoie 'aggregate' | 'exploratory' | 'pointed'. L'agrégation prime sur
    l'exploratoire (« résume toutes les X » = agrégatif)."""
    if is_aggregate(question):
        return "aggregate"
    if is_exploratory(question):
        return "exploratory"
    return "pointed"
