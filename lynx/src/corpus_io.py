"""Chargement / sauvegarde du corpus d'exigences (JSON plat ou enveloppé).

Au chargement, chaque exigence est validée et normalisée via le modèle
``Requirement`` : les entrées invalides sont écartées et signalées plutôt que
de casser l'UI plus loin (cast de niveau, champs manquants…).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import IO, List, Tuple, Union

from pydantic import ValidationError

from .config import DEFAULT_CORPUS
from .models import Requirement

logger = logging.getLogger(__name__)


def _unwrap(payload) -> List[dict]:
    if isinstance(payload, dict) and "exigences" in payload:
        return payload.get("exigences", [])
    if isinstance(payload, list):
        return payload
    return []


def validate_corpus(raw_items: List[dict]) -> Tuple[List[dict], List[str]]:
    """Valide/normalise une liste d'exigences. Renvoie (valides, erreurs)."""
    valides: List[dict] = []
    erreurs: List[str] = []
    seen: set[str] = set()
    for i, item in enumerate(raw_items):
        try:
            req = Requirement(**item)
        except ValidationError as exc:
            ident = item.get("id", f"#{i}") if isinstance(item, dict) else f"#{i}"
            erreurs.append(f"{ident}: {exc.error_count()} champ(s) invalide(s)")
            continue
        if req.id in seen:
            erreurs.append(f"{req.id}: identifiant dupliqué (ignoré)")
            continue
        seen.add(req.id)
        valides.append(req.model_dump())
    return valides, erreurs


def _raw_from_source(source: Union[str, Path, IO, None]) -> List[dict]:
    if source is None:
        source = DEFAULT_CORPUS
    if isinstance(source, (str, Path)):
        path = Path(source)
        if not path.exists():
            logger.warning("Corpus introuvable : %s", path)
            return []
        try:
            with path.open(encoding="utf-8") as fh:
                return _unwrap(json.load(fh))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.error("JSON de corpus illisible (%s) : %s", path, exc)
            return []
    # objet fichier (upload Streamlit)
    try:
        return _unwrap(json.load(source))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.error("JSON de corpus illisible : %s", exc)
        return []


def load_corpus(source: Union[str, Path, IO, None] = None) -> List[dict]:
    """Charge et valide un corpus ; renvoie uniquement les exigences valides."""
    valides, erreurs = validate_corpus(_raw_from_source(source))
    for err in erreurs:
        logger.warning("Corpus : %s", err)
    return valides


def load_corpus_report(source: Union[str, Path, IO, None] = None) -> Tuple[List[dict], List[str]]:
    """Comme ``load_corpus`` mais renvoie aussi la liste des erreurs (pour l'UI)."""
    return validate_corpus(_raw_from_source(source))


def load_many(sources: List[Union[str, Path, IO]]) -> Tuple[List[dict], List[str]]:
    """Fusionne plusieurs fichiers JSON en une matrice unique.

    Concatène les exigences de chaque source puis valide/déduplique l'ensemble :
    on obtient une seule matrice où un L1 d'un document peut référencer un L0
    d'un autre document (la traçabilité inter-documents devient un seul graphe).
    Renvoie (corpus_fusionné, erreurs).
    """
    raw_all: List[dict] = []
    errors: List[str] = []
    for src in sources:
        name = getattr(src, "name", str(src))
        raw = _raw_from_source(src)
        if not raw:
            errors.append(f"{name} : vide ou illisible")
            continue
        raw_all.extend(raw)
    valides, errs = validate_corpus(raw_all)  # dedup d'ids inter-documents inclus
    return valides, errors + errs


def save_corpus(corpus: List[dict], path: Union[str, Path] = DEFAULT_CORPUS) -> None:
    """Écrit le corpus de façon atomique (écriture dans un .tmp puis renommage)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": {"version": "6.0-impact-engine"}, "exigences": corpus}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
