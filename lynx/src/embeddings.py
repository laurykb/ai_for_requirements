"""Embeddings locaux (endpoint compatible OpenAI : Ollama bge-m3, vLLM…).

Sert de pré-filtre déterministe et reproductible pour la redondance / les
doublons : la similarité cosinus repère les quasi-doublons instantanément, le
LLM n'arbitrant que les cas ambigus. Sans effet si l'endpoint est indisponible.
"""

from __future__ import annotations

import hashlib
import time
from typing import Dict, List, Optional

import httpx
import numpy as np

from .config import EMBED_BASE_URL, EMBED_DISABLED, EMBED_DUP_THRESHOLD, EMBED_MODEL, LLM_API_KEY, LLM_TIMEOUT_SECONDS

_cache: Dict[str, List[float]] = {}
_available: Dict[str, object] = {}
_AVAILABLE_TTL = 10  # s


def _headers() -> dict:
    return {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}


def embeddings_available() -> bool:
    if EMBED_DISABLED:
        return False
    # Cache avec TTL : sans lui, un endpoint injoignable au premier appel figeait
    # le pré-filtre embeddings (dédup, impact latent) jusqu'au redémarrage du
    # process — même une fois Ollama revenu. Aligné sur llm.llm_available().
    now = time.monotonic()
    if "ok" not in _available or now - float(_available.get("ts", 0)) > _AVAILABLE_TTL:
        try:
            _available["ok"] = bool(get_embedding("test"))
        except Exception:
            _available["ok"] = False
        _available["ts"] = now
    return bool(_available["ok"])


def _key(text: str) -> str:
    return hashlib.sha256((EMBED_MODEL + "::" + text).encode("utf-8")).hexdigest()


def get_embedding(text: str) -> Optional[List[float]]:
    if EMBED_DISABLED or not text:
        return None
    res = get_embeddings([text])
    return res[0] if res else None


def get_embeddings(texts: List[str]) -> Optional[List[List[float]]]:
    """Embeddings de plusieurs textes en un seul appel. Cache 3 niveaux :
    L1 mémoire -> L2 SQLite (persistant) -> API. Écrit les nouveaux vecteurs dans les
    deux caches. Les vecteurs relus du L2 sont bit-identiques à ceux de l'API."""
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

    # L2 : cache disque persistant (survit au process).
    if missing_txt:
        from . import embed_store
        disk = embed_store.get_many([_key(t) for t in missing_txt])
        if disk:
            kept_idx, kept_txt = [], []
            for pos, t in enumerate(missing_txt):
                k = _key(t)
                if k in disk:
                    out[missing_idx[pos]] = disk[k]
                    _cache[k] = disk[k]
                else:
                    kept_idx.append(missing_idx[pos])
                    kept_txt.append(t)
            missing_idx, missing_txt = kept_idx, kept_txt

    if missing_txt:
        try:
            r = httpx.post(f"{EMBED_BASE_URL}/embeddings", headers=_headers(),
                           json={"model": EMBED_MODEL, "input": missing_txt}, timeout=LLM_TIMEOUT_SECONDS)
            r.raise_for_status()
            data = r.json()["data"]
            if len(data) != len(missing_txt):
                return None  # réponse incomplète : on ne devine pas l'alignement
            new_vecs: dict = {}
            for pos, item in enumerate(data):
                # L'API compatible OpenAI peut réordonner : on se fie au champ `index`.
                k = item.get("index", pos)
                vec = item["embedding"]
                out[missing_idx[k]] = vec
                key = _key(missing_txt[k])
                _cache[key] = vec
                new_vecs[key] = vec
            from . import embed_store
            embed_store.put_many(new_vecs)  # persiste les nouveaux (survit au process)
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


def _stack(vectors: List[Optional[List[float]]]):
    """Empile des vecteurs en matrice numpy L2-normalisée (une ligne par vecteur).

    Renvoie ``(matrice float64 (n, dim), mask booléen des lignes valides)``. Un
    vecteur None / vide / de mauvaise dimension devient une ligne nulle marquée
    invalide (sa similarité vaudra 0, jamais un faux positif).

    Précondition : les vecteurs valides partagent une même dimension (garantie ici
    par ``get_embeddings``, qui renvoie soit un vecteur complet du modèle unique, soit
    ``None`` — jamais un vecteur plus court). Un vecteur d'une autre dimension est
    traité comme invalide.
    """
    n = len(vectors)
    dim = next((len(v) for v in vectors if v), 0)
    m = np.zeros((n, dim or 1), dtype=np.float64)
    valid = np.zeros(n, dtype=bool)
    for i, v in enumerate(vectors):
        if v and dim and len(v) == dim:
            m[i] = v
            valid[i] = True
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms, valid


def duplicate_pairs(vectors: List[Optional[List[float]]], threshold: float):
    """Paires ``(i, j, score)`` (i < j) de cosinus >= ``threshold``.

    Remplace la double boucle O(N²) Python par une seule matmul numpy. Les
    vecteurs invalides sont exclus (jamais signalés comme doublons).
    """
    n = len(vectors)
    if n < 2:
        return []
    m, valid = _stack(vectors)
    sims = m @ m.T
    iu = np.triu_indices(n, k=1)
    vi, vj = iu
    keep = valid[vi] & valid[vj] & (sims[vi, vj] >= threshold)
    return [(int(vi[k]), int(vj[k]), float(sims[vi[k], vj[k]]))
            for k in np.nonzero(keep)[0]]


def similarities_to(target_vec: Optional[List[float]],
                    cand_vecs: List[Optional[List[float]]]) -> List[float]:
    """Cosinus de ``target_vec`` contre chaque candidat (0.0 si l'un est invalide)."""
    if not cand_vecs:
        return []
    m, valid = _stack([target_vec] + list(cand_vecs))
    if not valid[0]:
        return [0.0] * len(cand_vecs)
    sims = m[1:] @ m[0]
    sims = np.where(valid[1:], sims, 0.0)
    return [float(s) for s in sims]


def most_similar(target_text: str, candidates: List[tuple]) -> Optional[tuple]:
    """Renvoie (id, score) du candidat le plus proche, ou None si indispo.

    ``candidates`` = liste de (id, texte). Embeddings groupés en un seul appel.
    """
    if not candidates:
        return None
    vecs = get_embeddings([target_text] + [c[1] for c in candidates])
    if not vecs or vecs[0] is None:
        return None
    m, valid = _stack(vecs)
    if not valid[0]:
        return None
    sims = m[1:] @ m[0]
    sims = np.where(valid[1:], sims, -np.inf)
    j = int(np.argmax(sims))
    # Parité avec l'ancienne boucle (best_score initialisé à -1.0, mise à jour sur >
    # stricte) : aucun candidat valide, ou meilleur cosinus <= -1.0 -> None.
    if sims[j] <= -1.0:
        return None
    return (candidates[j][0], float(sims[j]))


def is_duplicate(target_text: str, candidates: List[tuple], threshold: float = EMBED_DUP_THRESHOLD):
    """(est_doublon, id_le_plus_proche, score) via similarité cosinus."""
    top = most_similar(target_text, candidates)
    if top is None:
        return (False, None, 0.0)
    cid, score = top
    return (score >= threshold, cid, score)
