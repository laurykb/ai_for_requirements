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
           total_s: float, ttft_s: float | None, ts: float,
           task_id: str | None = None, role: str | None = None,
           prompt_eval_s: float = 0.0, load_s: float = 0.0,
           ollama_total_s: float = 0.0) -> None:
    """Enregistre une génération. `gen_s` = durée de décodage (s) renvoyée par Ollama."""
    _BUFFER.append({
        "model": model,
        "gen_tokens": gen_tokens,
        "gen_s": gen_s,
        "tok_per_s": (gen_tokens / gen_s) if gen_s else None,
        "prompt_tokens": prompt_tokens,
        "prompt_eval_s": prompt_eval_s,
        "load_s": load_s,
        "ollama_total_s": ollama_total_s,
        "total_s": total_s,
        "ttft_s": ttft_s,
        "ts": ts,
        "task_id": task_id,
        "role": role,
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


def hardware_profile() -> dict:
    """Profil central utilisé par l API et les diagnostics de concurrence."""
    gpus = gpu_memory()
    total_mib = int(sum(total for _used, total in gpus))
    if len(gpus) >= 2 and total_mib >= 80 * 1024:
        name, slots = "dual_high_vram", 4
    elif total_mib >= 32 * 1024:
        name, slots = "high_vram", 2
    else:
        name, slots = "constrained", 1
    return {
        "name": name, "gpu_count": len(gpus), "total_vram_mib": total_mib,
        "recommendations": {
            "ollama_sched_spread": len(gpus) >= 2,
            "ollama_num_parallel": slots,
            "ollama_max_loaded_models": 4 if name == "dual_high_vram" else 2,
            "corpus_map_concurrency": slots,
            "ragas_judge_concurrency": slots,
            "eval_question_concurrency": 2 if name == "dual_high_vram" else 1,
            "enhance_max_workers": slots,
            "ce_device": "cuda:0" if gpus else "cpu",
        },
    }
