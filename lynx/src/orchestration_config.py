"""Configuration d'orchestration pilotable depuis l'UI.

L'ordre et l'activation des agents d'analyse d'impact sont persistés dans
``corpus/orchestration.json`` :

    {"deterministic": [{"name": "analyze_allocation", "enabled": true}, …],
     "semantic":      [{"name": "analyze_pertinence",  "enabled": true}, …]}

- déterministes : exécutés séquentiellement, dans l'ordre configuré ;
- sémantiques   : lancés en parallèle, l'ordre = ordre de lancement.

Sans fichier (ou entrée inconnue) : les listes par défaut de ``analyzers.py``.
Un agent absent du fichier est considéré actif (ajout de code sans migration).
"""
from __future__ import annotations

import json
from pathlib import Path

from . import analyzers as _an
from .config import DATA_DIR

CONFIG_PATH = Path(DATA_DIR) / "orchestration.json"

# Registre : nom stable -> fonction (source de vérité = analyzers.py).
_ALL = {f.__name__: f for f in _an.DETERMINISTIC_ANALYZERS + _an.SEMANTIC_ANALYZERS}


def _defaults() -> dict:
    return {
        "deterministic": [{"name": f.__name__, "enabled": True}
                          for f in _an.DETERMINISTIC_ANALYZERS],
        "semantic": [{"name": f.__name__, "enabled": True}
                     for f in _an.SEMANTIC_ANALYZERS],
    }


def load_config() -> dict:
    """Config courante, complétée des agents nouveaux (jamais silencieusement
    amputée) et purgée des noms inconnus."""
    cfg = _defaults()
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return cfg
    # Syntaxe valide mais structure invalide (racine non-dict, entrées non-dict,
    # entrée sans "enabled") : on normalise sans jamais lever hors de load_config.
    if not isinstance(raw, dict):
        return cfg
    for group in ("deterministic", "semantic"):
        raw_entries = raw.get(group, [])
        if not isinstance(raw_entries, list):
            continue
        saved = [{"name": e["name"], "enabled": bool(e.get("enabled", True))}
                 for e in raw_entries
                 if isinstance(e, dict) and e.get("name") in _ALL]
        saved_names = {e["name"] for e in saved}
        missing = [e for e in cfg[group] if e["name"] not in saved_names]
        if saved:
            cfg[group] = saved + missing
    return cfg


def save_config(cfg: dict) -> dict:
    """Valide (noms connus, groupes corrects) puis persiste. Retourne la
    config effective."""
    clean = {}
    for group, default_fns in (("deterministic", _an.DETERMINISTIC_ANALYZERS),
                               ("semantic", _an.SEMANTIC_ANALYZERS)):
        allowed = {f.__name__ for f in default_fns}
        entries, seen = [], set()
        for entry in cfg.get(group, []):
            name = entry.get("name") if isinstance(entry, dict) else None
            if name in allowed and name not in seen:
                entries.append({"name": name, "enabled": bool(entry.get("enabled", True))})
                seen.add(name)
        entries += [{"name": n, "enabled": True} for n in allowed if n not in seen]
        clean[group] = entries
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(clean, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    return clean


def active_analyzers() -> tuple[list, list]:
    """(déterministes, sémantiques) : fonctions actives, dans l'ordre configuré.
    C'est ce point que l'orchestrateur consulte à chaque analyse."""
    cfg = load_config()
    det = [_ALL[e["name"]] for e in cfg["deterministic"] if e["enabled"]]
    sem = [_ALL[e["name"]] for e in cfg["semantic"] if e["enabled"]]
    return det, sem
