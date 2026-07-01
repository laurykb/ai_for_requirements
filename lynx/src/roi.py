"""Instrumentation de la valeur (ROI).

Chaque défaut capté à la CONCEPTION (lors d'une édition ou d'un audit) est un
défaut qui n'aura pas à être détecté TARD, à la remontée du V (par les tests) :
c'est le *shift-left*. On le journalise pour quantifier la valeur de l'assistant.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from typing import Dict, List

from .config import DATA_DIR, ROI_MINUTES_PER_CATCH

ROI_LOG = DATA_DIR / "roi.jsonl"
_NOTABLE = {"WARNING", "BLOCKING", "BLOQUANT"}


def record_catches(source: str, action_type: str, target_id: str, findings: List[dict]) -> int:
    """Journalise les constats notables (WARNING/BLOQUANT) d'une analyse.

    ``findings`` = liste de dicts {scope, severity}. ``source`` = "edition"|"audit".
    Renvoie le nombre de défauts captés.
    """
    rows = []
    for f in findings:
        sev = str(f.get("severity") or f.get("sev") or "").upper()
        scope = f.get("scope") or f.get("axis") or ""
        if sev in _NOTABLE and scope not in ("", "AVAL"):  # AVAL = info de propagation, pas un défaut
            rows.append({"ts": datetime.now().isoformat(timespec="seconds"), "source": source,
                         "action_type": action_type, "target_id": target_id,
                         "axis": scope, "severity": sev})
    if rows:
        from . import jsonl_io
        for r in rows:
            jsonl_io.append(ROI_LOG, r)
    return len(rows)


def _read() -> List[dict]:
    if not ROI_LOG.exists():
        return []
    out = []
    with ROI_LOG.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def stats() -> Dict:
    """Agrégats de valeur : défauts captés tôt, par axe, temps estimé économisé."""
    rows = _read()
    if not rows:
        return {"total": 0}
    by_axis = dict(Counter(r.get("axis", "?") for r in rows))
    minutes = len(rows) * ROI_MINUTES_PER_CATCH
    return {
        "total": len(rows),
        "by_axis": by_axis,
        "minutes_saved": minutes,
        "hours_saved": round(minutes / 60, 1),
        "minutes_per_catch": ROI_MINUTES_PER_CATCH,
    }
