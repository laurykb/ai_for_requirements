"""Persistance des éditions et historique des versions.

- Un *corpus de travail* (``corpus/working.json``) sauvegarde l'état édité, pour
  qu'un rafraîchissement ne perde rien.
- Un *journal* (``corpus/history.jsonl``) garde une ligne par changement appliqué.
"""

from __future__ import annotations

import contextlib
import json
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional

from .config import DATA_DIR, DEFAULT_CORPUS
from .corpus_io import load_corpus, save_corpus

try:
    import fcntl  # verrou consultatif POSIX (multi-utilisateur)
    _HAS_FCNTL = True
except Exception:  # pragma: no cover - Windows
    _HAS_FCNTL = False

WORKING = DATA_DIR / "working.json"
HISTORY = DATA_DIR / "history.jsonl"
_LOCK = DATA_DIR / ".lock"


@contextlib.contextmanager
def _file_lock() -> Iterator[None]:
    """Verrou inter-process pour des écritures concurrentes sûres."""
    if not _HAS_FCNTL:
        yield
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_LOCK, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _working_path(workspace: Optional[str]) -> Path:
    return WORKING if not workspace else DATA_DIR / f"working_{workspace}.json"


def load_initial(workspace: Optional[str] = None) -> List[dict]:
    """Reprend le corpus de travail (du workspace) s'il existe, sinon le défaut."""
    path = _working_path(workspace)
    if path.exists():
        data = load_corpus(path)
        if data:
            return data
    return load_corpus(DEFAULT_CORPUS)


def save_working(corpus: List[dict], workspace: Optional[str] = None) -> None:
    with _file_lock():
        save_corpus(corpus, _working_path(workspace))  # écriture atomique (.tmp + replace)


def reset_working(workspace: Optional[str] = None) -> List[dict]:
    """Oublie les éditions : supprime le corpus de travail et recharge l'original."""
    with _file_lock():
        try:
            _working_path(workspace).unlink(missing_ok=True)
        except OSError:
            pass
    return load_corpus(DEFAULT_CORPUS)


def append_history(action_type: str, req_id: str, old_text: str,
                   new_text: str, rationale: str = "") -> None:
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "action": action_type,
        "req_id": req_id,
        "old_text": old_text,
        "new_text": new_text,
        "rationale": rationale,
    }
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock():
        with HISTORY.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def get_history(req_id: Optional[str] = None) -> List[dict]:
    """Renvoie l'historique (filtré sur ``req_id`` si fourni), du plus récent au plus ancien."""
    if not HISTORY.exists():
        return []
    out: List[dict] = []
    with HISTORY.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if req_id is None or rec.get("req_id") == req_id:
                out.append(rec)
    return list(reversed(out))
