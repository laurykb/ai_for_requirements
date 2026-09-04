#!/usr/bin/env python3
"""Stress du chat baseline sur le corpus XL (~500 exigences verbeuses).

Mesure, sous la source réservée de stress (la baseline vivante n'est jamais
touchée) :
  1. sérialisation Markdown + empreinte (coût du poll de statut, toutes les 4 s)
  2. ingestion complète (la politique HyPE élastique doit se couper seule
     au-delà de LYNX_CHAT_HYPE_MAX_REQS)
  3. latence de retrieval scoped sur des questions représentatives
puis purge l'index de stress.

Usage :  .venv/bin/python scripts/stress_baseline_chat.py [--corpus lynx/corpus/corpus_xl.json]
Nécessite Ollama (embeddings) et Mongo.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(1, str(ROOT / "lynx"))

QUESTIONS = (
    "Quelles sont les exigences de masse du système ?",
    "Quelles exigences portent sur la phase de décollage ?",
    "Y a-t-il des besoins sur la consommation d'énergie ?",
    "Liste les exigences de niveau L0.",
    "Quelles exigences dérivent de l'analyse opérationnelle ?",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=str(ROOT / "lynx" / "corpus" / "corpus_xl.json"))
    args = parser.parse_args()

    from src import corpus_io  # noqa: E402 (lynx/)
    raw = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    corpus, errors = corpus_io.validate_corpus(corpus_io._unwrap(raw))
    print(f"Corpus : {len(corpus)} exigences valides ({len(errors)} rejet(s))")

    from api.lynx_chat import _fingerprint, _render_markdown
    t = time.perf_counter()
    md = _render_markdown(corpus)
    t_render = time.perf_counter() - t
    t = time.perf_counter()
    _fingerprint(corpus)
    t_fp = time.perf_counter() - t
    print(f"Sérialisation Markdown : {t_render*1000:.0f} ms ({len(md)//1024} Ko) ; "
          f"empreinte : {t_fp*1000:.0f} ms (payée à chaque poll de 4 s)")

    from core.lynx_baseline_ingest import ingest_baseline
    from core.reserved_sources import LYNX_BASELINE_STRESS_SOURCE as SOURCE
    t = time.perf_counter()
    stats = ingest_baseline(corpus, source=SOURCE,
                            progress_callback=lambda m, p: print(f"  [{p:3d}%] {m}"))
    t_ingest = time.perf_counter() - t
    if stats.get("status") != "success":
        print(f"ÉCHEC ingestion : {stats.get('message')}")
        return 1
    print(f"Ingestion : {t_ingest:.1f} s — {stats['num_chunks']} chunks, "
          f"HyPE {stats['hype']} (attendu : coupé au-delà du seuil élastique)")

    from core.ask import _prepare_retrieval, clear_retrieval_caches
    latencies = []
    for q in QUESTIONS:
        t = time.perf_counter()
        result = _prepare_retrieval(q, source_filter=SOURCE)
        dt = time.perf_counter() - t
        latencies.append(dt)
        n = len(result[1]) if len(result) == 2 else 0
        print(f"  {dt*1000:6.0f} ms  {n:2d} passages  {q}")
    print(f"Retrieval scoped : médiane {statistics.median(latencies)*1000:.0f} ms, "
          f"max {max(latencies)*1000:.0f} ms")

    # Purge de l'index de stress.
    from env_config import MONGO_DB
    from utils.mongo import get_client
    client = get_client()
    client[MONGO_DB]["chunks"].delete_many({"source": SOURCE})
    client[MONGO_DB]["bm25_indexes"].delete_many({"source_doc": SOURCE})
    from retrieval.vector_store import get_vector_store
    get_vector_store().delete_source(SOURCE)
    clear_retrieval_caches()
    print("Index de stress purgé.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
