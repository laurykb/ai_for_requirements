"""Normalisation du périmètre documentaire (`source_filter`).

Le RAG accepte de restreindre la recherche à un ou plusieurs documents. Pour
unifier les trois chemins (sémantique, BM25, graphe) et l'outil agent, on
accepte indifféremment :
  - None / "" / []      -> aucun filtre (recherche sur TOUT l'index)
  - "doc.md"            -> un seul document
  - ["a.md", "b.md"]    -> plusieurs documents

`normalize_sources` ramène tout cela à `list[str]` (>=1 élément) ou `None`.
"""
from __future__ import annotations


def normalize_sources(source_filter) -> list[str] | None:
    """str | list | None -> list[str] non vide, ou None (= tout l'index)."""
    if not source_filter:
        return None
    if isinstance(source_filter, str):
        return [source_filter]
    # Itérable (list/tuple/set) : on retire les valeurs vides et les doublons
    # tout en préservant l'ordre.
    seen: dict = {}
    for s in source_filter:
        if s:
            seen.setdefault(s, None)
    return list(seen) or None
