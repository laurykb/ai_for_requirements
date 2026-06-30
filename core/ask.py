"""Entrées principales pour interroger le pipeline RAG."""

import os
import pickle
from pathlib import Path
from typing import Optional, Tuple

from utils.logging_config import get_logger
from utils.tracing import start_trace, span
from retrieval.vector_store import get_vector_store
from env_config import (
    NUM_CHUNKS,
    RRF_K,
    WEIGHT_SEMANTIC, WEIGHT_BM25,
    SELF_RAG_ENABLED, NUM_CHUNKS_PARENT_CHILD,
    CE_RELEVANCE_THRESHOLD, OUT_OF_SCOPE_MESSAGE,
    USE_CROSS_ENCODER,
    MONGO_URI, MONGO_DB,
    LOG_LEVEL,
)
from retrieval.retrieve import hybrid_retrieve
from core.llm_answer import answer, answer_stream
from nlp.query_rewriter import trim_only_rewrite
from indexing.keyword_index import load_bm25_from_mongo

logger = get_logger("rag.ask")
# Les dumps de retrieval (tables de chunks) ne sortent qu'en LOG_LEVEL=DEBUG.
_DEBUG = LOG_LEVEL.upper() == "DEBUG"

# -- Singletons / caches process-level -----------------------------------------
_vector_store = None  # magasin vectoriel (Chroma)
_bm25_cache: dict = {}  # source_filter -> bm25_tuple (ou None si indisponible)


def clear_retrieval_caches() -> None:
    """Vide les caches process-level (vector store/BM25).

    À appeler après une (ré)ingestion pour que les requêtes suivantes
    rechargent les index fraîchement écrits au lieu de servir des données périmées.
    """
    global _vector_store
    _vector_store = None
    _bm25_cache.clear()
    logger.info("Caches de retrieval vidés (vector store/BM25)")

def _get_vector_store():
    """Retourne le magasin vectoriel (singleton - ouvert une seule fois par process)."""
    global _vector_store
    if _vector_store is None:
        _vector_store = get_vector_store()
    return _vector_store


# ---------- charger un index BM25 pre-calcule ----------
def load_bm25_cache(path: str = "data/bm25_index.pkl") -> Optional[Tuple]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            bm25_tuple = pickle.load(f)
        if isinstance(bm25_tuple, tuple) and len(bm25_tuple) == 4:
            return bm25_tuple
    except Exception as e:
        logger.warning("[bm25] Impossible de charger le cache BM25 : %s", e)
    return None


def _bm25_fallback_path() -> Path:
    """Chemin du fallback BM25 local (format pkl historique)."""
    return Path(__file__).resolve().parent.parent / "data" / "bm25_index.pkl"


def _load_bm25(source_filter=None) -> Optional[Tuple]:
    """Charge l'index BM25 GLOBAL fusionné (multi-document), avec fallback pkl.

    On charge toujours l'index « tous documents » : le filtrage par `source_filter`
    (un ou plusieurs documents) est appliqué plus bas, au niveau des scores, dans
    `bm25_search` - correct sur l'index global et identique quel que soit le nombre
    de documents sélectionnés. `source_filter` est donc ignoré ici (gardé pour la
    signature historique). Cache process-level : sans lui, l'index était re-téléchargé
    et dé-picklé depuis Mongo à CHAQUE requête. Appeler clear_retrieval_caches()
    après une ré-ingestion.
    """
    if "__all__" in _bm25_cache:
        return _bm25_cache["__all__"]

    bm25_tuple = load_bm25_from_mongo(source_doc=None)  # None = fusion de tous les index
    if bm25_tuple is None:
        bm25_tuple = load_bm25_cache(str(_bm25_fallback_path()))

    _bm25_cache["__all__"] = bm25_tuple
    return bm25_tuple

def _clean_query(user_q: str) -> str:
    """Nettoie la question sans LLM (politesse, espaces). Les termes - acronymes et
    identifiants techniques (TOE, EAL3, FCS_IPSEC_EXT.1...) - sont gardés VERBATIM,
    ce qui est ce qu'on veut pour la recherche documentaire."""
    return trim_only_rewrite(user_q)


def process_query(user_q: str, selected_chunks=None, system_prompt=None, source_filter: str = None,
                  conversation_history: list = None):
    if not user_q:
        logger.warning("Aucune question fournie.")
        return None, None, None  # (response, chunks, citations)

    with start_trace("rag.query", query=user_q, source=source_filter,
                     mode=("generate_only" if selected_chunks is not None else "full")) as _tr:
        # Si des chunks sont fournis, ne faire que la generation de la reponse
        if selected_chunks is not None:
            with span("generation"):
                rep, citations = answer(user_q, selected_chunks, system_prompt=system_prompt,
                                        conversation_history=conversation_history)
            return rep, selected_chunks, citations

        q_main = query = _clean_query(user_q)

        # Ouvrir la collection Chroma deja indexee (singleton)
        collection = _get_vector_store()

        # Charger BM25 depuis MongoDB (multi-document) avec fallback pkl
        bm25_tuple = _load_bm25(source_filter)
        if bm25_tuple is None:
            logger.debug("[bm25] Aucun cache BM25 trouvé -> retrieval sans keyword search.")

        # Retrieve hybride (sémantique + BM25) + RRF
        with span("retrieve") as _rs:
            final_chunks, max_ce_score = hybrid_retrieve(
                collection=collection,
                query=query,
                bm25_tuple=bm25_tuple,
                topk_chunks=NUM_CHUNKS,
                rrf_k=RRF_K,
                rerank_on=True,
                debug=_DEBUG,
                weight_semantic=WEIGHT_SEMANTIC,
                weight_bm25=WEIGHT_BM25,
                source_filter=source_filter
            )
        if _rs is not None:
            _rs.set("num_chunks", len(final_chunks))

        # -- Détection hors-scope ----------------------------------------------
        if USE_CROSS_ENCODER and max_ce_score is not None and max_ce_score < CE_RELEVANCE_THRESHOLD:
            logger.debug("[scope] Hors-scope détecté (max CE=%.3f < %s)", max_ce_score, CE_RELEVANCE_THRESHOLD)
            _tr.set("hors_scope", True)
            return OUT_OF_SCOPE_MESSAGE, [], []

        if not final_chunks:
            logger.debug("[resultat] Aucun chunk pertinent trouvé.")
            return "Je n'ai pas trouvé d'information sur ce sujet dans vos documents.", [], []

        # Reponse finale (LLM de generation)
        with span("generation"):
            rep, citations = answer(q_main, final_chunks, system_prompt=system_prompt,
                                    conversation_history=conversation_history)
        _tr.set("num_chunks", len(final_chunks))
        return rep, final_chunks, citations  # (reponse, chunks, citations)


def _prepare_retrieval(user_q: str, source_filter: str = None,
                       conversation_history: list = None,
                       parent_child_on: bool = None):
    """
    Étapes communes de retrieval (nettoyage de la question + hybrid_retrieve).
    Retourne (q_main, final_chunks).
    """
    q_main = query = _clean_query(user_q)

    collection = _get_vector_store()

    bm25_tuple = _load_bm25(source_filter)

    # Quand Parent-Child est actif, on réduit le topk pour éviter un contexte trop long
    # (chaque chunk enfant -> ~4000 chars de contexte parent -> 8 chunks -> ~32K total)
    _pc_active = parent_child_on if parent_child_on is not None else True
    _topk = NUM_CHUNKS_PARENT_CHILD if _pc_active else NUM_CHUNKS

    with span("retrieve") as _rs:
        final_chunks, max_ce_score = hybrid_retrieve(
            collection=collection,
            query=query,
            bm25_tuple=bm25_tuple,
            topk_chunks=_topk,
            rrf_k=RRF_K,
            rerank_on=True,
            debug=_DEBUG,
            weight_semantic=WEIGHT_SEMANTIC,
            weight_bm25=WEIGHT_BM25,
            source_filter=source_filter,
            parent_child_on=parent_child_on,
        )
    if _rs is not None:
        _rs.set("num_chunks", len(final_chunks))

    # -- Détection hors-scope --------------------------------------------------
    # Si le cross-encoder a tourné et que son meilleur score est sous le seuil,
    # aucun chunk n'est pertinent -> on retourne un signal out_of_scope.
    if USE_CROSS_ENCODER and max_ce_score is not None and max_ce_score < CE_RELEVANCE_THRESHOLD:
        logger.debug("[scope] Hors-scope détecté (max CE=%.3f < %s)", max_ce_score, CE_RELEVANCE_THRESHOLD)
        return q_main, [], max_ce_score  # final_chunks vide + score pour l'appelant

    return q_main, final_chunks


def process_query_stream(user_q: str, system_prompt=None, source_filter: str = None,
                         conversation_history: list = None,
                         parent_child_on: bool = None,
                         self_rag_enabled: bool = None):
    """
    Version streaming de process_query() avec mémoire conversationnelle.
    Si le Self-RAG est actif, évalue et retente en non-streaming avant de streamer la
    meilleure réponse. `self_rag_enabled` permet de l'activer PAR REQUÊTE (None = défaut
    config SELF_RAG_ENABLED), comme parent_child_on.
    Retourne (token_generator, final_chunks, citations).
    Si aucun chunk n'est trouvé, retourne (None, [], []).
    """
    if not user_q:
        return None, [], []

    # -- Self-RAG : évaluation + retry (non-streaming) ------------------------
    use_self_rag = SELF_RAG_ENABLED if self_rag_enabled is None else self_rag_enabled
    if use_self_rag:
        from core.self_rag import self_rag_query
        best_answer, best_chunks, best_citations, self_rag_metrics = self_rag_query(
            user_q,
            system_prompt=system_prompt,
            source_filter=source_filter,
            conversation_history=conversation_history,
        )
        if not best_chunks:
            return None, [], []

        # On "streame" la réponse déjà générée token par token (simulé)
        def _replay_gen():
            for token in best_answer:
                yield token

        logger.info("[self-rag] Score final : %.2f en %d tentative(s)",
                    self_rag_metrics.get("self_rag_score", 0),
                    self_rag_metrics.get("self_rag_attempts", 1))
        return _replay_gen(), best_chunks, best_citations

    # -- Pipeline classique ----------------------------------------------------
    # Trace la phase de retrieval (synchrone). La génération est streamée par
    # l'appelant, hors de cette trace (timing génération : étape ultérieure).
    with start_trace("rag.query_stream", query=user_q, source=source_filter):
        retrieval_result = _prepare_retrieval(
            user_q, source_filter=source_filter,
            conversation_history=conversation_history,
            parent_child_on=parent_child_on,
        )

    # _prepare_retrieval retourne (q_main, chunks) ou (q_main, [], max_ce_score) si hors-scope
    if len(retrieval_result) == 3:
        q_main, final_chunks, max_ce_score = retrieval_result
        # Hors-scope : on streame le message d'information
        def _scope_gen():
            yield OUT_OF_SCOPE_MESSAGE
        return _scope_gen(), [], []

    q_main, final_chunks = retrieval_result

    if not final_chunks:
        logger.debug("[resultat] Aucun chunk pertinent trouvé.")
        return None, [], []

    token_gen, citations = answer_stream(
        q_main, final_chunks, system_prompt=system_prompt,
        conversation_history=conversation_history
    )
    return token_gen, final_chunks, citations


def retrieve_only(user_q: str, source_filter: str = None,
                  conversation_history: list = None,
                  parent_child_on: bool = None):
    """
    Exécute uniquement le retrieval (sans génération).
    Utilisé par le harnais d'évaluation pour mesurer la qualité du retrieval
    sans payer le coût (et la variance) de la génération LLM.
    Retourne (q_main, chunks). chunks vide si hors-scope ou rien trouvé.
    """
    with start_trace("rag.retrieve_only", query=user_q, source=source_filter) as _tr:
        result = _prepare_retrieval(
            user_q, source_filter=source_filter,
            conversation_history=conversation_history,
            parent_child_on=parent_child_on,
        )
        if len(result) == 3:  # (q_main, [], max_ce_score) -> hors-scope
            _tr.set("hors_scope", True)
            return result[0], []
        _tr.set("num_chunks", len(result[1]))
        return result  # (q_main, chunks)


if __name__ == "__main__":
    user_q = input("Question : ").strip()
    rep, chunks, citations = process_query(user_q)
    if rep:
        print(rep)
        if citations:
            print("\n--- Sources ---")
            for c in citations:
                page_str = f", page {c['page']}" if c['page'] else ""
                print(f"  [{c['idx']}] {c['source']}{page_str}")
