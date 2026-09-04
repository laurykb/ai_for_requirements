# indexing/store_mongo.py
"""Persistance MongoDB des chunks et des requêtes."""

from pymongo import MongoClient
from env_config import MONGO_URI, MONGO_DB
from utils.logging_config import get_logger

logger = get_logger("rag.store")

_mongo_client = None


def _get_collection(collection_name: str):
    """Retourne une collection MongoDB avec client singleton."""
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = MongoClient(MONGO_URI)
    return _mongo_client[MONGO_DB][collection_name]


def _doc_to_record(doc):
    """Convertit un Document LangChain en enregistrement Mongo sérialisable."""
    return {
        "_id": doc.metadata["id"],
        "content": doc.page_content,
        "chunk_idx": doc.metadata["chunk_idx"],
        "section_idx": doc.metadata["section_idx"],
        "source": doc.metadata["source"],
        "page_number": doc.metadata.get("page_number"),
        "chunk_type": doc.metadata.get("chunk_type", "chunk"),
        "quality_status": doc.metadata.get("quality_status", "accepted"),
        "quality_reasons": doc.metadata.get("quality_reasons", []),
        "content_provenance": doc.metadata.get("content_provenance", "raw"),
        # Enrichissement LLM (vide si non activé)
        "keywords": doc.metadata.get("keywords", []),
        "keywords_str": doc.metadata.get("keywords_str", ""),
        "questions": doc.metadata.get("questions", []),
        "questions_str": doc.metadata.get("questions_str", ""),
        # RAPTOR metadata
        "heading": doc.metadata.get("heading", ""),
        "breadcrumb": doc.metadata.get("breadcrumb", ""),
        "summary_num_chunks": doc.metadata.get("summary_num_chunks"),
        # tables/figures
        "table_description": doc.metadata.get("table_description", ""),
        "has_table": doc.metadata.get("has_table", False),
        "has_figure": doc.metadata.get("has_figure", False),
        # entités nommées
        "entities": doc.metadata.get("entities", {}),
        "entities_flat": doc.metadata.get("entities_flat", []),
        "entities_str": doc.metadata.get("entities_str", ""),
        # Baseline LynX : identité de l'exigence (citations -> arbre)
        "req_id": doc.metadata.get("req_id"),
        "req_niveau": doc.metadata.get("req_niveau"),
        "req_domaine": doc.metadata.get("req_domaine"),
        "ingest_version": doc.metadata.get("ingest_version"),
        "content_hash": doc.metadata.get("content_hash"),
    }


def save_chunks_to_mongo(docs, collection_name="chunks"):
    col = _get_collection(collection_name)

    for doc in docs:
        record = _doc_to_record(doc)
        col.update_one({"_id": record["_id"]}, {"$set": record}, upsert=True)

    logger.info("%d chunks enregistrés dans MongoDB.", len(docs))


def replace_source_chunks(docs, source: str, version: str, collection_name="chunks"):
    """Publie une version complète puis retire les anciennes occurrences.

    Les identifiants étant versionnés, une erreur pendant la préparation peut
    être annulée sans toucher à la version précédemment active.
    """
    col = _get_collection(collection_name)
    new_ids = []
    try:
        for doc in docs:
            record = _doc_to_record(doc)
            new_ids.append(record["_id"])
            col.replace_one({"_id": record["_id"]}, record, upsert=True)
    except Exception:
        if new_ids:
            col.delete_many({"_id": {"$in": new_ids}})
        raise
    col.delete_many({"source": source, "ingest_version": {"$ne": version}})
    logger.info("Version %s publiée pour %s (%d chunks).", version, source, len(docs))


def prepare_source_chunks(docs, source: str, version: str, collection_name="chunks"):
    """Écrit une version complète sans retirer la version actuellement active."""
    col = _get_collection(collection_name)
    ids = []
    try:
        for doc in docs:
            record = _doc_to_record(doc)
            ids.append(record["_id"])
            col.replace_one({"_id": record["_id"]}, record, upsert=True)
    except Exception:
        if ids:
            col.delete_many({"_id": {"$in": ids}})
        raise
    return len(ids)


def delete_source_version(source: str, version: str, collection_name="chunks"):
    return _get_collection(collection_name).delete_many(
        {"source": source, "ingest_version": version}
    ).deleted_count


def save_query_to_mongo(query, collection_name="queries"):
    col = _get_collection(collection_name)
    col.insert_one({"query": query})
    logger.debug("Requête enregistrée : %s", query)

"""
COMMANDE DOCKER : 
docker run -d \
  --name mongodb \
  -p 27017:27017 \
  -v ~/mongodb_data:/data/db \
  mongo:7.0
"""
