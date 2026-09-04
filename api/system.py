"""Observabilité & paramètres (mode expert) : perfs d'inférence, traces,
modèles Ollama (liste + changement à chaud), lecture/écriture du .env."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Literal
from env_config import MONGO_DB

from utils.mongo import get_client

router = APIRouter()


@router.get("/api/task-metrics")
def task_metrics(limit: int = 20, kind: str | None = None) -> dict:
    """Accomplissement, trajectoire et efficacité des dernières tâches Chat/SRA."""
    from utils.task_metrics import recent
    return {"tasks": recent(limit=limit, kind=kind)}


@router.get("/api/perf")
def perf_stats() -> dict:
    """Performance d'inférence de CE process API : tokens/s, time-to-first-token,
    latence (stats exactes Ollama) + VRAM live."""
    from utils import perf
    rows = perf.snapshot()
    return {"gpu": [{"used": int(u), "total": int(t)} for u, t in (perf.gpu_memory() or [])],
            "hardware_profile": perf.hardware_profile(),
            "aggregate": perf.aggregate(rows) if rows else None,
            "rows": [{"model": r["model"], "role": r.get("role"), "task_id": r.get("task_id"), "prompt_tokens": r.get("prompt_tokens", 0), "gen_tokens": r["gen_tokens"], "prompt_eval_s": round(r.get("prompt_eval_s", 0), 2), "load_s": round(r.get("load_s", 0), 2), "decode_s": round(r.get("gen_s", 0), 2),
                      "tok_per_s": round(r["tok_per_s"], 1) if r["tok_per_s"] else None,
                      "ttft_s": round(r["ttft_s"], 2) if r["ttft_s"] else None,
                      "total_s": round(r["total_s"], 1)} for r in rows[:30]]}


@router.get("/api/traces")
def traces(limit: int = 20) -> dict:
    """Dernières traces de requêtes (spans chronométrés, persistés en Mongo)."""
    try:
        client = get_client()
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
    from env_config import EMBED_MODEL
    embedding_models = [name for name in names if name == EMBED_MODEL or any(
        marker in name.lower() for marker in ("embed", "bge-", "e5-")
    )]
    generation_models = [name for name in names if name not in embedding_models]
    from utils.ollama_scheduler import snapshot as scheduler_snapshot
    return {"models": names, "generation_models": generation_models,
            "embedding_models": embedding_models, "routing": routing, "vectors": vectors,
            "scheduler": scheduler_snapshot()}


class ModelAction(BaseModel):
    model: str = Field(min_length=1, max_length=200)
    action: Literal["load", "unload"]


class RoutingUpdate(BaseModel):
    model_config = {"extra": "forbid"}
    routing: dict[str, str] = Field(min_length=1, max_length=8)


class ArsenalAction(RoutingUpdate):
    action: Literal["load", "unload"]
    embedding_model: str = Field(min_length=1, max_length=200)


@router.post("/api/models/routing")
def apply_model_routing(body: RoutingUpdate) -> dict:
    """Applique immédiatement une configuration de modèles par rôle."""
    allowed = {"rewrite", "agent", "planner", "extract", "synthesize", "generate", "judge", "enhance"}
    unknown = sorted(set(body.routing) - allowed)
    if unknown:
        raise HTTPException(422, "Rôles inconnus : " + ", ".join(unknown))
    cleaned = {role: model.strip() for role, model in body.routing.items()}
    if any(not model or len(model) > 200 for model in cleaned.values()):
        raise HTTPException(422, "Chaque rôle doit désigner un modèle Ollama valide.")
    from core.model_router import routing_table, set_role_models
    set_role_models(cleaned)
    return {"ok": True, "routing": routing_table()}


@router.post("/api/models/arsenal")
def manage_model_arsenal(body: ArsenalAction) -> dict:
    """Charge ou décharge en bloc tous les modèles distincts de l'arsenal."""
    routing_result = apply_model_routing(RoutingUpdate(routing=body.routing))
    generation_models = list(dict.fromkeys(body.routing.values()))
    import requests
    from env_config import OLLAMA_HOST
    from utils.ollama_scheduler import slot
    completed, failures = [], []
    keep = -1 if body.action == "load" else 0
    for model in generation_models:
        try:
            with slot("generate"):
                response = requests.post(
                    f"{OLLAMA_HOST}/api/generate",
                    json={"model": model, "keep_alive": keep}, timeout=600,
                )
            response.raise_for_status()
            completed.append(model)
        except Exception as exc:
            failures.append({"model": model, "error": str(exc)})
    try:
        with slot("embed_ingest"):
            response = requests.post(
                f"{OLLAMA_HOST}/api/embed",
                json={"model": body.embedding_model, "input": "", "keep_alive": keep},
                timeout=600,
            )
        response.raise_for_status()
        completed.append(body.embedding_model)
    except Exception as exc:
        failures.append({"model": body.embedding_model, "error": str(exc)})
    result = {"ok": not failures, "action": body.action,
              "models": list(dict.fromkeys(completed)), "failures": failures,
              "routing": routing_result["routing"]}
    if failures:
        raise HTTPException(502, detail=result)
    return result


@router.post("/api/models/generate")
def set_generation_model(body: ModelAction) -> dict:
    """Changement à chaud du modèle de génération + gestion VRAM (comme les
    boutons Charger/Décharger du Streamlit)."""
    import requests
    from env_config import OLLAMA_HOST
    try:
        from env_config import EMBED_MODEL
        if body.model == EMBED_MODEL or any(marker in body.model.lower() for marker in ("embed", "bge-", "e5-")):
            raise HTTPException(422, "Un modèle d embedding ne peut pas générer de réponse.")
        keep = -1 if body.action == "load" else 0
        from utils.ollama_scheduler import slot
        with slot("generate"):
            response = requests.post(f"{OLLAMA_HOST}/api/generate",
                                     json={"model": body.model, "keep_alive": keep}, timeout=600)
        response.raise_for_status()
        if body.action == "load":
            from core.model_router import set_generate_model
            set_generate_model(body.model)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Ollama : {e}")


# Clés .env modifiables depuis la vue Paramètres — source unique, partagée par la
# lecture (GET) et l'écriture (liste blanche du POST) : ajouter une clé ici la rend
# lisible ET modifiable, sans risque de dérive entre les deux endpoints.
_SETTABLE_KEYS = ("OLLAMA_HOST", "LLM_NUM_CTX", "EMBED_MODEL", "REWRITER_MODEL",
                  "AGENT_MODEL", "PLANNER_MODEL", "EXTRACTION_MODEL",
                  "SYNTHESIS_MODEL", "DEEP_RESEARCH_MODEL", "GEN_MODEL", "JUDGE_MODEL",
                  "ENHANCEMENT_MODEL", "NUM_CHUNKS", "WEIGHT_SEMANTIC",
                  "WEIGHT_BM25", "CE_RELEVANCE_THRESHOLD", "AUTO_KEYWORDS",
                  "AUTO_QUESTIONS", "CHUNKING_MODE", "RAPTOR_SUMMARIES",
                  "SELF_RAG_ENABLED", "SELF_RAG_THRESHOLD", "SELF_RAG_MAX_RETRIES",
                  "CE_DEVICE", "CORPUS_MAP_CONCURRENCY", "RAGAS_JUDGE_CONCURRENCY",
                  "ENHANCE_MAX_WORKERS")


@router.get("/api/settings")
def get_settings() -> dict:
    """Valeurs .env actuelles (celles que la vue Paramètres peut modifier)
    + le system prompt par défaut."""
    import env_config as cfg
    from core.llm_answer import DEFAULT_SYSTEM_PROMPT
    return {"values": {k: getattr(cfg, k) for k in _SETTABLE_KEYS},
            "default_system_prompt": DEFAULT_SYSTEM_PROMPT}


class EnvUpdates(BaseModel):
    updates: dict[str, str] = Field(min_length=1, max_length=64)

_ENV_ALLOWED = set(_SETTABLE_KEYS)
_NUMERIC_BOUNDS = {
    "LLM_NUM_CTX": (2048, 262144), "NUM_CHUNKS": (1, 50),
    "WEIGHT_SEMANTIC": (0, 1), "WEIGHT_BM25": (0, 1),
    "CE_RELEVANCE_THRESHOLD": (0, 1), "AUTO_KEYWORDS": (0, 10),
    "AUTO_QUESTIONS": (0, 10), "SELF_RAG_THRESHOLD": (0, 1),
    "SELF_RAG_MAX_RETRIES": (0, 10), "CORPUS_MAP_CONCURRENCY": (1, 64),
    "RAGAS_JUDGE_CONCURRENCY": (1, 64), "ENHANCE_MAX_WORKERS": (1, 64),
}
_BOOLEAN_KEYS = {"RAPTOR_SUMMARIES", "SELF_RAG_ENABLED"}


@router.post("/api/settings")
def save_settings(body: EnvUpdates) -> dict:
    """Écrit les réglages dans .env (liste blanche). Prend effet au prochain
    démarrage de l'API (comme dans le Streamlit)."""
    unknown = sorted(set(body.updates) - _ENV_ALLOWED)
    if unknown:
        raise HTTPException(422, "Clés de réglage inconnues : " + ", ".join(unknown))
    updates = {k: str(v).strip() for k, v in body.updates.items()}
    for key, value in updates.items():
        if len(value) > 10000 or "\n" in value or "\r" in value:
            raise HTTPException(422, f"Valeur invalide pour {key}.")
        if key in _BOOLEAN_KEYS and value.lower() not in {"true", "false", "1", "0", "yes", "no"}:
            raise HTTPException(422, f"Booléen invalide pour {key}.")
        if key in _NUMERIC_BOUNDS:
            try:
                number = float(value)
            except ValueError as exc:
                raise HTTPException(422, f"Nombre invalide pour {key}.") from exc
            low, high = _NUMERIC_BOUNDS[key]
            if not low <= number <= high:
                raise HTTPException(422, f"{key} doit être compris entre {low} et {high}.")
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
