# scripts/probe_corpus_synthesis.py
"""Sonde live : synthèse corpus map-reduce. .venv/bin/python -m scripts.probe_corpus_synthesis"""
import os
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
from core.synthesize_corpus import synthesize_corpus


def main():
    q = "Catégorise toutes les attaques et menaces connues de ce corpus."
    answer = ""
    for ev in synthesize_corpus(q, aspect="attaques et menaces"):
        t = ev["type"]
        if t == "stage":
            print(f"[stage] {ev['stage']}" + (f" docs={ev.get('documents')}" if ev.get("documents") else ""))
        elif t == "map":
            print(f"[map] {ev['index']}/{ev['total']} {ev['document']} -> {ev['n_items']} items")
        elif t == "token":
            answer += ev["text"]
        elif t == "done":
            print(f"[done] documents={ev['result']['documents']} n_items={ev['result']['n_items']}")
    print("\n=== SYNTHÈSE ===\n" + answer)


if __name__ == "__main__":
    main()
