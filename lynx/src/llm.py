"""Client LLM compatible OpenAI (httpx).

Marche tel quel avec Ollama (`/v1`), vLLM, SGLang, TGI ou une API cloud : il
suffit de pointer ``LLM_BASE_URL`` ailleurs. Aucune dépendance lourde (httpx
seul).

- ``call_skill`` / ``call_agent`` : appel JSON structuré (dict, ou ``{"error":…}``).
- ``stream_agent`` : appel texte en streaming (générateur de tokens).
- ``set_model`` : change le modèle à chaud.
- En cas d'indisponibilité, les analyseurs continuent.

Sorties structurées : quand un skill a un schéma déclaré dans ``schemas.py``,
la génération est contrainte (``response_format: json_schema``, repli
``json_object`` si le backend le rejette) et la réponse est validée par le
modèle Pydantic — un écart déclenche UN retry avec les erreurs de validation
réinjectées, puis ``{"error": "SCHEMA_VALIDATION_ERROR"}`` s'il persiste.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple, Type

import httpx
from pydantic import BaseModel, ValidationError

from . import telemetry

from .config import (
    LLM_API_KEY, LLM_BASE_URL, LLM_CACHE, LLM_CACHE_DIR, LLM_CACHE_DISK,
    LLM_DISABLED, LLM_MODEL, LLM_TIMEOUT_SECONDS, SKILLS_DIR,
)
from .schemas import constrain_generation, response_format as _schema_format, schema_for

_MODEL = LLM_MODEL
_available_cache: Dict[str, bool] = {}
_result_cache: Dict[str, dict] = {}
# Le backend accepte-t-il ``response_format: json_schema`` ? None = pas encore
# sondé ; False = rejeté une fois -> repli définitif sur json_object (process).
_json_schema_supported: Optional[bool] = None

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


# --- Cache disque (opt-out : LLM_CACHE_DISK=0) ---------------------------------
# Un fichier JSON par clé (écriture atomique tmp+rename : pas de verrou nécessaire,
# les agents parallèles écrivent des fichiers distincts ou le même contenu).
def _disk_get(key: str) -> Optional[dict]:
    if not LLM_CACHE_DISK:
        return None
    path = LLM_CACHE_DIR / f"{key}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _disk_put(key: str, result: dict) -> None:
    if not LLM_CACHE_DISK:
        return
    try:
        LLM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = LLM_CACHE_DIR / f".{key}.tmp"
        tmp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        tmp.replace(LLM_CACHE_DIR / f"{key}.json")
    except Exception:
        pass  # un cache qui échoue ne doit jamais casser l'appel


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
    # Cache avec TTL : l'ancien cache "pour toujours" figeait un Ollama
    # éteint au premier appel — l'audit IA restait alors désactivé jusqu'au
    # redémarrage du process, même une fois Ollama revenu.
    import time as _time
    now = _time.monotonic()
    if "ok" not in _available_cache or now - _available_cache.get("ts", 0) > 10:
        try:
            r = httpx.get(f"{LLM_BASE_URL}/models", headers=_headers(), timeout=3)
            _available_cache["ok"] = r.status_code == 200
        except Exception:
            _available_cache["ok"] = False
        _available_cache["ts"] = now
    return _available_cache["ok"]


def call_agent(system_prompt: str, user_data: Any, label: Optional[str] = None,
               schema: Optional[Type[BaseModel]] = None) -> Dict[str, Any]:
    """Appel JSON : renvoie le dict parsé (validé si schéma) ou ``{"error": ...}``.

    Cache par (modèle, prompt, entrée) -> reproductibilité des verdicts. Seules
    les réponses valides y entrent : une réponse en erreur (invocation, JSON
    illisible ou non conforme au schéma) n'est jamais mise en cache.
    ``label`` identifie l'agent (nom du skill) pour la boîte de verre ; il sert
    aussi à résoudre le schéma dans le registre si ``schema`` n'est pas fourni.
    """
    if LLM_DISABLED:
        return {"error": "LLM_DISABLED"}
    if schema is None:
        schema = schema_for(label)
    key = _cache_key(system_prompt, user_data) if LLM_CACHE else None
    if key is not None and key in _result_cache:
        cached = dict(_result_cache[key])
        _record({"label": label, "input": user_data, "output": cached,
                 "cached": True, "ok": True, "latency_ms": None})
        return cached
    if key is not None:
        disk = _disk_get(key)
        if disk is not None and not disk.get("error"):
            _result_cache[key] = dict(disk)
            _record({"label": label, "input": user_data, "output": disk,
                     "cached": True, "ok": True, "latency_ms": None})
            return dict(disk)
    result = _chat(system_prompt, user_data, temperature=0, label=label, schema=schema,
                   grammar=constrain_generation(label))
    if key is not None and not result.get("error"):
        _result_cache[key] = dict(result)
        _disk_put(key, result)
    return result


def _response_format(schema: Optional[Type[BaseModel]]) -> dict:
    """``json_schema`` strict quand un schéma existe et que le backend l'accepte."""
    if schema is not None and _json_schema_supported is not False:
        return _schema_format(schema)
    return {"type": "json_object"}


def _post_chat(messages: List[dict], temperature: float,
               schema: Optional[Type[BaseModel]]) -> dict:
    """Un POST /chat/completions, avec repli json_schema -> json_object.

    Si le backend rejette ``response_format: json_schema`` (400/404/422), on
    mémorise le refus pour le process et on rejoue l'appel en ``json_object``.
    """
    global _json_schema_supported
    fmt = _response_format(schema)
    body = {"model": _MODEL, "messages": messages, "temperature": temperature,
            "response_format": fmt}
    try:
        r = httpx.post(f"{LLM_BASE_URL}/chat/completions", headers=_headers(),
                       json=body, timeout=LLM_TIMEOUT_SECONDS)
        r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if fmt.get("type") != "json_schema" or exc.response is None \
                or exc.response.status_code not in (400, 404, 422):
            raise
        _json_schema_supported = False  # repli définitif pour ce process
        body["response_format"] = {"type": "json_object"}
        r = httpx.post(f"{LLM_BASE_URL}/chat/completions", headers=_headers(),
                       json=body, timeout=LLM_TIMEOUT_SECONDS)
        r.raise_for_status()
    else:
        if fmt.get("type") == "json_schema":
            _json_schema_supported = True
    return r.json()


def _chat_once(messages: List[dict], temperature: float,
               schema: Optional[Type[BaseModel]]) -> Tuple[Dict[str, Any], str, int]:
    """Un aller-retour LLM : renvoie (dict parsé ou ``{"error":…}``, brut, latence)."""
    t0 = time.time()
    try:
        data = _post_chat(messages, temperature, schema)
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {}) or {}
    except Exception as exc:
        latency = round((time.time() - t0) * 1000)
        telemetry.record({"model": _MODEL, "ok": False, "error": str(exc)[:80],
                          "latency_ms": latency})
        return {"error": "LLM_INVOCATION_ERROR", "detail": str(exc)[:200]}, "", latency
    latency = round((time.time() - t0) * 1000)
    telemetry.record({
        "model": _MODEL, "ok": True, "latency_ms": latency,
        "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens", 0)})
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = {"error": "JSON_PARSE_ERROR", "raw_output": (content or "")[:500]}
    return parsed, content or "", latency


def _validation_errors(exc: ValidationError) -> str:
    """Erreurs Pydantic condensées, réinjectables dans la conversation."""
    return " ; ".join(
        f"{'.'.join(map(str, e['loc'])) or '<racine>'} : {e['msg']}"
        for e in exc.errors()[:8])


_RETRY_PROMPT = ("Ta réponse précédente ne respecte pas le format JSON attendu. "
                 "Erreurs de validation : {errors}. "
                 "Réponds à nouveau avec UNIQUEMENT le JSON corrigé, strictement "
                 "conforme au format demandé, sans texte autour.")


def _chat(system_prompt: str, user_data: Any, temperature: float = 0,
          label: Optional[str] = None, schema: Optional[Type[BaseModel]] = None,
          validate_retry: bool = True, grammar: bool = True) -> Dict[str, Any]:
    """Un appel JSON sans cache (utilisé pour le cache et pour le vote).

    Avec ``schema``, la réponse est validée : un écart déclenche UN retry (les
    erreurs de validation sont réinjectées dans la conversation), puis
    ``{"error": "SCHEMA_VALIDATION_ERROR"}`` si l'écart persiste.
    ``validate_retry=False`` (vote) : le tirage non conforme est simplement écarté.
    ``grammar=False`` : validation seule, sans contrainte json_schema à la
    génération (skills listés dans ``schemas.GENERATION_LIBRE``).
    """
    if LLM_DISABLED:
        return {"error": "LLM_DISABLED"}
    gen_schema = schema if grammar else None
    messages = _messages(system_prompt, user_data)
    parsed, content, latency = _chat_once(messages, temperature, gen_schema)
    rec = {"label": label, "input": user_data, "latency_ms": latency}
    if parsed.get("error"):
        _record({**rec, "output": parsed, "ok": False})
        return parsed
    if schema is None:
        _record({**rec, "output": parsed, "ok": True})
        return parsed

    # Validation Pydantic (le dump normalise : ids coercés, gravités unifiées).
    try:
        parsed = schema.model_validate(parsed).model_dump()
        _record({**rec, "output": parsed, "ok": True, "validation": "valide"})
        return parsed
    except ValidationError as exc:
        errors = _validation_errors(exc)

    if validate_retry:
        retry_messages = messages + [
            {"role": "assistant", "content": content},
            {"role": "user", "content": _RETRY_PROMPT.format(errors=errors)}]
        parsed2, content2, latency2 = _chat_once(retry_messages, temperature, gen_schema)
        rec["latency_ms"] = latency + latency2
        if not parsed2.get("error"):
            try:
                parsed2 = schema.model_validate(parsed2).model_dump()
                _record({**rec, "output": parsed2, "ok": True,
                         "validation": "valide_apres_retry", "retries": 1})
                return parsed2
            except ValidationError as exc2:
                errors, content = _validation_errors(exc2), content2

    err = {"error": "SCHEMA_VALIDATION_ERROR", "detail": errors[:500],
           "raw_output": content[:500]}
    _record({**rec, "output": err, "ok": False, "validation": "invalide",
             "retries": 1 if validate_retry else 0})
    return err


def call_skill(skill_name: str, payload: Any,
               schema: Optional[Type[BaseModel]] = None) -> Dict[str, Any]:
    """Appelle un skill ; le schéma de réponse vient du registre ``schemas.py``
    (surcharge possible via ``schema``), sans rien changer pour les appelants."""
    try:
        system_prompt = load_skill_prompt(skill_name)
    except Exception as exc:
        return {"error": "SKILL_NOT_FOUND", "detail": str(exc)}
    return call_agent(system_prompt, payload, label=skill_name, schema=schema)


def sample_skill(skill_name: str, payload: Any, n: int = 3, temperature: float = 0.4) -> list:
    """``n`` tirages indépendants (température > 0, sans cache) pour le vote
    de self-consistency sur les verdicts à fort enjeu. Un tirage non conforme
    au schéma du skill est écarté (pas de retry : le vote absorbe la perte)."""
    try:
        system_prompt = load_skill_prompt(skill_name)
    except Exception:
        return []
    schema = schema_for(skill_name)
    out = []
    for _ in range(max(1, n)):
        r = _chat(system_prompt, payload, temperature=temperature,
                  label=f"{skill_name}#vote", schema=schema, validate_retry=False,
                  grammar=constrain_generation(skill_name))
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
