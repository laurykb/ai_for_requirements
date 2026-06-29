# État du projet & reprise

Point d'entrée pour reprendre le travail (y compris sur une **autre machine**, ex. Ubuntu 24.04).
Dépôt : **`github.com/laurykb/ai_for_requirements`**, branche **`main`**.

> **Ce dépôt = « AI for SSH », le merge de deux projets.** L'app hôte (`app/app.py`)
> réunit (1) l'**Outil RAG** documentaire décrit ci-dessous et (2) **AI for Requirements
> (LynX)**, embarqué tel quel dans `lynx/` (chargé via importlib, **sans modifier son
> code**, rendu plein écran dans le même process). Détails du merge : [README.md](README.md)
> § « Intégration des deux outils ». État/reprise propres à LynX : `lynx/README.md`,
> `lynx/ROADMAP.md`. Le RAG existe aussi en standalone dans le dépôt d'origine `rag_project`
> (branche `feat/rag-prod-transformation`).

## Où en est le projet (Outil RAG)

RAG hybride **local et souverain** (Ollama) sur documents techniques ANSSI / Critères Communs.
Philosophie : **chaque technique = un levier activable et mesurable**. Briques en place :

- **Retrieval** : hybride sémantique + BM25 + RRF + cross-encoder (rerank), GraphRAG, RAPTOR,
  parent-child, réécriture de requête. Mesuré : hit@k 0.93 / recall 0.90.
- **Génération** : réponse ancrée + citations ; affinage du contexte avant génération
  (déduplication + réordonnancement *lost-in-the-middle*) — `retrieval/context_refine.py`.
- **Agentique** (Tome 3) : outil `rag_search` → serveur **MCP** (`rag_mcp_server.py`) → **agent ReAct**
  (`core/agent.py`), streamé.
- **Orchestration** : **routeur** Auto/RAG/Agent **sans appel LLM** (`core/router.py`) +
  **vérificateur de fidélité** fusionné 1 appel (`core/evaluation.verify_answer`) + **auto-correction**
  (ex-Self-RAG, `core/self_rag.py`).
- **Routage de modèles par rôle** (`core/model_router.py`) ; **vector store abstrait** (Chroma défaut,
  Qdrant activable — `retrieval/vector_store.py`).
- **Observabilité** (traces Mongo, `utils/tracing.py`), **sécurité** anti-injection (`utils/security.py`),
  **évaluation** golden set + RAGAS-like (`evals/`, `core/evaluation.py`).
- **UI Streamlit** (`app/app.py`) : accueil épuré → chat avec upload de documents, sélecteur
  Auto/RAG/Agent, menu **Options** (leviers recherche + ingestion), exploration des chunks.
- **101 tests unitaires hors-ligne** : `python -m pytest`.

Voir aussi : [README.md](README.md) (positionnement + résultats), [SETUP_PORTABLE.md](SETUP_PORTABLE.md)
(installation multi-OS), [CONFIG_ARCHITECTURE.md](CONFIG_ARCHITECTURE.md), `notebooks/` (3 notebooks exécutés).

## Reprendre sur une nouvelle machine (Ubuntu 24.04)

1. **Cloner** : `git clone https://github.com/laurykb/ai_for_requirements.git && cd ai_for_requirements`
   (branche `main`). Le module `lynx/` (AI for Requirements) est inclus dans le dépôt.
2. **Installer** en suivant [SETUP_PORTABLE.md](SETUP_PORTABLE.md) :
   - `python3.11 -m venv .venv && source .venv/bin/activate`
   - `pip install -r requirements.txt` (CPU) — extras NVIDIA : `requirements-gpu.txt`.
     LynX a ses propres dépendances : `pip install -r lynx/requirements.txt`.
   - **Ollama** (Linux) puis : `ollama pull mistral-small3.2` + `ollama pull bge-m3`
     (+ optionnel `ollama pull llama3.1:8b`, `llama3.2:3b`).
   - **MongoDB** : sur Ubuntu, paquet natif (`mongod`) — pas besoin du binaire portable Windows.
   - **spaCy** : `python -m spacy download fr_core_news_sm`.
   - **Cross-encoder** : `BAAI/bge-reranker-v2-m3` dans `models/bge-reranker-v2-m3/` (≈2.2 Go, non versionné).
3. **Config** : `cp .env.example .env` puis ajuster. Spécificités GPU :
   - GPU **NVIDIA Turing** (ou si `bge-m3` renvoie des NaN) : `OLLAMA_FLASH_ATTENTION=0`.
   - **8 Go de VRAM** : `OLLAMA_NUM_PARALLEL=1` (+ `OLLAMA_KEEP_ALIVE=5m`). GPU plus large : assouplir.
4. **Données** : l'index (Chroma + chunks Mongo + BM25) **n'est pas versionné** (`.gitignore`).
   Ré-ingérer un PDF via l'UI (vue **Documents**) ou `python -m core.ingest`.
5. **Lancer** : `python serve.py` (Mongo + Ollama + app) ou `.venv/bin/python -m streamlit run app/app.py`.
   Éval : `python -m evals.run_eval --mode retrieval`.

## Améliorations — session 2026-06-29 (poste Ubuntu, 2×48 Go VRAM)

Portage et durcissement du PoC sur une machine bien plus capable que le poste d'origine (8 Go).
**Cadre assumé : PoC mono-poste, mono-utilisateur, sur Streamlit** (pas d'API/auth/multi-tenant).

- **Exploitation GPU (2×48 Go).** Modèles **résidents** (`OLLAMA_KEEP_ALIVE=-1` via drop-in systemd
  `/etc/systemd/system/ollama.service.d/override.conf`, + `OLLAMA_NUM_PARALLEL=4`,
  `OLLAMA_MAX_LOADED_MODELS=4`) → fin des rechargements (**~50 s → ~14 s** à froid). `num_ctx`
  **plafonné par rôle** dans `core/model_router.py` (rewrite/judge 8192, generate/agent `LLM_NUM_CTX`,
  enhance `ENHANCE_NUM_CTX=4096`) : sans ça, llama3.1 chargeait à 131072 = 30 Go de KV-cache et
  **figeait l'ingestion**. Enrichissement parallèle via `ENHANCE_MAX_WORKERS` (.env).
  Génération passée sur **mistral-small3.2** (`.env`), 70B (llama3.3) prévu plus tard.
- **Conformité « assistant documentaire ».** Réponse **dans la langue de la question** (multilingue,
  sans dépendance), **xlsx** accepté au dépôt, **résumé de document** (réutilise les résumés RAPTOR,
  `core/summarize.py`), **clarification** des questions vagues + **refus du nocif** (directives
  code-enforced dans `core/llm_answer.py`, en plus du préambule anti-injection existant).
- **Refonte UX du chat.** Flux **upload → absorption → réponse** : la question déposée avec un fichier
  (ou tapée pendant l'absorption) **attend que le document soit indexé** avant la réponse (gate global).
  **Options d'ingestion au moment du dépôt** (plus dans la popover « Options », qui ne garde que la
  recherche). **Sélection d'UN seul document à interroger** (cocher un autre décoche le précédent ;
  avertissement si aucun = recherche sur tout l'index). **Exploration par chunks dans le chat** ;
  **visualisation page blanche** (fond corrigé) + résumé restent dans l'onglet Documents (agrandie).
  **Renommage de session**, **documents en mémoire par session** (`core/chat_sessions.py`).
- **Robustesse mono-poste.** Bannière si **Ollama/Mongo hors ligne**, génération/vérif en `try/except`
  (message lisible au lieu d'un crash), **échec d'ingestion** non silencieux (poser quand même / abandonner),
  **réinitialisation du corpus** (Paramètres → zone dangereuse, sans toucher aux conversations).

## Prochaines étapes

- **llama3.3:70b** : reprendre `ollama pull llama3.3:70b`, puis basculer `GEN_MODEL` dans `.env`.
- **Éval chiffrée** avant/après les changements de génération : `python -m evals.run_eval` (le projet
  prône la mesure-avant-d'optimiser). Élargir le golden set (`evals/golden_qa_anssi.json`).
- **BM25 multi-document** : `data/bm25_index.pkl` est écrasé à chaque ingestion → la recherche
  « tous documents » est incomplète. Sans impact tant que la sélection est forcée à un seul document.
- Optionnel : A/B « précision du contexte » (`CONTEXT_DEDUP`/`CONTEXT_REORDER`), démo Qdrant.
