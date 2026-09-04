"""Cache d'embeddings persistant (SQLite), niveau L2 derrière le cache mémoire.

But : survivre au process. Au redémarrage, on relit les vecteurs du disque au lieu
de tout ré-embedder. Transparent pour les appelants (via ``embeddings.get_embeddings``).

Les vecteurs sont stockés en float64 (bit-identiques aux floats Python renvoyés par
l'API), donc le cache ne change aucune similarité : parité stricte préservée. Un cache
qui échoue ne casse jamais un appel — LynX reste fonctionnel sans lui.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np

from .config import EMBED_CACHE_DB, EMBED_CACHE_DISK, EMBED_CACHE_DISK_MAX, EMBED_MODEL

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _connect() -> Optional[sqlite3.Connection]:
    """Connexion singleton (thread-safe via ``check_same_thread=False`` + ``_lock``)."""
    global _conn
    if not EMBED_CACHE_DISK:
        return None
    if _conn is None:
        EMBED_CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(EMBED_CACHE_DB), check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute(
            "CREATE TABLE IF NOT EXISTS embeddings ("
            "key TEXT PRIMARY KEY, model TEXT, dim INTEGER, vec BLOB, created_at TEXT)")
        _conn.commit()
    return _conn


def get_many(keys: List[str]) -> Dict[str, List[float]]:
    """Vecteurs présents en cache disque, par clé. Les clés absentes ne sont pas
    renvoyées. Jamais d'exception propagée : en cas d'erreur SQLite, renvoie ce qui a
    pu être lu (au pire ``{}``)."""
    if not keys:
        return {}
    with _lock:
        try:
            conn = _connect()
            if conn is None:
                return {}
            out: Dict[str, List[float]] = {}
            CHUNK = 900  # SQLite limite le nombre de paramètres (~999)
            for start in range(0, len(keys), CHUNK):
                batch = keys[start:start + CHUNK]
                ph = ",".join("?" for _ in batch)
                for key, vec in conn.execute(
                        f"SELECT key, vec FROM embeddings WHERE key IN ({ph})", batch):
                    out[key] = np.frombuffer(vec, dtype=np.float64).tolist()
            return out
        except Exception:
            return {}


def put_many(items: Dict[str, List[float]]) -> None:
    """Upsert des vecteurs (float64 -> bytes). No-op si cache désactivé ou en erreur."""
    if not items:
        return
    with _lock:
        try:
            conn = _connect()
            if conn is None:
                return
            now = datetime.now().isoformat(timespec="seconds")
            rows = []
            for key, vec in items.items():
                arr = np.asarray(vec, dtype=np.float64)
                rows.append((key, EMBED_MODEL, int(arr.size), arr.tobytes(), now))
            conn.executemany(
                "INSERT OR REPLACE INTO embeddings (key, model, dim, vec, created_at) "
                "VALUES (?, ?, ?, ?, ?)", rows)
            conn.execute("DELETE FROM embeddings WHERE key IN (SELECT key FROM embeddings ORDER BY created_at ASC LIMIT MAX(0, (SELECT COUNT(*) FROM embeddings) - ?))", (max(1, EMBED_CACHE_DISK_MAX),))
            conn.commit()
        except Exception:
            pass  # un cache qui échoue ne doit jamais casser l'appel


def reset() -> None:
    """Ferme la connexion (tests / changement de chemin de la base)."""
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None
