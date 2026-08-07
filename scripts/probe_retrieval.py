# scripts/probe_retrieval.py
"""Sonde read-only : vérifie couverture multi-document + non-abstention exploratoire.
Usage : .venv/bin/python -m scripts.probe_retrieval"""
import os
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
from collections import Counter
from core.ask import _get_vector_store, _load_bm25, _should_abstain
from retrieval.retrieve import hybrid_retrieve
from retrieval.intent import classify_intent

QUERIES = [
    "c'est quoi un IDS ?",
    "Que dit le document sur les IDS intelligents ?",
    "Compare les menaces et les exigences entre les deux documents",
    "Quelles sont les exigences de chiffrement ?",
]


def main():
    col = _get_vector_store()
    bm = _load_bm25(None)
    for q in QUERIES:
        chunks, max_ce = hybrid_retrieve(collection=col, query=q, bm25_tuple=bm,
                                         source_filter=None, parent_child_on=False, debug=False)
        srcs = Counter((c.get("meta") or {}).get("source") for c in chunks)
        abstain = _should_abstain(max_ce, q)
        print(f"[{classify_intent(q):11}] abstain={abstain}  max_ce={max_ce}  "
              f"n={len(chunks)}  sources={dict(srcs)}\n   Q={q}")


if __name__ == "__main__":
    main()
