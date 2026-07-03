# retrieval/parent_child.py
"""
Parent-Child Retrieval (Small-to-Big).

Principe :
  - Le retrieval (semantic + BM25 + cross-encoder) identifie les chunks ENFANTS
    les plus pertinents (petits, précis, bons scores CE).
  - On remonte dans MongoDB pour récupérer tous les chunks des sections matchées
    en UNE SEULE requête ($in), puis on groupe en Python.
  - Le LLM reçoit le contexte parent (riche) au lieu du seul chunk enfant (court).
  - Le score de classement reste celui du chunk enfant (c'est lui qui a matché).

Optimisation anti N+1 :
  - expand_to_parent() collecte toutes les paires (source, section_idx) à chercher,
    lance UN SEUL find() avec $in, puis groupe les résultats en mémoire.
  - Latence : ~quelques ms quelle que soit la taille de fused_items.
"""

from collections import defaultdict
from pymongo import MongoClient
from env_config import MONGO_URI, MONGO_DB, PARENT_CHILD_MAX_CHARS
from utils.logging_config import get_logger

logger = get_logger("rag.parent_child")

# Singleton connexion MongoDB
_mongo_client = None
_mongo_col = None


def _get_collection():
    """Retourne la collection MongoDB chunks (singleton)."""
    global _mongo_client, _mongo_col
    if _mongo_col is None:
        _mongo_client = MongoClient(MONGO_URI)
        _mongo_col = _mongo_client[MONGO_DB]["chunks"]
    return _mongo_col


def _build_parent_text(siblings: list[dict], child_chunk_idx: int) -> str:
    """
    Concatène les chunks frères d'une section pour former le texte parent.
    Si le total dépasse PARENT_CHILD_MAX_CHARS, sélectionne une fenêtre
    centrée sur l'enfant.
    """
    full_text = "\n\n".join(s["content"] for s in siblings if s.get("content"))

    if len(full_text) <= PARENT_CHILD_MAX_CHARS:
        return full_text

    # Fenêtre centrée sur l'enfant
    child_pos = next(
        (i for i, s in enumerate(siblings) if s.get("chunk_idx") == child_chunk_idx),
        len(siblings) // 2
    )
    selected = [siblings[child_pos]]
    total_len = len(siblings[child_pos].get("content", ""))
    left, right = child_pos - 1, child_pos + 1

    while total_len < PARENT_CHILD_MAX_CHARS:
        added = False
        if left >= 0:
            c = siblings[left].get("content", "")
            if total_len + len(c) <= PARENT_CHILD_MAX_CHARS:
                selected.insert(0, siblings[left])
                total_len += len(c)
                left -= 1
                added = True
        if right < len(siblings):
            c = siblings[right].get("content", "")
            if total_len + len(c) <= PARENT_CHILD_MAX_CHARS:
                selected.append(siblings[right])
                total_len += len(c)
                right += 1
                added = True
        if not added:
            break

    selected.sort(key=lambda x: x.get("chunk_idx", 0))
    return "\n\n".join(s["content"] for s in selected if s.get("content"))


def expand_to_parent(fused_items: list[dict]) -> list[dict]:
    """
    Remplace le contenu 'doc' de chaque chunk enfant par le texte de sa section
    parente, en UNE SEULE requête MongoDB (anti-pattern N+1).

    Étapes :
      1. Collecter toutes les paires (source, section_idx) des items éligibles.
      2. Un seul find() MongoDB avec filtre $in sur ces paires.
      3. Grouper les résultats par (source, section_idx) en mémoire.
      4. Pour chaque item, construire le texte parent depuis le groupe.

    Les scores (ce_score, rrf, sim_est...) sont conservés - seul 'doc' change.
    """
    if not fused_items:
        return fused_items

    # 1) Collecter les sections à chercher (hors summary)
    to_fetch = []  # liste de (source, section_idx) uniques
    seen = set()
    item_keys = []  # (source, section_idx, chunk_idx, eligible) pour chaque item

    for item in fused_items:
        meta = item.get("meta") or {}
        source = meta.get("source", "")
        section_idx = meta.get("section_idx")
        chunk_idx = meta.get("chunk_idx", 0)
        chunk_type = meta.get("chunk_type", "text")

        # Exclure les résumés RAPTOR (déjà une synthèse) et les items sans clé
        eligible = (chunk_type != "summary" and source and section_idx is not None)
        item_keys.append((source, section_idx, chunk_idx, eligible))

        if eligible:
            key = (source, section_idx)
            if key not in seen:
                seen.add(key)
                to_fetch.append(key)

    if not to_fetch:
        return fused_items

    # 2) Une seule requête MongoDB avec $or exact sur les paires (source, section_idx)
    #    Évite le produit cartésien source x section_idx qui ramènerait des sections hors-scope.
    try:
        col = _get_collection()
        or_clauses = [{"source": src, "section_idx": sidx} for src, sidx in to_fetch]

        raw = list(
            col.find(
                {"$or": or_clauses},
                {"content": 1, "chunk_idx": 1, "source": 1, "section_idx": 1, "_id": 0}
            ).sort("chunk_idx", 1)
        )
    except Exception as e:
        logger.warning("[parent_child] Erreur MongoDB : %s", e)
        return fused_items

    # 3) Grouper par (source, section_idx) en mémoire
    groups: dict[tuple, list] = defaultdict(list)
    for doc in raw:
        key = (doc["source"], doc["section_idx"])
        if key in seen:  # ne garder que les sections demandées
            groups[key].append(doc)

    # 4) Appliquer l'expansion sur chaque item éligible
    for item, (source, section_idx, chunk_idx, eligible) in zip(fused_items, item_keys):
        if not eligible:
            continue

        siblings = groups.get((source, section_idx), [])
        if len(siblings) <= 1:
            continue  # section à un seul chunk : rien à étendre

        parent_text = _build_parent_text(siblings, chunk_idx)
        if parent_text and len(parent_text) > len(item.get("doc", "")):
            item["doc"] = parent_text
            item["parent_expanded"] = True

    return fused_items
