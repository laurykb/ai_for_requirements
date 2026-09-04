"""Télémétrie déterministe par tâche pour les orchestrations Chat et SRA."""
from __future__ import annotations

import contextvars
import os
import threading
import time
import uuid
from collections import Counter, deque
from contextlib import contextmanager

_current_task_id = contextvars.ContextVar("agentic_task_id", default=None)
_current_operation = contextvars.ContextVar("agentic_operation", default=None)
_lock = threading.Lock()
_active = {}
_recent = deque(maxlen=200)

def _persist(task):
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    try:
        from utils.mongo import get_db
        get_db()["task_metrics"].insert_one(dict(task))
    except Exception:
        pass

def current_task_id():
    return _current_task_id.get()

def begin(kind, budget=None, **metadata):
    task = {"task_id": uuid.uuid4().hex, "kind": kind, "status": "running", "started_at": time.time(), "metadata": dict(metadata), "budget": dict(budget or {}), "outcome": {}, "trajectory": {"llm_calls": 0, "llm_calls_by_role": {}, "llm_calls_by_model": {}, "llm_calls_by_operation": {}, "prompt_tokens": 0, "completion_tokens": 0, "llm_time_s": 0.0, "prompt_eval_s": 0.0, "decode_s": 0.0, "load_s": 0.0, "tool_calls": 0, "steps": 0, "replans": 0}, "quality": {"semantic_judge": "not_run"}}
    with _lock:
        _active[task["task_id"]] = task
    return task

def bind(task_id):
    return _current_task_id.set(task_id)

def current_operation():
    return _current_operation.get()


@contextmanager
def operation(name):
    token = _current_operation.set(name)
    try:
        yield
    finally:
        _current_operation.reset(token)

class TaskBudgetExceeded(RuntimeError):
    pass

def check_budget(task_id=None):
    task_id = task_id or current_task_id()
    with _lock:
        task = _active.get(task_id) if task_id else None
        if not task:
            return
        budget = task.get("budget") or {}
        if budget.get("max_llm_calls") is not None and task["trajectory"]["llm_calls"] >= budget["max_llm_calls"]:
            raise TaskBudgetExceeded("Budget d’appels LLM atteint")
        if budget.get("max_wall_s") is not None and time.time() - task["started_at"] >= budget["max_wall_s"]:
            raise TaskBudgetExceeded("Budget de temps atteint")
        total_tokens = task["trajectory"]["prompt_tokens"] + task["trajectory"]["completion_tokens"]
        if budget.get("max_total_tokens") is not None and total_tokens >= budget["max_total_tokens"]:
            raise TaskBudgetExceeded("Budget de tokens atteint")

def unbind(token):
    try:
        _current_task_id.reset(token)
    except ValueError:
        _current_task_id.set(None)

def record_llm_call(task_id, model, role, prompt_tokens, completion_tokens, total_s, prompt_eval_s=0.0, decode_s=0.0, load_s=0.0, operation=None):
    if not task_id:
        return
    with _lock:
        task = _active.get(task_id)
        if not task:
            return
        trajectory = task["trajectory"]
        trajectory["llm_calls"] += 1
        trajectory["prompt_tokens"] += max(0, int(prompt_tokens))
        trajectory["completion_tokens"] += max(0, int(completion_tokens))
        trajectory["llm_time_s"] += max(0.0, float(total_s))
        trajectory["prompt_eval_s"] += max(0.0, float(prompt_eval_s))
        trajectory["decode_s"] += max(0.0, float(decode_s))
        trajectory["load_s"] += max(0.0, float(load_s))
        by_role = Counter(trajectory["llm_calls_by_role"]); by_role[role or "unspecified"] += 1
        trajectory["llm_calls_by_role"] = dict(by_role)
        by_model = Counter(trajectory["llm_calls_by_model"]); by_model[model] += 1
        trajectory["llm_calls_by_model"] = dict(by_model)
        by_operation = Counter(trajectory["llm_calls_by_operation"]); by_operation[operation or _current_operation.get() or role or "unspecified"] += 1
        trajectory["llm_calls_by_operation"] = dict(by_operation)

def set_trajectory(task_id, **values):
    with _lock:
        task = _active.get(task_id) if task_id else None
        if task:
            task["trajectory"].update({key: value for key, value in values.items() if value is not None})

def set_outcome(task_id, **values):
    with _lock:
        task = _active.get(task_id) if task_id else None
        if task:
            task["outcome"].update(values)

def update_budget(task_id=None, **values):
    """Ajuste un budget après une phase dont la charge devient observable."""
    task_id = task_id or current_task_id()
    with _lock:
        task = _active.get(task_id) if task_id else None
        if task:
            task["budget"].update({key: value for key, value in values.items() if value is not None})
            return dict(task["budget"])
    return None

def set_quality(task_id, metrics):
    quality = {"semantic_judge": "completed", **dict(metrics or {})}
    with _lock:
        task = _active.get(task_id) if task_id else None
        if task:
            task["quality"] = quality
            return
    if task_id:
        try:
            from utils.mongo import get_db
            get_db()["task_metrics"].update_one(
                {"task_id": task_id}, {"$set": {"quality": quality}}
            )
        except Exception:
            pass

def finish(task_id, status="completed", **outcome):
    with _lock:
        task = _active.pop(task_id, None)
        if not task:
            return None
        task["outcome"].update(outcome); task["status"] = status; task["finished_at"] = time.time()
        task["latency_s"] = round(task["finished_at"] - task["started_at"], 3)
        llm_s = task["trajectory"]["llm_time_s"]; task["trajectory"]["llm_time_s"] = round(llm_s, 3)
        task["trajectory"]["llm_share"] = round(llm_s / task["latency_s"], 3) if task["latency_s"] else None
        task["efficiency"] = {"total_tokens": task["trajectory"]["prompt_tokens"] + task["trajectory"]["completion_tokens"], "llm_calls": task["trajectory"]["llm_calls"], "llm_time_s": task["trajectory"]["llm_time_s"], "prompt_eval_s": round(task["trajectory"]["prompt_eval_s"], 3), "decode_s": round(task["trajectory"]["decode_s"], 3), "load_s": round(task["trajectory"]["load_s"], 3), "latency_s": task["latency_s"]}
        _recent.appendleft(task)
        snapshot = dict(task)
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            threading.Thread(target=_persist, args=(snapshot,), daemon=True).start()
        return snapshot

def finish_sra(task_id, result, documents, row_key):
    if result.get("stopped"):
        return finish(task_id, "stopped", completion="stopped")
    if result.get("error"):
        error = str(result["error"])[:300]
        status = "budget_exceeded" if "Budget " in error else "failed"
        return finish(task_id, status, completion=status, error=error)
    table = result.get("table") or {}
    if table.get("completion_status") == "partial":
        return finish(task_id, "partial", completion="partial", requested_documents=len(documents), processed_documents=len(table.get("source_document_ids") or []), produced_rows=len(table.get(row_key) or []), warnings=len(table.get("warnings") or []), resume_available=bool(table.get("resume_available")))
    processed = table.get("source_document_ids") or []
    complete = set(processed) == set(documents)
    return finish(task_id, "completed" if complete else "partial", completion="complete" if complete else "partial", requested_documents=len(documents), processed_documents=len(processed), produced_rows=len(table.get(row_key) or []), warnings=len(table.get("warnings") or []))

def recent(limit=20, kind=None):
    with _lock:
        rows = list(_recent)
    if kind:
        rows = [row for row in rows if row.get("kind") == kind]
    wanted = max(1, min(limit, 100))
    if len(rows) < wanted:
        try:
            from utils.mongo import get_db
            query = {"kind": kind} if kind else {}
            stored = list(get_db()["task_metrics"].find(query).sort("_id", -1).limit(wanted))
            seen = {row.get("task_id") for row in rows}
            for row in stored:
                row.pop("_id", None)
                if row.get("task_id") not in seen:
                    rows.append(row)
        except Exception:
            pass
    return rows[:wanted]

