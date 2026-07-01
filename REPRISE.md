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
(installation multi-OS), [CONFIG_ARCHITECTURE.md](CONFIG_ARCHITECTURE.md),
[docs/INFERENCE_LOCALE.md](docs/INFERENCE_LOCALE.md) (goulots matériels + optimisation VRAM/GPU,
chiffres mesurés), [docs/MULTI_AGENT.md](docs/MULTI_AGENT.md) (orchestration, flux de données,
états, mémoire, formats — RAG + LynX), `notebooks/` (3 notebooks exécutés).

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

## Refonte UX & ingestion — session 2026-06-30 (suite)

Objectif : **réduire la friction** pour un utilisateur non-expert en IA (comparaison Mistral/
Claude/Gemini), sans perdre le côté pédagogique « chaque technique = un levier ».

- **Ingestion en FILE séquentielle multi-documents** (`app/app.py`). Remplace le singleton
  `_INGEST` par `_INGEST_JOBS` + un **worker unique** (`_ingest_worker`) qui absorbe les jobs
  l'un après l'autre. Chaque job a sa **barre de progression** (statut queued→running→success/
  error). Dépôt **multi-fichiers** dans l'onglet Documents (`accept_multiple_files`), options
  **partagées pour le lot** et **repliées** (« Options avancées »). Barres visibles partout :
  compactes dans la **barre latérale** (toutes les vues), détaillées dans **Documents**.
- **Chat DÉCOUPLÉ de l'ingestion** : on répond toujours sur l'index existant, plus de gate.
  Code mort supprimé (`_pending_upload_panel`, `_render_ingestion_activity`, `_launch_ingest`,
  `pending_upload` n'était jamais posé).
- **Mode SIMPLE (défaut) / EXPERT** (`ss.expert_mode`, toggle en bas de la barre latérale).
  Simple : un champ + un **trombone inline** (ajout avec défauts intelligents, façon Claude),
  pas de sélecteur Auto/RAG/Agent ni d'options de recherche, vues Graphe/Observabilité masquées,
  **exemples de questions cliquables** sur le chat vide. Expert : tout est exposé (comportement
  d'avant). Voir [[mono-poste-streamlit-scope]].
- **Dé-jargonnage** des libellés utilisateur : « absorber/ingérer » → « ajouter », « périmètre
  documentaire » → « Chercher dans », « chunks » masqués (mode simple), messages d'absence de
  réponse en langage humain (`OUT_OF_SCOPE_MESSAGE` dans `env_config.py`, fallbacks `core/ask.py`).
- **Chat épuré en mode simple** : l'exploration de documents/chunks dans le chat est supprimée
  (doublon avec l'onglet Documents — `_chat_document_panel` retiré) ; passages récupérés par
  message, vérification LLM-as-judge, badge flottant, compteur de documents et nom du modèle
  sont **réservés au mode expert**. Le chat simple = sélecteur « Chercher dans » + conversation
  + sources + saisie (trombone inline). Tout reste disponible en mode expert.
- **Choix du modèle de génération depuis le chat** (les deux modes) : popover « Modèle : … » +
  bouton **« Charger le modèle »**. Changement **à chaud** via un override runtime du
  `model_router` (`set_generate_model` / `get_generate_model` ; le rôle `generate` lit
  `_GENERATE_OVERRIDE or GEN_MODEL`) — effet immédiat, sans réécrire `.env` ni redémarrer ; le
  bouton charge aussi le modèle en VRAM (`keep_alive=-1`). « Charger le LLM » des Paramètres
  appelle désormais le même override. Test : `tests/test_model_router.test_generate_model_runtime_override`.
- ⚠️ **Non vérifié visuellement** (smoke-test Streamlit interrompu) : lancer `./start.sh` et
  cliquer le flux (ajout inline, file multi-doc, bascule Simple/Expert, choix du modèle) avant de committer.

## Simplification mesurée (session 2026-06-30, suite)

Démarche **scientifique** : mesurer l'apport de chaque levier sur un golden set élargi
(`evals/golden_qa_anssi_v2.json`, **30 questions** ancrées dans le contenu réel, labellisées par
construction), puis supprimer ce que la donnée ne justifie pas.

- **Mesure A/B (mode retrieval, hit@k mots-clés + context_recall)** : **GraphRAG = 0 effet**
  (deux fois), **réécriture de requête = négative + latence**, **parent-child = +0.055 hit@k**
  (à garder), **cross-encoder = tradeoff précision/rappel** (à garder). Self-RAG : qualité non
  mesurable (juge cassé dans le harnais) mais **×2.3 de latence** (5.7s -> 13.3s) ; gardé pour
  l'instant.
- **Suppressions (B), validées par la mesure** : retrait de **Qdrant** (jamais utilisé, 2e backend
  vectoriel), **réécriture de requête** (le trim-only garde les acronymes *verbatim*, ce qui est
  mieux et ce que la mesure confirme - voir `nlp/query_rewriter.py`), et **GraphRAG** entièrement
  (`nlp/graph_builder.py` + `retrieval/graph_retrieve.py` supprimés ; vue Graphe, toggles, étape
  d'ingestion, config retirés). **Résultat : 10 553 -> 9 294 lignes (-12 %)**, `qdrant-client`
  retiré des dépendances, 108 tests verts.
- **Non-régression vérifiée** : retrieval identique après suppression (hit@k 0.656 / recall 0.588,
  inchangés), car GraphRAG et réécriture étaient off par défaut et mesurés sans gain.

## Modernisation : sortie de LangChain (session 2026-06-30, suite)

Remplacement de `langchain_ollama.OllamaLLM` par un **client HTTP direct** (`core/llm_client.py`,
~60 lignes) qui parle à l'API native Ollama `/api/generate` - choix volontaire vs l'endpoint
OpenAI `/v1` car il **préserve `num_ctx`/`keep_alive`/`top_k`...** dont le projet dépend pour la
VRAM. Même interface (`invoke(prompt, stop=None)` / `stream(prompt)`), `build_llm` inchangé pour
les appelants. `langchain_core.documents.Document` remplacé par une petite dataclass
(`core/document.py`) ; base `langchain_core.embeddings.Embeddings` retirée (l'impl était déjà en
`requests`). **Les 3 dépendances `langchain-core/ollama/community` sont retirées** de
`requirements.txt`.

Validé en headless (vraies générations) : invoke, streaming, **stop-tokens de l'agent ReAct**,
embeddings (dim 1024), `process_query` complet (réponse + 13 citations), boucle agent (ok=True).
108 tests verts.

## Prochaines étapes

- **Découper `app.py`** (~1700 lignes) en modules (vues/composants) - prochaine étape de
  décomplexification (à valider à l'écran via `./start.sh`, pas couvert par les tests).
- **llama3.3:70b** : reprendre `ollama pull llama3.3:70b`, puis basculer `GEN_MODEL` dans `.env`.
- **Éval chiffrée** avant/après les changements de génération : `python -m evals.run_eval` (le projet
  prône la mesure-avant-d'optimiser). Élargir le golden set (`evals/golden_qa_anssi.json`).
- ~~**BM25 multi-document**~~ ✅ **fait** (2026-06-30) : chaque document a son propre index BM25 en
  **MongoDB** (`bm25_indexes`, upsert par `source_doc` — `indexing/keyword_index.save_bm25_to_mongo`).
  Au retrieval, `core/ask._load_bm25` charge **toujours l'index global fusionné**
  (`load_bm25_from_mongo(source_doc=None)`) ; le `.pkl` ne sert plus que de fallback hors-ligne.
  **Bug corrigé** : le filtrage par source se fait au niveau des scores sur l'index complet
  (`bm25_search(..., source_filter=)`), au lieu de tronquer les `ids` (ce qui désalignait positions ↔
  scores et cassait le filtrage sur l'index global). Couvert par `tests/test_bm25.py`.
- **Recherche MULTI-DOCUMENT unifiée** ✅ **fait** (2026-06-30) : le périmètre `source_filter` accepte
  désormais **un nom, une liste de noms, ou None** (= tout l'index), normalisé par
  `utils/sources.normalize_sources`. Unifié sur les **trois chemins** + l'agent :
  - **sémantique** (`retrieval/vector_store`) : clause `$in` (Chroma) / `MatchAny` (Qdrant) ;
  - **BM25** (`bm25_search`) : appartenance `source ∈ {sélection}` ;
  - **graphe** (`core/ask._load_entity_graph`) : **fusion** de plusieurs graphes via `nx.compose_all`
    (None = fusion de TOUS les graphes, au lieu du 1er seul auparavant) ;
  - **agent** : l'outil `rag_search` accepte `document` en string OU liste (`tools/rag_tool`).
  **UI** (`app/app.py`) : le multiselect autorise plusieurs documents (plus de déselection forcée) ;
  warning conservé quand aucun n'est coché. Tests : `tests/test_sources.py`, `tests/test_bm25.py`,
  `tests/test_vector_store.py` (cas multi-doc). ⚠️ Validé end-to-end sur l'index actuel (1 seul doc
  ingéré) : pour une démo réelle du mélange multi-doc, **ingérer un 2e document**
  (`data/ANSSI-CC-cible_2011-1-20.md` est dispo).
- Optionnel : A/B « précision du contexte » (`CONTEXT_DEDUP`/`CONTEXT_REORDER`), démo Qdrant.

## LynX (AI for Requirements) — session 2026-07-01

Travail sur le module embarqué `lynx/`. Axe : **transparence + remédiation**, rendu
épuré (détails dans `lynx/ROADMAP.md` § « Réalisé — 2026-07-01 » et `lynx/README.md`
§ « Transparence & remédiation »). Résumé :

- **Boîte de verre** du raisonnement multi-agents (édition *et* audit) — `lynx/src/trace.py`,
  `lynx/app.py` ; capture des échanges LLM via `llm.start_trace`/`stop_trace`.
- **Remap** : liens typés `LINK`/`UNLINK` entre exigences existantes (DAG, anti-cycle,
  analyse d'impact) — `lynx/src/{models,tree,orchestrator}.py`.
- **Suggestion de correction** (détection → réécriture conforme EN9100, appliquée
  puis re-vérifiée) — `lynx/src/correction.py`, `lynx/skills/suggest_correction.md`.
  Déclenchée **à la demande** sur exigence signalée (jamais en masse → latence maîtrisée).
- **40 tests déterministes verts** (`lynx/tests/test_engine.py`) ; **non-régression
  de l'éval LLM confirmée** (`python -m eval.run_eval` : précision 0.99 · rappel 0.91
  · F1 0.95, identique à la référence).
