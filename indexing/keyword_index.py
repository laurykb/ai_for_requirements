"""Indexation et recherche BM25 (avec persistance MongoDB)."""

from rank_bm25 import BM25Okapi
import re
import pickle
from pymongo import MongoClient
from env_config import MONGO_URI, MONGO_DB
from utils.sources import normalize_sources
from utils.logging_config import get_logger

logger = get_logger("rag.bm25")

_mongo_client = None


def _get_collection(collection_name: str):
    """Retourne la collection MongoDB demandée (client singleton)."""
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = MongoClient(MONGO_URI)
    return _mongo_client[MONGO_DB][collection_name]


def _tokenize(text):
    """Tokenisation simple compatible BM25."""
    return re.findall(r"\w+", (text or "").lower(), flags=re.UNICODE)


def _build_enriched_text(doc):
    """
    Concatène le contenu du chunk avec ses mots-clés et questions (si disponibles).
    Les keywords et questions sont répétés pour leur donner plus de poids dans BM25.
    """
    text = getattr(doc, "page_content", "") or ""
    meta = getattr(doc, "metadata", {})
    parts = [text]

    # Boost keywords
    keywords_str = meta.get("keywords_str", "")
    if keywords_str:
        parts.append(keywords_str)
        parts.append(keywords_str)

    # Boost questions
    questions_str = meta.get("questions_str", "")
    if questions_str:
        parts.append(questions_str)
        parts.append(questions_str)

    return " ".join(parts)


def build_bm25_index(docs):
    """Construit l'index BM25 et les structures associées.
    Les chunks au contenu vide/blanc sont ignorés (ils pollueraient les scores)."""
    docs = [d for d in docs if (getattr(d, "page_content", "") or "").strip()]
    texts = [getattr(d, "page_content", "") or "" for d in docs]
    ids = [getattr(d, "metadata", {}).get("id", f"doc_{i:04d}") for i, d in enumerate(docs)]
    metadatas = [getattr(d, "metadata", {}) for d in docs]
    enriched_texts = [_build_enriched_text(d) for d in docs]
    corpus_tokens = [_tokenize(t) for t in enriched_texts]
    bm25 = BM25Okapi(corpus_tokens)
    return bm25, ids, texts, metadatas


def bm25_search(bm25, ids, texts, metadatas, query, topn=10, source_filter=None):
    """Recherche BM25 classique et sortie au format pipeline.

    `source_filter` restreint le classement aux chunks dont la métadonnée
    `source` appartient au périmètre demandé (un nom, une liste de noms, ou
    None = tout l'index). Le filtrage se fait *après* le calcul des scores sur
    l'index complet : les positions restent alignées sur `ids/texts/metadatas`,
    ce qui rend la recherche correcte aussi bien sur un index par-document que
    sur l'index global multi-document (fusionné). Découper `ids` en amont
    casserait cet alignement (positions du corpus complet indexées dans une
    liste tronquée).
    """
    q_tokens = _tokenize(query)
    if not q_tokens:
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "scores": [[]]}
    scores = bm25.get_scores(q_tokens)
    candidates = range(len(scores))
    allowed = normalize_sources(source_filter)
    if allowed:
        allowed = set(allowed)
        candidates = [i for i in candidates if (metadatas[i] or {}).get("source") in allowed]
    order = sorted(candidates, key=lambda i: scores[i], reverse=True)[:topn]
    return {
        "ids": [[ids[i] for i in order]],
        "documents": [[texts[i] for i in order]],
        "metadatas": [[metadatas[i] for i in order]],
        "scores": [[float(scores[i]) for i in order]],
    }


# -----------------------------------------------------------------------------
#  BM25 multi-document : stockage / chargement dans MongoDB
# -----------------------------------------------------------------------------

def save_bm25_to_mongo(bm25_tuple, source_doc: str,
                       db_name="ragdb", collection_name="bm25_indexes"):
    """Sérialise l'index BM25 d'un document dans MongoDB (upsert par source_doc)."""
    col = _get_collection(collection_name)
    blob = pickle.dumps(bm25_tuple)
    col.update_one(
        {"source_doc": source_doc},
        {"$set": {"source_doc": source_doc, "index_blob": blob}},
        upsert=True
    )
    logger.info("[bm25] Index sauvegardé dans MongoDB pour '%s'", source_doc)


def load_bm25_from_mongo(source_doc: str = None,
                         db_name="ragdb", collection_name="bm25_indexes"):
    """
    Charge un ou plusieurs index BM25 depuis MongoDB.
    - source_doc=None  -> fusionne tous les index disponibles en un seul tuple global.
    - source_doc=<str> -> charge uniquement l'index du document demandé.
    Retourne un tuple (bm25, ids, texts, metadatas) ou None si absent.
    Résultat mis en cache en mémoire pour éviter la désérialisation MongoDB à chaque requête.
    """
    return _load_bm25_cached(source_doc, db_name, collection_name)


# -- Cache en mémoire pour les index BM25 --------------------------------------
_bm25_cache: dict = {}

def _load_bm25_cached(source_doc, db_name, collection_name):
    """Charge et met en cache le tuple BM25 (clé = source_doc ou '__all__')."""
    cache_key = source_doc or "__all__"
    if cache_key in _bm25_cache:
        return _bm25_cache[cache_key]

    result = _load_bm25_from_mongo_impl(source_doc, db_name, collection_name)
    if result is not None:
        _bm25_cache[cache_key] = result
    return result


def invalidate_bm25_cache(source_doc: str = None):
    """Invalide le cache BM25 (après ré-indexation). Sans argument -> vide tout le cache."""
    global _bm25_cache
    if source_doc is None:
        _bm25_cache.clear()
    else:
        _bm25_cache.pop(source_doc, None)
        _bm25_cache.pop("__all__", None)  # Invalide aussi le cache global


def _load_bm25_from_mongo_impl(source_doc: str = None,
                                db_name="ragdb", collection_name="bm25_indexes"):
    col = _get_collection(collection_name)

    query = {"source_doc": source_doc} if source_doc else {}
    docs = list(col.find(query))

    if not docs:
        return None

    if len(docs) == 1:
        return pickle.loads(docs[0]["index_blob"])

    # Fusion : concaténer ids / texts / metadatas et reconstruire un BM25 global
    all_ids, all_texts, all_metas = [], [], []
    for d in docs:
        _, ids, texts, metas = pickle.loads(d["index_blob"])
        all_ids.extend(ids)
        all_texts.extend(texts)
        all_metas.extend(metas)

    # Reconstruire les tokens à partir des textes bruts (enrichis si disponibles)
    from dataclasses import dataclass

    @dataclass
    class _FakeDoc:
        page_content: str
        metadata: dict

    fake_docs = [_FakeDoc(t, m) for t, m in zip(all_texts, all_metas)]
    enriched = [_build_enriched_text(d) for d in fake_docs]
    corpus_tokens = [_tokenize(t) for t in enriched]
    bm25 = BM25Okapi(corpus_tokens)
    return bm25, all_ids, all_texts, all_metas


def list_bm25_sources(db_name="ragdb", collection_name="bm25_indexes"):
    """Retourne la liste des source_doc ayant un index BM25 en base."""
    return _get_collection(collection_name).distinct("source_doc")
