"""Observabilité & paramètres (mode expert) : perfs d'inférence, traces,
modèles Ollama (liste + changement à chaud), lecture/écriture du .env."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pymongo import MongoClient

from env_config import MONGO_URI, MONGO_DB

router = APIRouter()


@router.get("/api/perf")
def perf_stats() -> dict:
    """Performance d'inférence de CE process API : tokens/s, time-to-first-token,
    latence (stats exactes Ollama) + VRAM live."""
    from utils import perf
    rows = perf.snapshot()
    return {"gpu": [{"used": int(u), "total": int(t)} for u, t in (perf.gpu_memory() or [])],
            "aggregate": perf.aggregate(rows) if rows else None,
            "rows": [{"model": r["model"], "gen_tokens": r["gen_tokens"],
                      "tok_per_s": round(r["tok_per_s"], 1) if r["tok_per_s"] else None,
                      "ttft_s": round(r["ttft_s"], 2) if r["ttft_s"] else None,
                      "total_s": round(r["total_s"], 1)} for r in rows[:30]]}


@router.get("/api/traces")
def traces(limit: int = 20) -> dict:
    """Dernières traces de requêtes (spans chronométrés, persistés en Mongo)."""
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=1500)
        rows = list(client[MONGO_DB]["traces"].find().sort("_id", -1).limit(max(1, min(limit, 100))))
    except Exception:
        return {"available": False, "traces": []}

    def strip(s: dict) -> dict:
        return {"name": s.get("name"), "duration_ms": s.get("duration_ms"),
                "metadata": {k: v for k, v in (s.get("metadata") or {}).items()},
                "children": [strip(c) for c in s.get("children", [])]}

    return {"available": True,
            "traces": [{"timestamp": str(t.get("timestamp", "")),
                        "duration_ms": t.get("duration_ms"),
                        "query": (t.get("metadata") or {}).get("query", ""),
                        "hors_scope": bool((t.get("metadata") or {}).get("hors_scope")),
                        "spans": [strip(c) for c in t.get("children", [])]}
                       for t in rows]}


@router.get("/api/models")
def models() -> dict:
    """Modèles Ollama disponibles + routage par rôle + état du magasin vectoriel."""
    import requests
    try:
        from env_config import OLLAMA_HOST
        tags = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5).json()
        names = [m["name"] for m in tags.get("models", [])]
    except Exception:
        names = []
    routing = {}
    try:
        from core.model_router import routing_table
        routing = routing_table()
    except Exception:
        pass
    vectors = None
    try:
        from retrieval.vector_store import get_vector_store
        vectors = get_vector_store().count()
    except Exception:
        pass
    return {"models": names, "routing": routing, "vectors": vectors}


class ModelAction(BaseModel):
    model: str
    action: str  # "load" (épingle en VRAM + active à chaud) | "unload"


@router.post("/api/models/generate")
def set_generation_model(body: ModelAction) -> dict:
    """Changement à chaud du modèle de génération + gestion VRAM (comme les
    boutons Charger/Décharger du Streamlit)."""
    import requests
    from env_config import OLLAMA_HOST
    try:
        if body.action == "load":
            from core.model_router import set_generate_model
            set_generate_model(body.model)
        keep = -1 if body.action == "load" else 0
        requests.post(f"{OLLAMA_HOST}/api/generate",
                      json={"model": body.model, "keep_alive": keep}, timeout=30)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(502, f"Ollama : {e}")


# Clés .env modifiables depuis la vue Paramètres — source unique, partagée par la
# lecture (GET) et l'écriture (liste blanche du POST) : ajouter une clé ici la rend
# lisible ET modifiable, sans risque de dérive entre les deux endpoints.
_SETTABLE_KEYS = ("NUM_CHUNKS", "EMBED_MODEL", "GEN_MODEL", "WEIGHT_SEMANTIC",
                  "WEIGHT_BM25", "CE_RELEVANCE_THRESHOLD", "AUTO_KEYWORDS",
                  "AUTO_QUESTIONS", "CHUNKING_MODE", "RAPTOR_SUMMARIES",
                  "SELF_RAG_ENABLED", "SELF_RAG_THRESHOLD", "SELF_RAG_MAX_RETRIES")


@router.get("/api/settings")
def get_settings() -> dict:
    """Valeurs .env actuelles (celles que la vue Paramètres peut modifier)
    + le system prompt par défaut."""
    import env_config as cfg
    from core.llm_answer import DEFAULT_SYSTEM_PROMPT
    return {"values": {k: getattr(cfg, k) for k in _SETTABLE_KEYS},
            "default_system_prompt": DEFAULT_SYSTEM_PROMPT}


class EnvUpdates(BaseModel):
    updates: dict[str, str]

_ENV_ALLOWED = set(_SETTABLE_KEYS)


@router.post("/api/settings")
def save_settings(body: EnvUpdates) -> dict:
    """Écrit les réglages dans .env (liste blanche). Prend effet au prochain
    démarrage de l'API (comme dans le Streamlit)."""
    updates = {k: v for k, v in body.updates.items() if k in _ENV_ALLOWED}
    if not updates:
        raise HTTPException(400, "Aucune clé autorisée dans la demande.")
    from pathlib import Path
    env_path = Path(__file__).resolve().parent.parent / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    remaining = dict(updates)
    out = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            key = s.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)
    out.extend(f"{k}={v}" for k, v in remaining.items())
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return {"ok": True, "saved": sorted(updates)}
