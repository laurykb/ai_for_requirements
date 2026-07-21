"""Recherche sémantique via le magasin vectoriel + adaptation format pipeline."""

from nlp.ollama_embedding import OllamaEmbedding
from env_config import NUM_CHUNKS

_embedding_singleton = None  # type: OllamaEmbedding | None


def _get_embedding_model():
    """Retourne le modèle d'embedding singleton."""
    global _embedding_singleton
    if _embedding_singleton is None:
        _embedding_singleton = OllamaEmbedding()
    return _embedding_singleton


def hits_to_lookup(hits):
    """Transforme une liste de `VectorHit` (backend-agnostique) en lookup `id -> payload`."""
    return {
        h.id: {"doc": h.document, "meta": h.metadata, "distance": h.distance}
        for h in hits
    }


def run_semantic_for_query(store, query, topn=NUM_CHUNKS, embedding_model=None, source_filter: str = None):
    """Exécute une recherche sémantique pour une requête, via l'interface VectorStore.

    `store` expose `query(embedding, n_results, source_filter) -> list[VectorHit]` ;
    le code ne dépend plus de la forme de réponse propre à un backend.
    """
    emb = embedding_model or _get_embedding_model()
    q_vec = emb.embed_query(query)
    hits = store.query(q_vec, n_results=topn, source_filter=source_filter)
    ranked_ids = [h.id for h in hits]
    return ranked_ids, hits_to_lookup(hits)


