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


def run_bm25_for_query(bm25_tuple, query, topn=NUM_CHUNKS, source_filter: str = None):
    """Lance une recherche BM25 sur le corpus complet ou filtré par source."""
    bm25_index, ids, texts, metadatas = bm25_tuple
    if source_filter:
        filtered = [(i, t, m) for i, t, m in zip(ids, texts, metadatas) if m.get("source") == source_filter]
        if filtered:
            ids_f, texts_f, metas_f = zip(*filtered)
            res = bm25_search(bm25_index, list(ids_f), list(texts_f), list(metas_f), query, topn=topn)
        else:
            return [], {}
    else:
        res = bm25_search(bm25_index, ids, texts, metadatas, query, topn=topn)
    return _ranked_ids_from_result(res), search_from_result(res)

