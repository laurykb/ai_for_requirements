"""
Harnais d'évaluation du RAG - exécutable.

Charge un jeu de Q/R doré, lance le pipeline, calcule les métriques et compare
au run précédent (non-régression). S'appuie sur core/evaluation.py.

Deux modes :
  - retrieval : retrieval seul (rapide, sans génération) -> métriques de RECHERCHE
                (hit@k mots-clés, rappel/précision du contexte).
  - full      : pipeline complet -> métriques de RÉPONSE (fidélité, pertinence,
                exact match, F1) en plus des métriques de recherche.

Usage (depuis la racine du projet) :
  PYTHONUTF8=1 python -m evals.run_eval --mode retrieval
  PYTHONUTF8=1 python -m evals.run_eval --mode full --no-judge --limit 3
  PYTHONUTF8=1 python -m evals.run_eval --mode full            # avec LLM-as-judge (lent)
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from utils.logging_config import get_logger

logger = get_logger("rag.eval")

# Métriques numériques agrégées (ordre d'affichage).
_METRIC_KEYS = [
    "keyword_hit_rate", "context_recall", "context_precision",
    "exact_match", "f1_token", "faithfulness", "answer_relevance",
    "context_relevance", "latency_s", "num_chunks_retrieved",
]

_DEFAULT_DATASET = Path(__file__).resolve().parent / "golden_qa_anssi.json"


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


def _evaluate_item(item: dict, mode: str, source_filter: str, use_judge: bool, judge) -> dict:
    from core.ask import process_query, retrieve_only
    from core.evaluation import (
        evaluate_single, keyword_hit_rate, context_recall, context_precision,
    )

    question = (item.get("question") or "").strip()
    reference = (item.get("answer") or item.get("reference") or "").strip()
    expected_kw = item.get("expected_keywords") or []

    t0 = time.time()
    try:
        if mode == "retrieval":
            _q_main, chunks = retrieve_only(question, source_filter=source_filter)
            chunks = chunks or []
            metrics = {
                "question": question,
                "keyword_hit_rate": keyword_hit_rate(chunks, expected_kw),
                "context_recall": context_recall(chunks, reference) if reference else None,
                "context_precision": context_precision(chunks, reference) if reference else None,
                "num_chunks_retrieved": len(chunks),
            }
        else:  # full
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

        metrics["latency_s"] = round(time.time() - t0, 2)
        metrics["status"] = "ok"
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
    parser.add_argument("--mode", choices=["retrieval", "full"], default="retrieval",
                        help="retrieval = recherche seule (rapide) ; full = pipeline complet.")
    parser.add_argument("--no-judge", action="store_true", help="Désactive le LLM-as-judge (mode full).")
    parser.add_argument("--limit", type=int, default=0, help="N'évalue que les N premières questions (0 = toutes).")
    parser.add_argument("--source-filter", default=None, help="Restreint le retrieval à un document source.")
    parser.add_argument("--no-save", action="store_true", help="Ne sauvegarde pas le run dans MongoDB.")
    parser.add_argument("--name", default=None, help="Nom du run (défaut : horodatage).")
    args = parser.parse_args()

    data = _load_dataset(Path(args.dataset))
    items = data["items"]
    if args.limit > 0:
        items = items[:args.limit]
    source_filter = args.source_filter  # None = recherche sur tout l'index
    use_judge = (args.mode == "full") and not args.no_judge
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
        # Ne comparer qu'aux runs du MÊME mode (le nom de run est préfixé par le mode)
        # pour rester apples-to-apples (nb de chunks, métriques disponibles).
        same_mode = [r for r in runs if (r.get("run_name") or "").startswith(args.mode)]
        previous = same_mode[0] if same_mode else None
    except Exception as e:
        logger.warning("Impossible de charger les runs précédents : %s", e)

    results = []
    for i, item in enumerate(items, 1):
        logger.info("  [%d/%d] %s", i, len(items), (item.get("question") or "")[:60])
        results.append(_evaluate_item(item, args.mode, source_filter, use_judge, judge))

    agg = _aggregate(results)
    _print_report(data.get("dataset", args.dataset), args.mode, results, agg)
    _print_regression(previous, agg)

    if not args.no_save:
        try:
            from core.evaluation import save_eval_run_to_mongo
            # save_eval_run_to_mongo recalcule son propre agrégat ; on stocke aussi le nôtre.
            for r in results:
                r.setdefault("status", "ok")
            run_id = save_eval_run_to_mongo(results, run_name=run_name)
            print(f"\n[sauvegarde] Run « {run_name} » enregistré dans MongoDB (id={run_id}).")
        except Exception as e:
            logger.warning("Sauvegarde Mongo impossible : %s", e)

    # Code de sortie non-nul si une régression nette est détectée (utile en CI).
    if previous:
        prev_agg = previous.get("aggregate", {}) or {}
        for k in ("keyword_hit_rate", "faithfulness", "answer_relevance"):
            cur, prev = agg.get(k), prev_agg.get(k)
            if cur is not None and prev is not None and cur - prev < -0.05:
                print(f"\n[CI] Régression nette sur {k} ({prev:.3f} -> {cur:.3f}).")
                return 1
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
