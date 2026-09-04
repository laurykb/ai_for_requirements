#!/usr/bin/env python3
"""Bloque l'export si le dernier benchmark RAG régresse fortement."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HIGHER_IS_BETTER = {"keyword_hit_rate": .03, "context_recall": .03,
                    "context_precision": .03, "faithfulness": .03,
                    "answer_relevancy": .03, "structured_axis_coverage": .05}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate(baseline: dict, candidate: dict) -> list[str]:
    failures = []
    if int(candidate.get("num_questions") or 0) < int(baseline.get("num_questions") or 0):
        failures.append("benchmark candidat plus petit que la baseline")
    old, new = baseline.get("aggregate") or {}, candidate.get("aggregate") or {}
    for metric, tolerance in HIGHER_IS_BETTER.items():
        if old.get(metric) is not None and new.get(metric) is None:
            failures.append(f"métrique absente : {metric}")
        elif old.get(metric) is not None and float(new[metric]) < float(old[metric]) - tolerance:
            failures.append(f"{metric}: {new[metric]} < {old[metric]} - {tolerance}")
    if old.get("latency_s") and new.get("latency_s") and float(new["latency_s"]) > float(old["latency_s"]) * 1.25:
        failures.append("latence supérieure de plus de 25 % à la baseline")
    return failures


def fingerprint(candidate: Path) -> str:
    digest = hashlib.sha256()
    for path in (candidate, ROOT / "env_config.py", ROOT / "core" / "llm_answer.py",
                 ROOT / "retrieval" / "retrieve.py"):
        digest.update(path.name.encode()); digest.update(path.read_bytes())
    return digest.hexdigest()[:20]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, default=ROOT / "evals" / "rag_quality_baseline.json")
    parser.add_argument("--candidate", type=Path, default=ROOT / "evals" / "last_eval.json")
    args = parser.parse_args()
    failures = evaluate(load(args.baseline), load(args.candidate))
    print(json.dumps({"ok": not failures, "fingerprint": fingerprint(args.candidate),
                      "failures": failures}, ensure_ascii=False, indent=2))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
