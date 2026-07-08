# LynX scaling — Phase 2 : `audit_matrix(scope=…)` incrémental — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajouter un paramètre `scope` à `audit_matrix` pour n'auditer sémantiquement
(LLM) que les exigences ciblées, et câbler génération + correction dessus — pour qu'elles
cessent de ré-auditer tout le corpus à froid. Parité stricte : `scope=None` inchangé,
et pour un `scope` donné, les constats sont exactement ceux qu'un audit complet
produirait pour ces exigences.

**Architecture:** Les passes déterministes/cross-matrice (structural, dédup numpy,
co-références) restent calculées **en plein** (≈46 ms à 525, mesuré) puis sont
**filtrées** à `scope`. Seule la boucle sémantique LLM `_audit_one` (le vrai coût, N
appels) est réellement restreinte à `scope`. Comme chaque `_audit_one(req)` est
indépendant, restreindre la boucle ne change aucun autre constat → parité par
construction. `generation.generate_children` passe `scope=ids_des_filles` ;
`autofix.run_batch_fix` passe `scope=ids_corrigés`.

**Tech Stack:** Python 3, numpy, httpx, pytest. Aucune nouvelle dépendance ni service.

## Global Constraints

- **Parité stricte** :
  - `audit_matrix(corpus)` (sans `scope`) doit rester **byte-identique** à aujourd'hui
    (mêmes `MatrixFinding`, même `score`, même `counts`). Test golden.
  - `audit_matrix(corpus, scope=S)` doit renvoyer **exactement** les constats qu'un
    audit complet produirait dont `req_id ∈ S` (test golden : `full.findings` filtré à
    `S` == `scoped.findings`), aux ordres près (comparaison par ensemble).
- Le `score` d'un rapport scopé est calculé sur les constats renvoyés (donc scopé) ;
  documenté. `generation` ne l'utilise pas ; `run_batch_fix` ne l'utilise que pour
  l'affichage (`score_apres`).
- Chaque `_audit_one(req)` est indépendant des autres exigences auditées : scoper la
  boucle ne doit modifier aucun constat d'une exigence hors scope.
- Rétro-compatibilité : `scope=None` = comportement actuel. Les appelants existants
  (bouton audit) ne changent pas.
- Tests depuis `lynx/` : `PYTHONPATH=. ../.venv/bin/python -m pytest tests/<file> -q`,
  fichier par fichier (pas le glob). venv : `../.venv/bin/python` (pas de `python` PATH).
- Messages, seuils, gravités inchangés.

---

## File Structure

- `lynx/src/audit.py` — **modifié** : `audit_matrix` et les helpers reçoivent `scope`.
  `_structural_findings`/`_embedding_duplicates`/`_coreference_findings` calculent en
  plein puis filtrent ; la boucle `_audit_one` ne parcourt que `scope`.
- `lynx/src/generation.py` — **modifié** : l'appel `audit_matrix(copie, deep=True)`
  (ligne ~120) passe `scope=set(par_id)` ; l'appel `run_batch_fix(...)` passe le scope.
- `lynx/src/autofix.py` — **modifié** : `run_batch_fix` accepte `scope` et le passe à ses
  `audit_matrix(...)` internes (ligne ~113).
- `lynx/tests/test_audit_scope.py` — **créé** : tests golden de parité scoped vs full.

---

### Task 1: `audit_matrix(scope=…)` dans `audit.py`

**Files:**
- Modify: `lynx/src/audit.py`
- Test: `lynx/tests/test_audit_scope.py`

**Interfaces:**
- Consumes: helpers existants (`_structural_findings`, `_embedding_duplicates`,
  `_coreference_findings`, `_audit_one`), `RequirementTree`.
- Produces:
  - `audit_matrix(corpus, deep=True, on_event=None, scope: Optional[Set[str]] = None) -> MatrixReport`
    — `scope=None` : inchangé ; `scope=S` : constats restreints à `req_id ∈ S`, boucle
    LLM restreinte à `S`.

- [ ] **Step 1: Write the failing test**

Créer `lynx/tests/test_audit_scope.py` :

```python
"""Parité golden : audit scopé == audit complet filtré au scope. Sans Ollama :
on mocke _audit_one (sémantique) et les embeddings, on teste la mécanique de scope."""
from src import audit
from src.audit import MatrixFinding


def _fake_audit_one(tree, req):
    # Un constat déterministe par exigence (indépendant des autres) : suffit à
    # tester le filtrage de la boucle sémantique.
    return [MatrixFinding(req.id, "REDACTION", "WARNING", f"pseudo-constat {req.id}")]


CORPUS = [
    {"id": "R0", "niveau": 0, "texte": "Le système doit voler.", "parent_id": None},
    {"id": "R1", "niveau": 1, "texte": "Le système doit décoller en 5 s.", "parent_id": "R0"},
    {"id": "R2", "niveau": 1, "texte": "Le système doit atterrir en 8 s.", "parent_id": "R0"},
]


def test_scope_none_matches_legacy(monkeypatch):
    monkeypatch.setattr(audit, "_audit_one", _fake_audit_one)
    monkeypatch.setattr(audit.embeddings, "embeddings_available", lambda: False)
    monkeypatch.setattr(audit.llm, "llm_available", lambda: True)
    monkeypatch.setattr(audit, "_coreference_findings", lambda corpus, tree: [])
    rep = audit.audit_matrix(CORPUS, deep=True)
    ids = sorted(f.req_id for f in rep.findings)
    assert ids == ["R0", "R1", "R2"]  # un pseudo-constat par exigence


def _key(f):
    return (f.req_id, f.axis, f.severity, f.message)


def test_scoped_equals_full_filtered(monkeypatch):
    monkeypatch.setattr(audit, "_audit_one", _fake_audit_one)
    monkeypatch.setattr(audit.embeddings, "embeddings_available", lambda: False)
    monkeypatch.setattr(audit.llm, "llm_available", lambda: True)
    monkeypatch.setattr(audit, "_coreference_findings", lambda corpus, tree: [])

    full = audit.audit_matrix(CORPUS, deep=True)
    scope = {"R1"}
    scoped = audit.audit_matrix(CORPUS, deep=True, scope=scope)

    full_in_scope = sorted((_key(f) for f in full.findings if f.req_id in scope))
    scoped_keys = sorted(_key(f) for f in scoped.findings)
    assert scoped_keys == full_in_scope
    # La boucle LLM n'a produit un constat QUE pour R1.
    assert all(f.req_id == "R1" for f in scoped.findings)


def test_scoped_semantic_loop_only_visits_scope(monkeypatch):
    visited = []
    def spy(tree, req):
        visited.append(req.id)
        return []
    monkeypatch.setattr(audit, "_audit_one", spy)
    monkeypatch.setattr(audit.embeddings, "embeddings_available", lambda: False)
    monkeypatch.setattr(audit.llm, "llm_available", lambda: True)
    monkeypatch.setattr(audit, "_coreference_findings", lambda corpus, tree: [])
    audit.audit_matrix(CORPUS, deep=True, scope={"R2"})
    assert visited == ["R2"]  # aucune exigence hors scope n'a été auditée par LLM
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/marsattacks/Documents/AI_for_ssh/lynx && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_audit_scope.py -q`
Expected: FAIL — `audit_matrix() got an unexpected keyword argument 'scope'`.

- [ ] **Step 3: Implémenter `scope`**

Dans `lynx/src/audit.py`, ajouter `Set` à l'import typing en tête :

```python
from typing import Callable, Dict, List, Optional, Set
```

Remplacer la signature et le corps de `audit_matrix` par :

```python
def audit_matrix(corpus: List[dict], deep: bool = True,
                 on_event: Optional[Callable[[int, int], None]] = None,
                 scope: Optional[Set[str]] = None) -> MatrixReport:
    """Audite le corpus. ``on_event(done, total)`` suit l'avancement sémantique.

    ``scope`` : si fourni, ne renvoie que les constats dont ``req_id`` est dans
    ``scope``, et ne lance la boucle sémantique LLM que pour ces exigences. Les passes
    déterministes/cross-matrice sont calculées en plein (rapide) puis filtrées — donc
    un audit scopé rend exactement les constats qu'un audit complet produirait pour
    ces exigences (parité). ``scope=None`` : audit complet inchangé.
    """
    def _in_scope(f: MatrixFinding) -> bool:
        return scope is None or f.req_id in scope

    findings: List[MatrixFinding] = [f for f in _structural_findings(corpus) if _in_scope(f)]
    findings += [f for f in _embedding_duplicates(corpus) if _in_scope(f)]

    try:
        tree = RequirementTree(corpus)
    except ValueError:
        tree = None  # doublons : on s'arrête au structurel

    if deep and tree is not None and llm.llm_available():
        findings += [f for f in _coreference_findings(corpus, tree) if _in_scope(f)]
        reqs = tree.all() if scope is None else [r for r in tree.all() if r.id in scope]
        total = len(reqs)
        done = 0
        with ThreadPoolExecutor(max_workers=LLM_MAX_CONCURRENCY) as pool:
            futures = [pool.submit(_audit_one, tree, r) for r in reqs]
            for fut in as_completed(futures):
                try:
                    findings += fut.result()
                except Exception as exc:
                    findings.append(MatrixFinding("?", "NON_AUDITE", "INFO",
                                                  f"Audit d'une exigence en erreur : {exc}"))
                done += 1
                if on_event:
                    try:
                        on_event(done, total)
                    except Exception:
                        pass

    penalty = sum(_PENALTY.get(f.severity, 0) for f in findings)
    score = max(0, 100 - penalty)
    counts: Dict[str, int] = {}
    for f in findings:
        counts[f.axis] = counts.get(f.axis, 0) + 1
    return MatrixReport(n=len(corpus), score=score, findings=findings, counts=counts)
```

Note : `_audit_one` renvoie des constats dont `req_id` == l'exigence auditée, donc la
boucle scopée n'a pas besoin de re-filtrer (mais un `_audit_one` qui pointerait une
autre exigence resterait cohérent, on ne filtre pas la sortie sémantique pour ne pas
perdre un constat légitime sur une sœur).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/marsattacks/Documents/AI_for_ssh/lynx && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_audit_scope.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/AI_for_ssh
git add lynx/src/audit.py lynx/tests/test_audit_scope.py
git commit -m "feat(lynx): audit_matrix(scope=…) — audit sémantique incrémental

Passes déterministes calculées en plein puis filtrées ; boucle LLM restreinte au
scope. scope=None inchangé. Parité golden (scoped == full filtré au scope).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Câbler `run_batch_fix(scope=…)` dans `autofix.py`

**Files:**
- Modify: `lynx/src/autofix.py`
- Test: `lynx/tests/test_autofix.py` (ajout d'un test que le scope est transmis)

**Interfaces:**
- Consumes: `audit_matrix(scope=…)` (Task 1).
- Produces: `run_batch_fix(corpus, findings, max_passes=3, on_progress=None, cancelled=None, deep=True, scope: Optional[Set[str]] = None)` — passe `scope` à ses `audit_matrix`.

- [ ] **Step 1: Write the failing test**

Ajouter à `lynx/tests/test_autofix.py` (imports en tête si besoin : `from unittest.mock import patch`) :

```python
def test_run_batch_fix_forwards_scope(monkeypatch):
    """run_batch_fix transmet le scope à ses ré-audits (audit incrémental)."""
    from src import autofix
    from src.audit import MatrixReport

    seen = {}
    def fake_audit(corpus, deep=True, on_event=None, scope=None):
        seen["scope"] = scope
        return MatrixReport(n=len(corpus), score=100, findings=[], counts={})
    monkeypatch.setattr(autofix, "audit_matrix", fake_audit)
    monkeypatch.setattr(autofix, "suggest_correction",
                        lambda work, rid, msgs: {"texte": "réécrit", "justification": ""})

    corpus = [{"id": "R1", "niveau": 0, "texte": "flou", "parent_id": None}]
    findings = [{"req_id": "R1", "axis": "REDACTION", "severity": "WARNING", "message": "flou"}]
    autofix.run_batch_fix(corpus, findings, max_passes=1, scope={"R1"})
    assert seen["scope"] == {"R1"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/marsattacks/Documents/AI_for_ssh/lynx && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_autofix.py::test_run_batch_fix_forwards_scope -q`
Expected: FAIL — `run_batch_fix() got an unexpected keyword argument 'scope'`.

- [ ] **Step 3: Ajouter le paramètre `scope`**

Dans `lynx/src/autofix.py`, ajouter l'import typing en tête si absent :

```python
from typing import Any, Callable, Dict, List, Optional, Set
```

Modifier la signature de `run_batch_fix` (ligne ~55) pour ajouter `scope` :

```python
def run_batch_fix(corpus: List[dict], findings, max_passes: int = 3,
                  on_progress: Optional[Callable[[dict], None]] = None,
                  cancelled: Optional[threading.Event] = None,
                  deep: bool = True, scope: Optional[Set[str]] = None) -> dict:
```

Et l'appel interne `audit_matrix(work, deep=deep, on_event=...)` (ligne ~113) devient :

```python
        rep = audit_matrix(work, deep=deep, scope=scope, on_event=lambda done, tot, p=passe: _emit(
            on_progress, {"phase": "audit", "passe": p,
                          "done": done, "total": tot, "req_id": None}))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/marsattacks/Documents/AI_for_ssh/lynx && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_autofix.py -q`
Expected: PASS (8 passed — les 7 existants + le nouveau).

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/AI_for_ssh
git add lynx/src/autofix.py lynx/tests/test_autofix.py
git commit -m "feat(lynx): run_batch_fix(scope=…) — ré-audits incrémentaux en correction

Transmet le scope aux ré-audits entre passes : la correction en lot ne ré-audite
plus tout le corpus, seulement les exigences corrigées.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Câbler `generation.py` + validation sur corpus réel

**Files:**
- Modify: `lynx/src/generation.py`
- Test: `lynx/tests/test_generation.py` (ajout : scope transmis)

**Interfaces:**
- Consumes: `audit_matrix(scope=…)` (Task 1), `run_batch_fix(scope=…)` (Task 2).
- Produces: `generate_children` auto-audite `scope=ids_des_filles`.

- [ ] **Step 1: Write the failing test**

Ajouter à `lynx/tests/test_generation.py` :

```python
def test_generate_children_scopes_audit_to_children(monkeypatch):
    """L'auto-audit de génération ne cible QUE les filles générées (pas tout le corpus)."""
    from src import generation
    from src.audit import MatrixReport

    captured = {}
    def fake_audit(corpus, deep=True, on_event=None, scope=None):
        captured["scope"] = scope
        return MatrixReport(n=len(corpus), score=100, findings=[], counts={})
    monkeypatch.setattr(generation, "audit_matrix", fake_audit)
    # L'agent de génération propose 2 filles.
    monkeypatch.setattr(generation.llm, "call_agent", lambda *a, **k: {
        "filles": [{"texte": "Le sous-système doit démarrer en 2 s.", "aspect_couvert": "démarrage"},
                   {"texte": "Le sous-système doit s'arrêter en 1 s.", "aspect_couvert": "arrêt"}],
        "aspects_non_couverts": []})

    corpus = [
        {"id": "R0", "niveau": 0, "texte": "Le système doit fonctionner.", "parent_id": None},
        {"id": "R1", "niveau": 1, "texte": "Le système doit démarrer.", "parent_id": "R0"},
    ]
    res = generation.generate_children(corpus, "R1")
    assert "error" not in res
    child_ids = {f["id_propose"] for f in res["filles"]}
    # L'audit a été scopé exactement aux filles générées.
    assert captured["scope"] == child_ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/marsattacks/Documents/AI_for_ssh/lynx && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_generation.py::test_generate_children_scopes_audit_to_children -q`
Expected: FAIL — `captured["scope"]` vaut `None` (l'audit n'est pas encore scopé).

- [ ] **Step 3: Scoper l'audit de génération**

Dans `lynx/src/generation.py`, l'appel `audit_matrix(copie, deep=True, on_event=...)`
(ligne ~120) devient (le scope = les ids des filles générées, déjà connus via `par_id`) :

```python
    from .audit import audit_matrix
    child_ids = set(par_id)
    emit({"phase": "audit", "passe": 0, "done": 0, "total": len(child_ids)})
    rapport = audit_matrix(copie, deep=True, scope=child_ids,
                           on_event=lambda done, total: emit(
        {"phase": "audit", "passe": 0, "done": done, "total": total}))
```

Et l'appel `run_batch_fix(copie, a_corriger, max_passes=2, deep=True, ...)` (ligne ~126)
passe le même scope :

```python
        out = run_batch_fix(
            copie, a_corriger, max_passes=2, deep=True, scope=child_ids,
            on_progress=lambda info: emit(
                {**info, "phase": "reecriture" if info.get("phase") == "correction"
                 else "audit"}),
            cancelled=cancelled)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/marsattacks/Documents/AI_for_ssh/lynx && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_generation.py tests/test_autofix.py tests/test_audit_scope.py -q`
Expected: PASS (tous verts).

- [ ] **Step 5: Non-régression (par fichier)**

```bash
cd /home/marsattacks/Documents/AI_for_ssh/lynx
for f in tests/test_*.py; do
  echo "== $f =="; PYTHONPATH=. ../.venv/bin/python -m pytest "$f" -q 2>&1 | tail -1
done
```
Expected: chaque fichier vert (les 12 existants + `test_audit_scope`). Un ÉCHEC =
régression → BLOCKED.

- [ ] **Step 6: Validation golden sur le corpus réel (525) — nécessite Ollama**

Prouve la parité scope sur de vrais embeddings + le fait que la boucle sémantique est
bien restreinte. On mocke `_audit_one` pour éviter 525 appels LLM réels, mais on garde
les vraies passes déterministes (structural + dédup numpy sur embeddings réels).

```bash
cd /home/marsattacks/Documents/AI_for_ssh/lynx && PYTHONPATH=. ../.venv/bin/python - <<'PY'
import json
from src import audit
from src.audit import MatrixFinding

corpus = json.load(open("corpus/corpus_xl.json"))["exigences"]
audit._audit_one = lambda tree, req: [MatrixFinding(req.id, "REDACTION", "WARNING", f"c-{req.id}")]
audit.llm.llm_available = lambda: True

full = audit.audit_matrix(corpus, deep=True)
# Prend 3 ids répartis.
ids = [corpus[10]["id"], corpus[260]["id"], corpus[500]["id"]]
scope = set(ids)
scoped = audit.audit_matrix(corpus, deep=True, scope=scope)

def key(f): return (f.req_id, f.axis, f.severity, f.message)
full_in = sorted(key(f) for f in full.findings if f.req_id in scope)
sc = sorted(key(f) for f in scoped.findings)
assert sc == full_in, f"PARITÉ ROMPUE : {len(sc)} scopé vs {len(full_in)} plein-filtré"
print(f"PARITÉ SCOPE OK sur corpus réel : {len(sc)} constats identiques pour scope={ids}")
print(f"Plein : {len(full.findings)} constats ; scopé : {len(scoped.findings)}.")
PY
```
Expected: `PARITÉ SCOPE OK`. Si l'assertion casse, ne pas committer : BLOCKED.

- [ ] **Step 7: Commit (clôture Phase 2)**

```bash
cd ~/Documents/AI_for_ssh
git add lynx/src/generation.py lynx/tests/test_generation.py
git commit -m "feat(lynx): génération scope son auto-audit aux filles (fin du cold-audit N)

generate_children audite/corrige scope=filles au lieu de tout le corpus : plus de
ré-audit à froid des centaines d'exigences existantes pour ajouter quelques filles.
Parité golden vérifiée sur le corpus réel 525.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage (Phase 2)** : la spec demande « store de verdicts + `audit_matrix(scope=…)` ;
génération/correction passent un scope ; gate = test de parité golden ». Le `scope` +
le câblage génération/correction + le golden sont couverts (Tasks 1-3). **Le store de
verdicts est volontairement omis** (décision documentée : YAGNI à ≤2000 — le déterministe
est à 46 ms mesuré, et le scope suffit à tuer le cold-audit ; à reconsidérer en Phase 3
si un score plein persistant devient nécessaire). Écart assumé vs la spec, cohérent avec
le curseur réel.

**2. Placeholder scan** : aucun TODO ; tout le code des steps est complet ; les
signatures modifiées montrent la ligne exacte.

**3. Type consistency** : `scope: Optional[Set[str]]` cohérent dans `audit_matrix`,
`run_batch_fix`, et l'usage `scope=set(par_id)`/`scope=child_ids` dans generation. Les
tests mockent `audit_matrix`/`_audit_one` avec la même signature (`scope=None` par
défaut).

**Note de parité** : les passes déterministes/cross-matrice sont « calculées en plein
puis filtrées », donc identiques à l'audit complet restreint. La seule chose réellement
scopée est la boucle sémantique `_audit_one`, dont chaque appel est indépendant → aucun
constat d'une exigence hors scope n'est modifié. Le golden test (`scoped == full filtré`)
verrouille cette propriété, en unit ET sur le corpus réel 525.

**Note sur le score scopé** : `MatrixReport.score` d'un rapport scopé porte sur les
constats renvoyés (scopés). `generation` ne lit pas le score ; `run_batch_fix` ne
l'expose que dans `score_apres` (affichage du récap). Comportement documenté, pas de
dépendance logique.
