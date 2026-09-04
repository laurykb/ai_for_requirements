"""File mono-job pour comparer prompts par défaut et prompts actifs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from datetime import datetime

_LOCK = threading.Lock()
_JOB: dict = {"status": "idle"}


def status() -> dict:
    with _LOCK:
        return dict(_JOB)


def _set(**values) -> None:
    with _LOCK:
        _JOB.update(values)


def _run_variant(root: Path, name: str, prompts: dict[str, str], log) -> int:
    env = dict(os.environ)
    env["PROMPT_OVERRIDES_JSON"] = json.dumps(prompts, ensure_ascii=False)
    result = subprocess.run([
        sys.executable, "-m", "evals.run_eval",
        "--dataset", "evals/golden_space_candidates_v1.json",
        "--mode", "adaptive_ragas", "--name", name,
    ], cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, check=False)
    return result.returncode


def _worker(defaults: dict[str, str], active: dict[str, str]) -> None:
    root = Path(__file__).resolve().parent.parent
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    baseline = f"prompt-ab-default-{stamp}"
    candidate = f"prompt-ab-active-{stamp}"
    log_path = root / "data" / f"prompt-ab-{stamp}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _set(status="running", stage="baseline", baseline=baseline,
         candidate=candidate, log=str(log_path), error=None)
    try:
        with log_path.open("w", encoding="utf-8") as log:
            code = _run_variant(root, baseline, defaults, log)
            if code:
                _set(status="error", stage="baseline", exit_code=code)
                return
            _set(stage="candidate")
            code = _run_variant(root, candidate, active, log)
            if code:
                _set(status="error", stage="candidate", exit_code=code)
                return
        _set(status="success", stage="done", exit_code=0)
    except Exception as exc:
        _set(status="error", error=str(exc), stage="internal")


def start(defaults: dict[str, str], active: dict[str, str]) -> dict:
    with _LOCK:
        if _JOB.get("status") == "running":
            raise RuntimeError("Une évaluation A/B est déjà en cours.")
        _JOB.clear()
        _JOB.update({"status": "queued", "stage": "queued"})
    thread = threading.Thread(target=_worker, args=(defaults, active),
                              name="prompt-ab-eval", daemon=True)
    thread.start()
    return status()
