#!/usr/bin/env python3
"""Reconstruit les vecteurs Chroma d'un document DEPUIS MongoDB (chunks déjà
enrichis) — sans ré-ingestion, sans perte de métadonnées.

    .venv/bin/python scripts/rebuild_vectors.py                  # toutes les sources
    .venv/bin/python scripts/rebuild_vectors.py <source.md>      # une source

Cas d'usage : vecteurs perdus (ancien bug clean_collection) alors que Mongo
contient toujours les chunks enrichis.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import MongoClient

from env_config import MONGO_URI, MONGO_DB, COLLECTION_NAME
from indexing.embedding import build_embeddings, index_chroma


class _Doc:
    """Adaptateur minimal : build_embeddings n'exige que page_content/metadata.
    Chroma n'accepte que des scalaires en métadonnées : on filtre le record
    Mongo (None écartés, listes jointes, dicts aplatis en str)."""
    def __init__(self, record: dict):
        self.page_content = record.get("content", "")
        meta: dict = {}
        for k, v in record.items():
            if k in ("_id", "content") or v is None:
                continue
            if isinstance(v, (str, int, float, bool)):
                meta[k] = v
            elif isinstance(v, list):
                meta[k] = ", ".join(str(x) for x in v)
            else:
                meta[k] = str(v)
        meta["id"] = record["_id"]
        self.metadata = meta


def rebuild(source: str | None = None) -> None:
    col = MongoClient(MONGO_URI)[MONGO_DB]["chunks"]
    sources = [source] if source else sorted(col.distinct("source"))
    for src in sources:
        records = list(col.find({"source": src}))
        docs = [_Doc(r) for r in records if r.get("content", "").strip()]
        if not docs:
            print(f"{src}: aucun chunk en Mongo — ignoré.")
            continue
        print(f"{src}: {len(docs)} chunks — embeddings…")
        texts, embeddings, metadatas, ids = build_embeddings(docs)
        index_chroma(ids, texts, metadatas, embeddings,
                     collection_name=COLLECTION_NAME,
                     clean_collection=False, replace_source=src)
        print(f"{src}: {len(ids)} vecteurs reconstruits.")


if __name__ == "__main__":
    rebuild(sys.argv[1] if len(sys.argv) > 1 else None)
