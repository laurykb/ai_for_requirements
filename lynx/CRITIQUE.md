# Auto-critique — du PoC à un outil utilisable

État du produit : un assistant de vérification d'édition d'exigences convaincant
en démo. Pour devenir un outil réel, il manque trois choses : **(a) l'observabilité
+ la vitesse**, **(b) l'audit global de la matrice** (pas seulement à l'édition),
**(c) la persistance + l'import réel**. Le reste (liens typés, évaluation,
multi-utilisateur) suit.

## Les vrais trous (par ordre d'importance)

### 1. Latence et absence de trace — le plus visible. ✅ FAIT
Avant : ~2 min par analyse, un spinner aveugle ; agents quasi séquentiels sur un
seul GPU ; aucune visibilité sur qui travaille.
Livré : modèle plus rapide (`mistral-small3.2`, ~35 s), parallélisme réel des
agents (résultats remontés à la complétion), **trace en direct** par agent
(`st.status` : « Pertinence amont — terminé »…), **streaming token par token** de
la réponse finale, et **cache** par exigence/corpus inchangé.

### 2. On réagit à une édition, on n'audite pas la matrice. ⬜ EN COURS
Manque conceptuel central vis-à-vis de l'objectif (« fiabiliser la matrice »). Le
système n'analyse qu'une action locale (cible + parent + sœurs + ancêtres). Il
doit pouvoir, **au chargement**, scanner tout l'arbre et lister tous les liens
manquants, incohérences, ambiguïtés et mauvaises rédactions, puis produire un
**score de fiabilité** de la matrice — vue globale d'abord, vérification continue
ensuite.

### 3. Le modèle de liens est trop pauvre. ✅ FAIT (sauf assistant de rédaction)
`parent_id` unique = arbre strict. Une vraie matrice est un **graphe (DAG)
multi-liens typés** (satisfait / dérive / vérifie / raffine), many-to-many,
multi-documents. Les exigences manquent d'**attributs** (méthode de vérification
IADT, source, rationale, criticité) réclamés par le guide EN9100. _(Fait depuis :
`lynx/src/models.py:52-59` enum `LinkType` DERIVE/REFINES/SATISFIES/VERIFIES/
ALLOCATES_TO ; `:83` `links: List[Link]` ; `:85-89` verification/source/rationale/
criticité/version.)_ **Reste vrai** : l'assistant de rédaction existe mais n'est pas
branché dans le verdict (`lynx/src/redaction.py:29 check_redaction` n'est importé
nulle part — ni `app.py`, ni `orchestrator.py`, ni `analyzers.py`).

### 4. Données, échelle, persistance. 🟡 PARTIEL
Corpus jouet de 34 exigences ; un référentiel réel = des milliers, répartis sur
Word/Excel/DOORS/ReqIF. La **persistance** est faite (plus de perte au
rafraîchissement, historique conservé). _(Fait depuis : `lynx/src/store.py:25-26,74-106`
— `working.json` + `history.jsonl`.)_ **Restent vrais** : aucun **import réel**
(ReqIF/Excel — l'import demeure JSON uniquement), pas de multi-utilisateur.
`streamlit-agraph` ne tiendra pas 2 000 nœuds : il faudra un
**graphe focalisé** (ego-graph), recherche et filtrage.

### 5. Confiance. ✅ FAIT
Verdicts LLM non déterministes et non validés. Pas de jeu d'**évaluation**
mesurant précision/rappel sur les défauts plantés, pas de **citation** du passage
fautif, pas de capture de **feedback** (« ce verdict était-il juste ? »). Pour la
confiance d'un ingénieur : tracer le pourquoi et mesurer la qualité. _(Fait depuis :
`lynx/eval/run_eval.py` (196 cas) ; `lynx/src/analyzers.py:53-56` champ `preuve` ;
`lynx/src/feedback.py`.)_

### 6. UI. 🟡 PARTIEL
Amélioré (accueil, graphe agrandi, streaming, trace). Restent : états vides
soignés, indice de rédaction en direct dans l'éditeur, focus-graph pour gros
corpus, finitions visuelles.

## Feuille de route

1. ~~Observabilité + vitesse~~ ✅
2. ~~Audit global de la matrice (score + tous les points faibles)~~ ✅
3. ~~Persistance des éditions + historique~~ ✅
4. Import réel (ReqIF / Excel) — reporté (on reste en JSON)
5. ~~Jeu d'évaluation (rappel sur défauts plantés)~~ ✅ — précision/calibration + feedback restent
6. ~~Modèle de liens typés + attributs EN9100~~ ✅ (fait depuis : `lynx/src/models.py:52-59,83,85-89`) — reste à brancher l'assistant de rédaction au verdict
7. Finitions UI / focus-graph pour l'échelle — partiel

## Travaux complémentaires (suite à AUTOCRITIQUE.md)

- ~~**Scaling hardware** : découplage d'Ollama via client compatible OpenAI (httpx),
  exploitable sur vLLM/SGLang local (2× RTX 6000) — voir HARDWARE.md~~ ✅
- ~~**Vague 0 — ne plus mentir** : incertitudes rendues visibles (roll-up partiel,
  exigence non auditée, badges de mode dégradé)~~ ✅
- ~~**Vague 1 — justesse du déterministe** : conversion d'unités, tolérance réelle +
  epsilon, plages « entre X et Y » exclues, classification par proposition,
  recollage des milliers ancré sur une unité~~ ✅
- Restant (AUTOCRITIQUE) : citations/preuves IA, reproductibilité (vote/cache),
  modèle DAG + attributs EN9100, concurrence/multi-utilisateur, focus-graph.
