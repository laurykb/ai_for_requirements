# Setup portable (Linux / macOS / Windows)

Guide pour installer et lancer le projet sur **n'importe quelle machine** — par
`git clone` (machine connectée) ou par **copie du dossier** (clé USB, machine sans
réseau : voir [Réseau restreint](#réseau-restreint-hors-ligne)). Le code est
multi-OS ; la base s'installe sans GPU. L'accélération NVIDIA est optionnelle.

---

## 1) Prérequis

| Outil | Rôle | Installation |
|---|---|---|
| **Python 3.11+** | runtime | python.org, `pyenv`, `brew install python@3.11`, `apt install python3.11` |
| **Ollama** | LLM + embeddings (local) | <https://ollama.com/download> (Linux/macOS/Windows) |
| **MongoDB** | chunks, BM25, traces | service local ou `docker run -p 27017:27017 mongo` |
| **Node.js ≥ 20** | front Next.js (`web/`) | <https://nodejs.org> ou `nvm install 20` |

> Sans GPU NVIDIA, tout tourne en **CPU** (plus lent).

---

## 2) créer l'environnement virtuel, dépendances

```bash
python -m venv .venv
```

> **Sans réseau** : copier le dossier du projet depuis la clé USB (voir la
> check-list de l'annexe), **sans `.venv/`** — le venv se recrée sur place,
> il n'est pas portable d'une machine à l'autre.

Activer le venv :

```bash
# Linux / macOS
source .venv/bin/activate
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
```

Installer les dépendances (**une seule commande** — RAG + LynX + dev inclus) :

```bash
pip install -r requirements.txt            # base CROSS-PLATFORM (CPU)
python -m spacy download fr_core_news_sm   # modèle NER français
```

Pour contribuer, lancer les tests ou utiliser les notebooks, installer plutôt
`pip install -r requirements-dev.txt`. Ce manifeste inclut le runtime ; les
outils Jupyter et pytest ne sont ainsi pas embarqués en production.

> Au premier `python serve.py`, un **pre-flight** liste ce qui manque encore
> (modèle spaCy, reranker, modèles Ollama, Node) avec la commande exacte à
> lancer — inutile de mémoriser les étapes ci-dessous.

---

## 3) Modèles

```bash
# LLM + embeddings (Ollama)
ollama pull mistral-small3.2   # génération / raisonnement (qualité)
ollama pull llama3.2:3b        # OPTIONNEL : mode rapide (~5× plus vite)
ollama pull bge-m3:latest      # embeddings

# Cross-encoder de reranking (téléchargé localement, ~2,2 Go)
pip install -U huggingface_hub
huggingface-cli download BAAI/bge-reranker-v2-m3 --local-dir models/bge-reranker-v2-m3
```

---

## 4) Configuration

```bash
cp .env.example .env      # Windows : copy .env.example .env
```

Variables clés (`.env`) :

```ini
OLLAMA_HOST=http://localhost:11434
MONGO_HOST=localhost
MONGO_PORT=27017
MONGO_DB=ragdb
EMBED_MODEL=bge-m3:latest
GEN_MODEL=mistral-small3.2:latest

# Mode rapide (latence ~5×, qualité moindre — voir README) :
# RAG_FAST_MODE=true
# GEN_MODEL=llama3.2:3b

# CE_DEVICE=cpu   # forcer le cross-encoder en CPU si pas de GPU
```

---

## 5) Lancer

```bash
python serve.py                                # front Next.js (:3000) + API FastAPI (:8000)
# python serve.py                  # ancienne UI Streamlit (legacy, :8501)
python -m evals.run_eval --mode retrieval      # évaluation retrieval (rapide)
python -m core.agent "Quel est le niveau EAL de la TOE ?"   # agent en CLI
python rag_mcp_server.py                        # serveur MCP (stdio)
```

---

## Réseau restreint (hors-ligne)

Pour une livraison produit Linux x86_64, utiliser prioritairement le paquet
automatisé et audité :

```bash
deploy/offline.sh prepare DESTINATION
```

Il remplace la copie brute du dépôt, précompile Next.js et contrôle les secrets
et traces de développement. Voir `INSTALLATION_HORS_LIGNE.md`. Les instructions
ci-dessous restent utiles pour préparer manuellement une autre plateforme.

Le projet tourne à 100 % en local — l'installation aussi. Tout ce qui se
télécharge se **prépare sur une machine connectée**, puis se **copie** :

| Artefact | Côté connecté | Côté restreint |
|---|---|---|
| Paquets Python | `pip download -r requirements.txt -d wheels/` | `pip install --no-index --find-links wheels/ -r requirements.txt` |
| Modèle spaCy | télécharger la roue compatible avec la version de spaCy retenue dans `requirements.txt` | installer la roue avec les autres dépendances |
| Reranker | déjà un dossier local | copier `models/bge-reranker-v2-m3/` tel quel |
| Modèles Ollama | `ollama pull …` puis récupérer `~/.ollama/models` | copier `~/.ollama/models` (blobs + manifests) |
| Front Next.js | `npm install` dans `web/` | copier `web/node_modules/` (`dev.sh` saute `npm install` s'il est présent) |
| Binaire Node.js ≥ 20 | télécharger l'archive <https://nodejs.org/dist/> (ou installeur) | dézipper et mettre `node`/`npm` dans le PATH (ou installeur hors-ligne) |
| Caches Docling / EasyOCR | 1re ingestion d'un PDF (peuple `~/.cache/docling` et `~/.EasyOCR`) | copier `~/.cache/docling` et `~/.EasyOCR` |
| Binaires MongoDB / Ollama | télécharger les installeurs | install hors-ligne ; renseigner `MONGO_BIN` / `OLLAMA_BIN` dans `.env` |

Volumes à prévoir : ~24 Go de modèles (détail : history/MIGRATION.md §8) + les roues
Python (torch et CUDA pèsent plusieurs Go).

### Check-list clé USB

> Formater la clé en **exFAT / NTFS / ext4** — le FAT32 refuse les fichiers
> de plus de 4 Go, et certains blobs Ollama (mistral-small3.2) font ~15 Go.

Contenu à préparer côté connecté :

1. **Le dossier du projet** — sans `.venv/` (non portable), avec
   `web/node_modules/` et `models/bge-reranker-v2-m3/` déjà en place ;
2. **`wheels/`** — toutes les roues Python (base, GPU éventuel, spaCy) ;
3. **`~/.ollama/models`** — blobs + manifests des modèles Ollama ;
4. **Caches** — `~/.cache/docling` et `~/.EasyOCR` (si des PDF seront ingérés) ;
5. **Installeurs** — Node.js ≥ 20, MongoDB, Ollama (+ Python 3.11+ si absent).

Ordre d'installation côté restreint :

1. Installeurs (Python, Node, MongoDB, Ollama) — renseigner `MONGO_BIN` /
   `OLLAMA_BIN` dans `.env` si les binaires ne sont pas dans le PATH ;
2. Copier le dossier du projet, puis `python -m venv .venv` + activation ;
3. `pip install --no-index --find-links wheels/ -r requirements.txt` ;
4. Copier `~/.ollama/models` et les caches ;
5. `python serve.py` — le pre-flight confirme que rien ne manque.

---

## Notes par OS

- **NVIDIA (Linux/Windows)** : exporter au lancement d'Ollama
  `OLLAMA_FLASH_ATTENTION=0` (sinon `bge-m3` peut produire des `NaN` sur GPU Turing)
  et `OLLAMA_NUM_PARALLEL=1` (VRAM contrainte). `OLLAMA_KEEP_ALIVE=5m` évite de
  pinner un modèle.
- **Windows** : si la console affiche mal les accents, lancer avec `PYTHONUTF8=1`
  (le code force déjà UTF-8 en interne, c'est un filet de sécurité).
- **macOS / CPU** : les temps de génération seront plus longs ; utiliser un modèle
  Ollama plus léger si nécessaire.

## .env

- `.env` est ignoré par Git (local). `.env.example` est le template versionné.
- Si `.env` est absent, `env_config.py` retombe sur `.env.example`, puis sur des
  valeurs par défaut internes.
