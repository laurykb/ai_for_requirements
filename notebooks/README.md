# Notebooks

Notebooks Jupyter de **documentation et de démonstration** du pipeline.

## Prérequis
- Ollama + MongoDB démarrés, et un corpus déjà ingéré (onglet *Documents* de l'app).
- Outillage : `pip install -r requirements-dev.txt`.

## Notebooks
| Fichier | Contenu |
|---|---|
| [`01_evaluation.ipynb`](01_evaluation.ipynb) | Évaluation chiffrée du retrieval (golden set, hit@k / recall / precision) — reproduit les chiffres du README. |
| [`02_pipeline.ipynb`](02_pipeline.ipynb) | Walkthrough du retrieval hybride : sémantique vs BM25 (complémentarité), puis fusion RRF + rerank cross-encoder. |
| [`03_agent_mcp.ipynb`](03_agent_mcp.ipynb) | La couche agentique : RAG-comme-outil → serveur MCP → agent ReAct streamé (Pensée/Action/Observation). |

## Lancer
```bash
jupyter lab            # ouvrir et exécuter interactivement
# ou exécuter en place (peuple les sorties) :
jupyter nbconvert --to notebook --execute --inplace notebooks/01_evaluation.ipynb
```
