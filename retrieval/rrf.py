"""Fusion de classements (sémantique, BM25) via Reciprocal Rank Fusion."""

from collections import defaultdict
from env_config import NUM_CHUNKS


def normalize_scores(scores):
    """
    Normalise une liste de scores numériques entre 0 et 1 (min-max scaling).
    Si tous les scores sont identiques, retourne 1.0 pour tous.
    """
    if not scores:
        return []
    min_s, max_s = min(scores), max(scores)
    if max_s == min_s:
        return [1.0 for _ in scores]
    return [(s - min_s) / (max_s - min_s) for s in scores]


def rrf(rank_lists, k=60):
    """
    Applique la Reciprocal Rank Fusion (RRF) sur plusieurs listes d'IDs classés.
    Chaque ID reçoit un score basé sur la somme des inverses de son rang dans chaque liste :
        score = sum(1 / (k + rang))
    Plus k est grand, plus la fusion est lissée (les premiers rangs sont moins favorisés).
    Retourne une liste triée (id, score RRF décroissant).
    """
    scores = defaultdict(float)
    for ranked_ids in rank_lists:
        for r, _id in enumerate(ranked_ids, start=1):
            scores[_id] += 1.0 / (k + r)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def fuse_with_rrf(
    lists_a,
    lookups_a,
    lists_b=None,
    lookups_b=None,
    rrf_k=60,
    topk_final=NUM_CHUNKS,
    weight_semantic=0.5,
    weight_bm25=0.5
):
    """
    Fusionne deux familles de classements (ex: semantic & bm25) via RRF, puis prépare le TOP-K final.
    - Normalise les scores BM25 et sémantiques (cosine) pour les rendre comparables.
    - Calcule le score RRF pour chaque ID.
    - Calcule un score global pondéré (poids semantic/bm25 ajustables).
    - Retourne les top-k résultats triés par score global.

    Args:
        lists_a: listes d'IDs classés (ex: résultats sémantiques)
        lookups_a: dictionnaires d'infos pour chaque ID (ex: score, texte...)
        lists_b: listes d'IDs classés (ex: résultats BM25)
        lookups_b: dictionnaires d'infos pour chaque ID (ex: score, texte...)
        rrf_k: paramètre de lissage RRF (plus k est grand, plus la fusion est douce)
        topk_final: nombre de résultats finaux à retourner
        weight_semantic: poids du score sémantique dans le score global
        weight_bm25: poids du score BM25 dans le score global
    """
    # Fusionne toutes les infos par id (A puis B)
    merged = {}
    for lu in (lookups_a or []):
        for _id, payload in lu.items():
            merged.setdefault(_id, payload)
    for lu in (lookups_b or []):
        for _id, payload in lu.items():
            merged.setdefault(_id, payload)

    # Normalisation des scores BM25 (si présents)
    bm25_scores = [item.get("bm25") for item in merged.values() if item.get("bm25") is not None]
    bm25_norms = normalize_scores(bm25_scores) if bm25_scores else []
    i = 0
    for item in merged.values():
        if item.get("bm25") is not None and bm25_norms:
            item["bm25_norm"] = bm25_norms[i]
            i += 1

    # Normalisation des scores sémantiques (sim_est, cosine) si présents
    sim_scores = [item.get("sim_est") for item in merged.values() if item.get("sim_est") is not None]
    sim_norms = normalize_scores(sim_scores) if sim_scores else []
    j = 0
    for item in merged.values():
        if item.get("sim_est") is not None and sim_norms:
            item["sim_norm"] = sim_norms[j]
            j += 1

    # Rassemble toutes les listes d'IDs ordonnées pour RRF (semantic + bm25)
    rank_lists = list(lists_a or [])
    if lists_b:
        rank_lists.extend(lists_b)
    if not any(rank_lists):
        return []

    # Applique la RRF pour obtenir un ordre fusionné
    fused = []
    for _id, score in rrf(rank_lists, k=rrf_k):
        if _id in merged:
            item = merged[_id].copy()
            item["id"] = _id
            item["rrf"] = score
            # Ajoute une estimation de similarité si distance présente (utile pour debug)
            if "distance" in item and item["distance"] is not None:
                try:
                    item["sim_est"] = 1.0 - float(item["distance"])
                except Exception:
                    pass
            # Calcule le score global pondéré (0 si absent)
            bm25_score = item.get("bm25_norm", 0.0)
            sim_score = item.get("sim_norm", 0.0)
            item["score_global"] = weight_semantic * sim_score + weight_bm25 * bm25_score
            fused.append(item)
        if len(fused) >= topk_final:
            break

    # Trie final selon le score global pondéré (du plus pertinent au moins pertinent)
    fused.sort(key=lambda x: x.get("score_global", 0.0), reverse=True)
    return fused

