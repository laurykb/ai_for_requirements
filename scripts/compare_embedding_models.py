#!/usr/bin/env python3
"""Compare deux modèles d'embedding sur les mêmes passages Chroma, sans écriture."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parent.parent
os.sys.path.insert(0, str(ROOT))


def embed_batch(model: str, texts: list[str], base_url: str, batch_size: int) -> np.ndarray:
    rows: list[list[float]] = []
    started = time.time()
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        response = requests.post(
            f"{base_url.rstrip('/')}/api/embed",
            json={"model": model, "input": batch},
            timeout=600,
        )
        response.raise_for_status()
        vectors = response.json()["embeddings"]
        if len(vectors) != len(batch):
            raise RuntimeError(f"{model}: lot incomplet {len(vectors)}/{len(batch)}")
        rows.extend(vectors)
        print(f"{model}: {min(start + batch_size, len(texts))}/{len(texts)}", flush=True)
    matrix = np.asarray(rows, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix /= np.maximum(norms, 1e-12)
    print(f"{model}: dimension={matrix.shape[1]}, durée={time.time() - started:.1f}s")
    return matrix


def keyword_score(text: str, keywords: list[str]) -> float:
    folded = text.casefold()
    return sum(1 for word in keywords if word.casefold() in folded) / max(1, len(keywords))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", default=["bge-m3:latest", "qwen3-embedding:0.6b"])
    parser.add_argument("--source", default="cds_mistral_anssi_thales-clean.md")
    parser.add_argument("--dataset", default="evals/golden_qa_anssi_v2.json")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--ollama", default="http://127.0.0.1:11434")
    parser.add_argument("--qwen-instruction", default="Given a French cybersecurity question, retrieve relevant passages from security specifications and requirements")
    parser.add_argument("--output", default="evals/embedding_model_comparison.json")
    args = parser.parse_args()

    from retrieval.vector_store import get_vector_store
    coll = get_vector_store()._coll()
    result = coll.get(where={"source": args.source}, include=["documents"])
    documents = result.get("documents") or []
    if not documents:
        raise SystemExit(f"Source absente de Chroma: {args.source}")
    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))["items"]
    questions = [item["question"] for item in dataset]
    payload = {"source": args.source, "documents": len(documents), "questions": len(questions), "models": {}}

    for model in args.models:
        doc_matrix = embed_batch(model, documents, args.ollama, args.batch_size)
        query_texts = ([f"Instruct: {args.qwen_instruction}\nQuery: {q}" for q in questions]
                       if model.startswith("qwen3-embedding") else questions)
        query_matrix = embed_batch(model, query_texts, args.ollama, args.batch_size)
        rows = []
        for item, query in zip(dataset, query_matrix):
            order = np.argsort(doc_matrix @ query)[::-1][:args.top_k]
            context = "\n".join(documents[int(index)] for index in order)
            rows.append({
                "question": item["question"],
                "keyword_hit_rate": keyword_score(context, item.get("expected_keywords") or []),
                "top_indices": [int(index) for index in order],
            })
        payload["models"][model] = {
            "dimension": int(doc_matrix.shape[1]),
            "keyword_hit_rate": round(sum(row["keyword_hit_rate"] for row in rows) / len(rows), 4),
            "rows": rows,
        }

    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({name: {"dimension": data["dimension"], "keyword_hit_rate": data["keyword_hit_rate"]}
                      for name, data in payload["models"].items()}, ensure_ascii=False, indent=2))
    print(f"Rapport: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
