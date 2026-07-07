"""
Harnais d'évaluation du CHEMIN AGENT (multi-hop) - exécutable.

Complète evals/run_eval.py (qui mesure le pipeline RAG direct) : ici chaque
question passe par l'AGENT (core.agent.run_agent - planificateur-exécuteur avec
repli ReAct), comme le fait le mode agent du chat. Les métriques sont calculées
sur la réponse finale et les passages cumulés par l'agent :

  - keyword_hit_rate : mots-clés attendus retrouvés dans les passages récupérés
  - context_recall   : couverture de la réponse de référence par le contexte
  - exact_match / f1_token : réponse générée vs réponse de référence
  - tool_calls / latency_s / stopped_reason : comportement de l'agent

Usage (depuis la racine du projet) :
  PYTHONUTF8=1 python -m evals.run_agent_eval                         # candidates multi-hop
  PYTHONUTF8=1 python -m evals.run_agent_eval --engine react          # force l'ancien ReAct
  PYTHONUTF8=1 python -m evals.run_agent_eval --out evals/agent_run.json --limit 5
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from utils.logging_config import get_logger

logger = get_logger("rag.eval.agent")

_DEFAULT_DATASET = Path(__file__).resolve().parent / "golden_multihop_candidates.json"

_METRIC_KEYS = ["keyword_hit_rate", "context_recall", "exact_match", "f1_token",
                "tool_calls", "latency_s"]


def _run_engine(engine: str, question: str) -> dict:
    """Exécute la question via le moteur choisi. `auto` = le chemin par défaut du
    produit (run_agent) ; `react` = l'agent ReAct historique, forcé (comparaisons)."""
    if engine == "react":
        from core.agent import ReActAgent
        return ReActAgent().run(question)
    from core.agent import run_agent
    return run_agent(question)


def _evaluate_item(item: dict, engine: str) -> dict:
    from core.evaluation import keyword_hit_rate, context_recall, exact_match, f1_token

    question = (item.get("question") or "").strip()
    reference = (item.get("answer") or "").strip()
    expected_kw = item.get("expected_keywords") or []

    t0 = time.time()
    try:
        res = _run_engine(engine, question)
        answer = (res.get("answer") or "").strip()
        chunks = res.get("chunks") or []
        metrics = {
            "question": question,
            "keyword_hit_rate": keyword_hit_rate(chunks, expected_kw),
            "context_recall": context_recall(chunks, reference) if reference else None,
            "exact_match": exact_match(answer, reference),
            "f1_token": f1_token(answer, reference),
            "tool_calls": res.get("tool_calls"),
            "stopped_reason": res.get("stopped_reason"),
            "num_chunks": len(chunks),
            "answer": answer[:600],
            "status": "ok",
        }
    except Exception as e:
        logger.exception("Échec évaluation agent : %s", question)
        metrics = {"question": question, "status": f"error: {e}"}
    metrics["latency_s"] = round(time.time() - t0, 2)
    return metrics


def _aggregate(results: list) -> dict:
    agg = {}
    ok = [r for r in results if r.get("status") == "ok"]
    for k in _METRIC_KEYS:
        vals = [r[k] for r in ok if isinstance(r.get(k), (int, float))]
        agg[k] = round(sum(vals) / len(vals), 4) if vals else None
    agg["n_ok"] = len(ok)
    agg["n_total"] = len(results)
    return agg


def main():
    parser = argparse.ArgumentParser(description="Harnais d'évaluation du chemin agent (multi-hop).")
    parser.add_argument("--dataset", default=str(_DEFAULT_DATASET),
                        help="Jeu Q/R (JSON, même format que golden_qa_anssi_v2).")
    parser.add_argument("--engine", choices=["auto", "react"], default="auto",
                        help="auto = chemin agent du produit ; react = ancien ReAct forcé.")
    parser.add_argument("--limit", type=int, default=0, help="N premières questions (0 = toutes).")
    parser.add_argument("--out", default=None, help="Fichier JSON de sortie (résultats + agrégat).")
    args = parser.parse_args()

    data = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    items = data["items"][: args.limit] if args.limit > 0 else data["items"]

    results = []
    for i, item in enumerate(items, 1):
        logger.info("[%d/%d] %s", i, len(items), (item.get("question") or "")[:70])
        r = _evaluate_item(item, args.engine)
        print(f"{i:2}. {(r.get('question') or '')[:58]:<58} "
              f"hit@k={r.get('keyword_hit_rate')} f1={r.get('f1_token')} "
              f"calls={r.get('tool_calls')} {r.get('latency_s')}s [{r.get('status')}]",
              flush=True)
        results.append(r)

    agg = _aggregate(results)
    print("\nMOYENNES :")
    for k in _METRIC_KEYS:
        if agg.get(k) is not None:
            print(f"  {k:<18} {agg[k]}")
    print(f"  ok/total           {agg['n_ok']}/{agg['n_total']}")

    if args.out:
        payload = {"dataset": data.get("dataset"), "engine": args.engine,
                   "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "aggregate": agg, "results": results}
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print(f"\n[sauvegarde] {args.out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
