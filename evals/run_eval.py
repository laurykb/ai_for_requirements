"""
Harnais d'évaluation du RAG - exécutable.

Charge un jeu de Q/R doré, lance le pipeline, calcule les métriques et compare
au run précédent (non-régression). S'appuie sur core/evaluation.py.

Trois modes :
  - retrieval : retrieval seul (rapide, sans génération) -> métriques de RECHERCHE
                (hit@k mots-clés, rappel/précision lexicaux du contexte).
  - full      : pipeline complet -> métriques de RÉPONSE (fidélité, pertinence,
                exact match, F1) en plus des métriques de recherche.
  - ragas     : pipeline complet -> métriques RAGAS par affirmation (fidélité,
                rappel/précision du contexte, pertinence de la réponse), style RAGAS.

Usage (depuis la racine du projet) :
  PYTHONUTF8=1 python -m evals.run_eval --mode retrieval
  PYTHONUTF8=1 python -m evals.run_eval --mode full --no-judge --limit 3
  PYTHONUTF8=1 python -m evals.run_eval --mode full            # avec LLM-as-judge (lent)
  PYTHONUTF8=1 python -m evals.run_eval --mode ragas --limit 3 # métriques RAGAS par affirmation
"""
from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
import time
from pathlib import Path

from utils.logging_config import get_logger

logger = get_logger("rag.eval")

# Métriques numériques agrégées (ordre d'affichage).
_METRIC_KEYS = [
    "keyword_hit_rate", "context_recall", "context_precision",
    "exact_match", "f1_token", "faithfulness", "answer_relevance",
    "answer_relevancy", "context_relevance", "taux_affirmations_sourcees",
    "precision_attribution", "structured_axis_coverage", "latency_s", "num_chunks_retrieved",
]

_DEFAULT_DATASET = Path(__file__).resolve().parent / "golden_qa_anssi_v2.json"
_LAST_EVAL_PATH = Path(__file__).resolve().parent / "last_eval.json"


def _write_last_eval(path, dataset_name, mode, run_name, aggregate, num_questions, breakdown=None):
    """Archive l'agrégat du dernier run dans evals/last_eval.json (tous modes,
    dont ragas). Boussole lisible hors Mongo. Best-effort côté appelant."""
    payload = {
        "dataset": dataset_name,
        "mode": mode,
        "run_name": run_name,
        "num_questions": num_questions,
        "aggregate": aggregate,
        "breakdown": breakdown or {},
    }
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                          encoding="utf-8")


def _load_dataset(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "items" not in data:
        raise ValueError(f"Dataset invalide (clé 'items' manquante) : {path}")
    return data


def _aggregate(results: list) -> dict:
    """Moyenne des métriques numériques disponibles (ignore None / non-numérique)."""
    agg = {}
    ok = [r for r in results if r.get("status") == "ok"]
    for k in _METRIC_KEYS:
        vals = [r[k] for r in ok if isinstance(r.get(k), (int, float))]
        agg[k] = round(sum(vals) / len(vals), 4) if vals else None
    return agg


def _aggregate_by(results: list, field: str) -> dict:
    """Agrège les métriques par catégorie/stratégie, sans mélanger les profils."""
    groups = {}
    for result in results:
        key = result.get(field) or "unknown"
        groups.setdefault(str(key), []).append(result)
    return {key: {"num_questions": len(rows), "aggregate": _aggregate(rows)}
            for key, rows in sorted(groups.items())}


def _breakdown(results: list) -> dict:
    return {
        "by_query_type": _aggregate_by(results, "query_type"),
        "by_strategy_mode": _aggregate_by(results, "strategy_mode"),
        "by_strategy_profile": _aggregate_by(results, "strategy_profile"),
    }


def _attribution_metrics(metrics: dict, question: str, generated: str,
                         chunks: list, use_judge: bool, judge) -> None:
    """Ajoute les métriques d'attribution par affirmation (mode full) :

    - taux_affirmations_sourcees : fraction des affirmations de la réponse
      soutenues par un passage (statuts sourcee + completee) ;
    - precision_attribution (si juge actif) : sur un échantillon de couples
      (affirmation, passage cité), le juge confirme-t-il le soutien ?
    """
    from core.attribution import attribute_answer, judge_attribution_support

    att = attribute_answer(question, generated, chunks)
    if not att.get("ok"):
        logger.warning("Attribution en échec : %s", att.get("error"))
        return
    metrics["n_affirmations"] = att["n_affirmations"]
    if att["n_affirmations"]:
        metrics["taux_affirmations_sourcees"] = round(
            (att["n_sourcees"] + att["n_completees"]) / att["n_affirmations"], 4)
        if use_judge and judge is not None:
            metrics["precision_attribution"] = judge_attribution_support(
                att["affirmations"], chunks, judge)


def _execute_adaptive(question: str, strategy: dict, source_filter=None):
    """Exécute réellement la stratégie choisie par le contrôleur."""
    mode = strategy["mode"]
    if mode == "synth":
        from core.synthesize_corpus import synthesize_corpus
        result = {}
        for event in synthesize_corpus(question):
            if event.get("type") == "done":
                result = event.get("result") or {}
        return result.get("answer", ""), result.get("chunks", []), []
    if mode == "agent":
        from core.planner import PlannerAgent
        if source_filter:
            from tools.rag_tool import run_tool
            def scoped(name, arguments):
                args = dict(arguments or {})
                args.setdefault("document", source_filter)
                return run_tool(name, args)
            result = PlannerAgent(tool_runner=scoped).run(question)
        else:
            result = PlannerAgent().run(question)
        return result.get("answer", ""), result.get("chunks", []), result.get("sources", [])
    from core.ask import process_query
    return process_query(question, source_filter=source_filter)


def _execute_deep(question: str):
    """Exécute le même pipeline Analyse profonde que l API, avec déchargement."""
    from api.rag import _deep_synthesis_events
    from core.model_router import build_llm
    from env_config import DEEP_RESEARCH_MODEL

    llm = build_llm(
        "synthesize", model=DEEP_RESEARCH_MODEL, think=True, num_predict=4096,
    ).invoke
    result = {}
    for event in _deep_synthesis_events(
        question, DEEP_RESEARCH_MODEL, llm=llm,
    ):
        if event.get("type") == "done":
            result = event.get("result") or {}
    return result.get("answer", ""), result.get("chunks", []), []


def _evaluate_item(item: dict, mode: str, source_filter: str, use_judge: bool, judge) -> dict:
    from core.ask import process_query, retrieve_only
    from core.evaluation import (
        evaluate_single, keyword_hit_rate, structured_axis_coverage, context_recall_lexical, context_precision_lexical,
        faithfulness_ragas, context_recall_ragas, context_precision_ragas, answer_relevancy_ragas,
    )

    question = (item.get("question") or "").strip()
    from core.router import select_query_strategy
    requested_mode = "deep" if mode == "deep_ragas" else item.get("mode", "auto")
    strategy = select_query_strategy(question, requested_mode=requested_mode)
    reference = (item.get("answer") or item.get("reference") or "").strip()
    expected_kw = item.get("expected_keywords") or []

    t0 = time.time()
    generated = ""
    try:
        if mode == "retrieval":
            _q_main, chunks = retrieve_only(question, source_filter=source_filter)
            chunks = chunks or []
            metrics = {
                "question": question,
                "keyword_hit_rate": keyword_hit_rate(chunks, expected_kw),
                "context_recall": context_recall_lexical(chunks, reference) if reference else None,
                "context_precision": context_precision_lexical(chunks, reference) if reference else None,
                "num_chunks_retrieved": len(chunks),
            }
        elif mode in ("ragas", "adaptive_ragas", "deep_ragas"):
            if mode == "deep_ragas":
                generated, chunks, _ = _execute_deep(question)
            elif mode == "adaptive_ragas":
                generated, chunks, _ = _execute_adaptive(
                    question, strategy, source_filter=source_filter)
            else:
                generated, chunks, _ = process_query(question, source_filter=source_filter)
            chunks = chunks or []
            metrics = {
                "question": question,
                "faithfulness": faithfulness_ragas(generated or "", chunks, llm=judge),
                "context_recall": context_recall_ragas(reference, chunks, llm=judge) if reference else None,
                "context_precision": context_precision_ragas(question, reference, chunks, llm=judge) if reference else None,
                "answer_relevancy": answer_relevancy_ragas(generated or "", question, llm=judge),
                "keyword_hit_rate": keyword_hit_rate(chunks, expected_kw),
                "num_chunks_retrieved": len(chunks),
            }
        else:  # full ou adaptive
            if mode == "adaptive":
                generated, chunks, _ = _execute_adaptive(
                    question, strategy, source_filter=source_filter)
            else:
                generated, chunks, _ = process_query(question, source_filter=source_filter)
            chunks = chunks or []
            metrics = evaluate_single(
                question=question,
                generated_answer=generated or "",
                retrieved_chunks=chunks,
                reference_answer=reference,
                use_llm_judge=use_judge,
                llm_judge=judge,
            )
            metrics["keyword_hit_rate"] = keyword_hit_rate(chunks, expected_kw)
            # Attribution par affirmation : taux d'affirmations sourcées, et
            # précision d'attribution (juge LLM : le passage cité soutient-il
            # l'affirmation ?). Best-effort : un échec n'invalide pas le run.
            try:
                _attribution_metrics(metrics, question, generated or "", chunks,
                                     use_judge, judge)
            except Exception as e:
                logger.warning("Attribution impossible pour « %s » : %s", question, e)

        if mode != "retrieval":
            metrics["structured_axis_coverage"] = structured_axis_coverage(
                generated or "", item.get("expected_axes") or [])

        metrics["latency_s"] = round(time.time() - t0, 2)
        metrics["status"] = "ok"
        metrics["policy_version"] = strategy["policy_version"]
        metrics["query_type"] = item.get("query_type") or strategy["query_type"]
        metrics["strategy_mode"] = strategy["mode"]
        metrics["strategy_profile"] = strategy["retrieval"]["profile"]
    except Exception as e:
        logger.exception("Échec évaluation de la question : %s", question)
        metrics = {"question": question, "status": f"error: {e}",
                   "latency_s": round(time.time() - t0, 2)}
    return metrics


def _fmt(v) -> str:
    if v is None:
        return "  -  "
    if isinstance(v, float):
        return f"{v:6.3f}"
    return f"{v:>5}"


def _print_report(dataset_name: str, mode: str, results: list, agg: dict):
    print("\n" + "=" * 78)
    print(f"  ÉVALUATION RAG - {dataset_name}  [mode={mode}]")
    print("=" * 78)
    for i, r in enumerate(results, 1):
        q = (r.get("question") or "")[:54]
        if r.get("status") != "ok":
            print(f"{i:2}.  {q}  ->  {r.get('status')}")
            continue
        hit = _fmt(r.get("keyword_hit_rate"))
        rec = _fmt(r.get("context_recall"))
        faith = _fmt(r.get("faithfulness"))
        lat = _fmt(r.get("latency_s"))
        print(f"{i:2}. {q:<54} hit@k={hit} recall={rec} faith={faith} {lat}s")

    print("-" * 78)
    print("  MOYENNES :")
    for k in _METRIC_KEYS:
        if agg.get(k) is not None:
            print(f"    {k:<22} {agg[k]}")
    print("=" * 78)


def _print_regression(previous: dict, agg: dict):
    if not previous:
        print("\n[non-régression] Aucun run précédent en base - référence établie par ce run.")
        return
    prev_agg = previous.get("aggregate", {}) or {}
    print(f"\n[non-régression] vs run précédent « {previous.get('run_name')} » "
          f"({previous.get('timestamp')}) :")
    # Pour ces métriques, une baisse est une régression (sauf latence : hausse = régression).
    for k in _METRIC_KEYS:
        cur, prev = agg.get(k), prev_agg.get(k)
        if cur is None or prev is None:
            continue
        delta = cur - prev
        if abs(delta) < 1e-6:
            arrow = "="
        elif k in ("latency_s", "num_chunks_retrieved"):
            arrow = "^" if delta > 0 else "v"
        else:
            arrow = "^" if delta > 0 else "v"
        flag = ""
        if k not in ("latency_s", "num_chunks_retrieved"):
            flag = "   régression" if delta < -0.02 else ("   amélioration" if delta > 0.02 else "")
        print(f"    {k:<22} {prev:7.3f} -> {cur:7.3f}  ({delta:+.3f}) {arrow}{flag}")


def main():
    parser = argparse.ArgumentParser(description="Harnais d'évaluation du RAG.")
    parser.add_argument("--dataset", default=str(_DEFAULT_DATASET), help="Chemin du jeu Q/R doré (JSON).")
    parser.add_argument("--mode", choices=["retrieval", "full", "ragas", "adaptive", "adaptive_ragas", "deep_ragas"], default="retrieval",
                        help="retrieval = recherche seule (rapide) ; full = pipeline complet ; "
                             "ragas = RAG direct + métriques RAGAS ; adaptive = politique Auto ; adaptive_ragas = stratégie choisie + métriques RAGAS ; deep_ragas = Analyse profonde + métriques RAGAS.")
    parser.add_argument("--no-judge", action="store_true",
                        help="Désactive le LLM-as-judge en mode full. Sans effet en mode ragas : "
                             "les métriques RAGAS (par affirmation) requièrent toujours le juge.")
    parser.add_argument("--limit", type=int, default=0, help="N'évalue que les N premières questions (0 = toutes).")
    parser.add_argument("--source-filter", default=None, help="Restreint le retrieval à un document source.")
    parser.add_argument("--query-type", default=None,
                        help="N évalue que les items portant ce query_type.")
    parser.add_argument("--no-save", action="store_true", help="Ne sauvegarde pas le run dans MongoDB.")
    parser.add_argument("--name", default=None, help="Nom du run (défaut : horodatage).")
    args = parser.parse_args()

    data = _load_dataset(Path(args.dataset))
    items = data["items"]
    if args.query_type:
        items = [item for item in items if item.get("query_type") == args.query_type]
    if args.limit > 0:
        items = items[:args.limit]
    source_filter = args.source_filter  # None = recherche sur tout l'index
    # Le mode ragas requiert TOUJOURS le juge (métriques par affirmation) ; --no-judge
    # ne s'applique qu'au mode full.
    use_judge = (args.mode in ("ragas", "adaptive_ragas", "deep_ragas")) or (args.mode in ("full", "adaptive") and not args.no_judge)
    run_name = args.name or f"{args.mode}-{time.strftime('%Y%m%d-%H%M%S')}"

    logger.info("Run « %s » : %d questions, mode=%s, judge=%s",
                run_name, len(items), args.mode, use_judge)

    judge = None
    if use_judge:
        from core.evaluation import _get_judge_llm
        judge = _get_judge_llm()

    # Charger le run précédent AVANT de sauvegarder le courant (pour la comparaison).
    previous = None
    try:
        from core.evaluation import load_eval_runs_from_mongo
        runs = load_eval_runs_from_mongo()
        dataset_name = data.get("dataset", args.dataset)
        comparable = [r for r in runs
                      if (r.get("mode") == args.mode and
                          r.get("dataset") == dataset_name and
                          r.get("num_questions") == len(items))]
        previous = comparable[0] if comparable else None
    except Exception as e:
        logger.warning("Impossible de charger les runs précédents : %s", e)

    workers = max(1, int(os.environ.get("EVAL_QUESTION_CONCURRENCY", "1")))
    def evaluate_indexed(pair):
        i, item = pair
        logger.info("  [%d/%d] %s", i, len(items), (item.get("question") or "")[:60])
        return _evaluate_item(item, args.mode, source_filter, use_judge, judge)

    indexed = list(enumerate(items, 1))
    if workers > 1:
        # PersistentClient initialise ses bindings Rust paresseusement et cette
        # première construction n est pas thread-safe. Préchauffage séquentiel.
        from retrieval.vector_store import get_vector_store
        get_vector_store().count()
    if workers == 1:
        results = [evaluate_indexed(pair) for pair in indexed]
    else:
        logger.info("  concurrence inter-questions=%d", workers)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(evaluate_indexed, indexed))

    agg = _aggregate(results)
    _print_report(data.get("dataset", args.dataset), args.mode, results, agg)
    breakdown = _breakdown(results)
    print("\n  VENTILATION PAR TYPE DE REQUÊTE :")
    for category, block in breakdown["by_query_type"].items():
        a = block["aggregate"]
        print(f"    {category:<16} n={block['num_questions']:<3} "
              f"hit={_fmt(a.get('keyword_hit_rate'))} "
              f"recall={_fmt(a.get('context_recall'))} "
              f"faith={_fmt(a.get('faithfulness'))}")
    _print_regression(previous, agg)

    # Archive l'agrégat hors Mongo (tous modes, dont ragas). Indépendant de la
    # sauvegarde Mongo : la boussole reste lisible même si Mongo est absent.
    try:
        _write_last_eval(_LAST_EVAL_PATH, data.get("dataset", args.dataset),
                         args.mode, run_name, agg, len(results), breakdown=breakdown)
        print(f"\n[archive] Agrégat écrit dans {_LAST_EVAL_PATH}.")
    except Exception as e:
        logger.warning("Écriture de last_eval.json impossible : %s", e)

    if not args.no_save:
        try:
            from core.evaluation import save_eval_run_to_mongo
            # save_eval_run_to_mongo recalcule son propre agrégat ; on stocke aussi le nôtre.
            for r in results:
                r.setdefault("status", "ok")
            run_id = save_eval_run_to_mongo(
                results, run_name=run_name, dataset=data.get("dataset", args.dataset),
                mode=args.mode, breakdown=breakdown)
            print(f"\n[sauvegarde] Run « {run_name} » enregistré dans MongoDB (id={run_id}).")
        except Exception as e:
            logger.warning("Sauvegarde Mongo impossible : %s", e)

    # Code de sortie non-nul si une régression nette est détectée (utile en CI).
    if previous:
        prev_agg = previous.get("aggregate", {}) or {}
        # Inclut answer_relevancy (mode ragas) ET answer_relevance (mode full).
        for k in ("keyword_hit_rate", "faithfulness", "answer_relevance",
                  "answer_relevancy", "context_recall", "context_precision"):
            cur, prev = agg.get(k), prev_agg.get(k)
            if cur is not None and prev is not None and cur - prev < -0.05:
                print(f"\n[CI] Régression nette sur {k} ({prev:.3f} -> {cur:.3f}).")
                return 1
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
