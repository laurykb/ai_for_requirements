"""Persistance des éditions et historique des versions.

- Un *corpus de travail* (``corpus/working.json``) sauvegarde l'état édité, pour
  qu'un rafraîchissement ne perde rien.
- Un *journal* (``corpus/history.jsonl``) garde une ligne par changement appliqué.
"""

from __future__ import annotations

import contextlib
import json
import hashlib
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
DRAFTS_DIR = DATA_DIR / "drafts"
VERSIONS_DIR = DATA_DIR / "versions"
ACTIVATIONS_DIR = DATA_DIR / "activations"
ACTIVE_MANIFEST = DATA_DIR / "active_manifest.json"
SCHEMA_VERSION = 2
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


def _migrate_corpus(corpus: List[dict]) -> List[dict]:
    """Migration en lecture, idempotente, des corpus historiques."""
    migrated: List[dict] = []
    for raw in corpus:
        req = dict(raw)
        req.setdefault("links", [])
        req.setdefault("occurrences", [])
        req.setdefault("collision_variants", [])
        req.setdefault("root_declared", False)
        migrated.append(req)
    return migrated


def _corpus_digest(corpus: List[dict]) -> str:
    canonical = json.dumps(corpus, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_initial(workspace: Optional[str] = None) -> List[dict]:
    """Reprend le corpus de travail (du workspace) s'il existe, sinon le défaut."""
    path = _working_path(workspace)
    if path.exists():
        return _migrate_corpus(load_corpus(path))
    return _migrate_corpus(load_corpus(DEFAULT_CORPUS))


def save_working(corpus: List[dict], workspace: Optional[str] = None) -> None:
    with _file_lock():
        save_corpus(corpus, _working_path(workspace))  # écriture atomique (.tmp + replace)


def _save_json(payload: dict, path: Path) -> None:
    """Écriture JSON atomique partagée par brouillons et versions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with _file_lock():
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)


def save_draft(draft: dict) -> None:
    _save_json(draft, DRAFTS_DIR / f"{draft['draft_id']}.json")


def save_activation_receipt(activation_id: str, receipt: dict) -> None:
    _save_json(receipt, ACTIVATIONS_DIR / f"{activation_id}.json")


def save_active_manifest(corpus: List[dict], activation_id: str, previous_version_id: str) -> None:
    """Pointeur atomique vers la baseline active et son empreinte vérifiable."""
    _save_json({"schema_version": SCHEMA_VERSION, "activation_id": activation_id,
                "previous_version_id": previous_version_id,
                "activated_at": datetime.now().isoformat(), "n": len(corpus),
                "corpus_sha256": _corpus_digest(corpus)}, ACTIVE_MANIFEST)


def load_activation_receipt(activation_id: str) -> dict | None:
    path = ACTIVATIONS_DIR / f"{activation_id}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_draft(draft_id: str) -> dict | None:
    path = DRAFTS_DIR / f"{draft_id}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_drafts() -> List[dict]:
    drafts = []
    for path in sorted(DRAFTS_DIR.glob("*.json"), reverse=True) if DRAFTS_DIR.exists() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            drafts.append({key: data.get(key) for key in
                           ("draft_id", "created_at", "source_names", "health", "diff")})
        except (OSError, json.JSONDecodeError):
            continue
    return drafts


def delete_draft(draft_id: str) -> None:
    with _file_lock():
        (DRAFTS_DIR / f"{draft_id}.json").unlink(missing_ok=True)


def save_version(corpus: List[dict], reason: str) -> str:
    version_id = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    path = VERSIONS_DIR / f"{version_id}.json"
    if path.exists():
        raise FileExistsError(f"Version immutable déjà présente : {version_id}")
    digest = _corpus_digest(corpus)
    _save_json({"schema_version": SCHEMA_VERSION, "version_id": version_id,
                "created_at": datetime.now().isoformat(), "reason": reason,
                "corpus_sha256": digest, "n": len(corpus), "exigences": corpus}, path)
    return version_id


def list_versions() -> List[dict]:
    versions = []
    for path in sorted(VERSIONS_DIR.glob("*.json"), reverse=True) if VERSIONS_DIR.exists() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            versions.append({"version_id": data.get("version_id"),
                             "created_at": data.get("created_at"),
                             "reason": data.get("reason"),
                             "n": len(data.get("exigences", []))})
        except (OSError, json.JSONDecodeError):
            continue
    return versions


def load_version(version_id: str) -> List[dict] | None:
    path = VERSIONS_DIR / f"{version_id}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        corpus = payload.get("exigences")
        if corpus is None or (payload.get("corpus_sha256") and payload["corpus_sha256"] != _corpus_digest(corpus)):
            return None
        return corpus
    except (OSError, json.JSONDecodeError):
        return None


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
