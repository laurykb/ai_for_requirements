# Fiche d’export & migration — AI for Requirements

Tout ce qu'il faut pour **rejouer le projet sur un autre PC** ou préparer une
**migration** (autre OS, autre GPU, conteneur, serveur). Objectif : exhaustif et
précis. Pour la carte du code, voir [`ARCHITECTURE.md`](../ARCHITECTURE.md).

> **Rappel de nature** : application **100 % locale, mono-poste, souveraine** —
> aucun appel réseau vers un tiers, aucune clé API. Tout (LLM, embeddings,
> reranker, base) tourne sur la machine.

---

## 1. Comment le projet marche (résumé opérationnel)

Trois couches (détail dans `ARCHITECTURE.md`) :

- **UI cible** : front **Next.js** (`web/`) → **API FastAPI** (`api/`).
- **UI legacy** : retirée (migration terminée) — historique dans `git log -- legacy/`.
- **Moteur partagé** : `core/ retrieval/ indexing/ nlp/ preprocessing/ utils/ tools/`
  (RAG) + `lynx/` (AI for Requirements).

**Lancement unique** : `python serve.py` démarre MongoDB + Ollama (s'ils ne
tournent pas), puis l'API FastAPI (`:8000`) + le front Next.js (`:3000`).

**Dépendances externes au runtime** : **MongoDB** (chunks, index BM25, sessions,
corpus LynX, traces) + **Ollama** (LLM + embeddings) + un **cross-encoder** local
(fichiers dans `models/`).

### Services & ports

| Service | Port | Rôle | Requis |
|---|---|---|---|
| MongoDB (`mongod`) | 27017 | chunks, BM25, sessions, corpus LynX, traces | **oui** |
| Ollama | 11434 | génération + embeddings | **oui** |
| API FastAPI (uvicorn) | 8000 | backend de l'UI cible | oui (UI cible) |
| Front Next.js | 3000 | UI cible | oui (UI cible) |

---

## 2. Prérequis système

| Composant | Version (cette machine) | Contrainte / note |
|---|---|---|
| OS | Ubuntu 24.04.4 LTS (kernel 6.17) | **Cross-platform** : Linux / macOS / Windows supportés |
| Python | 3.12.3 (dans `.venv`) | **3.11+** recommandé (cf. `requirements.txt`) |
| Node.js | v22.23.1 (npm 10.9.8), via nvm | **≥ 20** requis (front Next.js) ; `web/dev.sh` fait `nvm use default` |
| MongoDB | `mongod` (hors PATH ici → `MONGO_BIN`) | serveur MongoDB **4.4+ / 6.x / 7.x** ; données dans `./data/mongodb` |
| Ollama | 0.32.1 | serveur LLM local |
| GPU (optionnel) | NVIDIA, CUDA dispo (`torch.cuda=True`), 2×48 Go VRAM | accélère génération/rerank ~10× ; sinon CPU (macOS : backend MPS auto) |

---

## 3. Modèles à récupérer (le plus gros du transfert)

Les modèles **ne sont pas** dans le dépôt (gitignorés). À réinstaller ou copier.

### 3.1 Modèles Ollama (`ollama pull …`)

| Rôle | Modèle (tag) | Taille | Variable `.env` | Requis |
|---|---|---|---|---|
| Génération | `mistral-small3.2:latest` | ~15 Go | `GEN_MODEL` | **oui** |
| Embeddings | `bge-m3:latest` | ~1.2 Go | `EMBED_MODEL` | **oui** |
| Réécriture / rôles auxiliaires | `llama3.1:latest` | ~4.9 Go | `REWRITER_MODEL` | oui (défaut `.env.example`) |
| Mode rapide (optionnel) | `llama3.2:3b` | ~2 Go | `GEN_MODEL`/`AGENT_MODEL` si `RAG_FAST_MODE` | non (à `pull` si besoin) |

> `.env.example` mentionne `bge-m3:567m` ; la machine utilise `bge-m3:latest`.
> Aligner `.env` sur les tags réellement `pull`és. LynX consomme le même endpoint
> Ollama (compatible OpenAI `/v1`) avec `GEN_MODEL` par défaut.

### 3.2 Cross-encoder (reranker) — **hors Ollama**

- **Emplacement** : `models/bge-reranker-v2-m3/` (~**2.2 Go**, `model.safetensors`).
- **Origine** : HuggingFace `BAAI/bge-reranker-v2-m3`.
- **Migration** : soit **copier le dossier `models/`**, soit le laisser se
  re-télécharger (réseau au 1er usage). Désactivable via `USE_CROSS_ENCODER=false`.

### 3.3 spaCy (NER français)

```bash
python -m spacy download fr_core_news_sm
```

### 3.4 Docling (PDF → Markdown)

Docling + EasyOCR **téléchargent leurs modèles de layout/OCR au 1er usage**
(caches `~/.cache/docling` et `~/.EasyOCR`). Prévoir un accès réseau à la première ingestion, ou copier les
deux dossiers pour une machine hors-ligne.

---

## 4. Dépendances Python

**Trois fichiers** (installation détaillée : [SETUP_PORTABLE.md](../SETUP_PORTABLE.md)) :

| Fichier | Rôle |
|---|---|
| `requirements.txt` | **unique et cross-platform** — base RAG + LynX (`streamlit-agraph`) + dev (pytest, notebooks). Une commande : `pip install -r requirements.txt` |
| `requirements-gpu.txt` | optionnel — builds CUDA 12.x de torch/onnxruntime + CuPy |
| `requirements.lock.txt` | photographie `pip freeze` de la **machine de référence** (Linux/NVIDIA, roues `nvidia-*-cu13`) — pour la reproduire à l'identique |

> Historique : `lynx/requirements.txt` (épingle morte `streamlit==1.28.0`) et
> `requirements-dev.txt` ont été absorbés dans `requirements.txt` le 2026-07-21.

---

## 5. Dépendances front — `web/package.json` (Node ≥ 20)

**dependencies**

| Paquet | Version |
|---|---|
| next | `16.2.10` |
| react | `19.2.4` |
| react-dom | `19.2.4` |
| @xyflow/react | `^12.11.1` (graphe matrice LynX) |
| react-force-graph-3d | `^1.29.1` (vue 3D) |
| three | `^0.185.1` |
| three-spritetext | `^1.10.0` |
| react-markdown | `^10.1.0` |

**devDependencies** : `typescript ^5`, `eslint ^9`, `eslint-config-next 16.2.10`,
`tailwindcss ^4`, `@tailwindcss/postcss ^4`, `@types/node ^20`, `@types/react ^19`,
`@types/react-dom ^19`.

Installation : `cd web && npm install` (fait automatiquement par `web/dev.sh` au
1er lancement). `package-lock.json` fige les versions exactes.

---

## 6. Configuration — `.env`

Copier `.env.example` → `.env` et adapter. Variables clés :

| Variable | Défaut | Rôle |
|---|---|---|
| `MONGO_HOST` / `MONGO_PORT` / `MONGO_DB` | `localhost` / `27017` / `ragdb` | connexion Mongo |
| `MONGO_URI` | (dérivée) | override direct |
| `OLLAMA_HOST` | `http://localhost:11434` | endpoint Ollama |
| `EMBED_MODEL` / `GEN_MODEL` / `REWRITER_MODEL` | `bge-m3` / `mistral-small3.2` / `llama3.1` | modèles par rôle |
| `MONGO_BIN` / `MONGO_DBPATH` / `OLLAMA_BIN` | — / `./data/mongodb` / — | chemins pour le lanceur `serve.py` (install portable) |
| `USE_CROSS_ENCODER` / `CE_DEVICE` / `CE_RELEVANCE_THRESHOLD` | `true` / auto / `0.505` | reranker |
| `NUM_CHUNKS`, `RRF_K`, `WEIGHT_SEMANTIC`, `WEIGHT_BM25` | `15`, `60`, `0.3`, `0.7` | retrieval hybride |
| `CHUNKING_MODE`, `AUTO_KEYWORDS`, `AUTO_QUESTIONS`, `RAPTOR_SUMMARIES` | `technical`, `5`, `3`, `true` | ingestion / enrichissement |
| `RAG_FAST_MODE`, `ENHANCEMENT_MODEL`, `ENHANCE_MAX_WORKERS` | `false`, —, `3` | latence / parallélisme |
| `SELF_RAG_*`, `PARENT_CHILD_*`, `CONTEXT_*` | (voir `.env.example`) | couches optionnelles |
| `COLLECTION_NAME`, `LOG_LEVEL` | `test_rag`, `INFO` | Chroma / logs |

> `.env` peut contenir des secrets (`MONGO_USER`/`MONGO_PASSWORD`) → **ne jamais
> committer** (`.gitignore` le couvre). Seul `.env.example` est versionné.

---

## 7. Procédure d'export / réinstallation (pas à pas)

```bash
# 1. Récupérer le code (USB)

# 2. Venv Python DÉDIÉ (voir §8 : ne pas réutiliser celui de rag_project)
python -m venv .venv
source .venv/bin/activate            # Windows : .venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-gpu.txt  # si GPU NVIDIA CUDA 12.x
python -m spacy download fr_core_news_sm

# 3. Modèles Ollama
ollama pull mistral-small3.2 && ollama pull bge-m3 && ollama pull llama3.1
# (optionnel mode rapide : ollama pull llama3.2:3b)

# 4. Cross-encoder : copier models/bge-reranker-v2-m3/ (~2.2 Go) OU laisser
#    se re-télécharger au 1er usage.

# 5. Front
cd web && npm install && cd ..

# 6. Config
cp .env.example .env                 # puis adapter (modèles, MONGO_BIN si portable)

# 7. Données à transférer si on veut l'état existant (sinon ré-ingérer) :
#    data/mongodb/ (base), data/chroma_db/ (vecteurs), lynx/corpus/ (matrice LynX)

# 8. Lancer
python serve.py                      # UI Next.js + API FastAPI

# 9. Vérifier
python diagnostic.py                 # état services + routage + vector store
python -m pytest tests               # 154 tests RAG (hors-ligne)
cd lynx && python -m pytest tests    # 121 tests LynX
```

### Données/état (gitignorés) — à copier seulement si on veut conserver l'existant

| Dossier | Contenu | Sinon |
|---|---|---|
| `data/mongodb/` | base MongoDB (si install portable) | ré-ingérer les docs |
| `data/chroma_db/` | vecteurs Chroma | reconstruits à l'ingestion (`scripts/rebuild_vectors.py` depuis Mongo) |
| `models/` | cross-encoder | re-télécharge |
| `lynx/corpus/` | matrice de travail + cache LLM LynX | repart d'une matrice vierge |
| `.env` | config locale (secrets) | recopier `.env.example` |

---

## 8. Pièges connus & points de vigilance migration

- **Venv partagé avec `rag_project`** : sur cette machine, `.venv/` est commun
  aux deux projets. Pour un export propre, créer un venv dédié depuis
  `requirements.txt` (+ `-gpu`) au lieu de copier `.venv/` ; le lock reflète
  cette machine (voir §4).
- **`mongod` hors PATH** : ici MongoDB est en install portable → renseigner
  `MONGO_BIN` (et éventuellement `MONGO_DBPATH`) dans `.env` pour que `serve.py`
  le démarre. Sinon installer MongoDB en service et laisser `MONGO_URI` par défaut.
- **Builds GPU** : `requirements-gpu.txt` cible **CUDA 12.x** (`+cu121`). La
  machine actuelle tourne en **CUDA 13** (`+cu130`) — sur un autre GPU, réinstaller
  les wheels torch correspondant à la version de CUDA disponible.
- **Docling / EasyOCR hors-ligne** : modèles téléchargés au 1er usage → pour une
  machine sans réseau, copier `~/.cache/docling` et `~/.EasyOCR`.
- **Tailles à prévoir** : Ollama (mistral 15 Go + llama3.1 4.9 Go + bge-m3 1.2 Go)
  + reranker 2.2 Go ⇒ **~24 Go de modèles** hors dépendances Python (torch+CUDA
  pèsent aussi plusieurs Go).
- **Node via nvm** : `web/dev.sh` fait `nvm use default`. Sur une machine sans nvm,
  garantir Node ≥ 20 dans le PATH avant `npm install`.
