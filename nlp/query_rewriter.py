"""Nettoyage léger de la requête (sans LLM)."""

import re


def trim_only_rewrite(query: str) -> str:
    """Nettoie une requête : enlève la politesse en début/fin et les espaces inutiles,
    sans modifier aucun terme important."""
    q = (query or "").strip()
    q = re.sub(r"^(bonjour|salut|hey)\s*,?\s*", "", q, flags=re.I)
    q = re.sub(r"(merci|svp|stp)\s*\.?$", "", q, flags=re.I).strip()
    return q

