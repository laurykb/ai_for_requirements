# Corpus XL réaliste + hiérarchie dynamique (cycle en V) — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Générer un corpus d'ingénierie réaliste (~500 exigences verbeuses suivant le cycle en V : déclinaison descendante + vérification montante) et rendre la hiérarchie de niveaux dynamique, pour stresser LynX tel quel et documenter ses limites.

**Architecture:** Un générateur Python construit d'abord un arbre d'architecture déterministe (branche descendante : ids/parents/allocations/budgets cohérents), puis une branche de vérification (exigences liées par `VERIFIES` au niveau qu'elles vérifient), puis fait rédiger l'énoncé verbeux de chaque exigence par le LLM local (repli gabarit). Le modèle perd son plafond de niveau ; l'UI dérive niveaux, couleurs et libellés depuis le JSON chargé.

**Tech Stack:** Python 3.12 + Pydantic v2 (backend `lynx/src`), pytest ; Next.js 16 + React + Tailwind v4 + @xyflow/react + react-force-graph-3d (front `web/`).

## Global Constraints

- Backend testé avec `../.venv/bin/python -m pytest` depuis `lynx/` ; imports `from src import ...`.
- Le corpus de démo `lynx/corpus/corpus.json` (34 exigences, L0–L5) NE doit PAS être modifié ni régresser visuellement (couleurs/libellés identiques quand le niveau max vaut 5).
- Aucun câblage des champs enrichis dans les analyzers/`short()`/le débat : ils restent des données passives (le loader Pydantic les laisse tomber — comportement attendu).
- **Cycle en V** : la déclinaison est la branche descendante (`parent_id`, DERIVE) ; la vérification est la branche montante (liens `VERIFIES`, `niveau` = niveau vérifié). Le « test » n'est PAS un niveau plus profond.
- Front : pas de runner de test ; vérification par `npx eslint src --max-warnings 0` + `npx tsc --noEmit` (exit 0) + capture d'écran + sanity-check `node`.
- Commits fréquents, un par tâche. Messages en français, terminés par `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Redémarrer l'API après tout changement Python (uvicorn sans `--reload`) : commandes séparées (pkill puis relance).

---

### Task 1 : Supprimer le plafond de niveau du modèle

**Files:**
- Modify: `lynx/src/models.py` (champ `niveau` de `Requirement`, ~ligne 79)
- Test: `lynx/tests/test_engine.py` (ajout en fin de fichier)

**Interfaces:**
- Produces: `Requirement(niveau=n)` valide pour tout `n >= 0` (plus de borne haute).

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à la fin de `lynx/tests/test_engine.py` :

```python
def test_requirement_accepte_niveau_profond():
    """Le modèle accepte une profondeur > 5 (hiérarchie dynamique)."""
    import pytest as _pytest
    from pydantic import ValidationError
    from src.models import Requirement
    r = Requirement(id="REQ-L7-PROP-001", niveau=7, texte="x")
    assert r.niveau == 7
    with _pytest.raises(ValidationError):
        Requirement(id="X", niveau=-1)
```

- [ ] **Step 2 : Lancer le test, vérifier l'échec**

Run: `cd lynx && ../.venv/bin/python -m pytest tests/test_engine.py::test_requirement_accepte_niveau_profond -q`
Expected: FAIL (ValidationError : `niveau` doit être ≤ 5).

- [ ] **Step 3 : Retirer la borne haute**

Dans `lynx/src/models.py`, remplacer :

```python
    niveau: int = Field(..., ge=0, le=5, description="Profondeur L0..L5")
```

par :

```python
    niveau: int = Field(..., ge=0, description="Profondeur L0..Ln (dérivée du corpus)")
```

- [ ] **Step 4 : Lancer le test, vérifier le succès**

Run: `cd lynx && ../.venv/bin/python -m pytest tests/test_engine.py::test_requirement_accepte_niveau_profond -q`
Expected: PASS.

- [ ] **Step 5 : Non-régression de la suite**

Run: `cd lynx && ../.venv/bin/python -m pytest -q`
Expected: tout PASS.

- [ ] **Step 6 : Commit**

```bash
git add lynx/src/models.py lynx/tests/test_engine.py
git commit -m "feat(lynx): plafond de niveau supprimé (hiérarchie dérivée du corpus)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2 : Générateur — catalogues + arbre d'architecture (branche descendante)

**Files:**
- Create: `lynx/src/corpus_gen.py`
- Test: `lynx/tests/test_corpus_gen.py`

**Interfaces:**
- Produces:
  - `NIVEAUX: list[dict]` (`{niveau, label}`, 8 niveaux de déclinaison 0..7, PAS de niveau « Test »)
  - `NIVEAU_TYPE: list[str]`, `MODES: list[dict]`, `MODE_IDS: list[str]`, `SOUS_SYSTEMES: list[dict]`
  - `build_architecture(target: int = 350, max_depth: int = 7, fanout: list[int] | None = None) -> list[dict]`
    → éléments `{id, niveau, code, label, parent, domaine}` ; racine `id="AE-SYS"`, `parent=None`.

- [ ] **Step 1 : Écrire les tests qui échouent**

Créer `lynx/tests/test_corpus_gen.py` :

```python
from src import corpus_gen


def test_architecture_arbre_valide_et_profond():
    elems = corpus_gen.build_architecture()
    ids = {e["id"] for e in elems}
    racines = [e for e in elems if e["parent"] is None]
    assert len(racines) == 1 and racines[0]["id"] == "AE-SYS"
    by_id = {e["id"]: e for e in elems}
    for e in elems:
        if e["parent"] is not None:
            assert e["parent"] in ids
            assert e["niveau"] == by_id[e["parent"]]["niveau"] + 1
    assert len(ids) == len(elems)                    # ids uniques
    assert max(e["niveau"] for e in elems) == 7      # profondeur atteinte
    assert 250 <= len(elems) <= 500                  # branche descendante ~350


def test_catalogues_coherents():
    # 8 niveaux de DÉCLINAISON (aucun « Test » : la vérification est une branche à part)
    assert [n["niveau"] for n in corpus_gen.NIVEAUX] == list(range(8))
    assert all("test" not in n["label"].lower() for n in corpus_gen.NIVEAUX)
    assert all("id" in m and "label" in m for m in corpus_gen.MODES)
    assert len(corpus_gen.SOUS_SYSTEMES) >= 5
```

- [ ] **Step 2 : Lancer, vérifier l'échec**

Run: `cd lynx && ../.venv/bin/python -m pytest tests/test_corpus_gen.py -q`
Expected: FAIL (`ModuleNotFoundError: src.corpus_gen`).

- [ ] **Step 3 : Écrire le module (catalogues + arbre descendant)**

Créer `lynx/src/corpus_gen.py` :

```python
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
```

- [ ] **Step 4 : Lancer, vérifier le succès**

Run: `cd lynx && ../.venv/bin/python -m pytest tests/test_corpus_gen.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5 : Commit**

```bash
git add lynx/src/corpus_gen.py lynx/tests/test_corpus_gen.py
git commit -m "feat(lynx): générateur corpus XL — catalogues + arbre d'architecture

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3 : Générateur — exigences de déclinaison (branche descendante)

**Files:**
- Modify: `lynx/src/corpus_gen.py`
- Test: `lynx/tests/test_corpus_gen.py`

**Interfaces:**
- Consumes: `build_architecture()`, `MODE_IDS`, `NIVEAU_TYPE`
- Produces: `derive_requirements(elems: list[dict]) -> list[dict]` → une exigence de déclinaison
  par élément : `{id, niveau, type, domaine, texte:"", parent_id, test_status, alloue_a,
  contexte_operationnel, base_derivation, _element}`. Id `REQ-L{niveau}-{code}-{num}` ;
  `parent_id` = id d'exigence de l'élément parent.

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à `lynx/tests/test_corpus_gen.py` :

```python
def test_derive_requirements_structure_coherente():
    elems = corpus_gen.build_architecture()
    reqs = corpus_gen.derive_requirements(elems)
    assert len(reqs) == len(elems)
    ids = {r["id"] for r in reqs}
    by_id = {r["id"]: r for r in reqs}
    for r in reqs:
        if r["parent_id"] is not None:
            assert r["parent_id"] in ids
            assert r["niveau"] == by_id[r["parent_id"]]["niveau"] + 1
        assert r["alloue_a"] and isinstance(r["alloue_a"], list)
        assert set(r["contexte_operationnel"]) <= set(corpus_gen.MODE_IDS)
        assert r["base_derivation"]["phase"]
    assert all(r["id"].startswith("REQ-L") for r in reqs)
    assert sum(1 for r in reqs if r["parent_id"] is None) == 1
```

- [ ] **Step 2 : Lancer, vérifier l'échec**

Run: `cd lynx && ../.venv/bin/python -m pytest tests/test_corpus_gen.py::test_derive_requirements_structure_coherente -q`
Expected: FAIL (`derive_requirements` non défini).

- [ ] **Step 3 : Implémenter la dérivation**

Ajouter à `lynx/src/corpus_gen.py` :

```python
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
```

- [ ] **Step 4 : Lancer, vérifier le succès**

Run: `cd lynx && ../.venv/bin/python -m pytest tests/test_corpus_gen.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5 : Commit**

```bash
git add lynx/src/corpus_gen.py lynx/tests/test_corpus_gen.py
git commit -m "feat(lynx): exigences de déclinaison 1:1 depuis l'architecture (branche descendante)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4 : Générateur — branche de vérification (cycle en V, liens VERIFIES)

**Files:**
- Modify: `lynx/src/corpus_gen.py`
- Test: `lynx/tests/test_corpus_gen.py`

**Interfaces:**
- Consumes: `derive_requirements()` (exigences de spécification)
- Produces: `derive_verifications(spec_reqs: list[dict], stride: int = 2) -> list[dict]`
  → exigences de vérification `{id, niveau, type:"Vérification", domaine, texte:"",
  parent_id:None, test_status, verification, links:[{type:"VERIFIES", target:<spec_id>}],
  contexte_operationnel}`. `niveau` = niveau de l'exigence vérifiée (symétrie du V).
  Id `VER-L{niveau}-{code}-{num}`.

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à `lynx/tests/test_corpus_gen.py` :

```python
def test_verifications_cycle_en_v():
    elems = corpus_gen.build_architecture()
    spec = corpus_gen.derive_requirements(elems)
    spec_ids = {r["id"] for r in spec}
    spec_by_id = {r["id"]: r for r in spec}
    verifs = corpus_gen.derive_verifications(spec, stride=2)
    assert 0 < len(verifs) < len(spec)          # sous-ensemble
    vids = {v["id"] for v in verifs}
    assert len(vids) == len(verifs)             # ids uniques
    for v in verifs:
        assert v["type"] == "Vérification"
        assert v["parent_id"] is None            # hors arbre de décomposition
        assert v["verification"] in ("I", "A", "D", "T")
        liens = v["links"]
        assert len(liens) == 1 and liens[0]["type"] == "VERIFIES"
        cible = liens[0]["target"]
        assert cible in spec_ids                 # cible une vraie exigence
        assert v["niveau"] == spec_by_id[cible]["niveau"]  # symétrie du V
        assert v["id"].startswith("VER-L")
```

- [ ] **Step 2 : Lancer, vérifier l'échec**

Run: `cd lynx && ../.venv/bin/python -m pytest tests/test_corpus_gen.py::test_verifications_cycle_en_v -q`
Expected: FAIL (`derive_verifications` non défini).

- [ ] **Step 3 : Implémenter la branche de vérification**

Ajouter à `lynx/src/corpus_gen.py` :

```python
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
```

- [ ] **Step 4 : Lancer, vérifier le succès**

Run: `cd lynx && ../.venv/bin/python -m pytest tests/test_corpus_gen.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5 : Commit**

```bash
git add lynx/src/corpus_gen.py lynx/tests/test_corpus_gen.py
git commit -m "feat(lynx): branche de vérification (cycle en V, liens VERIFIES)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5 : Générateur — grandeurs typées avec roll-up des budgets

**Files:**
- Modify: `lynx/src/corpus_gen.py`
- Test: `lynx/tests/test_corpus_gen.py`

**Interfaces:**
- Consumes: `derive_requirements()` (exigences de déclinaison uniquement — PAS les vérifications)
- Produces: `attach_budgets(reqs: list[dict], budget_racine_kg: float = 25.0) -> None`
  (mutation in place : ajoute `grandeurs=[{grandeur:"masse", operateur:"<=", valeur, unite:"kg", mode:None, tolerance}]`,
  masse d'un parent = somme des enfants).

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à `lynx/tests/test_corpus_gen.py` :

```python
def test_budgets_bouclent():
    elems = corpus_gen.build_architecture()
    reqs = corpus_gen.derive_requirements(elems)
    corpus_gen.attach_budgets(reqs)
    by_id = {r["id"]: r for r in reqs}
    enfants = {}
    for r in reqs:
        if r["parent_id"]:
            enfants.setdefault(r["parent_id"], []).append(r)

    def masse(r):
        return next(g["valeur"] for g in r["grandeurs"] if g["grandeur"] == "masse")

    for pid, kids in enfants.items():
        assert abs(masse(by_id[pid]) - sum(masse(k) for k in kids)) < 1e-6
    assert all(masse(r) > 0 for r in reqs)
```

- [ ] **Step 2 : Lancer, vérifier l'échec**

Run: `cd lynx && ../.venv/bin/python -m pytest tests/test_corpus_gen.py::test_budgets_bouclent -q`
Expected: FAIL (`attach_budgets` non défini).

- [ ] **Step 3 : Implémenter le roll-up**

Ajouter à `lynx/src/corpus_gen.py` :

```python
def attach_budgets(reqs: List[dict], budget_racine_kg: float = 25.0) -> None:
    """Alloue une masse à chaque exigence de déclinaison : parent = somme des
    enfants (réparti à parts égales en descendant depuis la racine)."""
    enfants: Dict[str, List[dict]] = {}
    for r in reqs:
        if r["parent_id"]:
            enfants.setdefault(r["parent_id"], []).append(r)

    budgets: Dict[str, float] = {}

    def _set(rid: str, valeur: float) -> None:
        budgets[rid] = valeur
        kids = enfants.get(rid, [])
        if kids:
            part = valeur / len(kids)
            for k in kids:
                _set(k["id"], part)

    racine = next(r for r in reqs if r["parent_id"] is None)
    _set(racine["id"], budget_racine_kg)

    for r in reqs:
        r["grandeurs"] = [{
            "grandeur": "masse", "operateur": "<=",
            "valeur": round(budgets[r["id"]], 4), "unite": "kg",
            "mode": None, "tolerance": round(budgets[r["id"]] * 0.05, 4)}]
```

- [ ] **Step 4 : Lancer, vérifier le succès**

Run: `cd lynx && ../.venv/bin/python -m pytest tests/test_corpus_gen.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5 : Commit**

```bash
git add lynx/src/corpus_gen.py lynx/tests/test_corpus_gen.py
git commit -m "feat(lynx): grandeurs typées avec roll-up de budget (parent=Σ enfants)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6 : Générateur — rédaction verbeuse (déclinaison + vérification, LLM + repli)

**Files:**
- Modify: `lynx/src/corpus_gen.py`
- Test: `lynx/tests/test_corpus_gen.py`

**Interfaces:**
- Consumes: `llm.call_agent`, une fiche d'exigence
- Produces:
  - `fiche_prose(req: dict) -> dict`
  - `texte_gabarit(req: dict) -> str` (repli déterministe, verbeux ; branche selon `type`)
  - `rediger(req: dict, use_llm: bool = True) -> str`

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à `lynx/tests/test_corpus_gen.py` :

```python
def test_redaction_gabarit_verbeux_sans_llm():
    elems = corpus_gen.build_architecture()
    reqs = corpus_gen.derive_requirements(elems)
    corpus_gen.attach_budgets(reqs)
    r = reqs[10]
    txt = corpus_gen.rediger(r, use_llm=False)
    assert isinstance(txt, str) and len(txt) > 120 and "kg" in txt
    # une exigence de vérification se rédige aussi (sans grandeurs)
    verifs = corpus_gen.derive_verifications(reqs, stride=2)
    vtxt = corpus_gen.rediger(verifs[0], use_llm=False)
    assert len(vtxt) > 120 and "vérif" in vtxt.lower()
    fiche = corpus_gen.fiche_prose(r)
    assert fiche["id"] == r["id"] and "domaine" in fiche
```

- [ ] **Step 2 : Lancer, vérifier l'échec**

Run: `cd lynx && ../.venv/bin/python -m pytest tests/test_corpus_gen.py::test_redaction_gabarit_verbeux_sans_llm -q`
Expected: FAIL (`rediger` non défini).

- [ ] **Step 3 : Implémenter fiche + gabarit + rédaction (2 branches)**

Ajouter à `lynx/src/corpus_gen.py` :

```python
_PROSE_SYSTEM = (
    "Tu es ingénieur système. À partir de la fiche structurée d'une exigence, "
    "rédige son ÉNONCÉ en français : 3 à 5 phrases, précis et vérifiable, "
    "intégrant les contraintes et conditions fournies. N'invente pas de valeurs "
    'hors fiche. Réponds STRICTEMENT en JSON : {"texte": "<énoncé>"}'
)


def fiche_prose(req: dict) -> dict:
    if req.get("type") == "Vérification":
        cible = req.get("_verifie") or {}
        return {"id": req["id"], "type": "Vérification", "domaine": req["domaine"],
                "verifie": req["links"][0]["target"],
                "cible_texte": (cible.get("texte") or "")[:200],
                "methode": {"I": "inspection", "A": "analyse",
                            "D": "démonstration", "T": "essai"}.get(req.get("verification"), "essai"),
                "modes": ", ".join(req.get("contexte_operationnel") or [])}
    g = (req.get("grandeurs") or [{}])[0]
    return {"id": req["id"], "type": req["type"], "domaine": req["domaine"],
            "element_alloue": (req.get("alloue_a") or ["?"])[0],
            "contrainte": f"{g.get('grandeur')} {g.get('operateur')} "
                          f"{g.get('valeur')} {g.get('unite')} (± {g.get('tolerance')})",
            "modes": ", ".join(req.get("contexte_operationnel") or []),
            "phase": (req.get("base_derivation") or {}).get("phase", "conception")}


def texte_gabarit(req: dict) -> str:
    f = fiche_prose(req)
    if req.get("type") == "Vérification":
        return (
            f"Il sera vérifié par {f['methode']} que l'exigence {f['verifie']} est "
            f"satisfaite dans les conditions opérationnelles concernées ({f['modes']}). "
            f"La vérification porte sur le {f['domaine'].lower()} et couvre l'énoncé "
            f"suivant : « {f['cible_texte']} ». Le résultat est tracé et conditionne "
            f"l'acceptation de l'exigence vérifiée ; tout écart ouvre une non-conformité."
        )
    return (
        f"Dans le cadre du {f['domaine'].lower()}, l'exigence porte sur l'élément "
        f"{f['element_alloue']} du système de drone de surveillance. L'élément doit "
        f"respecter la contrainte {f['contrainte']} sur l'ensemble des conditions "
        f"opérationnelles concernées ({f['modes']}). Issue de la "
        f"{f['phase'].replace('_', ' ')}, cette exigence décline l'exigence de niveau "
        f"supérieur et doit être vérifiable par analyse ou essai. Toute dérogation à la "
        f"valeur allouée doit être justifiée et re-tracée."
    )


def rediger(req: dict, use_llm: bool = True) -> str:
    if use_llm:
        resp = llm.call_agent(_PROSE_SYSTEM, fiche_prose(req), label="gen_prose")
        txt = (resp or {}).get("texte") if isinstance(resp, dict) else None
        if txt and len((txt or "").strip()) > 60:
            return txt.strip()
    return texte_gabarit(req)
```

- [ ] **Step 4 : Lancer, vérifier le succès**

Run: `cd lynx && ../.venv/bin/python -m pytest tests/test_corpus_gen.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5 : Commit**

```bash
git add lynx/src/corpus_gen.py lynx/tests/test_corpus_gen.py
git commit -m "feat(lynx): rédaction verbeuse (déclinaison + vérification, LLM + repli gabarit)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7 : Générateur — assemblage, validation, écriture, CLI

**Files:**
- Modify: `lynx/src/corpus_gen.py`
- Create: `scripts/gen_corpus_xl.py`
- Test: `lynx/tests/test_corpus_gen.py`

**Interfaces:**
- Produces:
  - `build_corpus(target=350, stride=2, use_llm=True) -> dict` → `{meta, niveaux,
    modes_operationnels, architecture, exigences}` ; exigences = déclinaison + vérification,
    sans champ interne (`_element`/`_verifie` retirés).
  - `write_corpus(corpus, path=None) -> Path` (défaut `DATA_DIR/"corpus_xl.json"`).
  - CLI `scripts/gen_corpus_xl.py`.

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à `lynx/tests/test_corpus_gen.py` :

```python
def test_build_corpus_valide_par_corpus_io():
    from src import corpus_io
    corpus = corpus_gen.build_corpus(target=120, use_llm=False)
    assert set(corpus) >= {"meta", "niveaux", "modes_operationnels", "architecture", "exigences"}
    exs = corpus["exigences"]
    assert all("_element" not in r and "_verifie" not in r for r in exs)
    assert all(len(r["texte"]) > 120 for r in exs)
    # les deux branches sont présentes
    assert any(r["type"] == "Vérification" for r in exs)
    assert any(r["type"] != "Vérification" for r in exs)
    # validation par le pipeline réel : zéro erreur structurelle
    valides, erreurs = corpus_io.validate_corpus(exs)
    assert erreurs == [] and len(valides) == len(exs)
```

- [ ] **Step 2 : Lancer, vérifier l'échec**

Run: `cd lynx && ../.venv/bin/python -m pytest tests/test_corpus_gen.py::test_build_corpus_valide_par_corpus_io -q`
Expected: FAIL (`build_corpus` non défini).

- [ ] **Step 3 : Implémenter assemblage + écriture + CLI**

Ajouter à `lynx/src/corpus_gen.py` :

```python
def build_corpus(target: int = 350, stride: int = 2, use_llm: bool = True) -> dict:
    elems = build_architecture(target=target)
    spec = derive_requirements(elems)
    attach_budgets(spec)
    verifs = derive_verifications(spec, stride=stride)
    reqs = spec + verifs
    for r in reqs:
        r["texte"] = rediger(r, use_llm=use_llm)
        r.pop("_element", None)
        r.pop("_verifie", None)
    return {
        "meta": {"systeme": SYS_LABEL, "n": len(reqs),
                 "n_declinaison": len(spec), "n_verification": len(verifs),
                 "genere_par": "corpus_gen", "profondeur": max(r["niveau"] for r in reqs)},
        "niveaux": NIVEAUX,
        "modes_operationnels": MODES,
        "architecture": elems,
        "exigences": reqs,
    }


def write_corpus(corpus: dict, path: Optional[Any] = None):
    from pathlib import Path
    dest = Path(path) if path else (DATA_DIR / "corpus_xl.json")
    dest.write_text(json.dumps(corpus, ensure_ascii=False, indent=1), encoding="utf-8")
    return dest


def main() -> None:
    ap = argparse.ArgumentParser(description="Génère un corpus XL d'exigences (cycle en V).")
    ap.add_argument("--target", type=int, default=350, help="taille de la branche descendante")
    ap.add_argument("--stride", type=int, default=2, help="1 vérification pour N exigences")
    ap.add_argument("--no-llm", action="store_true", help="repli gabarit (pas d'appel LLM)")
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args()
    corpus = build_corpus(target=args.target, stride=args.stride, use_llm=not args.no_llm)
    dest = write_corpus(corpus, args.out)
    m = corpus["meta"]
    print(f"{m['n']} exigences ({m['n_declinaison']} déclinaison + "
          f"{m['n_verification']} vérification) → {dest}")


if __name__ == "__main__":
    main()
```

Créer `scripts/gen_corpus_xl.py` :

```python
#!/usr/bin/env python
"""Point d'entrée : génère lynx/corpus/corpus_xl.json.

Usage :  .venv/bin/python scripts/gen_corpus_xl.py [--target 350] [--stride 2] [--no-llm]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lynx"))
from src.corpus_gen import main  # noqa: E402

if __name__ == "__main__":
    main()
```

- [ ] **Step 4 : Lancer, vérifier le succès**

Run: `cd lynx && ../.venv/bin/python -m pytest tests/test_corpus_gen.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5 : Commit**

```bash
git add lynx/src/corpus_gen.py scripts/gen_corpus_xl.py lynx/tests/test_corpus_gen.py
git commit -m "feat(lynx): assemblage/validation/écriture du corpus XL (V) + CLI

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8 : Générer le vrai corpus_xl.json

**Files:**
- Create: `lynx/corpus/corpus_xl.json` (produit, non écrit à la main)

**Interfaces:** aucune (étape d'exécution + vérification).

- [ ] **Step 1 : Vérifier qu'Ollama répond (sinon repli gabarit)**

Run: `curl -s -o /dev/null -w '%{http_code}\n' localhost:11434/api/tags`
Expected: `200` (LLM dispo). Sinon, ajouter `--no-llm` à l'étape suivante.

- [ ] **Step 2 : Générer (arrière-plan, ~500 appels LLM → plusieurs minutes)**

Run: `cd /home/marsattacks/Documents/AI_for_ssh && .venv/bin/python scripts/gen_corpus_xl.py --target 350 --stride 2`
Expected: `NNN exigences (~350 déclinaison + ~175 vérification) → …/lynx/corpus/corpus_xl.json`.

- [ ] **Step 3 : Vérifier le fichier produit**

Run:
```bash
cd lynx && ../.venv/bin/python -c "
import json
c=json.load(open('corpus/corpus_xl.json'))
from collections import Counter
print('n=', c['meta']['n'], 'profondeur=', c['meta']['profondeur'])
print('branches:', c['meta']['n_declinaison'],'décl. +',c['meta']['n_verification'],'vérif.')
print('niveaux=', dict(Counter(r['niveau'] for r in c['exigences'])))
print('texte moyen=', sum(len(r['texte']) for r in c['exigences'])//len(c['exigences']),'car.')
# cycle en V : les liens VERIFIES pointent des cibles réelles
ids={r['id'] for r in c['exigences']}
vlinks=[l for r in c['exigences'] for l in (r.get('links') or []) if l['type']=='VERIFIES']
print('liens VERIFIES:', len(vlinks), '— cibles valides:', all(l['target'] in ids for l in vlinks))
"
```
Expected: `n ≈ 525`, `profondeur = 7`, niveaux répartis sur 0..7, texte moyen > 200 car., liens VERIFIES présents et tous valides.

- [ ] **Step 4 : Commit**

```bash
git add lynx/corpus/corpus_xl.json
git commit -m "data(lynx): corpus XL généré (cycle en V, ~525 exigences verbeuses, 8 niveaux)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9 : UI — helper de hiérarchie dynamique (couleurs + libellés)

**Files:**
- Create: `web/src/components/req-levels.ts`

**Interfaces:**
- Produces (TS) : `type NiveauCat`, `maxNiveau(reqs)`, `couleurNiveau(n, nMax)`,
  `libelleNiveau(n, cat?)`, plus `BASE_COLORS`/`BASE_LABELS` (non-régression).

- [ ] **Step 1 : Écrire le helper**

Créer `web/src/components/req-levels.ts` :

```ts
/** Hiérarchie de niveaux dérivée du corpus (plus de tableaux figés).
 * Non-régression : L0–L5 gardent exactement les teintes et libellés d'origine ;
 * au-delà, une échelle de teintes déterministe prend le relais. */

export type NiveauCat = { niveau: number; label: string };

export const BASE_COLORS = ["#818cf8", "#60a5fa", "#22d3ee", "#34d399", "#fbbf24", "#fb7185"];
export const BASE_LABELS = ["L0 · Besoin", "L1 · Système", "L2 · Sous-système",
                            "L3 · Composant", "L4 · Configuration", "L5 · Test"];

export function maxNiveau(reqs: { niveau: number }[]): number {
  return reqs.reduce((m, r) => Math.max(m, r.niveau ?? 0), 0);
}

/** Couleur d'un niveau : hex d'origine pour n<6, sinon teinte HSL répartie. */
export function couleurNiveau(n: number, nMax: number): string {
  if (n < BASE_COLORS.length) return BASE_COLORS[n];
  const extra = Math.max(1, nMax - BASE_COLORS.length + 1);
  const t = (n - BASE_COLORS.length + 1) / extra;      // 0..1
  const hue = Math.round(280 - 200 * t);               // violet → cyan
  return `hsl(${hue} 70% 65%)`;
}

/** Libellé d'un niveau : catalogue du corpus si présent, sinon repli. */
export function libelleNiveau(n: number, cat?: NiveauCat[]): string {
  const c = cat?.find((e) => e.niveau === n);
  if (c) return `L${n} · ${c.label}`;
  if (n < BASE_LABELS.length) return BASE_LABELS[n];
  return `L${n}`;
}
```

- [ ] **Step 2 : Sanity-check des fonctions pures (pas de runner de test front)**

Run:
```bash
cd web && node --input-type=module -e "
const BASE=['#818cf8','#60a5fa','#22d3ee','#34d399','#fbbf24','#fb7185'];
const couleur=(n,nMax)=>{if(n<BASE.length)return BASE[n];const e=Math.max(1,nMax-BASE.length+1);const t=(n-BASE.length+1)/e;return 'hsl('+Math.round(280-200*t)+' 70% 65%)';};
const libelle=(n)=>{const L=['L0 · Besoin','L1 · Système','L2 · Sous-système','L3 · Composant','L4 · Configuration','L5 · Test'];return n<L.length?L[n]:'L'+n;};
console.assert(couleur(3,5)===BASE[3],'L3 doit garder son hex');
console.assert(couleur(7,7).startsWith('hsl'),'L7 doit être en HSL');
console.assert(libelle(1)==='L1 · Système','repli L1');
console.assert(libelle(9)==='L9','repli générique L9');
console.log('OK req-levels');
"
```
Expected: `OK req-levels`.

- [ ] **Step 3 : Typecheck**

Run: `cd web && npx tsc --noEmit`
Expected: exit 0.

- [ ] **Step 4 : Commit**

```bash
git add web/src/components/req-levels.ts
git commit -m "feat(web): helper de hiérarchie de niveaux dérivée du corpus

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10 : UI — brancher la vue 2D (`req-graph.tsx`) sur la hiérarchie dynamique

**Files:**
- Modify: `web/src/components/req-graph.tsx`

**Interfaces:**
- Consumes: `couleurNiveau`, `libelleNiveau`, `maxNiveau`, `type NiveauCat` depuis `req-levels`.
- Produces: `NIVEAU_COLORS`/`LEVEL_LABELS` supprimés ; couleur portée par la donnée du nœud.

- [ ] **Step 1 : Retirer les tableaux figés, importer le helper**

Dans `web/src/components/req-graph.tsx`, remplacer :

```ts
// Couleurs par niveau L0..L5 (héritées de LynX, éclaircies pour le fond nuit).
// Hex bruts requis : consommées aussi par three.js (vue 3D), qui ne résout
// pas les var() CSS. Source unique pour les deux vues.
export const NIVEAU_COLORS = ["#818cf8", "#60a5fa", "#22d3ee", "#34d399", "#fbbf24", "#fb7185"];
export const LEVEL_LABELS = ["L0 · Besoin", "L1 · Système", "L2 · Sous-système",
                             "L3 · Composant", "L4 · Configuration", "L5 · Test"];
```

par :

```ts
import { couleurNiveau, libelleNiveau, maxNiveau, type NiveauCat } from "@/components/req-levels";
```

- [ ] **Step 2 : Couleur portée par la donnée du nœud**

Adapter `type ReqNodeData` en `{ rid: string; niveau: number; couleur: string; state: NodeState }`.
Dans le `useMemo` qui construit les `Node[]` (calcul `const nMax = maxNiveau(corpus);`), poser
`couleur: couleurNiveau(r.niveau, nMax)` dans `data`. Dans `ReqNode`, remplacer
`style={{ background: NIVEAU_COLORS[d.niveau] }}` par `style={{ background: d.couleur }}`.

- [ ] **Step 3 : Étiquettes de niveau via `libelleNiveau`**

Là où `LEVEL_LABELS[...]` alimentait les `LevelNode`, remplacer par `libelleNiveau(niveau, niveaux)`
(prop `niveaux?: NiveauCat[]` ajoutée à Task 12 ; en attendant `libelleNiveau(niveau)` — repli).
La couleur de pastille du `LevelNode` passe aussi par `couleurNiveau(niveau, nMax)`.

- [ ] **Step 4 : Lint + typecheck**

Run: `cd web && npx eslint src --max-warnings 0 && npx tsc --noEmit`
Expected: exit 0. Corriger tout usage restant de `NIVEAU_COLORS`/`LEVEL_LABELS` signalé par tsc.

- [ ] **Step 5 : Vérification visuelle (non-régression démo)**

Run:
```bash
S=/tmp/claude-1000/-home-marsattacks/712f120c-5332-4eba-8c0f-e101194426cc/scratchpad
/home/marsattacks/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome --headless=new --disable-gpu --no-sandbox --window-size=1440,1000 --virtual-time-budget=9000 --screenshot=$S/t10-2d.png http://localhost:3000/requirements 2>/dev/null; echo done
```
Lire `$S/t10-2d.png` : la matrice de démo (L0–L5) s'affiche avec les MÊMES couleurs/légende qu'avant.

- [ ] **Step 6 : Commit**

```bash
git add web/src/components/req-graph.tsx
git commit -m "feat(web): vue 2D branchée sur la hiérarchie dynamique (couleurs/libellés dérivés)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11 : UI — brancher la vue 3D (`req-graph-3d.tsx`) sur `nMax`

**Files:**
- Modify: `web/src/components/req-graph-3d.tsx`

**Interfaces:**
- Consumes: `couleurNiveau`, `maxNiveau`, `libelleNiveau` depuis `req-levels` ; `STATE_HEX`, `type Req` depuis `req-graph`.
- Produces: espacement des couches sur `nMax` détecté ; couleur de niveau via `couleurNiveau`.

- [ ] **Step 1 : Remplacer imports et clamp figés**

Dans `web/src/components/req-graph-3d.tsx` :
- Importer `couleurNiveau, maxNiveau, libelleNiveau` de `@/components/req-levels` ; retirer `NIVEAU_COLORS, LEVEL_LABELS` de l'import `req-graph` (garder `STATE_HEX`, `type Req`).
- Dans le `useMemo` des données : `const nMax = maxNiveau(corpus);`.
- Remplacer `const lvl = Math.max(0, Math.min(5, r.niveau ?? 0));` par `const lvl = Math.max(0, Math.min(nMax, r.niveau ?? 0));`.
- Remplacer l'espacement `fy: 220 - lvl * 88` par :

```ts
        fy: 220 - lvl * (nMax > 0 ? 440 / nMax : 88),
```

- [ ] **Step 2 : Couleur de nœud et libellé via helper**

Remplacer `NIVEAU_COLORS[(n as GNode).niveau]` par `couleurNiveau((n as GNode).niveau, nMax)`
(capturer `nMax` dans la closure du rendu) et `LEVEL_LABELS[g.niveau]` par `libelleNiveau(g.niveau)`.

- [ ] **Step 3 : Lint + typecheck**

Run: `cd web && npx eslint src --max-warnings 0 && npx tsc --noEmit`
Expected: exit 0.

- [ ] **Step 4 : Vérification visuelle 3D**

Run:
```bash
S=/tmp/claude-1000/-home-marsattacks/712f120c-5332-4eba-8c0f-e101194426cc/scratchpad
/home/marsattacks/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome --headless=new --disable-gpu --no-sandbox --window-size=1440,1000 --virtual-time-budget=9000 --screenshot=$S/t11-3d.png http://localhost:3000/requirements 2>/dev/null; echo done
```
Lire `$S/t11-3d.png` : pas de régression (couches lisibles). (La vraie 3D à 8 niveaux se vérifie Task 13 sur le corpus XL.)

- [ ] **Step 5 : Commit**

```bash
git add web/src/components/req-graph-3d.tsx
git commit -m "feat(web): vue 3D — espacement et couleurs dérivés du niveau max du corpus

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 12 : API + front — passthrough du catalogue `niveaux` (libellés sémantiques)

**Files:**
- Modify: `api/lynx_api.py` (endpoint `/corpus`, ~ligne 63)
- Modify: `web/src/components/requirements/index.tsx` (fetch corpus + passage du catalogue)
- Modify: `web/src/components/req-graph.tsx` (prop `niveaux`)

**Interfaces:**
- Produces: `/corpus` renvoie `{n, exigences, niveaux}` ; la vue 2D reçoit `niveaux?: NiveauCat[]`.

- [ ] **Step 1 : API — renvoyer `niveaux`**

Dans `api/lynx_api.py`, `get_corpus()` (~ligne 63) renvoie aujourd'hui `{n, exigences}`. Lire le
catalogue `niveaux` du corpus courant s'il existe et l'ajouter, sinon `[]` (le repli `libelleNiveau`
garde alors les libellés L0–L5 d'origine) :

```python
    from pathlib import Path as _Path
    import json as _json
    from src.config import DATA_DIR as _DD
    niveaux = []
    xl = _DD / "corpus_xl.json"
    if xl.exists():
        try:
            niveaux = _json.loads(xl.read_text(encoding="utf-8")).get("niveaux", [])
        except Exception:
            niveaux = []
    return {"n": len(exigences), "exigences": exigences, "niveaux": niveaux}
```

Note : implémentation simple — le catalogue est lu depuis `corpus_xl.json` s'il existe. Si le
corpus de démo est actif, `niveaux` peut rester `[]` (repli libellés d'origine) : ne pas régresser
la démo est le critère prioritaire.

- [ ] **Step 2 : Front — récupérer et propager `niveaux`**

Dans `web/src/components/requirements/index.tsx`, ajouter `niveaux?: NiveauCat[]` au type du fetch
`/corpus`, stocker `const [niveaux, setNiveaux] = useState<NiveauCat[]>([])`, et le passer en prop
au composant graphe. Importer `type NiveauCat` de `@/components/req-levels`.

- [ ] **Step 3 : Front — la vue 2D consomme le catalogue**

Dans `req-graph.tsx`, ajouter la prop `niveaux?: NiveauCat[]` et l'utiliser : `libelleNiveau(niveau, niveaux)`.

- [ ] **Step 4 : Lint + typecheck**

Run: `cd web && npx eslint src --max-warnings 0 && npx tsc --noEmit`
Expected: exit 0.

- [ ] **Step 5 : Vérifier l'API**

Redémarrer l'API (commandes séparées), puis :
Run: `curl -s localhost:8000/api/lynx/corpus | .venv/bin/python -c "import json,sys;d=json.load(sys.stdin);print('clés:',sorted(d));print('niveaux:',d.get('niveaux'))"`
Expected: la réponse contient la clé `niveaux` (liste, éventuellement vide sur la démo).

- [ ] **Step 6 : Commit**

```bash
git add api/lynx_api.py web/src/components/requirements/index.tsx web/src/components/req-graph.tsx
git commit -m "feat(web): libellés de niveaux sémantiques via catalogue du corpus

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 13 : Protocole de test + note de limites

**Files:**
- Create: `docs/superpowers/specs/corpus-xl-findings.md`

**Interfaces:** aucune (exécution + observation).

- [ ] **Step 1 : Charger le corpus XL dans le système**

Redémarrer l'API si besoin, puis :
```bash
curl -s -X POST localhost:8000/api/lynx/corpus/upload -F "files=@lynx/corpus/corpus_xl.json" | .venv/bin/python -c "import json,sys;d=json.load(sys.stdin);print('chargées:',d.get('n'))"
```
Expected: ~525 exigences chargées.

- [ ] **Step 2 : Auditer et chronométrer**

Run:
```bash
time timeout 3600 curl -s -N -X POST localhost:8000/api/lynx/audit -H 'Content-Type: application/json' -d '{"deep":true}' > /tmp/claude-1000/-home-marsattacks/712f120c-5332-4eba-8c0f-e101194426cc/scratchpad/xl-audit.sse
```
Noter : durée totale, score, exigences signalées, nombre d'échanges glass box.

- [ ] **Step 3 : Inspecter faux positifs, cycle en V, rendu du graphe**

Analyser `xl-audit.sse` (score, flagged, familles d'agents) ; échantillonner 10 constats pour juger
des faux positifs induits par la verbosité et par les liens VERIFIES (le système lit-il bien la
branche montante ?). Capturer le graphe 2D et 3D à ~525 nœuds (mêmes commandes chromium que Task 10/11)
et lire les images (lisibilité, profondeur 0..7, branche de vérification, perf).

- [ ] **Step 4 : Rédiger la note de limites**

Créer `docs/superpowers/specs/corpus-xl-findings.md`, sections : Échelle (temps/latence sur ~525
appels), Verbosité (faux positifs, tenue du débat), Cycle en V (le système exploite-t-il les liens
VERIFIES / la branche montante ?), Aveuglement à la donnée d'ingénierie (les champs `grandeurs`/
`alloue_a`/`contexte_operationnel` ignorés — cas où ça aurait aidé), UI (rendu à l'échelle). Chaque
limite priorisée (impact × effort) comme entrée de la prochaine itération.

- [ ] **Step 5 : Restaurer le corpus de démo**

Run: `curl -s -X POST localhost:8000/api/lynx/corpus/reset | .venv/bin/python -c "import json,sys;print('reset:',json.load(sys.stdin))"`
Expected: retour au corpus de démo (34 exigences).

- [ ] **Step 6 : Commit**

```bash
git add docs/superpowers/specs/corpus-xl-findings.md
git commit -m "docs(lynx): note de limites — LynX sur corpus XL (matière pour le câblage)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Notes d'exécution

- **Ordre** : Tasks 1→8 (backend/génération, cycle en V) puis 9→12 (UI dynamique) puis 13 (test). La Task 8 peut tourner en arrière-plan pendant les tâches UI.
- **Cycle en V** : la déclinaison est `parent_id`/DERIVE (descendant) ; la vérification est `links` VERIFIES (montant), `niveau` = niveau vérifié. Ne jamais traiter « Test » comme un niveau plus profond.
- **Non-régression démo** : après Task 10/11, vérifier visuellement que le corpus de démo L0–L5 rend à l'identique avant de continuer.
- **Front sans runner** : la seule preuve automatisable est tsc/eslint + sanity-check `node` + capture d'écran. Toujours LIRE la capture, ne pas se contenter de « done ».
