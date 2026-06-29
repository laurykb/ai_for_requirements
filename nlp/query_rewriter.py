"""Réécriture de requête robuste avec garde-fous métier."""

import re
from typing import Set

ACRO_RE = re.compile(r"\b[A-Z]{2,10}\b")


def extract_acronyms(text: str) -> set[str]:
    """
    Extrait tous les acronymes (lettres majuscules, 2 à 10 caractères) d'un texte.
    Args:
        text (str): Texte à analyser
    Returns:
        set[str]: Ensemble des acronymes trouvés
    """
    return set(ACRO_RE.findall(text or ""))


def trim_only_rewrite(query: str) -> str:
    """
    Nettoie une requête sans LLM :
    - enlève les formules de politesse en début/fin
    - supprime les espaces inutiles
    - ne modifie aucun terme important
    Args:
        query (str): Requête utilisateur
    Returns:
        str: Requête nettoyée
    """
    q = (query or "").strip()
    # Retire les salutations simples au début
    q = re.sub(r"^(bonjour|salut|hey)\s*,?\s*", "", q, flags=re.I)
    # Retire 'merci', 'svp', 'stp' en fin de phrase
    q = re.sub(r"(merci|svp|stp)\s*\.?$", "", q, flags=re.I).strip()
    return q


def guarded_rewrite(query: str, llm, vocab: Set[str], acronyms=None) -> str:
    """
    Réécriture prudente d'une requête utilisateur :
    - Conserve tous les acronymes d'origine
    - Conserve les mots du vocabulaire métier si possible
    - Supprime la politesse et le bruit
    - Si un acronyme d'origine disparaît, on garde la requête d'origine nettoyée
    Args:
        query (str): Requête utilisateur
        llm: Modèle de langage (doit avoir .invoke(prompt))
        vocab (set[str]): Ensemble des mots importants à préserver
    Returns:
        str: Requête réécrite ou nettoyée
    """
    if not query:
        return ""

    original = query.strip()
    acronyms = extract_acronyms(original)

    # Conserve les acronymes + les termes métier déjà présents dans la requête.
    keep_terms = {w for w in original.split() if w.lower() in vocab}
    keep_list = sorted(acronyms | keep_terms)
    keep_list = keep_list[:100]  # Limite de sécurité

    # Prompt pour le LLM : consignes claires pour préserver les termes importants
    prompt = f"""
Réécris la question pour une recherche documentaire :
- NE MODIFIE PAS ces termes EXACTEMENT : {keep_list}
- NE DÉVELOPPE PAS les acronymes (ex: TOE reste TOE)
- Supprime juste la politesse et les mots inutiles
- Réponds UNIQUEMENT par la question réécrite (une phrase)

Question :
{original}

Question réécrite :
""".strip()

    try:
        out = (llm.invoke(prompt) or "").strip()[:500]
        # Vérifie que tous les acronymes d'origine sont bien présents dans la sortie
        out_acros = extract_acronyms(out)
        if not acronyms.issubset(out_acros):
            # Si un acronyme a disparu, on fait un fallback simple
            return trim_only_rewrite(original)
        return out or trim_only_rewrite(original)
    except Exception:
        # Si le LLM échoue, on fait un fallback simple
        return trim_only_rewrite(original)

