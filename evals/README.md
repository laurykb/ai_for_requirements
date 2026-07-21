# Harnais d'évaluation du RAG

Mesure la qualité du pipeline pour piloter les améliorations **par les chiffres**
et détecter les **régressions** entre deux versions.

## Contenu

- `golden_qa_anssi_v2.json` — jeu de questions/réponses **doré** par défaut (30 items,
  réponses vérifiées dans le document source `ANSSI-Cible-Mistral.md`). Chaque item porte
  aussi des `expected_keywords` pour la métrique de recherche pure.
- `run_eval.py` — exécutable d'évaluation (s'appuie sur `core/evaluation.py`).
- `last_eval.json` — dernier run de référence archivé (moyennes agrégées).
- `archive/golden_qa_anssi_v1_legacy.json` — ancien jeu (10 items), conservé pour
  historique. Il vise une version antérieure de la cible et ne s'aligne plus qu'à ~26 %
  sur l'index courant ; ne pas l'utiliser tel quel (préférer le v2).

## Métriques

**Recherche (retrieval) — indépendantes du LLM de génération :**
- `keyword_hit_rate` — fraction des mots-clés attendus présents dans les top-k chunks.
- `context_recall` / `context_precision` — couverture / pertinence des passages vs la référence.

**Réponse (génération) — mode `full` :**
- `exact_match`, `f1_token` — comparaison à la réponse de référence.
- `faithfulness`, `answer_relevance`, `context_relevance` — LLM-as-judge (style RAGAS, local).

## Usage

Depuis la racine du projet (venv activé, `PYTHONUTF8=1` sous Windows) :

```bash
# Recherche seule (rapide) — qualité du retrieval
python -m evals.run_eval --mode retrieval

# Pipeline complet sans juge LLM (rapide) — ajoute exact_match / F1
python -m evals.run_eval --mode full --no-judge

# Pipeline complet avec LLM-as-judge (lent) — fidélité / pertinence
python -m evals.run_eval --mode full

# Options : --limit N, --source-filter NOM_DOC, --no-save, --name MON_RUN
```

## Non-régression

Chaque run est sauvegardé dans MongoDB (collection `eval_runs`). Au lancement
suivant, les moyennes sont **comparées au run précédent** (▲/▼ + alerte régression).
Le script renvoie un **code de sortie non-nul** si une régression nette est détectée
sur `keyword_hit_rate`, `faithfulness` ou `answer_relevance` → utilisable en CI.

## Étendre le jeu doré

Ajouter des items à `golden_qa_anssi_v2.json` : `question`, `answer` (vérifiée dans le
doc) et `expected_keywords`. Garder les réponses **factuelles et ancrées** ; ne pas
inventer. Idéalement ≥ 30 questions couvrant identification, fonctions de sécurité,
menaces, hypothèses et exigences pour une couverture représentative.
