"""Agrégation et persistance MongoDB des campagnes d'évaluation."""
from __future__ import annotations

import time
from typing import Optional

from utils.logging_config import get_logger
from utils.mongo import get_client

logger = get_logger("rag.eval.store")

AGGREGATED_METRICS = (
    "keyword_hit_rate", "exact_match", "f1_token", "context_recall",
    "context_precision", "faithfulness", "answer_relevance",
    "answer_relevancy", "context_relevance", "latency_s",
    "num_chunks_retrieved",
)


def aggregate_metrics(results: list) -> dict:
    """Moyenne des métriques numériques des résultats réussis."""
    successful = [result for result in results if result.get("status") == "ok"]
    if not successful:
        return {key: None for key in AGGREGATED_METRICS}
    aggregate = {}
    for key in AGGREGATED_METRICS:
        values = [result[key] for result in successful if result.get(key) is not None]
        aggregate[key] = round(sum(values) / len(values), 4) if values else None
    return aggregate


def save_eval_run_to_mongo(
    results: list,
    run_name: str,
    db_name: str = "ragdb",
    collection_name: str = "eval_runs",
    dataset: str | None = None,
    mode: str | None = None,
    breakdown: dict | None = None,
) -> str:
    document = {
        "run_name": run_name,
        "dataset": dataset,
        "mode": mode,
        "breakdown": breakdown or {},
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "aggregate": aggregate_metrics(results),
        "details": results,
        "num_questions": len(results),
    }
    inserted = get_client()[db_name][collection_name].insert_one(document)
    return str(inserted.inserted_id)


def load_eval_runs_from_mongo(
    db_name: str = "ragdb", collection_name: str = "eval_runs"
) -> list:
    runs = []
    for run in get_client()[db_name][collection_name].find({}, {"details": 0}):
        run["_id"] = str(run["_id"])
        runs.append(run)
    return sorted(runs, key=lambda item: item.get("timestamp", ""), reverse=True)


def load_eval_run_details(
    run_id: str, db_name: str = "ragdb", collection_name: str = "eval_runs"
) -> Optional[dict]:
    from bson import ObjectId
    from bson.errors import InvalidId

    try:
        object_id = ObjectId(run_id)
    except (InvalidId, TypeError):
        logger.warning("run_id invalide : %r", run_id)
        return None
    result = get_client()[db_name][collection_name].find_one({"_id": object_id})
    if result:
        result["_id"] = str(result["_id"])
    return result
