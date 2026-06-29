# AI for SSH — suite souveraine pour l'ingénierie de sécurité (PoC)

**AI for SSH** réunit dans une **seule application Streamlit 100 % locale** (aucune API
externe) deux outils complémentaires pour le travail sur les dossiers de sécurité
(ANSSI / Critères Communs) :

| Outil | Rôle | Documentation |
|---|---|---|
| **Outil RAG** (RAG documentaire) | Questions/réponses **sourcées** sur des cibles de sécurité : retrieval hybride, agent ReAct, serveur MCP, observabilité, **éval chiffrée**. | ce README |
| **AI for Requirements** (LynX) | **Seconde lecture** d'une matrice de traçabilité d'exigences : un système multi-agents mesure en temps réel l'impact d'un ajout/modif/suppression et rend un **verdict unique** (VALIDE / ATTENTION / BLOQUANT). | [`lynx/README.md`](lynx/README.md) |

Les deux outils partagent la même pile locale (Ollama : `mistral-small3.2` + `bge-m3`,
MongoDB) et le même thème sombre. La page d'accueil propose **un bouton par outil**.

> **Genèse — le merge.** AI for SSH est l'**unification de deux projets** auparavant
> distincts : le RAG documentaire (dépôt `rag_project`) et **LynX / AI for Requirements**.
> L'app hôte (`app/app.py`) embarque LynX **sans modifier son code** — voir
> [Intégration des deux outils](#intégration-des-deux-outils).

> **Ce que c'est** : une implémentation de référence **réutilisable et multi-OS** qui
> couvre l'essentiel de la feuille de route « AI Engineer » (RAG → agents → prod), avec
> une discipline *mesure-avant-d'optimiser*. **Ce que ce n'est pas** : une plateforme
> déployée à l'échelle. C'est un PoC abouti, pas un service en production (voir
> [Limites assumées](#limites-assumées)).

---

## Intégration des deux outils

`app/app.py` est l'**hôte** « AI for SSH ». Il expose l'Outil RAG (Accueil, Chat,
Documents, Graphe d'entités, Observabilité, Paramètres) via sa barre latérale, et l'**AI
for Requirements** en plein écran :

- **Chargement non intrusif** : `_load_lynx()` importe `lynx/app.py` via `importlib` sous
  le nom de module unique `lynx_main`, avec `lynx/` ajouté au `sys.path` → les
  `from src import …` de LynX se résolvent dans `lynx/src`. **Le code de LynX n'est pas
  modifié** (il reste exploitable en standalone, cf. `lynx/README.md`).
- **Même process, mono-poste** : pas de second serveur ni d'appel réseau ; LynX conserve
  sa navigation interne (`ss.page`).
- **Thème** : `_LYNX_DARK_CSS` corrige uniquement les fonds clairs / textes quasi-noirs
  que LynX code en dur, pour l'aligner sur le sombre de l'hôte.
- **Navigation** : `view == "requirements"` (bouton d'accueil « AI for Requirements »)
  rend LynX ; le retour « AI for SSH » est un bouton intégré dans l'UI de LynX.

Chaque outil garde sa propre **évaluation chiffrée** : RAG → `evals/` (hit@k 0.93,
recall 0.90) ; LynX → `lynx/eval/` (F1 micro 0.95 sur 196 cas).

---

## Pourquoi ce projet se distingue

La plupart des projets RAG s'arrêtent à « LangChain + une base vectorielle ». Ici, chaque
brique est **activable**, **mesurée** et **justifiée par des chiffres** :

- **Retrieval hybride** sémantique + BM25 + **GraphRAG** + reranking cross-encoder, fusionnés par RRF.
- **Évaluation intégrée** (golden set + métriques type RAGAS) → décisions pilotées par la donnée.
- **Observabilité** (traces/spans souverains) qui a réellement servi à diagnostiquer (ex. « la génération = 85 % de la latence »).
- **Agentique 2026** : RAG-comme-outil, **serveur MCP**, **agent ReAct** streamé.
- **Souveraineté** : tout tourne en local (Ollama + modèles auto-hébergés), zéro donnée envoyée à un tiers.

## Résultats mesurés

**Qualité du retrieval** (golden set de 10 Q/R, `python -m evals.run_eval --mode retrieval`) :

| Métrique | Valeur |
|---|---|
| hit@k (mots-clés attendus) | **0.93** |
| context recall | **0.90** |
| context precision | 0.66 |

→ Le **retrieval est solide** ; le maillon faible mesuré est la **génération** (qualité
du LLM local sur du français technique), pas la recherche.

**Latence** (diagnostiquée via les traces) : la **génération = ~85 %** du temps, car un
modèle 8B + contexte 16k déborde un GPU 8 Go (offload CPU). Leviers livrés :
- *Mode rapide* (modèle 3B + prompt épuré) : **~27 s vs ~150 s (~5,5×)**.
- *Agent retrieve-only* (1 seule génération finale au lieu d'une par recherche).
- *Streaming* : 1ʳᵉ pensée affichée à ~9 s (ressenti type Claude/ChatGPT).

**Décisions A/B** : GraphRAG mesuré **neutre** sur le retrieval factuel (+0,56 s/requête)
→ **désactivé par défaut**, mais activable pour les questions relationnelles.

## Stack & techniques

| Domaine | Ce qui est implémenté |
|---|---|
| Embeddings / Vector DB / RAG | retrieval hybride + RRF + rerank cross-encoder, RAPTOR, GraphRAG, parent-child, **2 backends** (Chroma/Qdrant), évaluation chiffrée |
| Agents / MCP / Observabilité | tracing/spans, anti-injection, RAG-comme-outil, **serveur MCP**, **agent ReAct** streamé |
| Prompt engineering | ancrage + citations, Self-RAG (auto-critique), prompts détaillé/épuré |
| Modèles | auto-hébergé (Ollama), **routage de modèles** par rôle |

## Architecture

```mermaid
flowchart LR
  subgraph Ingestion
    A[PDF] -->|Docling| B[Markdown] --> C[Chunking + enrichissement LLM]
    C --> D[Embeddings bge-m3]
    C --> E[BM25]
    C --> F[Graphe d'entités]
    D --> G[(Vector store<br/>Chroma/Qdrant)]
    E --> H[(MongoDB)]
    F --> H
  end
  subgraph Requête
    Q[Question] --> R[Rewrite] --> S[Retrieval parallèle<br/>sémantique + BM25 + graphe]
    G --> S
    H --> S
    S --> T[Fusion RRF] --> U[Rerank cross-encoder] --> V[Génération + citations]
  end
```

Couches agentiques au-dessus : `tools/rag_tool.py` (RAG-comme-outil) → `rag_mcp_server.py`
(MCP) → `core/agent.py` (ReAct streamé). Observabilité : `utils/tracing.py`.

## Démarrage rapide

```bash
git clone https://github.com/laurykb/ai_for_requirements.git && cd ai_for_requirements
python -m venv .venv && source .venv/bin/activate   # Windows : .venv\Scripts\Activate.ps1
pip install -r requirements.txt                      # + requirements-gpu.txt si GPU NVIDIA
pip install -r lynx/requirements.txt                 # dépendances du module AI for Requirements
python -m spacy download fr_core_news_sm
ollama pull mistral-small3.2 && ollama pull bge-m3   # (+ llama3.1:8b, llama3.2:3b en option)
cp .env.example .env
python serve.py            # démarre MongoDB + Ollama + l'app (Outil RAG + AI for Requirements)
# (ou, si les services tournent déjà : streamlit run app/app.py)
```

Guide complet (modèles, GPU, gotchas par OS) : **[SETUP_PORTABLE.md](SETUP_PORTABLE.md)**.
Prérequis runtime : **Ollama** + **MongoDB**.

## Points d'entrée

| Commande | Rôle |
|---|---|
| `streamlit run app/app.py` | UI (Chat / Documents / Graphe / **Observabilité** / Paramètres) |
| `python -m evals.run_eval --mode retrieval` | évaluation chiffrée (retrieval) |
| `python -m core.agent "…"` | agent ReAct en CLI |
| `python rag_mcp_server.py` | serveur MCP (stdio) |
| `python diagnostic.py` | état des services + routage + vector store |
| `python -m pytest` | 73 tests unitaires (fonctions pures, hors-ligne) |

## Structure

```text
app/         UI Streamlit unique
core/        orchestration (ask, ingest, llm_answer, agent, model_router, self_rag)
retrieval/   retrieval hybride, fusion RRF, rerank, vector_store (abstraction)
indexing/    chunking, embeddings, BM25, persistance Mongo
nlp/         rewrite, NER, GraphRAG, enrichissement
evals/       harnais d'évaluation + golden set
utils/       tracing, sécurité, logging
env_config.py  config portable centralisée
```

## Limites assumées

C'est un **PoC de référence**, à prendre comme tel :

- **Corpus de démo** : validé sur ~1 document (157 chunks). Le passage à un corpus
  multi-documents à grande échelle n'est pas éprouvé.
- **Génération** : le LLM local 8B est le maillon faible (français parfois imparfait) ;
  l'exactitude prime sur la vitesse pour des docs de sécurité → 8B par défaut.
- **Latence** : élevée sur petit GPU (offload CPU). Le vrai correctif est l'infra (GPU
  dédié ou modèle hébergé) ; le code (routage, streaming, abstractions) y est déjà prêt.
- **Pas de couche de service** (FastAPI/conteneur/auth/multi-tenant) : hors périmètre du PoC.

## Licence / contexte

Projet personnel d'apprentissage (AI Engineer). Documents ANSSI publics. Code MIT (au choix).
