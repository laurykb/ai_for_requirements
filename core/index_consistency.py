"""Contrôle transversal des trois jambes de l'index documentaire RAG."""
from __future__ import annotations

from core.reserved_sources import RESERVED_SOURCES
from retrieval.vector_store import get_vector_store
from utils.mongo import get_db


def rag_index_consistency() -> dict:
    """Compare Mongo, BM25 et Chroma source par source.

    Mongo est la référence du catalogue. Les chunks mis en quarantaine ne
    nécessitent pas de vecteur ; les unités HyPE peuvent à l'inverse produire
    davantage de vecteurs que de chunks, d'où le contrôle ``>= indexable``.
    """
    try:
        db = get_db()
        rows = list(db["chunks"].aggregate([
            {"$match": {"source": {"$nin": [None, *RESERVED_SOURCES]}}},
            {"$group": {
                "_id": "$source",
                "mongo": {"$sum": 1},
                "quarantined": {"$sum": {"$cond": [
                    {"$eq": ["$quality_status", "quarantined"]}, 1, 0
                ]}},
            }},
        ]))
        mongo = {str(row["_id"]): {
            "mongo": int(row["mongo"]),
            "indexable": int(row["mongo"] - row.get("quarantined", 0)),
        } for row in rows}
        bm25 = {
            str(row["source_doc"])
            for row in db["bm25_indexes"].find(
                {"source_doc": {"$nin": [None, *RESERVED_SOURCES]}},
                {"source_doc": 1, "_id": 0},
            )
        }
    except Exception as exc:
        return {"available": False, "in_sync": False, "error": str(exc),
                "sources": [], "orphans": []}

    vectors = get_vector_store().source_counts()
    if vectors is None:
        return {"available": False, "in_sync": False,
                "error": "Comptage Chroma indisponible.",
                "sources": [], "orphans": []}

    names = sorted(set(mongo) | set(bm25) | (set(vectors) - set(RESERVED_SOURCES)))
    sources = []
    orphans = []
    for name in names:
        counts = mongo.get(name, {"mongo": 0, "indexable": 0})
        vector_count = int(vectors.get(name, 0))
        bm25_ready = name in bm25
        reasons = []
        if counts["mongo"] == 0:
            reasons.append("Source absente de MongoDB.")
        if counts["mongo"] and not bm25_ready:
            reasons.append("Index BM25 absent.")
        if vector_count < counts["indexable"]:
            reasons.append(
                f"Index vectoriel incomplet ({vector_count}/{counts['indexable']})."
            )
        row = {
            "name": name,
            "mongo": counts["mongo"],
            "indexable": counts["indexable"],
            "bm25": bm25_ready,
            "vectors": vector_count,
            "in_sync": not reasons,
            "degraded_reasons": reasons,
        }
        if counts["mongo"] == 0:
            orphans.append(row)
        else:
            sources.append(row)

    return {
        "available": True,
        "in_sync": all(row["in_sync"] for row in sources) and not orphans,
        "sources": sources,
        "orphans": orphans,
        "totals": {
            "mongo": sum(row["mongo"] for row in sources),
            "indexable": sum(row["indexable"] for row in sources),
            "vectors": sum(row["vectors"] for row in sources),
            "orphan_vectors": sum(row["vectors"] for row in orphans),
        },
    }
