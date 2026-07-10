# LynX — assistant de vérification de la déclinaison d'exigences

Une **seconde lecture infatigable et exhaustive** d'une matrice de traçabilité
d'exigences. Au moment où un ingénieur ajoute, modifie ou supprime une exigence,
un système multi-agents mesure en temps réel l'impact sur toute l'arborescence et
détecte **tôt** (à la conception) ce qui ne serait sinon vu que **tard** (à la
remontée du V, par les tests) ou par relecture humaine (RPP).

100 % local et open-source, sans clé API.

> **Intégré à [AI for SSH](../README.md)** (aux côtés du RAG documentaire), tout en
> restant **utilisable en standalone** : l'app hôte importe `lynx/app.py` sans modifier
> son code. La nouvelle UI Next.js expose LynX via l'API FastAPI `api/lynx_api.py`.

## Fiabilité mesurée

Sur 196 cas labellisés par construction (16 domaines) :

| | Précision | Rappel | F1 |
|---|---|---|---|
| **micro** | **0.99** | 0.91 | **0.95** |

Posture **précision d'abord** : l'outil ne crie quasiment jamais au loup (1 faux
positif sur 196). Voir `METHODOLOGIE.md` et `eval/`.

## Les analyses

| Axe | Type | Question |
|---|---|---|
| Structure | déterministe | l'action est-elle valide (collision, niveau, cible) ? |
| Allocation | déterministe | la somme des budgets enfants dépasse-t-elle le plafond parent ? |
| Pertinence amont | LLM | la cible reste-t-elle cohérente avec ses ancêtres (N+1, N+2…) ? |
| Couverture | LLM | le parent reste-t-il entièrement couvert par ses filles ? |
| Redondance | embeddings + LLM | doublon ou sur-spécification entre sœurs ? |
| Pertinence aval | LLM | la cible reste-t-elle cohérente avec ses filles (déclinaison) ? |
| Impact latent | embeddings + LLM | des exigences **non reliées** sont-elles impactées par la modif ? |
| Co-références | LLM | les exigences partageant un référent concret (interface, valeur) se contredisent-elles ? |
| Aval | déterministe | quels descendants sont impactés ? |

Le tout est synthétisé en **un verdict unique** (VALIDE / ATTENTION / BLOQUANT),
streamé, avec citation de la preuve. Un audit global note la matrice entière.

## Transparence & remédiation

- **Boîte de verre** — pour chaque verdict *et* pour l'audit, un panneau montre en
  langage naturel ce que **chaque agent a reçu et répondu** (fan-out des analyseurs
  → agent de synthèse), avec un bandeau « pipeline » qui situe la gravité par agent.
  L'objectif : le moins de boîte noire possible, sans surcharge (on ne détaille que
  ce qui est signalé).
- **Remap (liens DAG)** — rattacher deux exigences existantes par un **lien typé**
  (DERIVE / REFINES / SATISFIES / VERIFIES / ALLOCATES_TO) sans créer d'enfant :
  ajout/retrait d'arêtes amont/aval, avec anti-cycle et analyse d'impact.
- **Suggestion de correction** — sur une exigence **signalée** (WARNING/BLOQUANT),
  un agent propose une **réécriture conforme** (règles EN9100 + contexte : parent,
  ancêtres, sœurs, filles, problèmes détectés). L'ingénieur la relit puis l'applique
  en un clic — elle **repasse par l'analyse d'impact**. À la demande uniquement,
  jamais en masse (l'audit ne fait que détecter) : latence maîtrisée.

## Installation

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
ollama pull mistral-small3.2     # LLM (jugement)
ollama pull bge-m3               # embeddings (redondance)
```

## Lancement

```bash
streamlit run app.py             # interface
python -m src.api --port 8800    # ou l'API headless (hors Streamlit)
```

## Tests & évaluation

```bash
python -m pytest tests/test_engine.py -v     # tests déterministes (sans LLM)
python -m eval.run_eval                       # évaluation complète (précision/rappel/F1)
python -m eval.run_eval --fast                # déterministe seul
```

## Documentation

- **`METHODOLOGIE.md`** — l'approche (moteur hybride, principes d'AI engineering,
  méthode d'évaluation, comment étendre).
- **`ROADMAP.md`** — ce qui reste à faire pour un produit réel.
- **`HARDWARE.md`** — montée en charge (Ollama réglé, vLLM).
- **`HARDENING.md`** — backlog de durcissement (revue adversariale).
- **`eval/COMPARAISON_MODELES.md`** — choix du modèle par la donnée.

## Configuration (env ou `src/config.py`)

`LLM_MODEL`, `LLM_BASE_URL` (Ollama `/v1` par défaut, ou vLLM), `EMBED_MODEL`,
`LLM_MAX_CONCURRENCY`, `LLM_VOTE` (self-consistency), seuils d'embeddings et de
tolérance d'allocation. Tout est local ; la « clé » API est un placeholder ignoré.
