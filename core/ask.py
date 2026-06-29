"""Entrées principales pour interroger le pipeline RAG."""

import os
import pickle
from pathlib import Path
from typing import Optional, Tuple

from utils.logging_config import get_logger
from utils.tracing import start_trace, span
from core.model_router import build_llm
from retrieval.vector_store import get_vector_store
from env_config import (
    NUM_CHUNKS,
    RRF_K,
    WEIGHT_SEMANTIC, WEIGHT_BM25,
    SELF_RAG_ENABLED, NUM_CHUNKS_PARENT_CHILD,
    CE_RELEVANCE_THRESHOLD, OUT_OF_SCOPE_MESSAGE,
    USE_CROSS_ENCODER,
    VOCAB_JSON_PATH,
    MONGO_URI, MONGO_DB,
    LOG_LEVEL, GRAPH_RAG_ENABLED,
)
from retrieval.retrieve import hybrid_retrieve
from core.llm_answer import answer, answer_stream
from nlp.vocab_builder import load_vocab
from nlp.query_rewriter import guarded_rewrite
from nlp.graph_builder import load_graph_from_mongo
from indexing.keyword_index import load_bm25_from_mongo

logger = get_logger("rag.ask")
# Les dumps de retrieval (tables de chunks) ne sortent qu'en LOG_LEVEL=DEBUG.
_DEBUG = LOG_LEVEL.upper() == "DEBUG"

# ── Singletons / caches process-level ─────────────────────────────────────────
_vector_store = None  # magasin vectoriel (abstrait — Chroma par défaut)
_vocab_cache = None  # (vocab, acronyms)
_bm25_cache: dict = {}  # source_filter -> bm25_tuple (ou None si indisponible)


def clear_retrieval_caches() -> None:
    """Vide les caches process-level (vector store/vocab/BM25/graphe).

    À appeler après une (ré)ingestion pour que les requêtes suivantes
    rechargent les index fraîchement écrits au lieu de servir des données périmées.
    """
    global _vector_store, _vocab_cache
    _vector_store = None
    _vocab_cache = None
    _bm25_cache.clear()
    _entity_graph_cache.clear()
    logger.info("Caches de retrieval vidés (vector store/vocab/BM25/graphe)")

def _get_vector_store():
    """Retourne le magasin vectoriel (singleton — ouvert une seule fois par process)."""
    global _vector_store
    if _vector_store is None:
        _vector_store = get_vector_store()
    return _vector_store

def _get_vocab():
    """Charge le vocabulaire une seule fois depuis le disque (cache en mémoire)."""
    global _vocab_cache
    if _vocab_cache is None:
        _vocab_cache = load_vocab(str(VOCAB_JSON_PATH))
    return _vocab_cache


def _build_rewriter():
    """Construit le LLM de réécriture (rôle 'rewrite' — voir core.model_router)."""
    return build_llm("rewrite")

# ---------- Cache graphe (par source_doc) ----------
_entity_graph_cache: dict = {}  # source_doc -> graphe (ou False si indisponible)


def get_docs_with_graph() -> list[str]:
    """
    Retourne la liste des source_doc qui ont un graphe d'entités dans MongoDB.
    Utilisé par le Streamlit pour distinguer les docs compatibles GraphRAG.
    """
    try:
        from pymongo import MongoClient
        client = MongoClient(MONGO_URI)
        db = client[MONGO_DB]
        return db["entity_graph"].distinct("source_doc")
    except Exception:
        return []


def _load_entity_graph(source_doc: str = None):
    """
    Charge le graphe d'entités depuis MongoDB.
    - Si source_doc est fourni : charge le graphe de ce document (avec cache).
    - Si source_doc est None : charge le premier graphe disponible (fallback).
    Retourne None si aucun graphe n'est disponible.
    """
    global _entity_graph_cache
    cache_key = source_doc or "__first__"

    if cache_key not in _entity_graph_cache:
        try:
            from pymongo import MongoClient
            client = MongoClient(MONGO_URI)
            db = client[MONGO_DB]
            col = db["entity_graph"]

            if source_doc:
                # Chercher d'abord le graphe exact, puis par correspondance partielle
                target = source_doc
                available = col.distinct("source_doc")
                if source_doc not in available:
                    # Correspondance partielle : trouver le graphe dont le nom est le plus proche
                    matches = [d for d in available if source_doc.replace(".pdf", "").replace(".md", "") in d]
                    target = matches[0] if matches else (available[0] if available else None)
                    if target and target != source_doc:
                        logger.debug("[graph] Graphe exact introuvable pour '%s' → utilisation de '%s'", source_doc, target)
            else:
                available = col.distinct("source_doc")
                target = available[0] if available else None

            if target:
                graph = load_graph_from_mongo(source_doc=target)
                _entity_graph_cache[cache_key] = graph if graph else False
                if not graph:
                    logger.debug("[graph] Graphe vide pour '%s' -> GraphRAG désactivé", target)
            else:
                _entity_graph_cache[cache_key] = False
                logger.debug("[graph] Aucun graphe en base -> GraphRAG désactivé")

        except Exception as e:
            logger.warning("[graph] Erreur chargement graphe : %s", e)
            _entity_graph_cache[cache_key] = False

    result = _entity_graph_cache.get(cache_key, False)
    return result if result else None

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


def _load_bm25(source_filter: str | None) -> Optional[Tuple]:
    """Charge BM25 depuis Mongo (cache process-level), avec fallback pkl.

    Sans ce cache, l'index BM25 était re-téléchargé et dé-picklé depuis Mongo
    à CHAQUE requête. Le résultat (y compris None) est mémorisé par source_filter ;
    appeler clear_retrieval_caches() après une ré-ingestion.
    """
    cache_key = source_filter or "__all__"
    if cache_key in _bm25_cache:
        return _bm25_cache[cache_key]

    bm25_tuple = load_bm25_from_mongo(source_doc=source_filter)
    if bm25_tuple is None:
        bm25_tuple = load_bm25_cache(str(_bm25_fallback_path()))

    _bm25_cache[cache_key] = bm25_tuple
    return bm25_tuple

def _condense_question(user_q: str, history: list[dict], llm_rewriter) -> str:
    """
    Si l'historique est non vide, reformule la question de suivi en une question
    autonome (standalone question) en tenant compte du contexte précédent.
    Ex: "Et pour la crypto ?" + historique → "Quelles sont les exigences crypto de FCS_CKM ?"
    """
    if not history or len(history) < 2:
        return user_q

    # Reconstruit les 2 derniers tours
    recent = history[-4:]
    turns = []
    for msg in recent:
        role = "Utilisateur" if msg["role"] == "user" else "Assistant"
        turns.append(f"{role}: {msg['content'][:400]}")
    history_str = "\n".join(turns)

    prompt = f"""Tu es un assistant qui reformule des questions de suivi en questions autonomes.

Historique de conversation :
{history_str}

Question de suivi : {user_q}

Reformule la question de suivi en une question autonome complète et précise,
qui peut être comprise sans l'historique. Réponds UNIQUEMENT avec la question reformulée,
sans explication, sans guillemets.

Question autonome :"""

    try:
        result = llm_rewriter.invoke(prompt).strip()
        # Garde la reformulation seulement si elle est pertinente
        if result and len(result) > 5 and result != user_q:
            logger.debug("[condense] '%s' → '%s'", user_q, result)
            return result
    except Exception as e:
        logger.warning("[condense] Erreur reformulation : %s", e)
    return user_q


def _rewrite_query(
    user_q: str,
    conversation_history: list | None,
    rewrite_enabled: bool,
) -> tuple[str, str]:
    """
    Retourne (question_rewrite, query_retrieval).
    - question_rewrite : version propre utilisée pour la réponse finale
    - query_retrieval  : entrée utilisée par le retriever (condensée + rewrite)
    """
    if not rewrite_enabled:
        from nlp.query_rewriter import trim_only_rewrite
        q_main = trim_only_rewrite(user_q)
        logger.debug("[rewrite] skipped (rewrite_enabled=False) : %s", q_main)
        return q_main, q_main

    vocab, acronyms = _get_vocab()
    llm_rewriter = _build_rewriter()
    user_q_condensed = _condense_question(user_q, conversation_history or [], llm_rewriter)
    q_main = guarded_rewrite(user_q_condensed, llm_rewriter, vocab, acronyms)
    query = user_q_condensed.strip() + "\n" + q_main.strip()
    logger.debug("[rewrite] %s", q_main)
    return q_main, query


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

        with span("rewrite"):
            q_main, query = _rewrite_query(
                user_q=user_q,
                conversation_history=conversation_history,
                rewrite_enabled=True,
            )

        # Ouvrir la collection Chroma deja indexee (singleton)
        collection = _get_vector_store()

        # Charger BM25 depuis MongoDB (multi-document) avec fallback pkl
        bm25_tuple = _load_bm25(source_filter)
        if bm25_tuple is None:
            logger.debug("[bm25] Aucun cache BM25 trouvé -> retrieval sans keyword search.")

        # Charger le graphe d'entites (GraphRAG) — filtré par document si source_filter fourni
        entity_graph = _load_entity_graph(source_doc=source_filter)

        # Retrieve hybride (semantic + bm25 + GraphRAG) + RRF
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
                entity_graph=entity_graph,
                source_filter=source_filter
            )
        if _rs is not None:
            _rs.set("num_chunks", len(final_chunks))

        # ── Détection hors-scope ──────────────────────────────────────────────
        if USE_CROSS_ENCODER and max_ce_score is not None and max_ce_score < CE_RELEVANCE_THRESHOLD:
            logger.debug("[scope] Hors-scope détecté (max CE=%.3f < %s)", max_ce_score, CE_RELEVANCE_THRESHOLD)
            _tr.set("hors_scope", True)
            return OUT_OF_SCOPE_MESSAGE, [], []

        if not final_chunks:
            logger.debug("[resultat] Aucun chunk pertinent trouvé.")
            return "Aucun chunk pertinent trouve.", [], []

        # Reponse finale (LLM de generation)
        with span("generation"):
            rep, citations = answer(q_main, final_chunks, system_prompt=system_prompt,
                                    conversation_history=conversation_history)
        _tr.set("num_chunks", len(final_chunks))
        return rep, final_chunks, citations  # (reponse, chunks, citations)


def _prepare_retrieval(user_q: str, source_filter: str = None,
                       conversation_history: list = None,
                       parent_child_on: bool = None,
                       rewrite_enabled: bool = True,
                       graph_rag_enabled: bool = GRAPH_RAG_ENABLED):
    """
    Étapes communes de retrieval (rewrite + hybrid_retrieve).
    Gère la condensation standalone pour les questions de suivi.
    Retourne (q_main, final_chunks).
    graph_rag_enabled : si False, GraphRAG est court-circuité (doc sans graphe ou option désactivée).
    """
    with span("rewrite"):
        q_main, query = _rewrite_query(
            user_q=user_q,
            conversation_history=conversation_history,
            rewrite_enabled=rewrite_enabled,
        )

    collection = _get_vector_store()

    bm25_tuple = _load_bm25(source_filter)

    # Charger le graphe d'entités filtré par document source
    # Court-circuit si graph_rag_enabled=False (doc sans graphe ou option désactivée)
    if graph_rag_enabled:
        entity_graph = _load_entity_graph(source_doc=source_filter)
        if entity_graph is None:
            logger.debug("[graph] Aucun graphe disponible pour ce document → GraphRAG désactivé")
    else:
        entity_graph = None
        logger.debug("[graph] GraphRAG désactivé par l'utilisateur")

    # Quand Parent-Child est actif, on réduit le topk pour éviter un contexte trop long
    # (chaque chunk enfant → ~4000 chars de contexte parent → 8 chunks → ~32K total)
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
            entity_graph=entity_graph,
            source_filter=source_filter,
            parent_child_on=parent_child_on,
        )
    if _rs is not None:
        _rs.set("num_chunks", len(final_chunks))

    # ── Détection hors-scope ──────────────────────────────────────────────────
    # Si le cross-encoder a tourné et que son meilleur score est sous le seuil,
    # aucun chunk n'est pertinent → on retourne un signal out_of_scope.
    if USE_CROSS_ENCODER and max_ce_score is not None and max_ce_score < CE_RELEVANCE_THRESHOLD:
        logger.debug("[scope] Hors-scope détecté (max CE=%.3f < %s)", max_ce_score, CE_RELEVANCE_THRESHOLD)
        return q_main, [], max_ce_score  # final_chunks vide + score pour l'appelant

    return q_main, final_chunks


def process_query_stream(user_q: str, system_prompt=None, source_filter: str = None,
                         conversation_history: list = None,
                         parent_child_on: bool = None,
                         rewrite_enabled: bool = True,
                         graph_rag_enabled: bool = GRAPH_RAG_ENABLED,
                         self_rag_enabled: bool = None):
    """
    Version streaming de process_query() avec mémoire conversationnelle.
    Si le Self-RAG est actif, évalue et retente en non-streaming avant de streamer la
    meilleure réponse. `self_rag_enabled` permet de l'activer PAR REQUÊTE (None = défaut
    config SELF_RAG_ENABLED), comme parent_child_on / rewrite_enabled / graph_rag_enabled.
    Retourne (token_generator, final_chunks, citations).
    Si aucun chunk n'est trouvé, retourne (None, [], []).
    """
    if not user_q:
        return None, [], []

    # ── Self-RAG : évaluation + retry (non-streaming) ────────────────────────
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

    # ── Pipeline classique ────────────────────────────────────────────────────
    # Trace la phase de retrieval (synchrone). La génération est streamée par
    # l'appelant, hors de cette trace (timing génération : étape ultérieure).
    with start_trace("rag.query_stream", query=user_q, source=source_filter):
        retrieval_result = _prepare_retrieval(
            user_q, source_filter=source_filter,
            conversation_history=conversation_history,
            parent_child_on=parent_child_on,
            rewrite_enabled=rewrite_enabled,
            graph_rag_enabled=graph_rag_enabled,
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
                  rewrite_enabled: bool = True,
                  graph_rag_enabled: bool = GRAPH_RAG_ENABLED):
    """
    Exécute uniquement le retrieval (rewrite + hybrid_retrieve), SANS génération.
    Utilisé par le harnais d'évaluation pour mesurer la qualité du retrieval
    sans payer le coût (et la variance) de la génération LLM.
    Retourne (q_main, chunks). chunks vide si hors-scope ou rien trouvé.
    """
    with start_trace("rag.retrieve_only", query=user_q, source=source_filter) as _tr:
        result = _prepare_retrieval(
            user_q, source_filter=source_filter,
            conversation_history=conversation_history,
            rewrite_enabled=rewrite_enabled,
            graph_rag_enabled=graph_rag_enabled,
        )
        if len(result) == 3:  # (q_main, [], max_ce_score) → hors-scope
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
