"""Pipeline de retrieval hybride : sémantique + BM25, fusion RRF, rerank, parent-child."""

import time
from concurrent.futures import ThreadPoolExecutor

from retrieval.semantic_search import run_semantic_for_query
from retrieval.keyword_bm25 import run_bm25_for_query
from retrieval.rrf import fuse_with_rrf
from retrieval.cross_encoder import rerank_cross_encoder
from retrieval.parent_child import expand_to_parent
from retrieval.hype import resolve_hype_hits
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
    CANDIDATE_POOL_MIN,
    CANDIDATE_POOL_MAX,
    CANDIDATE_POOL_PER_DOC,
    PER_DOC_FLOOR,
    MAX_CHUNKS,
    HYPE_ENABLED,
)
from retrieval.coverage import apply_coverage_floor, elastic_candidate_pool
from core.evidence_policy import rank_chat_candidates

logger = get_logger("rag.retrieval")


def _count_sources(bm25_tuple) -> int:
    """Nombre de documents distincts dans l'index BM25 fusionné (metas = 4e élément)."""
    if not bm25_tuple:
        return 0
    try:
        metas = bm25_tuple[3]
        return len({(m or {}).get("source") for m in metas})
    except (IndexError, TypeError):
        return 0


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
    candidate_pool: int = None,
):
    """
    Retourne (fused_chunks, max_ce_score).
    max_ce_score : score CE maximum observé sur tous les chunks après rerank.
                   None si le cross-encoder n'a pas tourné (USE_CROSS_ENCODER=False).
    """
    _t_start = time.perf_counter()

    if candidate_pool is None:
        candidate_pool = elastic_candidate_pool(
            _count_sources(bm25_tuple),
            pool_min=CANDIDATE_POOL_MIN,
            pool_max=CANDIDATE_POOL_MAX,
            per_doc=CANDIDATE_POOL_PER_DOC,
        )

    with span("parallel_search") as _sp:
        sem_ids, sem_lookup, bm_ids, bm_lookup = _run_parallel_retrievers(
            collection=collection,
            query=query,
            bm25_tuple=bm25_tuple,
            topk_chunks=candidate_pool,
            source_filter=source_filter,
        )
    if _sp is not None:
        _sp.set("sem", len(sem_ids))
        _sp.set("bm25", len(bm_ids))
    _t_retrieval = time.perf_counter()
    logger.debug("parallel search : %.0fms  (sem=%d bm25=%d)",
                 (_t_retrieval - _t_start) * 1000, len(sem_ids), len(bm_ids))

    # Résolution HyPE : les hits sur des vecteurs-question remontent vers leur
    # chunk parent. Inconditionnelle (no-op sans vecteurs-question) : des unités
    # HyPE peuvent exister même si le flag global est éteint — la baseline LynX
    # les génère avec sa propre politique élastique.
    sem_ids, sem_lookup = resolve_hype_hits(sem_ids, sem_lookup)

    # 1) Post-processing sémantique
    for i in sem_ids:
        if i in sem_lookup and sem_lookup[i].get("distance") is not None:
            sem_lookup[i]["sim_est"] = 1.0 - float(sem_lookup[i]["distance"])
    if debug:
        print_simple_results("Semantic results", [sem_lookup.get(i, {}) for i in sem_ids], max_items=candidate_pool)

    # 2) Debug BM25
    if debug and bm_ids:
        print_simple_results("BM25 results", [bm_lookup.get(i, {}) for i in bm_ids], max_items=candidate_pool)

    if not sem_ids and not bm_ids:
        return [], None

    # 3) Fusion RRF (sémantique + BM25) sur le pool élargi
    fused = fuse_with_rrf(
        lists_a=[sem_ids], lookups_a=[sem_lookup],
        lists_b=[bm_ids] if bm_ids else None, lookups_b=[bm_lookup] if bm_lookup else None,
        rrf_k=rrf_k, topk_final=candidate_pool,
        weight_semantic=weight_semantic, weight_bm25=weight_bm25
    )
    if debug:
        print_simple_results("Fusion RRF", fused, max_items=candidate_pool)
    # 5) Rerank Cross-Encoder sur le pool
    with span("rerank") as _rr:
        fused, max_ce_score = _maybe_apply_cross_encoder(
            query=query,
            fused=fused,
            rerank_on=rerank_on,
            topk_chunks=candidate_pool,
            debug=debug,
        )
    if _rr is not None and max_ce_score is not None:
        _rr.set("max_ce", round(max_ce_score, 3))

    # 5b) Autorité des preuves : pertinence × qualité/provenance. La quarantaine
    # est exclue défensivement même si un ancien index la contenait encore.
    fused = rank_chat_candidates(fused)

    # 5c) Plancher de couverture par document (mode « Tous » uniquement),
    # puis réduction au top-k final borné.
    if fused:
        _floor_min = CE_RELEVANCE_THRESHOLD if USE_CROSS_ENCODER else None
        fused = apply_coverage_floor(
            fused,
            base_k=topk_chunks,
            per_doc_floor=(PER_DOC_FLOOR if source_filter is None else 0),
            max_chunks=MAX_CHUNKS,
            floor_min_ce=(_floor_min if source_filter is None else None),
        )

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
