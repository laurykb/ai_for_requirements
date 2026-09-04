"""Entrées principales pour interroger le pipeline RAG."""

from typing import Optional, Tuple

from utils.logging_config import get_logger
from utils.tracing import start_trace, span
from retrieval.vector_store import get_vector_store
from env_config import (
    NUM_CHUNKS,
    RRF_K,
    WEIGHT_SEMANTIC, WEIGHT_BM25,
    SELF_RAG_ENABLED, NUM_CHUNKS_PARENT_CHILD,
    PARENT_CHILD_ENABLED,
    CE_RELEVANCE_THRESHOLD, OUT_OF_SCOPE_MESSAGE,
    USE_CROSS_ENCODER,
    LOG_LEVEL,
)
from retrieval.retrieve import hybrid_retrieve
from core.llm_answer import answer, answer_stream, refine_for_generation
from nlp.query_rewriter import trim_only_rewrite
from indexing.keyword_index import load_bm25_from_mongo

logger = get_logger("rag.ask")
# Les dumps de retrieval (tables de chunks) ne sortent qu'en LOG_LEVEL=DEBUG.
_DEBUG = LOG_LEVEL.upper() == "DEBUG"

# -- Singletons / caches process-level -----------------------------------------
_vector_store = None  # magasin vectoriel (Chroma)
_bm25_cache: dict = {}  # source_filter -> bm25_tuple (ou None si indisponible)



def clear_retrieval_caches() -> None:
    """Vide les caches process-level (vector store + BM25, y compris le cache
    BM25 fusionné de keyword_index). À appeler après une (ré)ingestion pour que
    les requêtes suivantes rechargent les index fraîchement écrits."""
    global _vector_store
    _vector_store = None
    _bm25_cache.clear()
    from indexing.keyword_index import invalidate_bm25_cache
    invalidate_bm25_cache()  # vide le cache "__all__" de keyword_index
    logger.info("Caches de retrieval vidés (vector store/BM25)")

def _get_vector_store():
    """Retourne le magasin vectoriel (singleton - ouvert une seule fois par process)."""
    global _vector_store
    if _vector_store is None:
        _vector_store = get_vector_store()
    return _vector_store


def _load_bm25(source_filter=None) -> Optional[Tuple]:
    """Charge l'index BM25 global fusionné depuis MongoDB.

    On charge toujours l'index « tous documents » : le filtrage par `source_filter`
    (un ou plusieurs documents) est appliqué plus bas, au niveau des scores, dans
    `bm25_search` - correct sur l'index global et identique quel que soit le nombre
    de documents sélectionnés. `source_filter` est donc ignoré ici. Le cache évite
    de reconstruire l'index Mongo à chaque requête ; l'ingestion l'invalide.
    """
    if "__all__" in _bm25_cache:
        return _bm25_cache["__all__"]

    bm25_tuple = load_bm25_from_mongo(source_doc=None)
    _bm25_cache["__all__"] = bm25_tuple
    return bm25_tuple

def _should_abstain(max_ce_score, question: str, source_filter=None) -> bool:
    """Décide de l'abstention hors-scope. On s'abstient si le meilleur score CE
    est sous le seuil — SAUF sur les questions exploratoires (génériques/
    définitionnelles) où l'on veut retourner le meilleur contexte disponible,
    et SAUF quand le périmètre est épinglé sur une source RÉSERVÉE (baseline
    d'exigences LynX) : corpus maîtrisé, sans bruit, dont le cross-encoder —
    calibré sur de la prose documentaire — score les énoncés au plancher. Là,
    on retourne toujours le meilleur contexte ; le refus éventuel appartient à
    la génération (prompt `baseline.system` : « la baseline ne couvre pas »).
    Mesuré par evals/run_baseline_eval (abstention au niveau génération)."""
    if not USE_CROSS_ENCODER or max_ce_score is None:
        return False
    if source_filter:
        from core.reserved_sources import RESERVED_SOURCES
        if source_filter in RESERVED_SOURCES:
            return False
    from retrieval.intent import is_exploratory
    if is_exploratory(question):
        return False
    return max_ce_score < CE_RELEVANCE_THRESHOLD


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
        # Si des chunks sont fournis, ne faire que la generation de la reponse.
        # Affinage AVANT génération, et retour de la liste affinée : les chunks
        # retournés = la liste numérotée [1..n] du contexte (contrat marqueur↔passage).
        if selected_chunks is not None:
            selected_chunks = refine_for_generation(selected_chunks)
            with span("generation"):
                rep, citations = answer(user_q, selected_chunks, system_prompt=system_prompt,
                                        conversation_history=conversation_history,
                                        already_refined=True)
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
        if _should_abstain(max_ce_score, user_q, source_filter):
            logger.debug("[scope] Hors-scope détecté (max CE=%.3f)", max_ce_score)
            _tr.set("hors_scope", True)
            return OUT_OF_SCOPE_MESSAGE, [], []

        if not final_chunks:
            logger.debug("[resultat] Aucun chunk pertinent trouvé.")
            return "Je n'ai pas trouvé d'information sur ce sujet dans vos documents.", [], []

        # Reponse finale (LLM de generation). Les chunks retournés = la liste
        # affinée réellement numérotée dans le contexte (contrat marqueur↔passage).
        final_chunks = refine_for_generation(final_chunks)
        with span("generation"):
            rep, citations = answer(q_main, final_chunks, system_prompt=system_prompt,
                                    conversation_history=conversation_history,
                                    already_refined=True)
        _tr.set("num_chunks", len(final_chunks))
        return rep, final_chunks, citations  # (reponse, chunks, citations)


def _effective_topk(parent_child_on):
    """Top-k cohérent avec le vrai gate PC. None => suit PARENT_CHILD_ENABLED."""
    active = PARENT_CHILD_ENABLED if parent_child_on is None else parent_child_on
    return NUM_CHUNKS_PARENT_CHILD if active else NUM_CHUNKS


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
    _topk = _effective_topk(parent_child_on)

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
    if _should_abstain(max_ce_score, user_q, source_filter):
        logger.debug("[scope] Hors-scope détecté (max CE=%.3f)", max_ce_score)
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

    # Affinage AVANT génération : les chunks retournés (affichés/persistés par
    # l'appelant, trame `retrieved`) sont EXACTEMENT la liste numérotée [1..n]
    # du contexte — le clic sur un marqueur [n] ouvre le bon passage.
    final_chunks = refine_for_generation(final_chunks)
    token_gen, citations = answer_stream(
        q_main, final_chunks, system_prompt=system_prompt,
        conversation_history=conversation_history, already_refined=True
    )
    return token_gen, final_chunks, citations


def retrieve_wide(query: str, source_filter: str = None, topn: int = None):
    """
    Retrieval LARGE, orienté BREADTH pour le pré-filtre corpus-large (voir
    `core.corpus.prefilter_documents`) — À NE PAS confondre avec `retrieve_only` /
    `_prepare_retrieval` (orientés génération) qui appliquent un plancher de
    couverture ET un plafond `MAX_CHUNKS` : sur un retrieval de génération, la
    liste finale ne fait que ~8-15 chunks, dominés par le(s) plus gros document(s)
    du corpus - un `[:topn]` dessus est un no-op qui ne laisse jamais entrer les
    petits documents pourtant topiquement pertinents. Ici, PAS de plancher de
    couverture, PAS de MAX_CHUNKS, rerank désactivé : on veut un maximum de
    documents distincts représentés, pas la meilleure réponse à une question.

    Retourne (query, chunks) où chunks est une liste de dicts
    `{"doc": str, "meta": {..., "source": str}}`, dédupliqués par id, limitée à
    `topn` éléments (défaut `CORPUS_PREFILTER_TOPN`). Défensif : n'utilise que
    ce qui est disponible (BM25 et/ou sémantique) et ne lève JAMAIS - retourne
    (query, []) en cas d'échec total.
    """
    from env_config import CORPUS_PREFILTER_TOPN
    from indexing.keyword_index import bm25_search
    from retrieval.semantic_search import run_semantic_for_query

    topn = topn or CORPUS_PREFILTER_TOPN
    chunks: list = []
    seen_ids: set = set()

    def _add(_id, doc, meta) -> None:
        if _id is not None:
            if _id in seen_ids:
                return
            seen_ids.add(_id)
        chunks.append({"doc": doc, "meta": meta or {}})

    # -- BM25 (large, tout le corpus) ------------------------------------------
    try:
        bm25_tuple = _load_bm25(None)
    except Exception as e:
        logger.warning("[retrieve_wide] Chargement BM25 impossible : %s", e)
        bm25_tuple = None
    if bm25_tuple:
        try:
            bm25_index, ids, texts, metas = bm25_tuple
            res = bm25_search(bm25_index, ids, texts, metas, query, topn=topn, source_filter=source_filter)
            r_ids = (res.get("ids") or [[]])[0]
            r_docs = (res.get("documents") or [[]])[0]
            r_metas = (res.get("metadatas") or [[]])[0]
            for i, _id in enumerate(r_ids):
                doc = r_docs[i] if i < len(r_docs) else None
                meta = r_metas[i] if i < len(r_metas) else None
                _add(_id, doc, meta)
        except Exception as e:
            logger.warning("[retrieve_wide] Recherche BM25 en échec : %s", e)

    # -- Sémantique (large, tout le corpus) ------------------------------------
    try:
        store = _get_vector_store()
        s_ids, s_lookup = run_semantic_for_query(store, query, topn=topn, source_filter=source_filter)
        for _id in s_ids:
            payload = s_lookup.get(_id) or {}
            _add(_id, payload.get("doc"), payload.get("meta"))
    except Exception as e:
        logger.warning("[retrieve_wide] Recherche sémantique en échec : %s", e)

    from core.evidence_policy import rank_chat_candidates
    return query, rank_chat_candidates(chunks)[:topn]


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
                page_str = f", page {c.get('page')}" if c.get('page') else ""
                print(f"  [{c['idx']}] {c['source']}{page_str}")
