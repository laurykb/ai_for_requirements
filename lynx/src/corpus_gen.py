"""Générateur de corpus XL : cycle en V (déclinaison + vérification).

Branche descendante : arbre d'architecture (éléments système) → une exigence
par élément (ids/parents/allocations/budgets cohérents). Branche montante :
exigences de vérification liées par VERIFIES au niveau vérifié. Prose verbeuse
par LLM local (repli gabarit). Sert à stresser LynX et à agrandir le golden set.
"""
from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List, Optional

from . import corpus_io, llm
from .config import DATA_DIR

# ── Catalogues portés par le corpus (lus par l'UI) ───────────────────────────
# 8 niveaux de DÉCLINAISON (branche descendante du V). La vérification est une
# branche à part (liens VERIFIES), pas un niveau plus profond.
NIVEAUX: List[dict] = [
    {"niveau": 0, "label": "Mission / Besoin"},
    {"niveau": 1, "label": "Système"},
    {"niveau": 2, "label": "Sous-système"},
    {"niveau": 3, "label": "Ensemble"},
    {"niveau": 4, "label": "Sous-ensemble"},
    {"niveau": 5, "label": "Équipement"},
    {"niveau": 6, "label": "Module"},
    {"niveau": 7, "label": "Composant"},
]
NIVEAU_TYPE = ["Besoin", "Système", "Sous-système", "Ensemble",
               "Sous-ensemble", "Équipement", "Module", "Composant"]

MODES: List[dict] = [
    {"id": "roulage", "label": "Roulage", "description": "Déplacement au sol avant/après vol"},
    {"id": "decollage", "label": "Décollage", "description": "Phase de décollage"},
    {"id": "montee", "label": "Montée", "description": "Prise d'altitude"},
    {"id": "croisiere", "label": "Croisière", "description": "Vol de croisière stabilisé"},
    {"id": "surveillance", "label": "Surveillance", "description": "Observation optronique sur zone"},
    {"id": "transmission", "label": "Transmission", "description": "Retour de données vers la station sol"},
    {"id": "retour", "label": "Retour", "description": "Trajet de retour vers la base"},
    {"id": "atterrissage", "label": "Atterrissage", "description": "Phase d'atterrissage"},
    {"id": "degrade", "label": "Mode dégradé", "description": "Fonctionnement sur panne partielle"},
    {"id": "maintenance", "label": "Maintenance", "description": "Au sol, hors mission"},
]
MODE_IDS = [m["id"] for m in MODES]

SOUS_SYSTEMES: List[dict] = [
    {"code": "PROP", "label": "Propulsion"},
    {"code": "STR", "label": "Structure"},
    {"code": "NAV", "label": "Navigation"},
    {"code": "LDD", "label": "Liaison de données"},
    {"code": "OPT", "label": "Charge utile optronique"},
]

SYS_LABEL = "Système de drone de surveillance optronique"
DEFAULT_FANOUT = [5, 3, 2, 2, 2, 1, 1]  # enfants par niveau parent 0..6


def _domaine(code: str) -> str:
    for s in SOUS_SYSTEMES:
        if s["code"] == code:
            return s["label"]
    return "Système"


def build_architecture(target: int = 350, max_depth: int = 7,
                       fanout: Optional[List[int]] = None) -> List[dict]:
    """Arbre d'éléments d'architecture (branche descendante), DFS pré-ordre.

    DFS : garantit d'atteindre ``max_depth`` (une branche complète) avant
    d'élargir. S'arrête dès que ``target`` éléments sont produits.
    """
    fanout = fanout or DEFAULT_FANOUT
    root = {"id": "AE-SYS", "niveau": 0, "code": "SYS",
            "label": SYS_LABEL, "parent": None, "domaine": "Système"}
    elems: List[dict] = [root]
    counters: Dict[str, int] = {}

    def expand(node: dict) -> None:
        d = node["niveau"]
        if d >= max_depth or len(elems) >= target:
            return
        k = fanout[d] if d < len(fanout) else 1
        for i in range(k):
            if len(elems) >= target:
                return
            code = (SOUS_SYSTEMES[i % len(SOUS_SYSTEMES)]["code"]
                    if d == 0 else node["code"])
            label = (SOUS_SYSTEMES[i % len(SOUS_SYSTEMES)]["label"]
                     if d == 0 else f"{NIVEAU_TYPE[d + 1]} {code}")
            seq = counters.get(code, 0) + 1
            counters[code] = seq
            child = {"id": f"AE-{code}-{seq:03d}", "niveau": d + 1, "code": code,
                     "label": label, "parent": node["id"], "domaine": _domaine(code)}
            elems.append(child)
            expand(child)  # DFS : descend d'abord

    expand(root)
    return elems


def _phase(niveau: int) -> str:
    if niveau <= 1:
        return "analyse_operationnelle"
    if niveau <= 3:
        return "analyse_fonctionnelle"
    return "conception"


def _modes_pour(niveau: int, seq: int) -> List[str]:
    """Sous-ensemble déterministe de modes (2 à 3), tiré du catalogue."""
    n = 2 + (seq % 2)
    start = (niveau + seq) % len(MODE_IDS)
    return [MODE_IDS[(start + j) % len(MODE_IDS)] for j in range(n)]


def derive_requirements(elems: List[dict]) -> List[dict]:
    """Une exigence de déclinaison par élément d'architecture (mapping 1:1)."""
    ae_to_req: Dict[str, str] = {}
    counters: Dict[tuple, int] = {}
    reqs: List[dict] = []
    for e in elems:
        code, niveau = e["code"], e["niveau"]
        seq = counters.get((niveau, code), 0) + 1
        counters[(niveau, code)] = seq
        rid = f"REQ-L{niveau}-{code}-{seq:03d}"
        ae_to_req[e["id"]] = rid
        parent_req = ae_to_req.get(e["parent"]) if e["parent"] else None
        reqs.append({
            "id": rid, "niveau": niveau, "type": NIVEAU_TYPE[niveau],
            "domaine": e["domaine"], "texte": "", "parent_id": parent_req,
            "test_status": "PENDING",
            "alloue_a": [e["id"]],
            "contexte_operationnel": _modes_pour(niveau, seq),
            "base_derivation": {
                "phase": _phase(niveau),
                "justification": f"Déclinaison de {e['label']} issue de la {_phase(niveau).replace('_', ' ')}.",
                "ref": f"AF-{len(reqs) + 1:04d}"},
            "_element": e,
        })
    return reqs


_IADT = ["I", "A", "D", "T"]  # Inspection / Analyse / Démonstration / Test


def derive_verifications(spec_reqs: List[dict], stride: int = 2) -> List[dict]:
    """Branche montante du V : une exigence de vérification pour un sous-ensemble
    d'exigences de spécification (une sur ``stride``), liée par VERIFIES.

    Le niveau de la vérification = niveau de l'exigence vérifiée (symétrie du V) ;
    elle n'appartient pas à l'arbre de décomposition (``parent_id`` None).
    """
    verifs: List[dict] = []
    counters: Dict[tuple, int] = {}
    for i, r in enumerate(spec_reqs):
        if i % stride != 0:
            continue
        # convention d'id : réutilise le code (domaine) de la cible
        code = r["id"].split("-")[2] if r["id"].count("-") >= 2 else "GEN"
        niveau = r["niveau"]
        seq = counters.get((niveau, code), 0) + 1
        counters[(niveau, code)] = seq
        methode = _IADT[(niveau + seq) % len(_IADT)]
        verifs.append({
            "id": f"VER-L{niveau}-{code}-{seq:03d}",
            "niveau": niveau, "type": "Vérification", "domaine": r["domaine"],
            "texte": "", "parent_id": None, "test_status": "PENDING",
            "verification": methode,
            "links": [{"type": "VERIFIES", "target": r["id"]}],
            "contexte_operationnel": r["contexte_operationnel"],
            "_verifie": r,  # travail interne (retiré à l'assemblage)
        })
    return verifs
