"""Client LLM compatible OpenAI (httpx).

Marche tel quel avec Ollama (`/v1`), vLLM, SGLang, TGI ou une API cloud : il
suffit de pointer ``LLM_BASE_URL`` ailleurs. Aucune dépendance lourde (httpx
seul).

- ``call_skill`` / ``call_agent`` : appel JSON structuré (dict, ou ``{"error":…}``).
- ``stream_agent`` : appel texte en streaming (générateur de tokens).
- ``set_model`` : change le modèle à chaud.
- Toute indisponibilité dégrade proprement (les analyseurs continuent).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any, Dict, Iterator, List, Optional

import httpx

from . import telemetry

from .config import (
    LLM_API_KEY, LLM_BASE_URL, LLM_CACHE, LLM_DISABLED, LLM_MODEL,
    LLM_TIMEOUT_SECONDS, SKILLS_DIR,
)

_MODEL = LLM_MODEL
_available_cache: Dict[str, bool] = {}
_result_cache: Dict[str, dict] = {}

# --- Boîte de verre : capture des échanges agent<->LLM -------------------------
# Quand une capture est active, chaque appel LLM (payload envoyé + réponse reçue)
# est journalisé pour être rendu lisible dans l'UI. Thread-safe car les agents
# sémantiques tournent en parallèle (ThreadPoolExecutor).
_trace_lock = threading.Lock()
_trace: Optional[List[dict]] = None


def start_trace() -> None:
    """Démarre la capture des échanges (remise à zéro du tampon)."""
    global _trace
    with _trace_lock:
        _trace = []


def stop_trace() -> List[dict]:
    """Arrête la capture et renvoie les échanges journalisés."""
    global _trace
    with _trace_lock:
        out = list(_trace) if _trace is not None else []
        _trace = None
        return out


def _record(rec: dict) -> None:
    with _trace_lock:
        if _trace is not None:
            _trace.append(rec)


def trace_event(label: str, inp: Any, out: Any, **meta) -> None:
    """Journalise un échange d'un agent *non-LLM* (ex. routeur embeddings).

    No-op hors d'une capture active. Permet aux étapes déterministes d'apparaître
    dans la boîte de verre au même titre que les agents LLM.
    """
    rec = {"label": label, "input": inp, "output": out, "ok": True, "latency_ms": None}
    rec.update(meta)
    _record(rec)


def clear_cache() -> None:
    _result_cache.clear()


def _cache_key(system_prompt: str, user_data: Any) -> str:
    h = hashlib.sha256()
    for part in (_MODEL, system_prompt, _as_text(user_data)):
        h.update(part.encode("utf-8"))
    return h.hexdigest()


def set_model(name: str) -> None:
    global _MODEL
    if name:
        _MODEL = name


def current_model() -> str:
    return _MODEL


def _headers() -> dict:
    return {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}


def _as_text(user_data: Any) -> str:
    return user_data if isinstance(user_data, str) else json.dumps(user_data, ensure_ascii=False)


def _messages(system_prompt: str, user_data: Any) -> List[dict]:
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": _as_text(user_data)}]


def load_skill_prompt(skill_name: str) -> str:
    path = SKILLS_DIR / f"{skill_name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Skill introuvable : {path}")
    return path.read_text(encoding="utf-8")


def llm_available() -> bool:
    if LLM_DISABLED:
        return False
    if "ok" not in _available_cache:
        try:
            r = httpx.get(f"{LLM_BASE_URL}/models", headers=_headers(), timeout=3)
            _available_cache["ok"] = r.status_code == 200
        except Exception:
            _available_cache["ok"] = False
    return _available_cache["ok"]


def call_agent(system_prompt: str, user_data: Any, label: Optional[str] = None) -> Dict[str, Any]:
    """Appel JSON : renvoie le dict parsé ou ``{"error": ...}``.

    Cache par (modèle, prompt, entrée) -> reproductibilité des verdicts.
    ``label`` identifie l'agent (nom du skill) pour la boîte de verre.
    """
    if LLM_DISABLED:
        return {"error": "LLM_DISABLED"}
    key = _cache_key(system_prompt, user_data) if LLM_CACHE else None
    if key is not None and key in _result_cache:
        cached = dict(_result_cache[key])
        _record({"label": label, "input": user_data, "output": cached,
                 "cached": True, "ok": True, "latency_ms": None})
        return cached
    result = _chat(system_prompt, user_data, temperature=0, label=label)
    if key is not None and not result.get("error"):
        _result_cache[key] = dict(result)
    return result


def _chat(system_prompt: str, user_data: Any, temperature: float = 0,
          label: Optional[str] = None) -> Dict[str, Any]:
    """Un appel JSON sans cache (utilisé pour le cache et pour le vote)."""
    if LLM_DISABLED:
        return {"error": "LLM_DISABLED"}
    body = {
        "model": _MODEL,
        "messages": _messages(system_prompt, user_data),
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    t0 = time.time()
    try:
        r = httpx.post(f"{LLM_BASE_URL}/chat/completions", headers=_headers(),
                       json=body, timeout=LLM_TIMEOUT_SECONDS)
        r.raise_for_status()
        data = r.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {}) or {}
    except Exception as exc:
        latency = round((time.time() - t0) * 1000)
        telemetry.record({"model": _MODEL, "ok": False, "error": str(exc)[:80],
                          "latency_ms": latency})
        err = {"error": "LLM_INVOCATION_ERROR", "detail": str(exc)[:200]}
        _record({"label": label, "input": user_data, "output": err,
                 "ok": False, "latency_ms": latency})
        return err
    latency = round((time.time() - t0) * 1000)
    telemetry.record({
        "model": _MODEL, "ok": True, "latency_ms": latency,
        "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens", 0)})
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = {"error": "JSON_PARSE_ERROR", "raw_output": (content or "")[:500]}
    _record({"label": label, "input": user_data, "output": parsed,
             "ok": not parsed.get("error"), "latency_ms": latency})
    return parsed


def call_skill(skill_name: str, payload: Any) -> Dict[str, Any]:
    try:
        system_prompt = load_skill_prompt(skill_name)
    except Exception as exc:
        return {"error": "SKILL_NOT_FOUND", "detail": str(exc)}
    return call_agent(system_prompt, payload, label=skill_name)


def sample_skill(skill_name: str, payload: Any, n: int = 3, temperature: float = 0.4) -> list:
    """``n`` tirages indépendants (température > 0, sans cache) pour le vote
    de self-consistency sur les verdicts à fort enjeu."""
    try:
        system_prompt = load_skill_prompt(skill_name)
    except Exception:
        return []
    out = []
    for _ in range(max(1, n)):
        r = _chat(system_prompt, payload, temperature=temperature, label=f"{skill_name}#vote")
        if not r.get("error"):
            out.append(r)
    return out


def stream_agent(system_prompt: str, user_data: Any, label: Optional[str] = None) -> Iterator[str]:
    """Appel texte en streaming : produit les tokens au fil de l'eau (SSE)."""
    if LLM_DISABLED:
        return
    body = {
        "model": _MODEL,
        "messages": _messages(system_prompt, user_data),
        "temperature": 0,
        "stream": True,
    }
    t0 = time.time()
    full = ""
    try:
        with httpx.stream("POST", f"{LLM_BASE_URL}/chat/completions", headers=_headers(),
                          json=body, timeout=LLM_TIMEOUT_SECONDS) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0]["delta"].get("content", "")
                except Exception:
                    continue
                if delta:
                    full += delta
                    yield delta
    except Exception:
        return
    finally:
        if full:
            _record({"label": label, "input": user_data, "output": {"message": full},
                     "streamed": True, "ok": True,
                     "latency_ms": round((time.time() - t0) * 1000)})
