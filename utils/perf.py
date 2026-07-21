"""Mesure de performance d'inférence (mono-poste, en mémoire).

Le client LLM enregistre ici les statistiques EXACTES renvoyées par Ollama après
chaque génération (tokens générés, durée, time-to-first-token...). L'UI les lit pour
afficher tokens/s, latence et VRAM - sans toucher au code RAG (la mesure reste dans la
couche serving). Buffer en mémoire (les 200 dernières générations) : remis à zéro au
redémarrage, suffisant pour un usage interactif.
"""
from __future__ import annotations

import statistics
from collections import deque

_BUFFER: deque[dict] = deque(maxlen=200)


def record(model: str, gen_tokens: int, gen_s: float, prompt_tokens: int,
           total_s: float, ttft_s: float | None, ts: float) -> None:
    """Enregistre une génération. `gen_s` = durée de décodage (s) renvoyée par Ollama."""
    _BUFFER.append({
        "model": model,
        "gen_tokens": gen_tokens,
        "gen_s": gen_s,
        "tok_per_s": (gen_tokens / gen_s) if gen_s else None,
        "prompt_tokens": prompt_tokens,
        "total_s": total_s,
        "ttft_s": ttft_s,
        "ts": ts,
    })


def snapshot() -> list[dict]:
    """Copie des générations enregistrées, de la plus récente à la plus ancienne."""
    return list(reversed(_BUFFER))


def clear() -> None:
    _BUFFER.clear()


def aggregate(rows: list[dict]) -> dict:
    """Médianes utiles pour un usage interactif (robustes aux valeurs extrêmes)."""
    def med(key):
        vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        return round(statistics.median(vals), 2) if vals else None
    return {
        "count": len(rows),
        "tok_per_s": med("tok_per_s"),
        "ttft_s": med("ttft_s"),
        "total_s": med("total_s"),
        "gen_tokens": med("gen_tokens"),
    }


def gpu_memory() -> list[tuple[float, float]]:
    """VRAM (utilisée, totale) en Mo par GPU via nvidia-smi. [] si indisponible."""
    import subprocess
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3).stdout
        gpus = []
        for line in out.strip().splitlines():
            used, total = (x.strip() for x in line.split(","))
            gpus.append((float(used), float(total)))
        return gpus
    except Exception:
        return []
