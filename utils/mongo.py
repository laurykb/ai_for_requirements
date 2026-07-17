"""Client MongoDB partagé pour tout le RAG.

Un **singleton paresseux à timeout court** (1500 ms), réutilisé partout au lieu
d'ouvrir un `MongoClient` par appel. Deux raisons, apprises à la dure et
répétées jusqu'ici dans 4 modules :

- un client par appel **fuit** des sockets/threads au fil des requêtes ;
- sans `serverSelectionTimeoutMS`, chaque opération **gèle ~30 s** quand Mongo
  est éteint ; 1500 ms garantit que l'app répond vite même sans base.

`utils.tracing` garde volontairement son propre client (budget 500 ms, encore
plus serré, sur le chemin d'observabilité) et n'utilise pas ce module.
"""
from __future__ import annotations

from pymongo import MongoClient

from env_config import MONGO_URI, MONGO_DB

_client: MongoClient | None = None


def get_client() -> MongoClient:
    """Client Mongo singleton (timeout de sélection 1500 ms)."""
    global _client
    if _client is None:
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=1500)
    return _client


def get_db():
    """Base par défaut (`MONGO_DB`) via le client singleton."""
    return get_client()[MONGO_DB]
