# Passe finale de portabilité — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Exporter AI_for_ssh sur une machine (y compris réseau restreint) avec une seule commande pip, une seule doc d'installation, et un `serve.py` qui signale chaque prérequis manquant avec la commande exacte.

**Architecture:** Fusion des 5 manifestes Python en 3 (`requirements.txt` unique + `requirements-gpu.txt` optionnel + lock régénéré) ; pre-flight léger `check_setup()` dans `serve.py` (filesystem + HTTP, zéro import lourd) ; consolidation docs avec `SETUP_PORTABLE.md` comme unique source de vérité, dotée d'une annexe « Réseau restreint ».

**Tech Stack:** Python 3.11+, pip, pytest ; FastAPI/Next.js inchangés.

**Spec:** `docs/superpowers/specs/2026-07-21-portabilite-design.md`

## Global Constraints

- Cible : **réseau restreint** (pas d'internet, pas d'assistant IA) — toute étape de téléchargement doit avoir un équivalent « copie hors-ligne » documenté.
- Pre-flight **non bloquant** et **sans import lourd** (pas de `import spacy/torch` ; filesystem + `urllib` uniquement).
- Le venv du projet est `.venv/` (partagé avec `rag_project`, cf. MIGRATION.md §8) — le lock se régénère avec `.venv/bin/pip freeze`.
- Suite de tests : 154 tests racine (`python -m pytest`) + 121 LynX (`cd lynx && python -m pytest tests/`) — tout doit rester vert.
- Variable du chemin reranker : `CROSS_ENCODER_LOCAL_PATH` (défaut `<racine>/models/bge-reranker-v2-m3`, cf. `env_config.py:235-238`).
- Style commits : convention existante (`chore:`, `refactor(rag):`, `docs:` …), branche `chore/declutter-aiforssh`.

---

### Task 1 : requirements fusionnés (5 fichiers → 3)

**Files:**
- Modify: `requirements.txt`
- Delete: `lynx/requirements.txt`, `requirements-dev.txt`
- Regenerate: `requirements.lock.txt`

**Interfaces:**
- Produces: `requirements.txt` unique installant base + LynX + dev ; c'est ce que Task 3 documentera.

- [ ] **Step 1 : Fusionner les sections dans `requirements.txt`**

Remplacer l'en-tête du fichier (les lignes de commentaire avant la première section) par :

```text
# ===================================================================
# Requirements — UNIQUE et CROSS-PLATFORM (Linux / macOS / Windows, CPU)
# ===================================================================
# UNE seule commande installe tout (RAG + LynX + dev), partout, sans GPU :
#
#     python -m venv .venv
#     # Linux/macOS :  source .venv/bin/activate
#     # Windows     :  .venv\Scripts\activate
#     pip install -r requirements.txt
#     python -m spacy download fr_core_news_sm   # modèle NER français
#
# ACCÉLÉRATION GPU NVIDIA (optionnelle, ~10× sur la génération/rerank) :
#     pip install -r requirements-gpu.txt        # builds CUDA de torch/onnxruntime
#
# Python 3.11+ recommandé. Services requis au runtime : Ollama + MongoDB.
# Guide complet (dont install HORS-LIGNE / réseau restreint) : SETUP_PORTABLE.md.
# Reproduction exacte de la machine de référence : requirements.lock.txt.
# ===================================================================
```

Puis, **avant** la section `# ─────────────── Tests (dev) ───────────────`, insérer :

```text
# ─────────────── LynX / AI for Requirements (module lynx/) ───────────────
# pydantic et httpx sont déjà tirés transitivement (fastapi, mcp) ;
# streamlit-agraph sert au graphe DAG de l'UI Streamlit legacy.
streamlit-agraph>=0.0.45
```

Et remplacer la section finale `# ─────────────── Tests (dev) ───────────────` (qui ne contient que `pytest>=8.0`) par :

```text
# ─────────────── Dev : tests & notebooks ───────────────
pytest>=8.0
nbformat>=5.9
nbconvert>=7.0
ipykernel>=6.0
jupyterlab>=4.0
```

Le reste du fichier (sections Deep Learning → Utilitaires) est inchangé.

- [ ] **Step 2 : Supprimer les deux manifestes absorbés**

```bash
git rm lynx/requirements.txt requirements-dev.txt
```

- [ ] **Step 3 : Vérifier que la fusion résout dans un venv témoin**

```bash
python3 -m venv /tmp/claude-1000/-home-marsattacks/35f49346-be46-42f6-85fe-529a1ee2b761/scratchpad/check-venv
/tmp/claude-1000/-home-marsattacks/35f49346-be46-42f6-85fe-529a1ee2b761/scratchpad/check-venv/bin/pip install --dry-run -q -r requirements.txt 2>&1 | tail -3
```

Attendu : la résolution se termine par `Would install …` (liste de paquets), **aucune erreur** `ResolutionImpossible`. (Nécessite internet — on est sur la machine de dev, pas la machine cible.)

- [ ] **Step 4 : Régénérer le lock avec en-tête explicite**

```bash
{ cat <<'EOF'
# ===================================================================
# Lock complet — photographie exacte de la MACHINE DE RÉFÉRENCE
# (Linux x86_64, NVIDIA, roues nvidia-*-cu13). Sert à reproduire cette
# machine à l'identique ; pour une install cross-platform, utiliser
# requirements.txt. Régénéré par : .venv/bin/pip freeze
# ===================================================================
EOF
.venv/bin/pip freeze; } > requirements.lock.txt
wc -l requirements.lock.txt
```

Attendu : ~220-260 lignes ; `grep streamlit-agraph requirements.lock.txt` renvoie `streamlit-agraph==0.0.45`.

- [ ] **Step 5 : Vérifier que rien ne référence les fichiers supprimés**

```bash
grep -rn --include='*.py' --include='*.sh' --include='*.toml' --include='*.cfg' \
  -E 'lynx/requirements\.txt|requirements-dev\.txt' --exclude-dir=.venv --exclude-dir=node_modules .
```

Attendu : seules des mentions **doc** (README.md, MIGRATION.md, lynx/README.md — traitées en Task 3). Aucune mention dans du code ou script.

- [ ] **Step 6 : Suite de tests complète**

```bash
python -m pytest -q && (cd lynx && python -m pytest tests/ -q)
```

Attendu : `154 passed` puis `121 passed` (aucun test ne lit les requirements, c'est un filet de non-régression).

- [ ] **Step 7 : Commit**

```bash
git add requirements.txt requirements.lock.txt
git commit -m "chore(deps): un seul requirements.txt (base + LynX + dev), lock régénéré

- absorbe lynx/requirements.txt (épingle streamlit==1.28.0 morte, conflit résolu)
- absorbe requirements-dev.txt (notebooks)
- lock = photographie de la machine de référence (en-tête explicite)"
```

(`git rm` du Step 2 est déjà indexé, il part dans ce commit.)

---

### Task 2 : pre-flight `check_setup()` dans `serve.py`

**Files:**
- Modify: `serve.py` (fonctions ajoutées après `_wait`, appel dans `main()`)
- Test: `tests/test_serve_preflight.py` (nouveau)

**Interfaces:**
- Consumes: `_port_open(port)` existant dans `serve.py:33`.
- Produces: `collect_missing(root: Path, env: Mapping, spacy_ok: bool, ollama_tags: list[str] | None, node_ok: bool) -> list[tuple[str, str]]` (pure, testée) et `check_setup() -> None` (wrapper runtime, appelée dans `main()` après les `_wait`).

- [ ] **Step 1 : Écrire les tests (échouent d'abord)**

Créer `tests/test_serve_preflight.py` :

```python
"""Pre-flight de serve.py : collect_missing est pure et testable hors-ligne."""
from pathlib import Path

from serve import collect_missing


def _env(tmp_path, **extra):
    base = {"CROSS_ENCODER_LOCAL_PATH": str(tmp_path / "reranker")}
    base.update(extra)
    return base


def _reranker_ok(tmp_path):
    d = tmp_path / "reranker"
    d.mkdir()
    (d / "model.safetensors").touch()
    return d


def test_tout_present_rien_a_signaler(tmp_path):
    _reranker_ok(tmp_path)
    missing = collect_missing(
        tmp_path, _env(tmp_path, EMBED_MODEL="bge-m3:567m", GEN_MODEL="mistral-small3.2:latest"),
        spacy_ok=True, ollama_tags=["bge-m3:567m", "mistral-small3.2:latest"], node_ok=True,
    )
    assert missing == []


def test_spacy_absent(tmp_path):
    _reranker_ok(tmp_path)
    missing = collect_missing(tmp_path, _env(tmp_path), spacy_ok=False,
                              ollama_tags=None, node_ok=True)
    assert any("spaCy" in label for label, _ in missing)
    assert any("spacy download fr_core_news_sm" in cmd for _, cmd in missing)


def test_reranker_absent_ou_vide(tmp_path):
    # dossier inexistant
    missing = collect_missing(tmp_path, _env(tmp_path), spacy_ok=True,
                              ollama_tags=None, node_ok=True)
    assert any("reranker" in label for label, _ in missing)
    # dossier présent mais vide
    (tmp_path / "reranker").mkdir()
    missing = collect_missing(tmp_path, _env(tmp_path), spacy_ok=True,
                              ollama_tags=None, node_ok=True)
    assert any("huggingface-cli download" in cmd for _, cmd in missing)


def test_modele_ollama_manquant(tmp_path):
    _reranker_ok(tmp_path)
    missing = collect_missing(
        tmp_path, _env(tmp_path, EMBED_MODEL="bge-m3:567m", GEN_MODEL="mistral-small3.2:latest"),
        spacy_ok=True, ollama_tags=["bge-m3:567m"], node_ok=True,
    )
    assert [c for _, c in missing] == ["ollama pull mistral-small3.2:latest"]


def test_ollama_injoignable_pas_de_faux_positif(tmp_path):
    # ollama_tags=None (service down) : on ne signale PAS les modèles.
    _reranker_ok(tmp_path)
    missing = collect_missing(
        tmp_path, _env(tmp_path, EMBED_MODEL="bge-m3:567m", GEN_MODEL="mistral-small3.2:latest"),
        spacy_ok=True, ollama_tags=None, node_ok=True,
    )
    assert missing == []


def test_comparaison_modeles_ignore_le_tag(tmp_path):
    # EMBED_MODEL "bge-m3:567m" doit matcher le tag installé "bge-m3:latest".
    _reranker_ok(tmp_path)
    missing = collect_missing(
        tmp_path, _env(tmp_path, EMBED_MODEL="bge-m3:567m"),
        spacy_ok=True, ollama_tags=["bge-m3:latest"], node_ok=True,
    )
    assert missing == []


def test_node_absent(tmp_path):
    _reranker_ok(tmp_path)
    missing = collect_missing(tmp_path, _env(tmp_path), spacy_ok=True,
                              ollama_tags=None, node_ok=False)
    assert any("Node" in label for label, _ in missing)
```

- [ ] **Step 2 : Vérifier qu'ils échouent**

```bash
python -m pytest tests/test_serve_preflight.py -q
```

Attendu : `ImportError: cannot import name 'collect_missing' from 'serve'`.

- [ ] **Step 3 : Implémenter dans `serve.py`**

Insérer après la fonction `_wait` (`serve.py:80-86`) :

```python
# ─────────────────────────── Pre-flight (portabilité) ───────────────────────────
# Vérifications RAPIDES (filesystem + HTTP, aucun import lourd) de ce qui bloque
# un nouveau venu. Non bloquant : l'app démarre quand même, comme pour Mongo/
# Ollama absents. L'audit approfondi reste `python diagnostic.py`.

def collect_missing(root, env, spacy_ok, ollama_tags, node_ok):
    """Pure : liste [(prérequis manquant, commande pour corriger)].

    ollama_tags : modèles Ollama installés, ou None si le service est injoignable
    (dans ce cas on ne signale rien — pas de faux positif).
    """
    missing = []
    if not spacy_ok:
        missing.append(("modèle spaCy fr_core_news_sm",
                        "python -m spacy download fr_core_news_sm"))
    ce_dir = Path(env.get("CROSS_ENCODER_LOCAL_PATH")
                  or root / "models" / "bge-reranker-v2-m3")
    if not ce_dir.is_dir() or not any(ce_dir.iterdir()):
        missing.append(("cross-encoder de reranking (models/bge-reranker-v2-m3)",
                        "huggingface-cli download BAAI/bge-reranker-v2-m3 "
                        "--local-dir models/bge-reranker-v2-m3"))
    if ollama_tags is not None:
        bases = {t.split(":")[0] for t in ollama_tags}
        for var in ("EMBED_MODEL", "GEN_MODEL"):
            model = env.get(var, "")
            if model and model.split(":")[0] not in bases:
                missing.append((f"modèle Ollama {model} ({var})",
                                f"ollama pull {model}"))
    if not node_ok:
        missing.append(("Node.js >= 20 (front Next.js)",
                        "voir SETUP_PORTABLE.md § Prérequis"))
    return missing


def _spacy_model_ok():
    import importlib.util
    return importlib.util.find_spec("fr_core_news_sm") is not None


def _ollama_tags():
    if not _port_open(11434):
        return None
    try:
        import json
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as r:
            data = json.load(r)
        return [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        return None


def _node_ok():
    # dev.sh charge nvm lui-même : nvm présent suffit.
    return bool(shutil.which("node")) or (Path.home() / ".nvm").is_dir()


def check_setup():
    missing = collect_missing(ROOT, os.environ, _spacy_model_ok(),
                              _ollama_tags(), _node_ok())
    if not missing:
        return
    print("Prérequis manquants (l'app démarre quand même) :")
    for label, cmd in missing:
        print(f"  ✗ {label}\n    → {cmd}")
    print("  (réseau restreint : voir SETUP_PORTABLE.md § Réseau restreint)\n")
```

Puis dans `main()` (`serve.py:117-128`), après `_wait(11434, "Ollama")` et avant `print("\nLancement de l'application...\n")`, ajouter :

```python
    check_setup()
```

(Après les `_wait` pour que l'interrogation `/api/tags` porte sur un Ollama démarré.)

- [ ] **Step 4 : Vérifier que les tests passent**

```bash
python -m pytest tests/test_serve_preflight.py -q
```

Attendu : `7 passed`.

- [ ] **Step 5 : Vérification runtime sans lancer l'app**

```bash
python -c "import serve; serve.check_setup()"                       # machine complète
CROSS_ENCODER_LOCAL_PATH=/tmp/inexistant python -c "import serve; serve.check_setup()"  # manque simulé
```

Attendu : 1re commande **silencieuse** (ou signale les vrais manques) ; 2e affiche `✗ cross-encoder…` + la commande `huggingface-cli download …` + le renvoi réseau restreint — puis rend la main (non bloquant).

- [ ] **Step 6 : Suite complète (non-régression)**

```bash
python -m pytest -q
```

Attendu : `161 passed` (154 + 7 nouveaux).

- [ ] **Step 7 : Commit**

```bash
git add serve.py tests/test_serve_preflight.py
git commit -m "feat(serve): pre-flight des prérequis au démarrage

spaCy fr_core_news_sm, reranker local, modèles Ollama, Node >= 20 :
chaque manque affiche la commande exacte (non bloquant, zéro import lourd).
Renvoi vers SETUP_PORTABLE.md § Réseau restreint pour l'install hors-ligne."
```

---

### Task 3 : docs — SETUP_PORTABLE.md source unique + annexe réseau restreint

**Files:**
- Modify: `SETUP_PORTABLE.md`, `README.md:152-166`, `MIGRATION.md` (§4, §8), `lynx/README.md:61-67`

**Interfaces:**
- Consumes: le `requirements.txt` unique (Task 1) et le comportement pre-flight (Task 2).

- [ ] **Step 1 : SETUP_PORTABLE.md — simplifier l'install et ajouter l'annexe**

Dans la section « 2) Cloner, environnement virtuel, dépendances », remplacer le bloc « Installer les dépendances » par :

````markdown
Installer les dépendances (**une seule commande** — RAG + LynX + dev inclus) :

```bash
pip install -r requirements.txt            # base CROSS-PLATFORM (CPU)
pip install -r requirements-gpu.txt        # OPTIONNEL : GPU NVIDIA (CUDA 12.x)
python -m spacy download fr_core_news_sm   # modèle NER français
```

> Au premier `python serve.py`, un **pre-flight** liste ce qui manque encore
> (modèle spaCy, reranker, modèles Ollama, Node) avec la commande exacte à
> lancer — inutile de mémoriser les étapes ci-dessous.
````

Puis ajouter, **avant** la section « Notes par OS », l'annexe :

````markdown
---

## Réseau restreint (hors-ligne)

Le projet tourne à 100 % en local — l'installation aussi. Tout ce qui se
télécharge se **prépare sur une machine connectée**, puis se **copie** :

| Artefact | Côté connecté | Côté restreint |
|---|---|---|
| Paquets Python | `pip download -r requirements.txt -d wheels/` (+ `-r requirements-gpu.txt` si GPU) | `pip install --no-index --find-links wheels/ -r requirements.txt` |
| Modèle spaCy | `pip download fr-core-news-sm -d wheels/` (roue pip standard) | installée avec les autres roues |
| Reranker | déjà un dossier local | copier `models/bge-reranker-v2-m3/` tel quel |
| Modèles Ollama | `ollama pull …` puis récupérer `~/.ollama/models` | copier `~/.ollama/models` (blobs + manifests) |
| Front Next.js | `npm install` dans `web/` | copier `web/node_modules/` (`dev.sh` saute `npm install` s'il est présent) |
| Caches Docling / EasyOCR | 1re ingestion d'un PDF (peuple `~/.cache`) | copier `~/.cache/docling` et `~/.EasyOCR` |
| Binaires MongoDB / Ollama | télécharger les installeurs | install hors-ligne ; renseigner `MONGO_BIN` / `OLLAMA_BIN` dans `.env` |

Volumes à prévoir : ~24 Go de modèles (détail : MIGRATION.md §8) + les roues
Python (torch et CUDA pèsent plusieurs Go).
````

- [ ] **Step 2 : README.md — démarrage rapide sur le requirements unique**

Dans « Démarrage rapide » (`README.md:152-163`), remplacer les deux lignes pip :

```bash
pip install -r requirements.txt                      # + requirements-gpu.txt si GPU NVIDIA
pip install -r lynx/requirements.txt                 # dépendances du module AI for Requirements
```

par une seule :

```bash
pip install -r requirements.txt                      # TOUT (RAG + LynX + dev) ; + requirements-gpu.txt si GPU NVIDIA
```

et compléter la ligne de renvoi existante vers SETUP_PORTABLE.md pour mentionner le réseau restreint :

```markdown
Guide complet (modèles, GPU, gotchas par OS, **install hors-ligne / réseau
restreint**) : **[SETUP_PORTABLE.md](SETUP_PORTABLE.md)**.
```

- [ ] **Step 3 : MIGRATION.md — §4 réécrit pour 3 fichiers, §8 allégé**

Remplacer tout le §4 (de `## 4. Dépendances Python` jusqu'à la ligne avant `## 5.`) par :

````markdown
## 4. Dépendances Python

**Trois fichiers** (installation détaillée : [SETUP_PORTABLE.md](SETUP_PORTABLE.md)) :

| Fichier | Rôle |
|---|---|
| `requirements.txt` | **unique et cross-platform** — base RAG + LynX (`streamlit-agraph`) + dev (pytest, notebooks). Une commande : `pip install -r requirements.txt` |
| `requirements-gpu.txt` | optionnel — builds CUDA 12.x de torch/onnxruntime + CuPy |
| `requirements.lock.txt` | photographie `pip freeze` de la **machine de référence** (Linux/NVIDIA, roues `nvidia-*-cu13`) — pour la reproduire à l'identique |

> Historique : `lynx/requirements.txt` (épingle morte `streamlit==1.28.0`) et
> `requirements-dev.txt` ont été absorbés dans `requirements.txt` le 2026-07-21.
````

Dans le §8, **supprimer** le point « **Conflit Streamlit** : … » (résolu par la fusion) et **remplacer** le point « **Venv partagé avec `rag_project`** » par :

```markdown
- **Venv partagé avec `rag_project`** : sur cette machine, `.venv/` est commun
  aux deux projets. Pour un export propre, créer un venv dédié depuis
  `requirements.txt` (+ `-gpu`) au lieu de copier `.venv/` ; le lock reflète
  cette machine (voir §4).
```

- [ ] **Step 4 : lynx/README.md — install par renvoi**

Remplacer la section « ## Installation » (`lynx/README.md:61-67`) par :

````markdown
## Installation

LynX est un **module embarqué** d'AI for SSH : ses dépendances sont dans le
`requirements.txt` **à la racine** (une seule commande, voir
[SETUP_PORTABLE.md](../SETUP_PORTABLE.md)). Modèles : `ollama pull
mistral-small3.2` (jugement) et `ollama pull bge-m3` (redondance).
````

- [ ] **Step 5 : Vérifier qu'aucune référence morte ne subsiste**

```bash
grep -rn --include='*.md' -E 'lynx/requirements\.txt|requirements-dev\.txt' \
  --exclude-dir=.venv --exclude-dir=node_modules --exclude-dir=docs/superpowers .
```

Attendu : plus **aucune** occurrence hors historique (la mention « Historique » de MIGRATION.md §4 est tolérée — elle décrit la fusion).

- [ ] **Step 6 : Commit**

```bash
git add SETUP_PORTABLE.md README.md MIGRATION.md lynx/README.md
git commit -m "docs: SETUP_PORTABLE.md source unique d'install + annexe réseau restreint

- install en une commande pip (requirements unique)
- annexe hors-ligne : quoi préparer côté connecté, quoi copier
- MIGRATION §4 réécrit (3 fichiers), conflit Streamlit retiré (résolu)
- lynx/README : install par renvoi vers la racine"
```
