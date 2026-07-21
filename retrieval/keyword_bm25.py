"""Adaptateur BM25 pour le pipeline de retrieval hybride."""

from indexing.keyword_index import bm25_search
from env_config import NUM_CHUNKS


def _ranked_ids_from_result(res):
    """Extrait la liste d'IDs classés depuis la réponse BM25."""
    return res["ids"][0] if res and res.get("ids") else []


def search_from_result(res):
    """Transforme un résultat BM25 brut en lookup `id -> payload`."""
    out = {}
    if not res or not res.get("ids"):
        return out
    ids = res["ids"][0]
    docs = res["documents"][0]
    metas = res["metadatas"][0]
    scores = res.get("scores", [[]])[0] if res.get("scores") else None
    for i, id in enumerate(ids):
        out[id] = {
            "doc": docs[i],
            "meta": metas[i],
            "bm25": float(scores[i]) if scores is not None else None,
        }
    return out


def run_bm25_for_query(bm25_tuple, query, topn=NUM_CHUNKS, source_filter=None):
    """Lance une recherche BM25 sur le corpus complet ou filtré par source.

    Le filtrage par `source_filter` est délégué à `bm25_search`, qui l'applique
    sur les scores de l'index complet (alignement positions <-> ids préservé).
    Indispensable sur l'index global multi-document : découper `ids` ici
    désalignerait les positions et casserait le classement.
    """
    bm25_index, ids, texts, metadatas = bm25_tuple
    res = bm25_search(bm25_index, ids, texts, metadatas, query, topn=topn, source_filter=source_filter)
    return _ranked_ids_from_result(res), search_from_result(res)

