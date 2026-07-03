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
    }


def save_chunks_to_mongo(docs, collection_name="chunks"):
    col = _get_collection(collection_name)

    for doc in docs:
        record = _doc_to_record(doc)
        col.update_one({"_id": record["_id"]}, {"$set": record}, upsert=True)

    logger.info("%d chunks enregistrés dans MongoDB.", len(docs))


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