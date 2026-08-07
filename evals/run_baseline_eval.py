#!/usr/bin/env python3
"""Évaluation chiffrée du chat baseline LynX (retrieval + abstention).

Indexe la baseline de RÉFÉRENCE (evals/baseline_reference.json) sous une
source réservée dédiée — la baseline vivante du chat n'est jamais touchée —
puis mesure sur le golden set (evals/baseline_golden.json) :

  - hit@k        : les exigences attendues sont-elles dans les passages
                   remontés ? (rappel par cas, moyenné)
  - first_rank   : rang du premier passage attendu (1 = en tête)
  - abstention   : les questions hors-baseline s'abstiennent-elles, et les
                   questions légitimes passent-elles la porte hors-scope ?

Usage :  .venv/bin/python -m evals.run_baseline_eval [--keep-index]
Écrit evals/last_baseline_eval.json (comparaison de runs). Nécessite Ollama
(embeddings + questions HyPE) et Mongo. L'index d'éval est purgé à la fin
(sauf --keep-index, pour inspection).
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

EVAL_TOPK_DEFAULT = None  # None = réglage du pipeline (MAX_CHUNKS)


def load_reference() -> list[dict]:
    return json.loads((HERE / "baseline_reference.json").read_text(encoding="utf-8"))["exigences"]


def load_golden() -> list[dict]:
    return json.loads((HERE / "baseline_golden.json").read_text(encoding="utf-8"))["cases"]


def score_case(case: dict, retrieved_req_ids: list[str], abstained: bool) -> dict:
    """Score d'UN cas du golden set — pur, testable sans index."""
    expected = list(case.get("expected_req_ids") or [])
    if case.get("expect_abstain"):
        return {"question": case["question"], "kind": "abstention",
                "ok": abstained, "retrieved": retrieved_req_ids}
    found = [r for r in expected if r in retrieved_req_ids]
    ranks = [retrieved_req_ids.index(r) + 1 for r in found]
    return {"question": case["question"], "kind": "retrieval",
            "expected": expected, "found": found,
            "recall": len(found) / len(expected) if expected else 1.0,
            "first_rank": min(ranks) if ranks else None,
            "abstained": abstained, "ok": bool(found) and not abstained,
            "retrieved": retrieved_req_ids}


def aggregate(rows: list[dict]) -> dict:
    """Agrégats du run — pur, testable."""
    retr = [r for r in rows if r["kind"] == "retrieval"]
    abst = [r for r in rows if r["kind"] == "abstention"]
    ranks = [r["first_rank"] for r in retr if r["first_rank"]]
    return {
        "cases": len(rows),
        "retrieval_ok": sum(r["ok"] for r in retr), "retrieval_total": len(retr),
        "mean_recall": round(sum(r["recall"] for r in retr) / len(retr), 3) if retr else None,
        "mean_first_rank": round(sum(ranks) / len(ranks), 2) if ranks else None,
        "false_abstentions": sum(1 for r in retr if r["abstained"]),
        "abstention_ok": sum(r["ok"] for r in abst), "abstention_total": len(abst),
    }


REFUSAL_MARKERS = ("je ne sais pas", "ne couvre pas", "pas d'information",
                   "aucune information", "hors du périmètre", "hors périmètre")


def looks_like_refusal(answer: str) -> bool:
    """La génération a-t-elle refusé de répondre (baseline non couvrante) ?
    Pur, testable — marqueurs du contrat de refus des prompts système."""
    low = (answer or "").lower()
    return any(m in low for m in REFUSAL_MARKERS)


def _generation_refuses(question: str, source: str) -> tuple[bool, str]:
    """Abstention de 2e ligne : le périmètre réservé ne coupe plus au retrieval,
    c'est la GÉNÉRATION qui doit dire « la baseline ne couvre pas »."""
    from api.prompts import baseline_system_default
    from core.ask import process_query
    answer, _chunks, _cit = process_query(
        question, source_filter=source,
        system_prompt=baseline_system_default())
    return looks_like_refusal(answer or ""), (answer or "")[:200]


def _retrieve(question: str, source: str) -> tuple[list[str], bool, float | None]:
    """(req_ids remontés dans l'ordre, abstention ?, meilleure similarité
    sémantique observée — matière à calibration de CE_GATE_SEMANTIC_BYPASS)."""
    from core.ask import _prepare_retrieval
    result = _prepare_retrieval(question, source_filter=source)
    if len(result) == 3:          # hors-scope : abstention
        return [], True, None
    _q, chunks = result
    req_ids, seen = [], set()
    best_sim = None
    for c in chunks or []:
        sim = c.get("sim_est")
        if sim is not None and (best_sim is None or sim > best_sim):
            best_sim = sim
        rid = (c.get("meta") or {}).get("req_id")
        if rid and rid not in seen:
            seen.add(rid)
            req_ids.append(rid)
    return req_ids, not chunks, best_sim


def _purge(source: str) -> None:
    from env_config import MONGO_DB
    from utils.mongo import get_client
    client = get_client()
    client[MONGO_DB]["chunks"].delete_many({"source": source})
    client[MONGO_DB]["bm25_indexes"].delete_many({"source_doc": source})
    from retrieval.vector_store import get_vector_store
    get_vector_store().delete_source(source)
    from core.ask import clear_retrieval_caches
    clear_retrieval_caches()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-index", action="store_true",
                        help="conserve l'index d'éval (inspection) au lieu de le purger")
    args = parser.parse_args()

    from core.lynx_baseline_ingest import ingest_baseline
    from core.reserved_sources import LYNX_BASELINE_EVAL_SOURCE as SOURCE

    corpus = load_reference()
    golden = load_golden()
    print(f"Indexation de la baseline de référence ({len(corpus)} exigences)…")
    t0 = time.time()
    stats = ingest_baseline(corpus, source=SOURCE,
                            progress_callback=lambda m, p: print(f"  [{p:3d}%] {m}"))
    if stats.get("status") != "success":
        print(f"ÉCHEC ingestion : {stats.get('message')}")
        return 1
    t_ingest = time.time() - t0
    print(f"Index prêt en {t_ingest:.1f}s — HyPE {stats['hype']}")

    rows = []
    for case in golden:
        t = time.time()
        req_ids, gate_abstained, best_sim = _retrieve(case["question"], SOURCE)
        abstained, level = gate_abstained, "gate"
        if case.get("expect_abstain") and not gate_abstained:
            # Le périmètre réservé ne coupe plus au retrieval : on vérifie la
            # 2e ligne de défense (la génération doit refuser).
            abstained, extract = _generation_refuses(case["question"], SOURCE)
            level = "generation"
        row = score_case(case, req_ids, abstained)
        row["latency_s"] = round(time.time() - t, 2)
        row["best_sim"] = round(best_sim, 3) if best_sim is not None else None
        if row["kind"] == "abstention":
            row["level"] = level if abstained else None
            if level == "generation" and not abstained:
                row["answer_extract"] = extract
        rows.append(row)
        mark = "✓" if row["ok"] else "✗"
        print(f"  {mark} [{row['kind']}] {case['question'][:60]}"
              + (f" -> {row.get('found')}" if row["kind"] == "retrieval" else
                 f" -> abstention={abstained} ({level})")
              + (f" (sim={row['best_sim']})" if row["best_sim"] is not None else ""))

    summary = aggregate(rows)
    summary["ingest_s"] = round(t_ingest, 1)
    summary["hype"] = stats["hype"]
    out = {"ran_at": time.time(), "summary": summary, "rows": rows}
    (HERE / "last_baseline_eval.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n── Résumé ──")
    print(f"  retrieval : {summary['retrieval_ok']}/{summary['retrieval_total']} cas OK, "
          f"rappel moyen {summary['mean_recall']}, 1er rang moyen {summary['mean_first_rank']}, "
          f"{summary['false_abstentions']} fausse(s) abstention(s)")
    print(f"  abstention: {summary['abstention_ok']}/{summary['abstention_total']} hors-sujet coupés")
    print("  -> evals/last_baseline_eval.json")

    if not args.keep_index:
        _purge(SOURCE)
        print("Index d'éval purgé.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
