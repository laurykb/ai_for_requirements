# Guide d'étude — comment ce projet est construit

Porte d'entrée pour un **relecteur** (étudiant, pair) qui veut comprendre *comment*
le projet a été réalisé et donner un retour utile. Ce guide donne un **ordre de
lecture**, une **carte des modules** et les **décisions de conception** à critiquer.
Pour le positionnement et les résultats, voir [README.md](README.md).

> **Un dépôt, deux outils.** L'app hôte (`app/`) est l'**Outil RAG** documentaire ;
> **LynX** (dans `lynx/`) est l'assistant de vérification d'exigences, embarqué tel
> quel (chargé via importlib, sans modifier son code). Les deux partagent la même
> pile locale : **Ollama** (`mistral-small3.2` + `bge-m3`) et **MongoDB**. 100 %
> local, pas de clé API.

## Comment lire ce dépôt

Ordre conseillé (du plus parlant au plus technique) :

1. **`README.md`** puis **ce guide** — le quoi et le pourquoi.
2. **Un flux de bout en bout**, en suivant les pointeurs ci-dessous : choisis le RAG
   *ou* LynX, lis les 4-5 fichiers du chemin principal, ignore le reste au début.
3. **Les tests** (`tests/`, `lynx/tests/`) — ils montrent le comportement attendu en
   quelques lignes, sans dépendre d'Ollama.
4. **Les décisions de conception** (dernière section) — c'est là que le retour a le
   plus de valeur.

Principe transverse du projet : **chaque technique est un levier activable et
mesurable** (rien n'est « magique »), et on **mesure avant d'optimiser** (les évals).

---

## Partie A — l'Outil RAG

**But** : répondre à une question sur des documents techniques (ANSSI / Critères
Communs) avec des réponses *ancrées* et *citées*, en local.

### Flux d'une question (chemin principal)

```
question ─▶ core/router.py      (route Auto/RAG/Agent, SANS appel LLM)
         ─▶ core/ask.py         (orchestre le retrieval hybride)
              ├─ retrieval/semantic_search.py   (vecteurs, Chroma)
              ├─ retrieval/keyword_bm25.py      (BM25, Mongo)
              ├─ retrieval/rrf.py               (fusion des deux classements)
              ├─ retrieval/cross_encoder.py     (rerank fin)
              └─ retrieval/context_refine.py    (dédup + anti lost-in-the-middle)
         ─▶ core/llm_answer.py   (génère la réponse ancrée + citations)
         ─▶ core/evaluation.py   (vérifie la fidélité de la réponse)
         ─▶ core/self_rag.py     (auto-correction si la vérif échoue)
```

Variante **agent** : `core/agent.py` (boucle ReAct) appelle l'outil
`tools/rag_tool.py`, exposé aussi en **MCP** via `rag_mcp_server.py`.

### Carte des modules

| Rôle | Fichiers |
|---|---|
| Entrée / config | `serve.py`, `api/main.py` (+ `web/`), `env_config.py` ; UI legacy : `legacy/app/main.py` |
| Ingestion | `core/ingest.py`, `indexing/{chunking,embedding,keyword_index,store_mongo}.py`, `nlp/chunk_enhancer.py`, `preprocessing/pdf_to_markdown.py` |
| Retrieval hybride | `retrieval/{retrieve,semantic_search,keyword_bm25,rrf,cross_encoder,parent_child,context_refine,vector_store}.py` |
| Génération | `core/{llm_answer,llm_client,summarize}.py` |
| Orchestration | `core/{router,ask,self_rag,evaluation,model_router}.py` |
| Agent / MCP | `core/agent.py`, `tools/rag_tool.py`, `rag_mcp_server.py` |
| UI (Streamlit) | `app/{main,chat,documents,ingestion,settings_view,common}.py` |
| Transverse | `utils/{security,tracing,sources,text_utils,logging_config}.py` |
| Évaluation | `evals/run_eval.py`, `evals/README.md` |

---

## Partie B — LynX (vérification d'exigences)

**But** : à chaque édition d'une matrice de traçabilité (ajout / modif / suppression /
lien), un **système multi-agents** mesure l'impact sur toute l'arborescence et rend
un verdict unique (VALIDE / ATTENTION / BLOQUANT), avec la preuve.

### Architecture = fan-out (à retenir)

Les agents **ne se parlent pas**. L'orchestrateur envoie à chaque analyseur un
extrait de la matrice ; chacun rend un avis *indépendant* ; un agent de **synthèse**
agrège le tout. C'est ce que rend visible la **boîte de verre** (`src/trace.py`).

```
action ─▶ orchestrator.build_candidate_tree()   (arbre « candidat » après action)
       ─▶ run_impact_analysis()  ──┬─ déterministes : allocation, aval (instantané)
                                   └─ LLM (parallèle) : pertinence, couverture, redondance
       ─▶ synthèse streamée ─▶ verdict + preuve
```

### Carte des modules (`lynx/src/`)

| Rôle | Fichiers |
|---|---|
| Schémas de données | `models.py` (Requirement, Action, Link, Finding, ImpactReport) |
| Graphe (DAG) | `tree.py` (navigation + CRUD immutable, `with_link`/`with_unlink`) |
| Analyseurs (agents) | `analyzers.py` (allocation, aval, pertinence, couverture, redondance, pertinence aval, impact latent, co-références) |
| Orchestration | `orchestrator.py` (candidat, run, synthèse) |
| Client LLM | `llm.py` (httpx, cache, **capture de trace**) ; `embeddings.py` |
| Audit global | `audit.py` |
| Boîte de verre | `trace.py` (humanise les échanges agents) |
| Remédiation | `correction.py` + `redaction.py` (réécriture conforme EN9100) |
| Prompts des agents | `skills/*.md` (un fichier par agent, éditable sans toucher au code) |
| Persistance / mesure | `store.py`, `corpus_io.py`, `feedback.py`, `roi.py`, `telemetry.py` |
| Interfaces | `api/lynx_api.py` (routeur FastAPI → front Next.js `web/`) ; `lynx/app.py` (Streamlit, legacy) |

Détails d'approche : `lynx/METHODOLOGIE.md`. Reste à faire : `lynx/ROADMAP.md`.

---

## Lancer & tester

```bash
# Application complète (RAG + LynX) : Mongo + Ollama + Streamlit
bash start.sh                       # ou : python serve.py

# Tests (hors-ligne, sans Ollama)
python -m pytest tests                                     # RAG (~110)
cd lynx && python -m pytest                                # LynX (40)

# Évaluations chiffrées (avec LLM)
cd lynx && python -m eval.run_eval          # précision/rappel/F1 (réf. F1 0.95)
python -m evals.run_eval --mode retrieval   # RAG : hit@k / recall
```

---

## Où porter ton regard (décisions à critiquer)

Les choix les plus intéressants à challenger — c'est là qu'un retour aide vraiment :

- **RAG — mesurer avant d'optimiser.** Des leviers ont été *retirés* car la donnée ne
  les justifiait pas (GraphRAG = 0 effet, réécriture de requête négative). Question :
  la méthode d'A/B (`evals/`) est-elle assez robuste ?
- **RAG — routeur sans LLM** (`core/router.py`) : router Auto/RAG/Agent par heuristique
  plutôt que par un appel LLM. Bon compromis latence/robustesse, ou trop rigide ?
- **RAG — sortie de LangChain** au profit d'un client HTTP direct (`core/llm_client.py`)
  pour garder la main sur `num_ctx`/`keep_alive` (VRAM). Simplicité vs portabilité.
- **LynX — précision d'abord.** La couverture ne se déclenche qu'à la suppression pour
  éviter les faux positifs (F1 0.95, 1 seul faux positif / 196). Bon arbitrage
  précision/rappel, ou trop conservateur ? Cf. `lynx/METHODOLOGIE.md`.
- **LynX — fan-out vs chaîne d'agents.** Des agents indépendants + une synthèse, plutôt
  qu'une conversation entre agents. Plus simple et parallèle — qu'est-ce qu'on perd ?
- **LynX — déterministe d'abord, LLM en dernier recours** (ex. routeur embeddings de la
  redondance qui n'appelle le LLM que dans la zone ambiguë). Où est la bonne frontière ?
- **Transverse — cadre PoC mono-poste assumé** (Streamlit, verrou fichier, pas d'auth).
