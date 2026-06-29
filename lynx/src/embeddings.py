"""Embeddings locaux (endpoint compatible OpenAI : Ollama bge-m3, vLLM…).

Sert de pré-filtre DÉTERMINISTE et reproductible pour la redondance / les
doublons : la similarité cosinus repère les quasi-doublons instantanément, le
LLM n'arbitrant que les cas ambigus. Dégrade proprement si indisponible.
"""

from __future__ import annotations

import hashlib
from typing import Dict, List, Optional

import httpx

from .config import EMBED_BASE_URL, EMBED_DISABLED, EMBED_DUP_THRESHOLD, EMBED_MODEL, LLM_API_KEY, LLM_TIMEOUT_SECONDS

_cache: Dict[str, List[float]] = {}
_available: Dict[str, bool] = {}


def _headers() -> dict:
    return {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}


def embeddings_available() -> bool:
    if EMBED_DISABLED:
        return False
    if "ok" not in _available:
        try:
            v = get_embedding("test")
            _available["ok"] = bool(v)
        except Exception:
            _available["ok"] = False
    return _available["ok"]


def _key(text: str) -> str:
    return hashlib.sha256((EMBED_MODEL + "::" + text).encode("utf-8")).hexdigest()


def get_embedding(text: str) -> Optional[List[float]]:
    if EMBED_DISABLED or not text:
        return None
    res = get_embeddings([text])
    return res[0] if res else None


def get_embeddings(texts: List[str]) -> Optional[List[List[float]]]:
    """Embeddings de PLUSIEURS textes en UN seul appel (latence ↓). Utilise le cache."""
    if EMBED_DISABLED or not texts:
        return None
    out: List[Optional[List[float]]] = [None] * len(texts)
    missing_idx, missing_txt = [], []
    for i, t in enumerate(texts):
        k = _key(t)
        if k in _cache:
            out[i] = _cache[k]
        else:
            missing_idx.append(i)
            missing_txt.append(t)
    if missing_txt:
        try:
            r = httpx.post(f"{EMBED_BASE_URL}/embeddings", headers=_headers(),
                           json={"model": EMBED_MODEL, "input": missing_txt}, timeout=LLM_TIMEOUT_SECONDS)
            r.raise_for_status()
            data = r.json()["data"]
            if len(data) != len(missing_txt):
                return None  # réponse incomplète : on ne devine pas l'alignement
            for pos, item in enumerate(data):
                # L'API compatible OpenAI peut réordonner : on se fie au champ `index`.
                k = item.get("index", pos)
                vec = item["embedding"]
                out[missing_idx[k]] = vec
                _cache[_key(missing_txt[k])] = vec
        except Exception:
            return None
    return out  # aligné sur ``texts`` (peut contenir None si un item a échoué)


def cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def most_similar(target_text: str, candidates: List[tuple]) -> Optional[tuple]:
    """Renvoie (id, score) du candidat le plus proche, ou None si indispo.

    ``candidates`` = liste de (id, texte). Embeddings groupés en un seul appel.
    """
    if not candidates:
        return None
    vecs = get_embeddings([target_text] + [c[1] for c in candidates])
    if not vecs or vecs[0] is None:
        return None
    tv = vecs[0]
    best_id, best_score = None, -1.0
    for i, (cid, _) in enumerate(candidates):
        cv = vecs[i + 1]
        if cv is None:
            continue
        s = cosine(tv, cv)
        if s > best_score:
            best_id, best_score = cid, s
    return (best_id, best_score) if best_id is not None else None


def is_duplicate(target_text: str, candidates: List[tuple], threshold: float = EMBED_DUP_THRESHOLD):
    """(est_doublon, id_le_plus_proche, score) via similarité cosinus."""
    top = most_similar(target_text, candidates)
    if top is None:
        return (False, None, 0.0)
    cid, score = top
    return (score >= threshold, cid, score)
