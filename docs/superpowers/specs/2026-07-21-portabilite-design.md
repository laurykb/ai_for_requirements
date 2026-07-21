# Passe finale de portabilité — design

**Date** : 2026-07-21 · **Branche** : `chore/declutter-aiforssh`

## Objectif

Rendre l'export du projet sur une nouvelle machine aussi simple et lisible que
possible pour un développeur qui découvre le dépôt : une seule commande pip,
une seule doc d'installation, et `serve.py` qui signale ce qui manque avec la
commande exacte pour corriger.

## État de départ (constat)

- 5 fichiers de dépendances : `requirements.txt`, `requirements-gpu.txt`,
  `requirements-dev.txt`, `requirements.lock.txt`, `lynx/requirements.txt`.
- `lynx/requirements.txt` épingle `streamlit==1.28.0` alors que la base exige
  `>=1.40` (conflit documenté dans MIGRATION.md §« Conflit Streamlit ») ; le
  venv réel tourne en 1.58 — l'épingle est morte.
- Le lock est périmé (`streamlit-agraph` absent, versions décalées).
- L'installation est expliquée à 4 endroits : README.md, SETUP_PORTABLE.md,
  MIGRATION.md, lynx/README.md.
- Étapes post-pip non automatisées : modèle spaCy, reranker HF (~2,2 Go),
  modèles Ollama.

## Décisions (validées)

1. **Fusion des requirements** — forme cible : 3 fichiers.
2. **Doc unique** — `SETUP_PORTABLE.md` est LA source de vérité d'installation.
3. **Pas de script d'install** — `serve.py` vérifie au démarrage et guide
   (affiche la commande exacte pour chaque manque, non bloquant).

## 1. Dépendances : 5 fichiers → 3

- `requirements.txt` **unique**, en sections commentées :
  - base actuelle (deep learning CPU, embeddings, indexation, Docling, NLP,
    Mongo, MCP, UI Streamlit legacy, FastAPI, utilitaires) ;
  - section **LynX** : `streamlit-agraph>=0.0.45` (seule dépendance manquante ;
    `pydantic`/`httpx` sont déjà transitives) ;
  - section **dev/notebooks** : contenu de `requirements-dev.txt`
    (`nbformat`, `nbconvert`, `ipykernel`, `jupyterlab` ; `pytest` déjà là).
- **Supprimés** : `lynx/requirements.txt`, `requirements-dev.txt`.
- **Gardés** : `requirements-gpu.txt` (seul fichier optionnel),
  `requirements.lock.txt` **régénéré** depuis le venv courant (`pip freeze`).
  Le lock actuel est doublement périmé (il épingle `torch==2.5.1+cu121` alors
  que le venv tourne en 2.12.1). Un freeze reflète forcément la machine de
  référence (Linux/NVIDIA, roues `nvidia-*-cu13`) : un en-tête le dira
  explicitement — le lock sert à reproduire la machine de référence, les
  installs cross-platform passent par `requirements.txt`.
- Effet : `pip install -r requirements.txt` suffit ; le conflit Streamlit
  disparaît.

## 2. `serve.py` : pre-flight au démarrage

Fonction `check_setup()` appelée en tête de `main()`. Contraintes : rapide,
**aucun import lourd** (filesystem + HTTP uniquement). Vérifie :

| Vérification | Méthode | Commande affichée si manque |
|---|---|---|
| modèle spaCy `fr_core_news_sm` | dossier dans site-packages (pas d'import spacy) | `python -m spacy download fr_core_news_sm` |
| reranker local | dossier `models/bge-reranker-v2-m3/` non vide | `huggingface-cli download BAAI/bge-reranker-v2-m3 --local-dir models/bge-reranker-v2-m3` |
| modèles Ollama (`EMBED_MODEL`, `GEN_MODEL`) | `GET /api/tags` (seulement si Ollama répond) | `ollama pull <modèle>` |
| Node ≥ 20 | `node --version` via nvm/PATH | renvoi vers SETUP_PORTABLE.md |

- **Non bloquant** : l'app démarre quand même (même philosophie que les checks
  Mongo/Ollama actuels). Affichage `✗` + commande exacte.
- `diagnostic.py` reste l'outil d'audit approfondi — pas de duplication : le
  pre-flight ne couvre que ce qui bloque un nouveau venu.

## 3. Docs : une seule source de vérité

- `SETUP_PORTABLE.md` : LE guide — prérequis → `pip install -r
  requirements.txt` (une commande) → `python serve.py` (qui signale les
  manques). Notes par OS en annexe. Mention `requirements-gpu.txt` optionnel.
- `README.md` : section installation réduite à ~3 lignes + lien vers
  SETUP_PORTABLE.md.
- `MIGRATION.md` : §4 (tables des 4 manifestes) réécrit pour 3 fichiers ;
  renvoi vers SETUP_PORTABLE.md ; note « Conflit Streamlit » supprimée
  (résolu).
- `lynx/README.md` : section install → renvoi vers la racine.

## Vérification

- Suite de tests complète relancée après fusion (les 272 verts le restent).
- Preuve de résolution pip : `pip install -r requirements.txt --dry-run`
  dans un venv témoin (vérifie que la fusion résout sans conflit).
- `python serve.py` lancé sur machine complète : aucun `✗` affiché ;
  simulation d'un manque (reranker renommé temporairement) : `✗` + commande
  exacte affichés, l'app démarre quand même.

## Hors périmètre

- Pas de `pyproject.toml`/packaging, pas de `install.sh`.
- Pas de refonte de `diagnostic.py`.
- Pas de changement du flux `web/dev.sh` (npm install au premier lancement).

## Commits

Commits atomiques sur `chore/declutter-aiforssh` :
1. fusion requirements + suppression des 2 fichiers + lock régénéré ;
2. pre-flight `check_setup()` dans `serve.py` ;
3. docs (SETUP_PORTABLE, README, MIGRATION, lynx/README).
