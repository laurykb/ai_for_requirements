"""Résolution des versions actives pour les sources documentaires versionnées."""
from __future__ import annotations

from core.reserved_sources import LYNX_BASELINE_SOURCE


def active_version(source: str) -> str | None:
    """Retourne la version publiée d'une source, ou None pour le format legacy."""
    if source != LYNX_BASELINE_SOURCE:
        return None
    try:
        from utils.mongo import get_db
        row = get_db()["lynx_chat_meta"].find_one(
            {"_id": "baseline"}, {"active_version": 1}
        )
        return (row or {}).get("active_version")
    except Exception:
        return None


def metadata_is_active(meta: dict, source: str) -> bool:
    """Filtre une métadonnée sans masquer les index historiques non versionnés."""
    version = active_version(source)
    if source != LYNX_BASELINE_SOURCE:
        return True
    # Avant la première publication versionnée, seuls les chunks historiques
    # sans ingest_version sont actifs ; une préparation ne doit pas fuiter.
    if version is None:
        return not (meta or {}).get("ingest_version")
    return (meta or {}).get("ingest_version") == version
