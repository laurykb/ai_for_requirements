# Architecture — AI for SSH

Carte de lecture du dépôt. Objectif : qu'un ingénieur situe **où vit quoi** en
une page, sans lire tous les docstrings. Application **100 % locale, mono-poste**
(Ollama + MongoDB + Chroma), qui réunit deux outils : le **RAG documentaire** et
**LynX / AI for Requirements**.

## Les trois couches

```text
                    ┌────────────────── UI ──────────────────┐
   CIBLE (défaut)   │  web/  (Next.js)  ──► api/  (FastAPI)   │   python serve.py
                    └───────────────┬────────────────────────┘
                                    │  (l'UI présente le moteur, sans le dupliquer)
        ┌───────────────────────────┴───────────────────────────┐
        │  MOTEUR PARTAGÉ                        LynX             │
        │  core/ retrieval/ indexing/            lynx/src/        │
        │  nlp/ preprocessing/ utils/            lynx/skills/     │
        │  tools/ env_config.py                  lynx/eval/       │
        └───────────────────────────┬───────────────────────────┘
                                     │
        ┌────────────────────────────┴──────────────────────────┐
        │  DONNÉES / RUNTIME                                      │
        │  MongoDB (chunks, index BM25, sessions, corpus LynX)   │
        │  Chroma (vecteurs)   models/ (reranker)   Ollama (LLM) │
        └────────────────────────────────────────────────────────┘
```

**Principe** : l'UI est un adaptateur mince. Toute la logique RAG vit dans
le moteur partagé ; toute la logique LynX vit dans `lynx/src`. `api/` ne fait
que présenter ce moteur — il ne le duplique pas.

## UI

La migration Streamlit → Next.js est terminée : l'UI legacy a été retirée
(`git log -- legacy/` pour l'historique). Le front Next.js est l'unique UI ;
`lynx/app.py` (Streamlit standalone de LynX) reste utilisable ponctuellement
avec `pip install streamlit streamlit-agraph`.

| | Où |
|---|---|
| Front | `web/` (Next.js 16) |
| Backend | `api/` (FastAPI, même process que le moteur) |
| Lancement | `python serve.py` |
| RAG | `api/rag.py` |
| LynX | `api/lynx_api.py` + `api/lynx_chat.py` |

## Points d'entrée

| Entrée | Rôle |
|---|---|
| `serve.py` | Démarre Mongo + Ollama, puis l'application (API FastAPI + front Next.js) |
| `api/main.py` | Assemble les routeurs FastAPI (rag, sessions, documents, system, prompts, lynx_api, lynx_chat) |
| `rag_mcp_server.py` | Serveur MCP (stdio) exposant le RAG comme outil |
| `core/agent.py` | Agent ReAct en CLI (`python -m core.agent "…"`) |
| `evals/`, `lynx/eval/` | Harnais d'évaluation chiffrée (RAG ; LynX) |
| `diagnostic.py` | État des services + routage + vector store |

## Flux RAG (de la question à la réponse sourcée)

1. **Ingestion** — `preprocessing/pdf_to_markdown.py` → `indexing/chunking.py`
   (chunking sémantique) → `nlp/chunk_enhancer.py` (enrichissement + RAPTOR) →
   `indexing/embedding.py` (Chroma) + `indexing/keyword_index.py` (BM25 → Mongo).
   Orchestré par `core/ingest.py`.
2. **Retrieval** — `core/ask.py` → `retrieval/retrieve.py::hybrid_retrieve` :
   sémantique (Chroma) + BM25 en parallèle → fusion `retrieval/rrf.py` → rerank
   `retrieval/cross_encoder.py` (bge-reranker, `models/`) → `parent_child` →
   `context_refine`.
3. **Génération** — `core/llm_answer.py` (Ollama, streaming, citations). Couches
   optionnelles : `core/self_rag.py`, `core/planner.py` + `core/agent.py` (ReAct),
   `core/router.py` + `core/model_router.py`, `core/attribution.py`.

## Flux LynX (seconde lecture d'une matrice d'exigences)

`lynx/src/orchestrator.py` construit la matrice candidate, lance les analyseurs
(`analyzers.py` : déterministes séquentiels + sémantiques en parallèle), agrège
en `ImpactReport`, puis synthétise un verdict (VALIDE / ATTENTION / BLOQUANT). Les
« agents » sont des **prompts** (`lynx/skills/*.md`) associés à des schémas Pydantic
(`schemas.py`). Audit global : `audit.py`. Boîte de verre : `trace.py`.

### Chat sur la baseline (`api/lynx_chat.py`)

L'onglet Chat d'AI for Requirements réutilise **tout** le flux RAG ci-dessus,
mais son périmètre documentaire est verrouillé sur un document réservé
(`baseline-exigences-lynx.md`) : la matrice de l'arbre est sérialisée en
Markdown (une section par domaine, un titre par exigence → les citations
pointent des identifiants d'exigences) puis ingérée par le pipeline standard.
`/api/lynx/chat/sync` réindexe à la demande ; `/api/lynx/chat/status` compare
l'empreinte de l'arbre à celle de l'index (bandeau « à jour / désynchronisé »
de l'UI). Le document réservé est exclu de `/api/sources` : chaque monde ne
voit que ses documents et ses conversations.

## Stockage

- **MongoDB** — chunks, index BM25 sérialisé, sessions de conversation, corpus de
  travail LynX (`working.json` + historique).
- **Chroma** — vecteurs (embeddings bge-m3).

> **Multi-installations sur une même machine** : le serveur Mongo (:27017) est
> partagé, mais chaque installation a SON Chroma local (`data/chroma_db`). Avec
> le `MONGO_DB=ragdb` par défaut, deux installations lancées côte à côte se
> polluent mutuellement (documents/sessions communs, vecteurs incohérents).
> Donner à chacune sa base : `scripts/migrate_mongo_db.py <base_dédiée>` puis
> `MONGO_DB=<base_dédiée>` dans le `.env` (voir l'en-tête du script).
- **`models/`** — poids du cross-encoder (gitignoré, local).
- **Ollama** — génération (`mistral-small3.2`) + embeddings (`bge-m3`).

> `data/`, `models/`, `web/.next`, `web/node_modules`, `.venv`, `lynx/corpus/`
> sont **gitignorés** : ce sont des états runtime, pas des sources à relire.
