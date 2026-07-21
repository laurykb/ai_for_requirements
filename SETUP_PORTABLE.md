# Setup portable (Linux / macOS / Windows)

Guide pour cloner et lancer le projet sur **n'importe quelle machine**. Le code est
multi-OS ; la base s'installe sans GPU. L'accélération NVIDIA est optionnelle.

---

## 1) Prérequis

| Outil | Rôle | Installation |
|---|---|---|
| **Python 3.11+** | runtime | python.org, `pyenv`, `brew install python@3.11`, `apt install python3.11` |
| **Ollama** | LLM + embeddings (local) | <https://ollama.com/download> (Linux/macOS/Windows) |
| **MongoDB** | chunks, BM25, traces | service local ou `docker run -p 27017:27017 mongo` |

> Sans GPU NVIDIA, tout tourne en **CPU** (plus lent). Sur **macOS**, PyTorch utilise
> automatiquement le backend **MPS** (Apple Silicon) si disponible.

---

## 2) Cloner, environnement virtuel, dépendances

```bash
git clone https://github.com/laurykb/ai_for_requirements.git && cd ai_for_requirements
python -m venv .venv
```

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
pip install -r requirements-gpu.txt        # OPTIONNEL : GPU NVIDIA (CUDA 12.x)
python -m spacy download fr_core_news_sm   # modèle NER français
```

> Au premier `python serve.py`, un **pre-flight** liste ce qui manque encore
> (modèle spaCy, reranker, modèles Ollama, Node) avec la commande exacte à
> lancer — inutile de mémoriser les étapes ci-dessous.

---

## 3) Modèles

```bash
# LLM + embeddings (Ollama)
ollama pull mistral-small3.2   # génération / raisonnement (qualité)
ollama pull llama3.2:3b        # OPTIONNEL : mode rapide (~5× plus vite)
ollama pull bge-m3:567m        # embeddings

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
EMBED_MODEL=bge-m3:567m
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
# python serve.py --streamlit                  # ancienne UI Streamlit (legacy, :8501)
python -m evals.run_eval --mode retrieval      # évaluation retrieval (rapide)
python -m core.agent "Quel est le niveau EAL de la TOE ?"   # agent en CLI
python rag_mcp_server.py                        # serveur MCP (stdio)
```

---

## Réseau restreint (hors-ligne)

Le projet tourne à 100 % en local — l'installation aussi. Tout ce qui se
télécharge se **prépare sur une machine connectée**, puis se **copie** :

| Artefact | Côté connecté | Côté restreint |
|---|---|---|
| Paquets Python | `pip download -r requirements.txt -d wheels/` (+ `-r requirements-gpu.txt` si GPU) | `pip install --no-index --find-links wheels/ -r requirements.txt` ; puis si GPU : `pip install --no-index --find-links wheels/ -r requirements-gpu.txt` |
| Modèle spaCy | `pip download fr-core-news-sm -d wheels/` (roue pip standard) | installée avec les autres roues |
| Reranker | déjà un dossier local | copier `models/bge-reranker-v2-m3/` tel quel |
| Modèles Ollama | `ollama pull …` puis récupérer `~/.ollama/models` | copier `~/.ollama/models` (blobs + manifests) |
| Front Next.js | `npm install` dans `web/` | copier `web/node_modules/` (`dev.sh` saute `npm install` s'il est présent) |
| Caches Docling / EasyOCR | 1re ingestion d'un PDF (peuple `~/.cache`) | copier `~/.cache/docling` et `~/.EasyOCR` |
| Binaires MongoDB / Ollama | télécharger les installeurs | install hors-ligne ; renseigner `MONGO_BIN` / `OLLAMA_BIN` dans `.env` |

Volumes à prévoir : ~24 Go de modèles (détail : MIGRATION.md §8) + les roues
Python (torch et CUDA pèsent plusieurs Go).

---

## Notes par OS

- **NVIDIA (Linux/Windows)** : exporter au lancement d'Ollama
  `OLLAMA_FLASH_ATTENTION=0` (sinon `bge-m3` peut produire des `NaN` sur GPU Turing)
  et `OLLAMA_NUM_PARALLEL=1` (VRAM contrainte). `OLLAMA_KEEP_ALIVE=5m` évite de
  pinner un modèle.
- **Windows** : si la console affiche mal les accents, lancer avec `PYTHONUTF8=1`
  (le code force déjà UTF-8 en interne, c'est un filet de sécurité).
- **macOS / CPU** : ne pas installer `requirements-gpu.txt`. Les temps de génération
  seront plus longs (voir le mode rapide).

## .env

- `.env` est ignoré par Git (local). `.env.example` est le template versionné.
- Si `.env` est absent, `env_config.py` retombe sur `.env.example`, puis sur des
  valeurs par défaut internes.
