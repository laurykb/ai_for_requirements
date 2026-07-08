# LynX scaling — Phase 0 : dédup vectorisée numpy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer le cosinus Python O(N²) par du numpy vectorisé dans les trois
chemins de similarité de LynX, sans changer un seul verdict (parité stricte).

**Architecture:** On ajoute des helpers numpy dans `embeddings.py` (`_stack`,
`duplicate_pairs`, `similarities_to`, `most_similar` réécrit), puis on recâble
`audit._embedding_duplicates` et `analyzers.analyze_impact_latent` dessus. Les seuils,
les messages et l'ordre des constats restent identiques. C'est un refactor de
performance pur : aucune sémantique ne change, ce qui le rend trivialement
« rollback-able ».

**Tech Stack:** Python 3, numpy (déjà installé, 2.5.0), httpx, pytest.

## Global Constraints

- **Parité de comportement stricte** : mêmes constats (`req_id`, `axis`/`analyzer`,
  `severity`, `message` au caractère près), mêmes scores, mêmes seuils. Prouvé par
  tests de parité contre une implémentation Python de référence.
- Seuils inchangés : `EMBED_DUP_THRESHOLD=0.95`, `EMBED_DISTINCT_THRESHOLD=0.62`,
  `EMBED_LATENT_THRESHOLD=0.70`, `LATENT_TOPK=5` (lus depuis `config.py`, ne pas
  redéfinir).
- Messages en français, copiés **verbatim** de l'existant (ex. `"Doublon probable : "
  "{id1} ≈ {id2} (similarité {s:.2f})."`).
- Mono-poste souverain, in-process : pas de nouvelle dépendance externe autre que
  numpy (déjà présente).
- Les tests tournent depuis `lynx/` : `PYTHONPATH=. ../.venv/bin/python -m pytest ...`.
  Ils ne doivent JAMAIS nécessiter Ollama (mocker `get_embeddings` /
  `embeddings_available`).

---

## File Structure

- `lynx/src/embeddings.py` — **modifié** : ajoute `import numpy as np` + helpers
  `_stack`, `duplicate_pairs`, `similarities_to` ; réécrit `most_similar` en numpy.
  `cosine(a, b)` scalaire conservé tel quel (back-compat). Responsabilité inchangée :
  pré-filtre vectoriel déterministe.
- `lynx/src/audit.py` — **modifié** : `_embedding_duplicates` (ligne 161) appelle
  `embeddings.duplicate_pairs` au lieu de la double boucle Python.
- `lynx/src/analyzers.py` — **modifié** : la boucle de scoring de
  `analyze_impact_latent` (lignes ~464-473) appelle `embeddings.similarities_to`.
- `lynx/tests/test_embeddings_numpy.py` — **créé** : tests de parité numpy vs
  référence Python pour les helpers.
- `lynx/tests/test_audit_dedup_parity.py` — **créé** : parité de
  `_embedding_duplicates` au niveau audit.
- `requirements.txt` — **modifié** : épingler `numpy` (aujourd'hui seulement dans
  `requirements.lock.txt`), car `embeddings.py` l'importe désormais en dur.

---

### Task 1: Helpers numpy dans `embeddings.py`

**Files:**
- Modify: `lynx/src/embeddings.py`
- Test: `lynx/tests/test_embeddings_numpy.py`

**Interfaces:**
- Consumes: rien (feuille).
- Produces :
  - `duplicate_pairs(vectors: List[Optional[List[float]]], threshold: float) -> List[Tuple[int, int, float]]`
    — indices `(i, j, score)` avec `i < j`, `score >= threshold`, vecteurs invalides exclus.
  - `similarities_to(target_vec: Optional[List[float]], cand_vecs: List[Optional[List[float]]]) -> List[float]`
    — cosinus de `target_vec` contre chaque candidat ; `0.0` si l'un des deux est invalide.
  - `most_similar(target_text: str, candidates: List[tuple]) -> Optional[tuple]`
    — signature inchangée : `(id, score)` du plus proche, ou `None`.

- [ ] **Step 1: Write the failing test**

Créer `lynx/tests/test_embeddings_numpy.py` :

```python
"""Parité numpy vs cosinus Python de référence, sans Ollama (vecteurs injectés)."""
import math

import numpy as np
import pytest

from src import embeddings


def _ref_cosine(a, b):
    """Cosinus de référence (ancienne implémentation Python), pour la parité."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _rand_vecs(n, dim=16, seed=0):
    rng = np.random.default_rng(seed)
    return [list(map(float, rng.normal(size=dim))) for _ in range(n)]


def test_duplicate_pairs_matches_reference():
    vecs = _rand_vecs(12, seed=1)
    # Fabrique un quasi-doublon certain (indices 3 et 7).
    vecs[7] = [x * 1.0001 for x in vecs[3]]
    threshold = 0.95
    # Référence : double boucle Python O(N²).
    ref = []
    for i in range(len(vecs)):
        for j in range(i + 1, len(vecs)):
            s = _ref_cosine(vecs[i], vecs[j])
            if s >= threshold:
                ref.append((i, j))
    got = [(i, j) for i, j, _ in embeddings.duplicate_pairs(vecs, threshold)]
    assert got == ref
    # Les scores correspondent aussi (tolérance flottante).
    for i, j, s in embeddings.duplicate_pairs(vecs, threshold):
        assert abs(s - _ref_cosine(vecs[i], vecs[j])) < 1e-9


def test_duplicate_pairs_excludes_invalid():
    vecs = _rand_vecs(5, seed=2)
    vecs[2] = None
    vecs[4] = []  # invalide
    pairs = embeddings.duplicate_pairs(vecs, 0.0)  # seuil 0 : toutes les paires valides
    idx = {i for i, _, _ in pairs} | {j for _, j, _ in pairs}
    assert 2 not in idx and 4 not in idx


def test_similarities_to_matches_reference():
    vecs = _rand_vecs(8, seed=3)
    target, cands = vecs[0], vecs[1:]
    got = embeddings.similarities_to(target, cands)
    ref = [_ref_cosine(target, c) for c in cands]
    assert len(got) == len(ref)
    for g, r in zip(got, ref):
        assert abs(g - r) < 1e-9


def test_similarities_to_invalid_target_returns_zeros():
    cands = _rand_vecs(3, seed=4)
    assert embeddings.similarities_to(None, cands) == [0.0, 0.0, 0.0]


def test_most_similar_picks_reference_best(monkeypatch):
    vecs = _rand_vecs(6, seed=5)
    texts = [f"t{i}" for i in range(len(vecs))]
    monkeypatch.setattr(embeddings, "get_embeddings", lambda ts: vecs)
    candidates = [(f"ID{i}", texts[i + 1]) for i in range(len(vecs) - 1)]
    best_id, best_score = embeddings.most_similar(texts[0], candidates)
    ref_scores = [_ref_cosine(vecs[0], vecs[i + 1]) for i in range(len(candidates))]
    ref_best = int(np.argmax(ref_scores))
    assert best_id == candidates[ref_best][0]
    assert abs(best_score - ref_scores[ref_best]) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd lynx && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_embeddings_numpy.py -q`
Expected: FAIL — `AttributeError: module 'src.embeddings' has no attribute 'duplicate_pairs'`.

- [ ] **Step 3: Write minimal implementation**

Dans `lynx/src/embeddings.py`, ajouter `import numpy as np` en tête (près des autres
imports) :

```python
import numpy as np
```

Puis ajouter les helpers (après `cosine`, avant `most_similar`) :

```python
def _stack(vectors: List[Optional[List[float]]]):
    """Empile des vecteurs en matrice numpy L2-normalisée (une ligne par vecteur).

    Renvoie ``(matrice float64 (n, dim), mask booléen des lignes valides)``. Un
    vecteur None / vide / de mauvaise dimension devient une ligne nulle marquée
    invalide (sa similarité vaudra 0, jamais un faux positif).
    """
    n = len(vectors)
    dim = next((len(v) for v in vectors if v), 0)
    m = np.zeros((n, dim or 1), dtype=np.float64)
    valid = np.zeros(n, dtype=bool)
    for i, v in enumerate(vectors):
        if v and dim and len(v) == dim:
            m[i] = v
            valid[i] = True
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms, valid


def duplicate_pairs(vectors: List[Optional[List[float]]], threshold: float):
    """Paires ``(i, j, score)`` (i < j) de cosinus >= ``threshold``.

    Remplace la double boucle O(N²) Python par une seule matmul numpy. Les
    vecteurs invalides sont exclus (jamais signalés comme doublons).
    """
    n = len(vectors)
    if n < 2:
        return []
    m, valid = _stack(vectors)
    sims = m @ m.T
    iu = np.triu_indices(n, k=1)
    vi, vj = iu
    keep = valid[vi] & valid[vj] & (sims[vi, vj] >= threshold)
    return [(int(vi[k]), int(vj[k]), float(sims[vi[k], vj[k]]))
            for k in np.nonzero(keep)[0]]


def similarities_to(target_vec: Optional[List[float]],
                    cand_vecs: List[Optional[List[float]]]) -> List[float]:
    """Cosinus de ``target_vec`` contre chaque candidat (0.0 si l'un est invalide)."""
    if not cand_vecs:
        return []
    m, valid = _stack([target_vec] + list(cand_vecs))
    if not valid[0]:
        return [0.0] * len(cand_vecs)
    sims = m[1:] @ m[0]
    sims = np.where(valid[1:], sims, 0.0)
    return [float(s) for s in sims]
```

Puis **remplacer** le corps de `most_similar` par la version numpy (même signature,
même sémantique : meilleur candidat valide, ou None) :

```python
def most_similar(target_text: str, candidates: List[tuple]) -> Optional[tuple]:
    """Renvoie (id, score) du candidat le plus proche, ou None si indispo.

    ``candidates`` = liste de (id, texte). Embeddings groupés en un seul appel.
    """
    if not candidates:
        return None
    vecs = get_embeddings([target_text] + [c[1] for c in candidates])
    if not vecs or vecs[0] is None:
        return None
    m, valid = _stack(vecs)
    if not valid[0]:
        return None
    sims = m[1:] @ m[0]
    sims = np.where(valid[1:], sims, -np.inf)
    if not np.isfinite(sims).any():
        return None
    j = int(np.argmax(sims))
    return (candidates[j][0], float(sims[j]))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd lynx && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_embeddings_numpy.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/AI_for_ssh
git add lynx/src/embeddings.py lynx/tests/test_embeddings_numpy.py
git commit -m "perf(lynx): helpers numpy de similarité (duplicate_pairs, similarities_to, most_similar)

Vectorise le cosinus ; parité prouvée contre une référence Python. Aucun
changement de seuil ni de sémantique.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Vectoriser `audit._embedding_duplicates`

**Files:**
- Modify: `lynx/src/audit.py:161-185`
- Test: `lynx/tests/test_audit_dedup_parity.py`

**Interfaces:**
- Consumes: `embeddings.duplicate_pairs` (Task 1).
- Produces: `_embedding_duplicates(corpus)` inchangé en signature/sortie (liste de
  `MatrixFinding`).

- [ ] **Step 1: Write the failing test**

Créer `lynx/tests/test_audit_dedup_parity.py` :

```python
"""Parité de _embedding_duplicates : mêmes constats que la référence Python O(N²)."""
import math

import numpy as np

from src import audit
from src.config import EMBED_DUP_THRESHOLD


def _ref_cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _ref_findings(corpus, vecs):
    """Ancienne logique : double boucle Python, message identique."""
    items = [(r["id"], r.get("texte", "")) for r in corpus if r.get("texte")]
    vmap = {items[i][0]: vecs[i] for i in range(min(len(items), len(vecs)))}
    out = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            id1, id2 = items[i][0], items[j][0]
            v1, v2 = vmap.get(id1), vmap.get(id2)
            if not v1 or not v2:
                continue
            s = _ref_cosine(v1, v2)
            if s >= EMBED_DUP_THRESHOLD:
                out.append((id1, f"Doublon probable : {id1} ≈ {id2} (similarité {s:.2f})."))
    return out


def test_embedding_duplicates_parity(monkeypatch):
    rng = np.random.default_rng(7)
    corpus = [{"id": f"REQ-{i}", "texte": f"exigence {i}"} for i in range(10)]
    vecs = [list(map(float, rng.normal(size=16))) for _ in corpus]
    vecs[5] = [x * 1.00005 for x in vecs[2]]  # quasi-doublon 2≈5

    monkeypatch.setattr(audit.embeddings, "embeddings_available", lambda: True)
    monkeypatch.setattr(audit.embeddings, "get_embeddings", lambda texts: vecs)

    findings = audit._embedding_duplicates(corpus)
    got = [(f.req_id, f.message) for f in findings]
    assert got == _ref_findings(corpus, vecs)
    # Le doublon injecté est bien détecté.
    assert any("REQ-2" == f.req_id and "REQ-5" in f.message for f in findings)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd lynx && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_audit_dedup_parity.py -q`
Expected: FAIL — le test échoue si l'ordre ou les scores diffèrent (avant refactor,
l'ancien code passe déjà ; le test sert de filet AVANT/APRÈS). Si l'ancien code passe
déjà, aller au Step 3 : le but est qu'il continue à passer après vectorisation.

_(Note : ce test passe avec l'ancienne ET la nouvelle implémentation — c'est un test
de non-régression. Le lancer d'abord confirme qu'il est vert, puis on refactorise en
gardant le vert.)_

- [ ] **Step 3: Refactoriser `_embedding_duplicates`**

Dans `lynx/src/audit.py`, remplacer le corps de `_embedding_duplicates` (lignes
161-185) par :

```python
def _embedding_duplicates(corpus: List[dict]) -> List[MatrixFinding]:
    """Détecte les doublons quasi-identiques dans toute la matrice (embeddings)."""
    if not embeddings.embeddings_available():
        return []
    items = [(r["id"], r.get("texte", "")) for r in corpus if r.get("texte")]
    vlist = embeddings.get_embeddings([t for _, t in items])  # un seul appel (batch)
    if not vlist:
        return []
    findings: List[MatrixFinding] = []
    for i, j, s in embeddings.duplicate_pairs(vlist, EMBED_DUP_THRESHOLD):
        id1, id2 = items[i][0], items[j][0]
        findings.append(MatrixFinding(id1, "REDONDANCE", "BLOQUANT",
            f"Doublon probable : {id1} ≈ {id2} (similarité {s:.2f})."))
    return findings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd lynx && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_audit_dedup_parity.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/AI_for_ssh
git add lynx/src/audit.py lynx/tests/test_audit_dedup_parity.py
git commit -m "perf(lynx): dédup d'audit vectorisée (fin de l'O(N²) Python)

_embedding_duplicates appelle embeddings.duplicate_pairs. Constats identiques
(test de parité). Gain majeur sur audit + génération + correction qui partagent
audit_matrix.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Vectoriser `analyze_impact_latent`

**Files:**
- Modify: `lynx/src/analyzers.py:460-473`
- Test: `lynx/tests/test_embeddings_numpy.py` (ajout d'un test d'intégration analyseur)

**Interfaces:**
- Consumes: `embeddings.similarities_to` (Task 1).
- Produces: `analyze_impact_latent(ctx)` inchangé en comportement.

- [ ] **Step 1: Write the failing test**

Ajouter à `lynx/tests/test_embeddings_numpy.py` :

```python
def test_impact_latent_scoring_matches_reference(monkeypatch):
    """La sélection des candidats proches ne change pas après vectorisation."""
    from src import analyzers
    from src.config import EMBED_LATENT_THRESHOLD

    target_vec = [1.0, 0.0, 0.0, 0.0]
    # 3 candidats : un proche (>=seuil), un lointain, un invalide.
    cand_vecs = [[0.99, 0.14, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], None]
    sims = analyzers.embeddings.similarities_to(target_vec, cand_vecs)
    ref = [_ref_cosine(target_vec, c) if c else 0.0 for c in cand_vecs]
    for g, r in zip(sims, ref):
        assert abs(g - r) < 1e-9
    retenus = [i for i, s in enumerate(sims) if s >= EMBED_LATENT_THRESHOLD]
    assert retenus == [0]  # seul le candidat proche passe le seuil
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd lynx && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_embeddings_numpy.py::test_impact_latent_scoring_matches_reference -q`
Expected: PASS déjà (il teste `similarities_to` de Task 1 + le seuil). S'il échoue,
c'est un signal de régression dans Task 1 — corriger avant de continuer.

- [ ] **Step 3: Refactoriser la boucle de scoring**

Dans `lynx/src/analyzers.py`, remplacer le bloc de scoring de
`analyze_impact_latent` (lignes 464-473, la boucle `for i, c in enumerate(candidates)`
qui appelle `embeddings.cosine`) par :

```python
    sims = embeddings.similarities_to(vecs[0], vecs[1:])
    scored = [(c, s) for c, s in zip(candidates, sims) if s >= EMBED_LATENT_THRESHOLD]
    if not scored:
        _route([], [], note=f"aucune exigence proche parmi {len(candidates)} (seuil {EMBED_LATENT_THRESHOLD})")
        return []
```

Le reste de la fonction (tri `scored.sort`, `top = scored[:LATENT_TOPK]`, appel LLM)
est inchangé.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd lynx && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_embeddings_numpy.py tests/test_engine.py -q`
Expected: PASS (tous verts — `test_engine.py` couvre le flux d'analyse d'impact).

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/AI_for_ssh
git add lynx/src/analyzers.py lynx/tests/test_embeddings_numpy.py
git commit -m "perf(lynx): scoring d'impact latent vectorisé (similarities_to)

analyze_impact_latent remplace sa boucle cosinus Python par un produit numpy.
Sélection des candidats identique (parité + test_engine vert).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Épingler numpy + régression complète + mesure

**Files:**
- Modify: `requirements.txt`
- Test: toute la suite `lynx/tests/`

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: rien (verrouillage + preuve de non-régression).

- [ ] **Step 1: Épingler numpy dans requirements.txt**

`embeddings.py` importe désormais numpy en dur. Ajouter dans `requirements.txt` (à
côté des autres dépendances cœur ; version alignée sur `requirements.lock.txt` =
`numpy==2.4.6`) la ligne :

```
numpy>=2.0
```

_(Contrainte souple : le lock fige la version exacte ; le requirements garde juste
la borne basse compatible.)_

- [ ] **Step 2: Suite lynx complète (non-régression)**

Run: `cd lynx && PYTHONPATH=. ../.venv/bin/python -m pytest -q`
Expected: PASS — tous les tests existants (`test_engine`, `test_autofix`,
`test_generation`, `test_debate`, `test_eval_harness`, `test_llm_*`,
`test_corpus_gen`) **plus** les nouveaux, tous verts. Si un test existant casse,
c'est une vraie régression de parité → corriger avant de continuer.

- [ ] **Step 3: Validation sur le VRAI corpus (525 exigences) — parité + perf**

C'est la validation qui compte : « si ça marche sur notre corpus (525), la mise à
l'échelle passera ». On charge le corpus réel (`lynx/corpus/corpus_xl.json`, clé
`exigences`, 525 items), on prend les embeddings **une fois** via Ollama (le
`bge-m3` déjà servi), puis on prouve (a) parité stricte numpy vs référence Python sur
les vrais vecteurs, (b) le gain de temps. Nécessite Ollama up — donc étape manuelle,
pas un gate pytest.

```bash
cd ~/Documents/AI_for_ssh/lynx && PYTHONPATH=. ../.venv/bin/python - <<'PY'
import json, math, time
from src import embeddings
from src.config import EMBED_DUP_THRESHOLD

exigences = json.load(open("corpus/corpus_xl.json"))["exigences"]
textes = [e.get("texte", "") for e in exigences if e.get("texte")]
print(f"{len(textes)} exigences avec texte")

if not embeddings.embeddings_available():
    raise SystemExit("Ollama/bge-m3 indisponible : lancer le serveur d'abord.")
vecs = embeddings.get_embeddings(textes)   # un seul appel batch

def ref_pairs(vecs, thr):
    def cos(a, b):
        if not a or not b: return 0.0
        dot = sum(x*y for x, y in zip(a, b))
        na = math.sqrt(sum(x*x for x in a)); nb = math.sqrt(sum(y*y for y in b))
        return dot/(na*nb) if na and nb else 0.0
    out = []
    for i in range(len(vecs)):
        for j in range(i+1, len(vecs)):
            s = cos(vecs[i], vecs[j])
            if s >= thr: out.append((i, j))
    return out

t = time.time(); np_pairs = embeddings.duplicate_pairs(vecs, EMBED_DUP_THRESHOLD); t_np = time.time()-t
t = time.time(); py_pairs = ref_pairs(vecs, EMBED_DUP_THRESHOLD); t_py = time.time()-t

assert [(i, j) for i, j, _ in np_pairs] == py_pairs, "PARITÉ ROMPUE sur le vrai corpus !"
print(f"PARITÉ OK : {len(np_pairs)} doublons détectés, identiques.")
print(f"Dédup 525 réelles : numpy={t_np*1000:.0f}ms  python={t_py*1000:.0f}ms  "
      f"x{t_py/max(t_np,1e-6):.0f}")
PY
```

Expected: `PARITÉ OK` (mêmes paires que la référence Python sur les vrais vecteurs)
et un facteur d'accélération à consigner. Si la parité casse ici, ne pas committer :
c'est une régression réelle sur les données cibles.

- [ ] **Step 4: Commit**

```bash
cd ~/Documents/AI_for_ssh
git add requirements.txt
git commit -m "chore(lynx): épingler numpy (dépendance dure de embeddings)

Phase 0 du passage à l'échelle LynX terminée : dédup et scoring vectorisés,
parité prouvée (unit + corpus réel 525 exigences), O(N²) Python éliminé.
Bench dédup 525 réelles : numpy ~Xms vs python ~Yms (xZ).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage (Phase 0)** : la spec, Phase 0, demande « remplacer le cosinus
Python O(N²) par numpy » — couvert par Task 1 (helpers) + Task 2 (audit dédup) +
Task 3 (impact latent). Les autres usages de `cosine` : `most_similar` (réécrit en
Task 1, utilisé par `analyze_redondance`) est couvert. `cosine(a, b)` scalaire reste
pour toute utilisation ponctuelle — non O(N²), pas besoin de le toucher. Aucun autre
site O(N²) recensé (`grep cosine/most_similar/_embedding_duplicates`).

**2. Placeholder scan** : aucun TODO/TBD ; tout le code des steps est complet ; les
messages français sont copiés verbatim.

**3. Type consistency** : `duplicate_pairs` renvoie `List[Tuple[int, int, float]]` —
consommé en `(i, j, s)` par Task 2. `similarities_to` renvoie `List[float]` — consommé
par Task 3 en `zip(candidates, sims)`. `most_similar` garde `(id, score)`. Cohérent.

**Note de parité** : les comparaisons de seuil (`>=`) sur des flottants numpy (float64)
vs Python (float64) coïncident à ~1e-12 ; un basculement de verdict n'arriverait que
pour une similarité EXACTEMENT au seuil (probabilité nulle avec de vrais embeddings).
Les tests de parité utilisent une tolérance 1e-9 sur les scores et une égalité stricte
sur les paires/constats.
