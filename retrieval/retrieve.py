"""Pipeline de retrieval hybride : sémantique + BM25, fusion RRF, rerank, parent-child."""

import time
from concurrent.futures import ThreadPoolExecutor

from retrieval.semantic_search import run_semantic_for_query
from retrieval.keyword_bm25 import run_bm25_for_query
from retrieval.rrf import fuse_with_rrf
from retrieval.cross_encoder import rerank_cross_encoder
from retrieval.parent_child import expand_to_parent
from utils.debug_utils import print_simple_results
from utils.logging_config import get_logger
from utils.tracing import span
from env_config import (
    USE_CROSS_ENCODER,
    CROSS_ENCODER_LOCAL_PATH,
    CE_DEVICE,
    NUM_CHUNKS,
    RRF_K,
    WEIGHT_SEMANTIC,
    WEIGHT_BM25,
    PARENT_CHILD_ENABLED,
    CE_RELEVANCE_THRESHOLD,
)

logger = get_logger("rag.retrieval")


def _run_parallel_retrievers(collection, query, bm25_tuple, topk_chunks, source_filter):
    """Lance les recherches sémantique et BM25 en parallèle."""
    with ThreadPoolExecutor(max_workers=2) as executor:
        fut_sem = executor.submit(
            run_semantic_for_query,
            collection,
            query,
            topn=topk_chunks,
            source_filter=source_filter,
        )
        fut_bm25 = executor.submit(
            run_bm25_for_query,
            bm25_tuple,
            query,
            topn=topk_chunks,
            source_filter=source_filter,
        ) if bm25_tuple else None

        sem_ids, sem_lookup = fut_sem.result()
        bm_ids, bm_lookup = fut_bm25.result() if fut_bm25 else ([], {})

    return sem_ids, sem_lookup, bm_ids, bm_lookup


def _maybe_apply_cross_encoder(query, fused, rerank_on, topk_chunks, debug):
    """Applique le rerank Cross-Encoder et retourne (fused, max_ce_score)."""
    if not (rerank_on and USE_CROSS_ENCODER and fused):
        return fused, None

    t_before = time.perf_counter()
    reranked = rerank_cross_encoder(query, fused, model_path=CROSS_ENCODER_LOCAL_PATH, device=CE_DEVICE)
    t_after = time.perf_counter()
    logger.debug("cross-encoder rerank : %.0fms", (t_after - t_before) * 1000)

    max_ce_score = max((it.get("ce_score", 0.0) for it in reranked), default=None)
    if max_ce_score is not None:
        logger.debug("max CE score : %.3f (seuil=%s)", max_ce_score, CE_RELEVANCE_THRESHOLD)
    if debug:
        print_simple_results("Classement final", reranked, max_items=topk_chunks)
    return reranked, max_ce_score


def hybrid_retrieve(
    collection,
    query,
    bm25_tuple,
    topk_chunks=NUM_CHUNKS,
    rrf_k=RRF_K,
    rerank_on=True,
    debug=True,
    weight_semantic=WEIGHT_SEMANTIC,
    weight_bm25=WEIGHT_BM25,
    source_filter: str = None,
    parent_child_on: bool = None,
):
    """
    Retourne (fused_chunks, max_ce_score).
    max_ce_score : score CE maximum observé sur tous les chunks après rerank.
                   None si le cross-encoder n'a pas tourné (USE_CROSS_ENCODER=False).
    """
    _t_start = time.perf_counter()

    with span("parallel_search") as _sp:
        sem_ids, sem_lookup, bm_ids, bm_lookup = _run_parallel_retrievers(
            collection=collection,
            query=query,
            bm25_tuple=bm25_tuple,
            topk_chunks=topk_chunks,
            source_filter=source_filter,
        )
    if _sp is not None:
        _sp.set("sem", len(sem_ids))
        _sp.set("bm25", len(bm_ids))
    _t_retrieval = time.perf_counter()
    logger.debug("parallel search : %.0fms  (sem=%d bm25=%d)",
                 (_t_retrieval - _t_start) * 1000, len(sem_ids), len(bm_ids))

    # 1) Post-processing sémantique
    for i in sem_ids:
        if i in sem_lookup and sem_lookup[i].get("distance") is not None:
            sem_lookup[i]["sim_est"] = 1.0 - float(sem_lookup[i]["distance"])
    if debug:
        print_simple_results("Semantic results", [sem_lookup.get(i, {}) for i in sem_ids], max_items=topk_chunks)

    # 2) Debug BM25
    if debug and bm_ids:
        print_simple_results("BM25 results", [bm_lookup.get(i, {}) for i in bm_ids], max_items=topk_chunks)

    if not sem_ids and not bm_ids:
        return [], None

    # 3) Fusion RRF (sémantique + BM25)
    fused = fuse_with_rrf(
        lists_a=[sem_ids], lookups_a=[sem_lookup],
        lists_b=[bm_ids] if bm_ids else None, lookups_b=[bm_lookup] if bm_lookup else None,
        rrf_k=rrf_k, topk_final=topk_chunks,
        weight_semantic=weight_semantic, weight_bm25=weight_bm25
    )
    if debug:
        print_simple_results("Fusion RRF", fused, max_items=topk_chunks)
    # 5) Rerank Cross-Encoder
    with span("rerank") as _rr:
        fused, max_ce_score = _maybe_apply_cross_encoder(
            query=query,
            fused=fused,
            rerank_on=rerank_on,
            topk_chunks=topk_chunks,
            debug=debug,
        )
    if _rr is not None and max_ce_score is not None:
        _rr.set("max_ce", round(max_ce_score, 3))

    # 6) Parent-Child : remplace le contenu enfant par la section parente complète
    # parent_child_on=None -> utilise la valeur de config ; True/False -> override
    _pc_active = PARENT_CHILD_ENABLED if parent_child_on is None else parent_child_on
    if _pc_active and fused:
        fused = expand_to_parent(fused)
        if debug:
            expanded = sum(1 for it in fused if it.get("parent_expanded"))
            logger.debug("[parent_child] %d/%d chunks étendus au contexte parent", expanded, len(fused))

    _t_end = time.perf_counter()
    logger.debug("TOTAL pipeline retrieval : %.0fms -> %d chunks", (_t_end - _t_start) * 1000, len(fused))
    return fused, max_ce_score
