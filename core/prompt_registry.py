"""Registre local des templates LLM modifiables et versionnés.

Les overrides vivent dans MongoDB. Si Mongo est indisponible, le pipeline garde
toujours son template livré avec le code.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from string import Formatter

from env_config import MONGO_DB


def _collection(name: str):
    from utils.mongo import get_client
    return get_client()[MONGO_DB][name]


def template_fields(template: str) -> set[str]:
    return {name for _, name, _, _ in Formatter().parse(template) if name}


def validate_template(candidate: str, default: str) -> list[str]:
    if not (candidate or "").strip():
        return ["Le template ne peut pas être vide."]
    try:
        fields = template_fields(candidate)
    except ValueError as exc:
        return [f"Accolades invalides : {exc}"]
    missing = sorted(template_fields(default) - fields)
    return [f"Variable obligatoire manquante : {{{name}}}" for name in missing]


def get_prompt(key: str, default: str) -> str:
    raw = os.environ.get("PROMPT_OVERRIDES_JSON", "")
    if raw:
        try:
            isolated = json.loads(raw)
            value = isolated.get(key)
            if isinstance(value, str) and value.strip():
                return value
        except (ValueError, TypeError, AttributeError):
            pass
    try:
        row = _collection("prompt_overrides").find_one({"key": key})
        value = (row or {}).get("template")
        return value if isinstance(value, str) and value.strip() else default
    except Exception:
        return default


def save_prompt(key: str, template: str, default: str, author: str = "ui") -> dict:
    errors = validate_template(template, default)
    if errors:
        raise ValueError(" ".join(errors))
    now = datetime.now(timezone.utc)
    current = _collection("prompt_overrides").find_one({"key": key}) or {}
    version = int(current.get("version", 0)) + 1
    row = {"key": key, "template": template, "version": version,
           "updated_at": now, "author": author}
    _collection("prompt_overrides").update_one({"key": key}, {"$set": row}, upsert=True)
    _collection("prompt_versions").insert_one({**row, "action": "save"})
    return {**row, "updated_at": now.isoformat()}


def reset_prompt(key: str) -> dict:
    now = datetime.now(timezone.utc)
    current = _collection("prompt_overrides").find_one({"key": key}) or {}
    _collection("prompt_versions").insert_one({
        "key": key, "template": current.get("template", ""),
        "version": int(current.get("version", 0)) + 1,
        "updated_at": now, "author": "ui", "action": "reset",
    })
    _collection("prompt_overrides").delete_one({"key": key})
    return {"key": key, "reset": True, "updated_at": now.isoformat()}


def history(key: str, limit: int = 20) -> list[dict]:
    try:
        rows = list(_collection("prompt_versions").find(
            {"key": key}, {"_id": 0}).sort("updated_at", -1).limit(max(1, min(limit, 100))))
    except Exception:
        return []
    for row in rows:
        if hasattr(row.get("updated_at"), "isoformat"):
            row["updated_at"] = row["updated_at"].isoformat()
    return rows
