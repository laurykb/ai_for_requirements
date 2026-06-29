"""Observabilité des appels LLM : latence, tokens, succès.

Chaque appel est journalisé dans ``corpus/telemetry.jsonl`` (désactivable via
``LLM_TELEMETRY=0``). ``stats()`` agrège pour piloter coût/latence/fiabilité.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from .config import DATA_DIR

TELEMETRY = DATA_DIR / "telemetry.jsonl"
_ENABLED = os.environ.get("LLM_TELEMETRY", "1") != "0"


def record(event: Dict) -> None:
    if not _ENABLED:
        return
    event = {"ts": datetime.now().isoformat(timespec="seconds"), **event}
    try:
        from . import jsonl_io
        jsonl_io.append(TELEMETRY, event)
    except Exception:
        pass


def _read() -> List[dict]:
    if not TELEMETRY.exists():
        return []
    out = []
    with TELEMETRY.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def stats() -> Dict:
    """Agrégats : nb appels, taux de succès, latence moyenne, tokens cumulés."""
    rows = _read()
    if not rows:
        return {"calls": 0}
    n = len(rows)
    ok = sum(1 for r in rows if r.get("ok"))
    lat = [r.get("latency_ms", 0) for r in rows if r.get("latency_ms")]
    toks = sum(r.get("total_tokens", 0) for r in rows)
    return {
        "calls": n,
        "success_rate": round(ok / n, 3),
        "avg_latency_ms": round(sum(lat) / len(lat)) if lat else 0,
        "total_tokens": toks,
    }
