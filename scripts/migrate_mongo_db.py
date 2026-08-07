#!/usr/bin/env python3
"""Migration des données Mongo vers une base dédiée à CETTE installation.

Pourquoi : plusieurs installations d'AI for SSH sur la même machine partagent
le même serveur Mongo (:27017) et, par défaut, la même base `ragdb` — alors
que chaque installation a SON magasin vectoriel local (data/chroma_db). Deux
applications lancées côte à côte se polluent alors mutuellement (documents,
sessions, index BM25) avec des vecteurs incohérents.

Remède : donner à chaque installation sa base, par exemple :

    1. .venv/bin/python scripts/migrate_mongo_db.py ragdb_export
    2. ajouter  MONGO_DB=ragdb_export  au .env
    3. redémarrer (python serve.py)

Copie (upsert idempotent) toutes les collections applicatives ; la base
source n'est PAS modifiée (--drop-source pour la purger ensuite, une fois
la nouvelle base vérifiée). --dry-run pour un aperçu sans écriture.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Collections applicatives (voir utils/mongo.py et les modules propriétaires).
COLLECTIONS = (
    "chunks",           # passages indexés (RAG + baseline LynX)
    "bm25_indexes",     # index BM25 par document
    "chat_sessions",    # conversations persistées (RAG + chat baseline)
    "queries",          # historique de requêtes
    "traces",           # boîte de verre (observabilité)
    "task_metrics",     # métriques déterministes de tâches
    "entity_graph",     # graphe d'entités
    "lynx_chat_meta",   # fraîcheur de l'index baseline (empreinte)
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target_db", help="nom de la base cible (ex. ragdb_export)")
    parser.add_argument("--source-db", default=None,
                        help="base source (défaut : MONGO_DB courant, ragdb)")
    parser.add_argument("--dry-run", action="store_true", help="aperçu sans écriture")
    parser.add_argument("--drop-source", action="store_true",
                        help="PURGE les collections copiées de la base source après copie")
    args = parser.parse_args()

    from env_config import MONGO_DB
    from utils.mongo import get_client
    source_name = args.source_db or MONGO_DB
    if source_name == args.target_db:
        print(f"Source et cible identiques ({source_name}) : rien à faire.")
        return 1

    client = get_client()
    src, dst = client[source_name], client[args.target_db]
    print(f"{source_name} -> {args.target_db}" + (" (dry-run)" if args.dry_run else ""))

    total = 0
    for name in COLLECTIONS:
        n = src[name].count_documents({})
        total += n
        print(f"  {name:<15} {n:>6} document(s)")
        if args.dry_run or n == 0:
            continue
        for doc in src[name].find():
            dst[name].replace_one({"_id": doc["_id"]}, doc, upsert=True)

    if args.dry_run:
        print(f"Aperçu : {total} document(s) seraient copiés.")
        return 0

    print(f"Copié : {total} document(s).")
    if args.drop_source:
        for name in COLLECTIONS:
            src[name].drop()
        print(f"Collections purgées de {source_name}.")
    print(f"\nÉtape suivante : ajouter  MONGO_DB={args.target_db}  au .env puis redémarrer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
