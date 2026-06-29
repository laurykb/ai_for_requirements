"""Boucle de feedback : capture du retour ingénieur sur les verdicts.

Chaque « verdict juste / faux » est consigné dans ``corpus/feedback.jsonl``.
``export_dataset`` transforme ces retours en exemples étiquetés, base d'un futur
fine-tuning d'un petit modèle spécialisé (cf. README).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from .config import DATA_DIR

FEEDBACK = DATA_DIR / "feedback.jsonl"


def record(action_type: str, target_id: str, verdict: str, message: str, correct: bool) -> None:
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "action_type": action_type, "target_id": target_id,
        "verdict": verdict, "message": message, "correct": bool(correct),
    }
    from . import jsonl_io
    jsonl_io.append(FEEDBACK, entry)


def _read() -> List[dict]:
    if not FEEDBACK.exists():
        return []
    out = []
    with FEEDBACK.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def stats() -> Dict:
    rows = _read()
    if not rows:
        return {"total": 0}
    good = sum(1 for r in rows if r.get("correct"))
    return {"total": len(rows), "justes": good, "faux": len(rows) - good,
            "taux_justesse": round(good / len(rows), 3)}


def export_dataset(path: Path | str = DATA_DIR / "feedback_dataset.jsonl") -> int:
    """Exporte les retours en exemples {prompt, completion, label} pour entraînement."""
    rows = _read()
    path = Path(path)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps({
                "input": f"Action {r['action_type']} sur {r['target_id']}",
                "verdict": r["verdict"], "message": r["message"],
                "label_correct": r["correct"],
            }, ensure_ascii=False) + "\n")
    return len(rows)
