# LynX scaling — Phase 1 : cache d'embeddings persistant (SQLite) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajouter un cache d'embeddings persistant sur disque (SQLite) en niveau L2
derrière le cache mémoire, pour ne plus ré-embedder tout le corpus au redémarrage —
sans changer une seule valeur de vecteur (parité stricte).

**Architecture:** Nouveau module `lynx/src/embed_store.py` : un fichier SQLite
(`corpus/lynx_store.sqlite`, table `embeddings`) stockant les vecteurs en **float64
bit-identiques** à ce que l'API a renvoyé. `embeddings.get_embeddings` devient un cache
3 niveaux : L1 mémoire → L2 SQLite → Ollama, avec réécriture. Transparent pour tous les
appelants (dédup, impact latent, co-références, `most_similar`). Pas d'index ANN (YAGNI
au curseur ≤2000). LynX reste autonome : cache désactivable, aucun échec si le fichier
est absent.

**Tech Stack:** Python 3, sqlite3 (stdlib), numpy (déjà là), httpx, pytest.

## Global Constraints

- **Parité stricte** : le cache ne doit changer AUCUNE similarité. Les vecteurs sont
  stockés/relus en **float64** (`np.float64`), donc bit-identiques aux floats Python
  renvoyés par l'API. Un vecteur relu du disque == le vecteur qu'aurait renvoyé Ollama.
- **LynX reste autonome** : si `EMBED_CACHE_DISK=0` ou si SQLite échoue, `get_embeddings`
  se comporte exactement comme avant (calcul API + cache mémoire). Un cache qui échoue
  ne casse jamais un appel.
- **Thread-safe** : `get_embeddings` est appelé depuis plusieurs threads (les analyseurs
  sémantiques tournent dans un `ThreadPoolExecutor`). Toute opération SQLite est protégée
  par un `threading.Lock` et la connexion est ouverte avec `check_same_thread=False`.
- **Clé de cache inchangée** : `_key(text) = sha256(EMBED_MODEL + "::" + text)` (déjà
  dans `embeddings.py`). Texte modifié → clé différente → ré-embed automatique ; modèle
  différent → pas de collision. Ne PAS réinventer la clé.
- **Isolation des tests** : les tests ne doivent JAMAIS écrire dans le vrai
  `corpus/lynx_store.sqlite` (monkeypatch du chemin vers `tmp_path`, comme le fait déjà
  `conftest.py` pour le cache LLM).
- Tests depuis `lynx/` : `PYTHONPATH=. ../.venv/bin/python -m pytest tests/<file> -q`.
  Ne PAS lancer le glob `tests/` entier (hang pré-existant hors sujet) ; lancer les
  fichiers un par un.
- venv : pas de `python` sur le PATH, toujours `../.venv/bin/python`.

---

## File Structure

- `lynx/src/embed_store.py` — **créé** : store SQLite persistant. Responsabilité unique :
  lire/écrire des vecteurs par clé de hash. `get_many`, `put_many`, `reset`, `_connect`.
- `lynx/src/config.py` — **modifié** : ajoute `EMBED_CACHE_DISK` et `EMBED_CACHE_DB`.
- `lynx/src/embeddings.py` — **modifié** : `get_embeddings` consulte L2 entre L1 et l'API,
  et écrit les nouveaux vecteurs en L2.
- `lynx/tests/conftest.py` — **modifié** : fixture autouse isolant `embed_store` vers
  `tmp_path` (aucun test ne touche le vrai fichier).
- `lynx/tests/test_embed_store.py` — **créé** : tests unitaires du store (round-trip
  float64, persistance après reset, désactivation, batch >900 clés).
- `lynx/tests/test_embeddings_cache_tiers.py` — **créé** : tests du câblage 3 niveaux
  (L2 évite l'appel API, cold-start persiste, parité des vecteurs).

---

### Task 1: Module `embed_store.py` + config + isolation des tests

**Files:**
- Create: `lynx/src/embed_store.py`
- Modify: `lynx/src/config.py`
- Modify: `lynx/tests/conftest.py`
- Test: `lynx/tests/test_embed_store.py`

**Interfaces:**
- Consumes: `config.EMBED_CACHE_DISK`, `config.EMBED_CACHE_DB`, `config.EMBED_MODEL`.
- Produces:
  - `get_many(keys: List[str]) -> Dict[str, List[float]]` — vecteurs présents (absents non renvoyés).
  - `put_many(items: Dict[str, List[float]]) -> None` — upsert (no-op si désactivé/erreur).
  - `reset() -> None` — ferme la connexion (tests / changement de chemin).

- [ ] **Step 1: Ajouter la config**

Dans `lynx/src/config.py`, après le bloc `# --- Embeddings ...`, ajouter :

```python
# --- Cache d'embeddings persistant (SQLite) --------------------------------
# Survit au process : au redémarrage, on relit les vecteurs du disque au lieu de
# tout ré-embedder. EMBED_CACHE_DISK=0 pour couper (LynX reste fonctionnel).
EMBED_CACHE_DISK = os.environ.get("EMBED_CACHE_DISK", "1") != "0"
EMBED_CACHE_DB = Path(os.environ.get("EMBED_CACHE_DB", DATA_DIR / "lynx_store.sqlite"))
```

(`Path` et `os` sont déjà importés en tête de `config.py` ; `DATA_DIR` y est défini.)

- [ ] **Step 2: Write the failing test**

Créer `lynx/tests/test_embed_store.py` :

```python
"""Store d'embeddings SQLite : round-trip float64 exact, persistance, désactivation."""
import numpy as np
import pytest

from src import embed_store


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch, tmp_path):
    monkeypatch.setattr(embed_store, "EMBED_CACHE_DISK", True)
    monkeypatch.setattr(embed_store, "EMBED_CACHE_DB", tmp_path / "s.sqlite")
    embed_store.reset()
    yield
    embed_store.reset()


def test_put_then_get_roundtrip_is_exact():
    v = list(np.random.default_rng(0).normal(size=8))
    embed_store.put_many({"k1": v})
    got = embed_store.get_many(["k1"])
    assert "k1" in got
    # Round-trip float64 : valeurs strictement identiques (pas de perte de précision).
    assert got["k1"] == v


def test_get_missing_key_absent():
    embed_store.put_many({"a": [1.0, 2.0]})
    got = embed_store.get_many(["a", "absente"])
    assert set(got.keys()) == {"a"}


def test_persistence_across_reset(monkeypatch, tmp_path):
    # Même fichier, connexion recréée = simulate un redémarrage de process.
    db = tmp_path / "persist.sqlite"
    monkeypatch.setattr(embed_store, "EMBED_CACHE_DB", db)
    embed_store.reset()
    embed_store.put_many({"kp": [3.0, 4.0, 5.0]})
    embed_store.reset()  # ferme la connexion
    got = embed_store.get_many(["kp"])  # rouvre depuis le disque
    assert got["kp"] == [3.0, 4.0, 5.0]


def test_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(embed_store, "EMBED_CACHE_DISK", False)
    embed_store.reset()
    embed_store.put_many({"x": [1.0]})
    assert embed_store.get_many(["x"]) == {}


def test_large_batch_over_sqlite_param_limit():
    items = {f"k{i}": [float(i)] for i in range(2000)}
    embed_store.put_many(items)
    got = embed_store.get_many([f"k{i}" for i in range(2000)])
    assert len(got) == 2000
    assert got["k1999"] == [1999.0]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /home/marsattacks/Documents/AI_for_ssh/lynx && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_embed_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.embed_store'`.

- [ ] **Step 4: Write the module**

Créer `lynx/src/embed_store.py` :

```python
"""Cache d'embeddings persistant (SQLite), niveau L2 derrière le cache mémoire.

But : survivre au process. Au redémarrage, on relit les vecteurs du disque au lieu
de tout ré-embedder. Transparent pour les appelants (via ``embeddings.get_embeddings``).

Les vecteurs sont stockés en float64 (bit-identiques aux floats Python renvoyés par
l'API), donc le cache ne change AUCUNE similarité : parité stricte préservée. Un cache
qui échoue ne casse jamais un appel — LynX reste fonctionnel sans lui.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np

from .config import EMBED_CACHE_DB, EMBED_CACHE_DISK, EMBED_MODEL

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _connect() -> Optional[sqlite3.Connection]:
    """Connexion singleton (thread-safe via ``check_same_thread=False`` + ``_lock``)."""
    global _conn
    if not EMBED_CACHE_DISK:
        return None
    if _conn is None:
        EMBED_CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(EMBED_CACHE_DB), check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute(
            "CREATE TABLE IF NOT EXISTS embeddings ("
            "key TEXT PRIMARY KEY, model TEXT, dim INTEGER, vec BLOB, created_at TEXT)")
        _conn.commit()
    return _conn


def get_many(keys: List[str]) -> Dict[str, List[float]]:
    """Vecteurs présents en cache disque, par clé. Les clés absentes ne sont pas
    renvoyées. Jamais d'exception propagée : en cas d'erreur SQLite, renvoie ce qui a
    pu être lu (au pire ``{}``)."""
    if not keys:
        return {}
    with _lock:
        try:
            conn = _connect()
            if conn is None:
                return {}
            out: Dict[str, List[float]] = {}
            CHUNK = 900  # SQLite limite le nombre de paramètres (~999)
            for start in range(0, len(keys), CHUNK):
                batch = keys[start:start + CHUNK]
                ph = ",".join("?" for _ in batch)
                for key, vec in conn.execute(
                        f"SELECT key, vec FROM embeddings WHERE key IN ({ph})", batch):
                    out[key] = np.frombuffer(vec, dtype=np.float64).tolist()
            return out
        except Exception:
            return {}


def put_many(items: Dict[str, List[float]]) -> None:
    """Upsert des vecteurs (float64 -> bytes). No-op si cache désactivé ou en erreur."""
    if not items:
        return
    with _lock:
        try:
            conn = _connect()
            if conn is None:
                return
            now = datetime.now().isoformat(timespec="seconds")
            rows = []
            for key, vec in items.items():
                arr = np.asarray(vec, dtype=np.float64)
                rows.append((key, EMBED_MODEL, int(arr.size), arr.tobytes(), now))
            conn.executemany(
                "INSERT OR REPLACE INTO embeddings (key, model, dim, vec, created_at) "
                "VALUES (?, ?, ?, ?, ?)", rows)
            conn.commit()
        except Exception:
            pass  # un cache qui échoue ne doit jamais casser l'appel


def reset() -> None:
    """Ferme la connexion (tests / changement de chemin de la base)."""
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None
```

- [ ] **Step 5: Isoler embed_store dans conftest**

Dans `lynx/tests/conftest.py`, ajouter une fixture autouse pour qu'AUCUN test n'écrive
dans le vrai fichier (ajouter l'import et la fixture après la fixture existante) :

```python
from src import embed_store


@pytest.fixture(autouse=True)
def _embed_store_isole(monkeypatch, tmp_path):
    """Aucun test n'écrit dans le vrai corpus/lynx_store.sqlite."""
    monkeypatch.setattr(embed_store, "EMBED_CACHE_DB", tmp_path / "lynx_store.sqlite")
    embed_store.reset()
    yield
    embed_store.reset()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /home/marsattacks/Documents/AI_for_ssh/lynx && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_embed_store.py -q`
Expected: PASS (5 passed).

- [ ] **Step 7: Commit**

```bash
cd ~/Documents/AI_for_ssh
git add lynx/src/embed_store.py lynx/src/config.py lynx/tests/conftest.py lynx/tests/test_embed_store.py
git commit -m "feat(lynx): store d'embeddings persistant SQLite (L2)

Nouveau module embed_store : vecteurs float64 bit-identiques en SQLite sous
corpus/. Thread-safe, désactivable, ne casse jamais un appel. Isolation tests via
conftest. Pas encore branché dans get_embeddings (Task 2).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Brancher le L2 dans `get_embeddings`

**Files:**
- Modify: `lynx/src/embeddings.py:48-77` (`get_embeddings`)
- Test: `lynx/tests/test_embeddings_cache_tiers.py`

**Interfaces:**
- Consumes: `embed_store.get_many`, `embed_store.put_many` (Task 1).
- Produces: `get_embeddings` inchangé en signature (`List[str] -> Optional[List[List[float]]]`),
  comportement identique côté valeurs, mais lit/écrit le L2.

- [ ] **Step 1: Write the failing test**

Créer `lynx/tests/test_embeddings_cache_tiers.py` :

```python
"""Cache 3 niveaux : L2 évite l'appel API, le cold-start persiste, parité des vecteurs."""
import numpy as np
import pytest

from src import embeddings, embed_store


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    # L1 vide, L2 sur tmp (le conftest isole déjà EMBED_CACHE_DB, on force le reset).
    embeddings._cache.clear()
    monkeypatch.setattr(embed_store, "EMBED_CACHE_DISK", True)
    embed_store.reset()
    yield
    embeddings._cache.clear()
    embed_store.reset()


def _fake_api(vectors_by_text):
    """Renvoie un faux httpx.post qui répond des embeddings pour les textes demandés."""
    class _Resp:
        def __init__(self, texts):
            self._texts = texts
        def raise_for_status(self):
            pass
        def json(self):
            return {"data": [{"index": i, "embedding": vectors_by_text[t]}
                             for i, t in enumerate(self._texts)]}
    calls = {"n": 0, "texts": []}
    def fake_post(url, headers=None, json=None, timeout=None):
        texts = json["input"]
        calls["n"] += 1
        calls["texts"].append(list(texts))
        return _Resp(texts)
    return fake_post, calls


def test_cold_start_calls_api_and_persists(monkeypatch):
    vecs = {"a": [1.0, 2.0, 3.0], "b": [4.0, 5.0, 6.0]}
    fake_post, calls = _fake_api(vecs)
    monkeypatch.setattr(embeddings.httpx, "post", fake_post)
    out = embeddings.get_embeddings(["a", "b"])
    assert out == [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
    assert calls["n"] == 1  # un appel API
    # Persisté en L2 : présent par clé.
    disk = embed_store.get_many([embeddings._key("a"), embeddings._key("b")])
    assert disk[embeddings._key("a")] == [1.0, 2.0, 3.0]


def test_warm_l2_skips_api(monkeypatch):
    vecs = {"a": [1.0, 2.0, 3.0]}
    # Pré-remplit le L2 puis vide le L1 : l'appel suivant NE doit PAS toucher l'API.
    embed_store.put_many({embeddings._key("a"): vecs["a"]})
    embeddings._cache.clear()
    def boom(*args, **kwargs):
        raise AssertionError("l'API ne doit pas être appelée quand le L2 a le vecteur")
    monkeypatch.setattr(embeddings.httpx, "post", boom)
    out = embeddings.get_embeddings(["a"])
    assert out == [[1.0, 2.0, 3.0]]  # parité : vecteur identique au disque


def test_partial_l2_hit_only_calls_api_for_misses(monkeypatch):
    embed_store.put_many({embeddings._key("known"): [9.0, 9.0]})
    embeddings._cache.clear()
    fake_post, calls = _fake_api({"newone": [1.0, 1.0]})
    monkeypatch.setattr(embeddings.httpx, "post", fake_post)
    out = embeddings.get_embeddings(["known", "newone"])
    assert out == [[9.0, 9.0], [1.0, 1.0]]
    assert calls["texts"] == [["newone"]]  # l'API n'a reçu que le manquant
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/marsattacks/Documents/AI_for_ssh/lynx && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_embeddings_cache_tiers.py -q`
Expected: FAIL — `test_warm_l2_skips_api` / `test_partial_l2_hit...` échouent car `get_embeddings`
appelle encore l'API (le L2 n'est pas consulté).

- [ ] **Step 3: Câbler le L2**

Dans `lynx/src/embeddings.py`, remplacer le corps de `get_embeddings` par :

```python
def get_embeddings(texts: List[str]) -> Optional[List[List[float]]]:
    """Embeddings de plusieurs textes en un seul appel. Cache 3 niveaux :
    L1 mémoire -> L2 SQLite (persistant) -> API. Écrit les nouveaux vecteurs dans les
    deux caches. Les vecteurs relus du L2 sont bit-identiques à ceux de l'API."""
    if EMBED_DISABLED or not texts:
        return None
    out: List[Optional[List[float]]] = [None] * len(texts)
    missing_idx, missing_txt = [], []
    for i, t in enumerate(texts):
        k = _key(t)
        if k in _cache:
            out[i] = _cache[k]
        else:
            missing_idx.append(i)
            missing_txt.append(t)

    # L2 : cache disque persistant (survit au process).
    if missing_txt:
        from . import embed_store
        disk = embed_store.get_many([_key(t) for t in missing_txt])
        if disk:
            kept_idx, kept_txt = [], []
            for pos, t in enumerate(missing_txt):
                k = _key(t)
                if k in disk:
                    out[missing_idx[pos]] = disk[k]
                    _cache[k] = disk[k]
                else:
                    kept_idx.append(missing_idx[pos])
                    kept_txt.append(t)
            missing_idx, missing_txt = kept_idx, kept_txt

    if missing_txt:
        try:
            r = httpx.post(f"{EMBED_BASE_URL}/embeddings", headers=_headers(),
                           json={"model": EMBED_MODEL, "input": missing_txt}, timeout=LLM_TIMEOUT_SECONDS)
            r.raise_for_status()
            data = r.json()["data"]
            if len(data) != len(missing_txt):
                return None  # réponse incomplète : on ne devine pas l'alignement
            new_vecs: dict = {}
            for pos, item in enumerate(data):
                # L'API compatible OpenAI peut réordonner : on se fie au champ `index`.
                k = item.get("index", pos)
                vec = item["embedding"]
                out[missing_idx[k]] = vec
                key = _key(missing_txt[k])
                _cache[key] = vec
                new_vecs[key] = vec
            from . import embed_store
            embed_store.put_many(new_vecs)  # persiste les nouveaux (survit au process)
        except Exception:
            return None
    return out  # aligné sur ``texts`` (peut contenir None si un item a échoué)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/marsattacks/Documents/AI_for_ssh/lynx && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_embeddings_cache_tiers.py tests/test_embed_store.py tests/test_embeddings_numpy.py -q`
Expected: PASS (tous verts : câblage + store + parité numpy Phase 0 intacte).

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/AI_for_ssh
git add lynx/src/embeddings.py lynx/tests/test_embeddings_cache_tiers.py
git commit -m "feat(lynx): get_embeddings en cache 3 niveaux (L1 mémoire -> L2 SQLite -> API)

Consulte le store disque avant l'API et y écrit les nouveaux vecteurs. Fin du
ré-embed au cold-start. Vecteurs relus bit-identiques (parité). Appelants inchangés.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Validation cold-start sur corpus réel + non-régression

**Files:**
- Test: toute la suite `lynx/tests/` (par fichier)

**Interfaces:**
- Consumes: Tasks 1-2.
- Produces: rien (preuve de gain + non-régression).

- [ ] **Step 1: Non-régression (par fichier)**

Ne PAS lancer `pytest tests/` en glob (hang pré-existant). Lancer chaque fichier :

```bash
cd /home/marsattacks/Documents/AI_for_ssh/lynx
for f in tests/test_*.py; do
  echo "== $f =="; PYTHONPATH=. ../.venv/bin/python -m pytest "$f" -q 2>&1 | tail -1
done
```

Expected: chaque fichier vert. Référence : les fichiers Phase 0 (test_embeddings_numpy 6,
test_audit_dedup_parity 1, test_engine, test_autofix 7, test_generation 5, test_debate 9,
test_eval_harness 5, test_llm_cache 4, test_llm_schemas 9, test_corpus_gen 7) **plus**
test_embed_store (5) et test_embeddings_cache_tiers (3). Si `test_engine` stalle sur des
appels LLM réels (Ollama lancé), le relancer avec `LLM_DISABLE=1` en préfixe (intention
documentée du fichier). Un ÉCHEC (pas un stall) = régression → BLOCKED.

- [ ] **Step 2: Validation cold-start sur le corpus réel (525) — nécessite Ollama**

Prouve que le 2e passage (nouvelle connexion = simulate redémarrage) ne rappelle PAS
l'API. Nécessite Ollama up (bge-m3).

```bash
cd /home/marsattacks/Documents/AI_for_ssh/lynx && PYTHONPATH=. ../.venv/bin/python - <<'PY'
import json, time, tempfile, os
from pathlib import Path
from src import embeddings, embed_store

# Base temporaire dédiée (ne touche pas le vrai fichier).
tmp = Path(tempfile.mkdtemp()) / "cold.sqlite"
embed_store.EMBED_CACHE_DB = tmp
embed_store.reset()

exigences = json.load(open("corpus/corpus_xl.json"))["exigences"]
textes = [e.get("texte", "") for e in exigences if e.get("texte")][:200]  # échantillon
if not embeddings.embeddings_available():
    raise SystemExit("Ollama/bge-m3 indisponible : lancer le serveur d'abord.")

# 1er passage : calcul + persistance.
t = time.time(); v1 = embeddings.get_embeddings(textes); cold = time.time() - t

# Simule un redémarrage : vide L1, ferme/rouvre L2 (même fichier).
embeddings._cache.clear()
embed_store.reset()

# Interdit tout appel API : si le L2 ne suffit pas, ça lèvera.
import httpx
_orig = httpx.post
def _boom(*a, **k):
    raise AssertionError("cold-start a rappelé l'API : le L2 n'a pas servi")
httpx.post = _boom
try:
    t = time.time(); v2 = embeddings.get_embeddings(textes); warm = time.time() - t
finally:
    httpx.post = _orig

assert v2 == v1, "PARITÉ ROMPUE : vecteurs relus != vecteurs calculés"
print(f"COLD-START OK : {len(textes)} embeddings servis depuis SQLite sans appel API.")
print(f"1er passage (API+persist) : {cold*1000:.0f}ms ; 2e (disque seul) : {warm*1000:.0f}ms")
PY
```

Expected: `COLD-START OK` (le 2e passage sert 200 vecteurs depuis le disque, zéro appel
API, vecteurs identiques). Si l'assertion parité casse, ne pas continuer : BLOCKED.

- [ ] **Step 3: Commit (clôture Phase 1)**

```bash
cd ~/Documents/AI_for_ssh
git commit --allow-empty -m "test(lynx): Phase 1 validée — cold-start servi depuis SQLite, parité OK

Non-régression par fichier verte ; sur le corpus réel, le 2e passage sert les
embeddings depuis le disque sans appel API, vecteurs bit-identiques.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage (Phase 1)** : la spec révisée demande « cache-through 3 niveaux
(L1 mémoire → L2 SQLite → Ollama) transparent derrière `get_embeddings`, pas d'index
ANN, gate = parité + plus de ré-embed au cold-start ». Couvert : Task 1 (store SQLite
`embeddings`), Task 2 (câblage 3 niveaux), Task 3 (validation cold-start + parité). Pas
d'ANN (conforme). Schéma table = celui de la spec (`key/model/dim/vec/created_at`).

**2. Placeholder scan** : aucun TODO/placeholder ; tout le code des steps est complet ;
la clé réutilise `_key` existant (pas réinventée).

**3. Type consistency** : `get_many(keys: List[str]) -> Dict[str, List[float]]` et
`put_many(items: Dict[str, List[float]])` sont consommés cohéremment dans `get_embeddings`
(clés = `_key(t)`, valeurs = listes de floats). `reset()` sans argument. La fixture
conftest utilise `embed_store.reset()`.

**Note de parité** : stockage/relecture en **float64** → `np.frombuffer(...).tolist()`
rend exactement les floats Python d'origine (Python `float` == float64), donc un vecteur
servi depuis le L2 est bit-identique à celui de l'API. Aucune similarité, aucun verdict
ne change. Les tests l'asservissent par égalité stricte (`got["k1"] == v`, `v2 == v1`).

**Note d'isolation** : la fixture autouse de `conftest.py` (Task 1 Step 5) redirige
`EMBED_CACHE_DB` vers `tmp_path` pour TOUS les tests → aucun test n'écrit dans le vrai
`corpus/lynx_store.sqlite`. Les fixtures locales des deux fichiers de test forcent en
plus `reset()` + `_cache.clear()` pour partir d'un état propre.
