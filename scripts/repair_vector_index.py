#!/usr/bin/env python3
"""Reconstruit uniquement la jambe Chroma depuis les chunks Mongo existants."""
from __future__ import annotations

import argparse
import hashlib
import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.document import Document
from core.index_consistency import rag_index_consistency
from indexing.embedding import build_embeddings
from retrieval.vector_store import get_vector_store
from utils.mongo import get_db


def _documents_for_source(source: str) -> list[Document]:
    rows = list(get_db()["chunks"].find(
        {"source": source, "quality_status": {"$ne": "quarantined"}}
    ).sort("chunk_idx", 1))
    docs = []
    for row in rows:
        content = str(row.pop("content", "") or "").strip()
        item_id = str(row.pop("_id"))
        if not content:
            continue
        metadata = dict(row)
        metadata["id"] = item_id
        metadata["source"] = source
        docs.append(Document(page_content=content, metadata=metadata))
    return docs


def repair_source(source: str) -> int:
    docs = _documents_for_source(source)
    if not docs:
        raise RuntimeError(f"{source}: aucun chunk Mongo indexable.")
    texts, vectors, metadatas, original_ids = build_embeddings(
        docs, hype_enabled=False
    )
    if len(vectors) != len(docs):
        raise RuntimeError(
            f"{source}: embeddings incomplets ({len(vectors)}/{len(docs)})."
        )
    token = hashlib.sha256(
        f"{source}:{time.time_ns()}".encode("utf-8")
    ).hexdigest()[:12]
    vector_ids = [f"repair::{token}::{item_id}" for item_id in original_ids]
    get_vector_store().replace_source(
        source, vector_ids, texts, metadatas, vectors
    )
    return len(vectors)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Répare Chroma depuis Mongo sans modifier Mongo ni BM25."
    )
    parser.add_argument("--apply", action="store_true",
                        help="Effectue les écritures (sinon audit à blanc).")
    parser.add_argument("--source", action="append", default=[],
                        help="Limite la réparation à une source (répétable).")
    parser.add_argument("--purge-orphans", action="store_true",
                        help="Supprime les vecteurs dont la source est absente de Mongo.")
    args = parser.parse_args()

    before = rag_index_consistency()
    if not before.get("available"):
        raise SystemExit(before.get("error") or "Audit des index indisponible.")
    requested = set(args.source)
    targets = [row for row in before["sources"]
               if not row["in_sync"] and (not requested or row["name"] in requested)]
    print(f"Sources à réparer : {len(targets)}")
    for row in targets:
        print(f"- {row['name']} : {row['vectors']}/{row['indexable']} vecteurs")
    if before["orphans"]:
        print("Sources vectorielles orphelines :")
        for row in before["orphans"]:
            print(f"- {row['name']} : {row['vectors']} vecteurs")
    if not args.apply:
        print("Audit à blanc : relancer avec --apply pour réparer.")
        return 1 if targets or before["orphans"] else 0

    store = get_vector_store()
    for row in targets:
        count = repair_source(row["name"])
        print(f"Réparé : {row['name']} ({count} vecteurs)")
    if args.purge_orphans:
        for row in before["orphans"]:
            store.delete_source(row["name"])
            print(f"Orphelin supprimé : {row['name']} ({row['vectors']} vecteurs)")

    from core.ask import clear_retrieval_caches
    clear_retrieval_caches()
    after = rag_index_consistency()
    print(f"Cohérence finale : {'OK' if after.get('in_sync') else 'ÉCHEC'}")
    return 0 if after.get("in_sync") else 2


if __name__ == "__main__":
    raise SystemExit(main())
