# Méthodologie

Comment ce projet est construit, et comment le reprendre / l'étendre.

## 1. Le problème (cycle en V)

En ingénierie système, les exigences se **déclinent** de haut en bas (L0 besoin →
L5 réalisation) et se **valident** de bas en haut (remontée du V, par les tests).
Aujourd'hui, une déclinaison **incomplète** n'est souvent détectée que **tard**, à
la remontée (par les tests), ou par relecture documentaire humaine (RPP) —
fastidieuse et faillible.

**LynX** agit comme une **seconde lecture, infatigable et exhaustive**, qui décale
la détection « à gauche » (à la conception) et fiabilise l'action humaine, à
chaque édition et au chargement (audit global). Il **assiste**, ne remplace pas :
l'ingénieur garde la décision (dérogation justifiée).

## 2. Architecture — moteur hybride

Principe directeur : **donner à chaque tâche le bon outil**.

| Couche | Pour quoi | Pourquoi |
|---|---|---|
| **Déterministe** (Python) | allocation budgétaire (somme enfants vs plafond), validité structurelle (liens, cycles, doublons), propagation aval | exact, instantané, reproductible — aucune raison d'appeler un LLM |
| **Embeddings** (bge-m3) | redondance / doublons (similarité cosinus) | déterministe, rapide, scalable ; *routeur* : tranche les cas clairs, n'appelle le LLM que dans la bande ambiguë |
| **LLM** (mistral-small3.2) | jugement sémantique nuancé : pertinence amont, couverture, redondance subtile | seul capable du raisonnement sur le sens |

Les analyseurs travaillent sur un **DAG** (`parent_id` + liens typés). Un
orchestrateur applique l'action sur un arbre candidat, lance les analyseurs (les
sémantiques en parallèle), agrège un `ImpactReport`, et synthétise un verdict
unique streamé.

Tout est **local et open-source** (endpoint compatible OpenAI : Ollama / vLLM),
**sans clé API**. Voir `HARDWARE.md` pour la montée en charge (vLLM).

## 3. Principes d'AI engineering appliqués

- **Précision d'abord** : un faux positif détruit la confiance d'un expert plus
  vite qu'un oubli. On tune pour minimiser les faux positifs (précision 0.99).
- **Développement piloté par l'éval** : on ne tune pas au feeling. Chaque
  changement est validé contre `eval/run_eval.py`. C'est ainsi qu'on a trouvé et
  corrigé deux vrais trous (allocation : rappel 0.34 → 0.92 ; redondance : 5 faux
  positifs → 0).
- **Reproductibilité** : température 0 + cache par (modèle, prompt, entrée) →
  mêmes entrées, même verdict.
- **Honnêteté (pas de complaisance)** : on ne masque jamais une incertitude
  (constats INFO visibles, « non audité » affiché, fiabilité mesurée montrée).
- **Citations** : chaque constat sémantique cite le passage / la sœur / l'ancêtre
  fautif — l'expert vérifie et décide.
- **Observabilité** : chaque appel LLM est journalisé (latence, tokens, succès).
- **Valeur mesurée (ROI)** : on compte les défauts captés à la conception
  (shift-left vs remontée du V), le temps estimé économisé, l'accord humain.

## 4. La méthode d'évaluation

Le cœur de la confiance. Sur un corpus **propre et contrôlé**, chaque cas applique
une action qui **injecte un défaut connu** (ou non) — le label est fiable **par
construction**, pas par jugement d'un LLM.

- Dataset : `eval/corpus_eval.json` (6 cas de référence) + `eval/cases_generated.json`
  (190 cas générés par construction, 16 domaines, validés structurellement).
- Métriques : **précision / rappel / F1 par axe** (`eval/run_eval.py`), seuil de
  non-régression sur la précision.
- Résultat courant : `eval/last_eval.json` (lu par l'UI pour afficher la fiabilité).

Reproduire :
```bash
python -m eval.run_eval            # complet (LLM)
python -m eval.run_eval --fast     # déterministe seul
```
Comparer des modèles : voir `eval/COMPARAISON_MODELES.md`.

## 5. Comment étendre

- **Ajouter un analyseur** : une fonction `analyze_x(ctx) -> List[Finding]` dans
  `src/analyzers.py`, ajoutée à `SEMANTIC_ANALYZERS` ou `DETERMINISTIC_ANALYZERS`.
- **Ajouter / modifier un agent** : éditer le prompt dans `skills/<nom>.md`
  (rechargé à chaud, aucun cache prompt). Ajouter des exemples few-shot si utile.
- **Ajouter des cas d'éval** : enrichir `eval/cases_generated.json` (label par
  construction) puis relancer `run_eval`. Ne JAMAIS tuner sans re-mesurer.
- **Régler un seuil** : `src/config.py` (seuils embeddings, tolérance allocation,
  modèle, concurrence) — puis valider par l'éval.

## 6. Structure du code

```
app.py              UI Streamlit (accueil + graphe)
src/
  models.py         Pydantic : Requirement, Action, Finding, ImpactReport, liens typés
  tree.py           graphe DAG (parents/enfants/ancêtres/descendants)
  extract.py        extraction numérique (unités, conversion, plages, milliers)
  analyzers.py      allocation, aval (déterministes) + pertinence/couverture/redondance (LLM)
  orchestrator.py   applique l'action -> analyseurs -> ImpactReport -> synthèse streamée
  audit.py          audit global de la matrice (score + points faibles)
  llm.py            client compatible OpenAI (httpx) + cache + vote
  embeddings.py     embeddings (batch) + routeur de redondance
  redaction.py      assistant de rédaction (règles EN9100)
  store.py / corpus_io.py   persistance, import multi-documents
  telemetry.py / feedback.py / roi.py   observabilité, feedback, valeur
  api.py            service HTTP JSON (hors Streamlit)
skills/             prompts des agents (.md)
eval/               harnais d'évaluation + datasets + comparaison de modèles
tests/              tests déterministes (sans LLM)
```

Docs liées : `README.md` (démarrage), `ROADMAP.md` (reste à faire),
`HARDENING.md` (backlog durcissement), `HARDWARE.md` (échelle),
`CRITIQUE.md` / `AUTOCRITIQUE.md` (auto-critiques), `eval/COMPARAISON_MODELES.md`.
