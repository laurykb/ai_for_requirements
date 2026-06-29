"""Append concurrentiel sûr à un fichier JSONL (verrou inter-process)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Union

try:
    import fcntl
    _HAS_FCNTL = True
except Exception:  # pragma: no cover - Windows
    _HAS_FCNTL = False


def append(path: Union[str, Path], obj: dict) -> None:
    path = Path(path)
    line = json.dumps(obj, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        if _HAS_FCNTL:
            fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.write(line)
            fh.flush()
        finally:
            if _HAS_FCNTL:
                fcntl.flock(fh, fcntl.LOCK_UN)
