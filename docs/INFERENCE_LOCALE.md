# Inférence locale : goulots d'étranglement et optimisation matérielle

Objectif : faire tourner un RAG **comme un service d'inférence local hautement optimisé**,
100 % souverain (Ollama + ChromaDB + MongoDB), sans clé API.

Ce document explicite ce qui, sur une inférence locale, **limite le débit et la latence**,
et comment on l'a géré. Tous les chiffres ci-dessous sont **mesurés sur nos machines**, pas
théoriques. Les réglages sont surchargeables dans `.env` (voir `env_config.py`).

## 1. Le cadre matériel

Le projet a été développé sur **deux profils**, ce qui rend les arbitrages explicites :

| Profil | VRAM | Conséquence |
|---|---|---|
| Poste d'origine | **1 GPU, 8 Go** | VRAM = contrainte dure ; les modèles ne co-résident pas |
| Poste actuel | **2 GPU, 48 Go chacun** | confort, mais le KV-cache peut encore déborder si mal réglé |

La détection est automatique (`env_config._detect_available_gpus`) : le code adapte le
placement des modèles au nombre de GPU trouvés.

## 2. Les goulots d'étranglement physiques identifiés

1. **Capacité VRAM** — le goulot n°1. Doivent tenir *ensemble* : le LLM de génération, son
   **KV-cache** (proportionnel à `num_ctx` × slots parallèles), l'embedder (bge-m3) et le
   cross-encoder de rerank. Sur 8 Go, ils ne tiennent pas tous → **swaps**.
2. **Latence de (re)chargement / swap de modèle** — charger un modèle à froid coûte ~**50 s**.
   Décharger/recharger entre deux requêtes effondre le débit.
3. **Contention GPU** — LLM et cross-encoder qui se disputent le même GPU se ralentissent.
4. **Débit de génération** — le *decode* token-par-token est borné par la **bande passante
   mémoire** du GPU et surtout par la **taille du modèle** (pas par le nombre de chunks).
5. **CPU / I/O à l'ingestion** — conversion PDF (Docling), NER (spaCy), écritures Mongo.

## 3. Gestion de la VRAM

- **Modèles résidents** (`OLLAMA_KEEP_ALIVE=-1`, via un drop-in systemd
  `/etc/systemd/system/ollama.service.d/override.conf`) + `OLLAMA_MAX_LOADED_MODELS` :
  on **garde les modèles chargés** → fin des rechargements. Mesuré : **~50 s → ~14 s à froid**.
- **`num_ctx` plafonné par rôle** (`core/model_router._ROLE_PARAMS`) : rewrite/judge = 8192,
  generate/agent = `LLM_NUM_CTX` (16384), enhance = `ENHANCE_NUM_CTX` (4096). **Pourquoi c'est
  critique** : sans plafond, Ollama charge le contexte par défaut du modèle (131072 pour
  llama3.1) → **~30 Go de KV-cache** pour un seul slot ; avec `OLLAMA_NUM_PARALLEL>1` le
  KV-cache est multiplié par le nombre de slots → débordement observé jusqu'à **148 Go**
  (VRAM + offload CPU), ce qui **figeait l'ingestion**.
- **Anti-swap sur petit GPU** : sur 8 Go, llama et bge-m3 **ne co-résident pas**. Pinner un
  modèle « Forever » n'évite pas le swap mais **wedge le scheduler** Ollama (modèle coincé en
  « Stopping… », l'embedder ne charge plus → **vecteurs nuls** = jambe sémantique morte). On
  laisse donc le cycle de vie au serveur Ollama, et on tolère le swap à froid via un timeout
  d'embeddings large (`EMBED_TIMEOUT_S=180`).
- **Flash-attention** : désactivable (`OLLAMA_FLASH_ATTENTION=0`) sur GPU Turing ou si bge-m3
  renvoie des NaN.

## 4. Partitionnement

On ne fait **pas** de tensor/pipeline parallelism *nous-mêmes* (Ollama place le modèle, et
répartit automatiquement les couches d'un modèle trop gros sur les 2 GPU). Notre
partitionnement est **par tâche** et **par device** :

- **Par rôle** (`core/model_router.py`) : un modèle **léger** pour les tâches mécaniques
  (réécriture, agent, juge), un modèle **fort** pour la génération finale. On ne paie pas un
  gros modèle pour reformuler une requête.
- **Par device** (`env_config.py`) : avec 2 GPU, **LLM sur `cuda:0`**, **cross-encoder de
  rerank sur `cuda:1`** (`CE_DEVICE`). Les deux tournent **en parallèle** sans se disputer la
  VRAM ni le calcul.

> Pour aller plus loin (vrai tensor-parallelism d'un gros modèle), le chemin propre est de
> pointer le client (`core/llm_client.py`) vers un serveur **vLLM/SGLang** : l'interface ne
> change pas, seul `LLM_BASE_URL` bouge.

## 5. Optimisation des flux

- **Slots concurrents** côté serveur (`OLLAMA_NUM_PARALLEL`) : plusieurs requêtes traitées de
  front (au prix du KV-cache, d'où le plafond `num_ctx`).
- **Retrieval parallèle** (`retrieval/retrieve.py`) : recherche **sémantique** et **BM25**
  lancées dans des threads séparés, puis fusionnées (RRF).
- **Enrichissement parallèle** à l'ingestion (`ENHANCE_MAX_WORKERS`, aligné sur
  `OLLAMA_NUM_PARALLEL`) : l'enrichissement LLM = **~85 % du temps d'ingestion**, donc c'est là
  qu'on parallélise.
- **Cache d'embeddings** (`nlp/ollama_embedding`) : une requête déjà vue n'est pas re-embeddée.
- **Éval en mode `retrieval`** (`evals/run_eval.py`) : mesurer la recherche **sans** payer la
  génération (rapide, déterministe).

## 6. Récapitulatif des leviers (mesuré)

| Levier | Fichier / réglage | Effet mesuré |
|---|---|---|
| Modèles résidents | `OLLAMA_KEEP_ALIVE=-1` | démarrage à froid **50 s → 14 s** |
| `num_ctx` plafonné | `model_router._ROLE_PARAMS` | évite **~30 Go** (→ 148 Go) de KV-cache |
| Split LLM / rerank | `CE_DEVICE=cuda:1` | les deux GPU travaillent en parallèle |
| Modèle léger en génération | `GEN_MODEL=llama3.2:3b` + `RAG_FAST_MODE` | **150 s → 27 s** (~5-6×) |
| Retrieval threadé | `retrieval/retrieve.py` | sémantique + BM25 en parallèle |
| Timeout embeddings | `EMBED_TIMEOUT_S=180` | absorbe le swap à froid (sinon vecteurs nuls) |

**Contre-mesure importante (mesurée) :** plafonner le nombre de chunks **ne gagne rien** en
vitesse — la latence de génération vient de la **taille du modèle**, pas du contexte. Réduire
les chunks ne fait que **risquer d'éjecter la bonne info**. On garde donc tous les chunks par
défaut.

## 7. Ce qu'on pourrait pousser plus loin

- **vLLM / SGLang** pour le tensor-parallelism et le *continuous batching* (débit multi-requêtes
  bien supérieur à Ollama) — branchable via `LLM_BASE_URL` sans toucher au pipeline.
- **Quantification** plus agressive (AWQ/GPTQ) pour faire tenir un plus gros modèle.
- **Mesure systématique** : brancher un suivi tokens/s et VRAM utilisée (les traces
  `utils/tracing` chronomètrent déjà chaque span : retrieval, rerank, génération).
