"""Client LLM Ollama minimal (HTTP direct, sans langchain).

Parle à l'API native d'Ollama (`/api/generate`), ce qui permet de garder les
options serveur dont le projet dépend (`num_ctx`, `keep_alive`, `top_k`,
`repeat_penalty`...) - que l'endpoint OpenAI `/v1` n'expose pas toutes.

Interface utilisée par le reste du code :
  - ``invoke(prompt, stop=None) -> str``  : génération complète (bloquant).
  - ``stream(prompt, stop=None)``         : génère les tokens un par un.
"""
from __future__ import annotations

import json
from typing import Iterator

import time

import requests

from env_config import OLLAMA_HOST
from utils import perf

# Génération potentiellement longue (réponse ancrée + contexte) : marge confortable.
_TIMEOUT_S = 600


def _record_stats(model: str, data: dict, total_s: float, ttft_s: float | None) -> None:
    """Enregistre les stats EXACTES renvoyées par Ollama (durées en nanosecondes)."""
    try:
        perf.record(
            model=model,
            gen_tokens=int(data.get("eval_count") or 0),
            gen_s=(data.get("eval_duration") or 0) / 1e9,
            prompt_tokens=int(data.get("prompt_eval_count") or 0),
            total_s=total_s,
            ttft_s=ttft_s,
            ts=time.time(),
        )
    except Exception:
        pass   # la mesure ne doit jamais casser la génération


class OllamaClient:
    def __init__(self, model: str, options: dict | None = None,
                 keep_alive=None, base_url: str = OLLAMA_HOST):
        self.model = model
        self.options = options or {}
        self.keep_alive = keep_alive
        self.base_url = (base_url or "http://localhost:11434").rstrip("/")

    def _payload(self, prompt: str, stream: bool, stop, fmt=None) -> dict:
        options = dict(self.options)
        if stop:
            options["stop"] = stop
        payload = {"model": self.model, "prompt": prompt, "stream": stream, "options": options}
        if fmt:
            # Sortie contrainte Ollama (ex: "json") : le serveur garantit un JSON valide.
            payload["format"] = fmt
        if self.keep_alive is not None:
            payload["keep_alive"] = self.keep_alive
        return payload

    def invoke(self, prompt: str, stop=None, format=None) -> str:
        t0 = time.perf_counter()
        r = requests.post(f"{self.base_url}/api/generate",
                          json=self._payload(prompt, False, stop, fmt=format), timeout=_TIMEOUT_S)
        r.raise_for_status()
        data = r.json()
        if data.get("error"):
            # Ollama renvoie parfois un 200 avec un corps {"error": ...}
            # (éviction du modèle, OOM VRAM) : ne pas persister une réponse vide.
            raise RuntimeError(f"Ollama: {data['error']}")
        _record_stats(self.model, data, time.perf_counter() - t0, ttft_s=None)
        return data.get("response", "")

    def stream(self, prompt: str, stop=None) -> Iterator[str]:
        t0 = time.perf_counter()
        ttft_s = None
        with requests.post(f"{self.base_url}/api/generate",
                           json=self._payload(prompt, True, stop),
                           stream=True, timeout=_TIMEOUT_S) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                if data.get("error"):
                    raise RuntimeError(f"Ollama: {data['error']}")
                token = data.get("response", "")
                if token:
                    if ttft_s is None:
                        ttft_s = time.perf_counter() - t0   # time-to-first-token
                    yield token
                if data.get("done"):
                    _record_stats(self.model, data, time.perf_counter() - t0, ttft_s)
                    break
