# LynX — Auto-critique consolidée

*Architecte projet — date : 2026-06-25*

Ce document fusionne cinq passes d'auto-critique (moteur déterministe, conception IA & confiance, produit & UX, architecture/échelle/ops, fidélité au domaine EN9100). Les constats ont été dédupliqués, regroupés par thème et reclassés non pas selon la gravité déclarée passe par passe, mais selon **l'impact sur un usage réel** : un outil de fiabilisation de matrice de traçabilité dont les verdicts ne sont ni reproductibles, ni vérifiables, ni fidèles au modèle métier, et dont l'état se corrompt à plusieurs utilisateurs.

Position honnête de départ : **LynX est un excellent démonstrateur, pas encore un système.** L'architecture est saine dans ses intentions (déterministe d'abord, LLM ensuite, dégradation propre, immutabilité de l'arbre), la direction produit est soignée, mais trois failles structurelles le rendent inadapté à l'usage opérationnel : (1) le modèle de données ne sait pas représenter une matrice de traçabilité, (2) les verdicts — déterministes ET sémantiques — sont faux ou non vérifiables sur données réelles sans le signaler, (3) l'état est global/mono-utilisateur et se corrompt silencieusement en équipe.

---

## Synthèse des verdicts par thème

| Thème | Constat dominant | Impact réel |
|---|---|---|
| A. Fidélité au domaine (modèle de données) | Arbre strict mono-parent au lieu d'un DAG de liens typés | **Critique** — l'objet de valeur (la matrice) est inexprimable |
| B. Justesse du moteur déterministe | Extraction numérique et roll-up faux silencieusement | **Critique** — le cœur « fiable » produit des BLOQUANTS fictifs et masque de vrais dépassements |
| C. Confiance IA (reproductibilité, preuve) | Verdicts non reproductibles, non sourcés, éval gonflée | **Critique** — non auditable en contexte EN9100 |
| D. Concurrence & persistance | État global, fichiers partagés sans verrou | **Critique** — corruption silencieuse dès 2 utilisateurs |
| E. UX d'audit & actionnabilité | Trace éphémère, verdict non actionnable, graphe non scalable | **Majeur** — invendable au-delà de la démo |
| F. Ops & déploiement | Couplage Ollama localhost, pas d'API, pas d'observabilité, pas d'authN | **Majeur à critique** — non déployable hors poste local |

---

## A. Fidélité au domaine — le modèle de données ne représente pas une matrice (CRITIQUE)

C'est, à mon sens, la faille la plus profonde car **structurelle** : elle ne se corrige pas par un patch, elle conditionne tout le reste. L'outil prétend « fiabiliser la matrice de traçabilité » mais son modèle ne sait pas exprimer une matrice.

- **Arbre strict vs DAG many-to-many.** `Requirement` n'a qu'un seul `parent_id: Optional[str]` (`src/models.py:80`) et `tree.py` n'indexe qu'une arête par nœud. Une matrice EN9100 réelle est un graphe orienté : une exigence transverse (interfaces, environnement, sûreté, masse) satisfait plusieurs besoins amont et peut être couverte par plusieurs domaines. Tout ce qui est transverse devient non-représentable ou mal rattaché. _(Résolu depuis : `models.py:83 links: List[Link]` + enum `LinkType` DAG `:52-59`.)_
- **Liens non typés.** Un seul `parent_id` confond *dérive de*, *satisfait*, *vérifie*, *raffine*. Le corpus en porte la preuve : les éléments L5 (essais) sont rattachés comme *enfants de décomposition* alors qu'ils sont des artefacts de **vérification** (lien VERIFIES remontant). Le moteur ne peut donc pas distinguer une propagation de spécification descendante d'une preuve de vérification — ce qui fausse à la fois l'allocation, la couverture et l'analyse amont.
- **Budgets many-to-many invisibles.** Le pack batterie est spécifié côté Propulsion ET compté comme poste de budget masse côté Structure : ce sont les mêmes équipements physiques, mais aucun lien transverse ne les relie. Le roll-up budgétaire reste donc un calcul intra-branche fragile, incapable de propager l'impact masse d'un changement fonctionnel.
- **Attributs intrinsèques EN9100 absents** alors que les propres règles du projet (`skills/regles_redaction.md`) les exigent : méthode de vérification IADT (R-METHODE-VERIF), source/origine (R-TRACABILITE), rationale (R-NECESSAIRE), criticité/DAL, version/révision. L'outil affirme appliquer ces règles mais le modèle ne peut même pas stocker la valeur à contrôler. _(Résolu depuis : `models.py:85-89` verification/source/rationale/criticité/version + enum `VerifMethod` `:43-49`.)_
- **`test_status` est un `str` libre** (`src/models.py:81`, défaut `"PENDING"`) — non typé en Enum contrairement à `ActionType`/`Severity`/`Scope` —, qui confond statut de vérification (Not Verified / In Verification / Verified / Failed) et résultat d'essai, et n'a pas de sens clair pour les niveaux L0..L4. _(Toujours vrai.)_
- **Pas de baseline / configuration, pas de multi-documents.** Aucune notion de gel de référentiel à une revue (SRR/PDR/CDR), aucun document/segment source (bord vs sol). Le défaut « sur-spécification » planté (antenne sol fille d'une exigence portée bord) est en réalité un symptôme : il manque un document « Station sol » auquel rattacher l'exigence.
- **Orphelins silencieux.** `with_deleted` laisse des enfants pointant dans le vide, traités comme racines de fait sans signalement — une rupture de traçabilité absorbée au lieu d'être déclarée non-conforme.

**Direction** : remplacer `parent_id` par `links: List[TraceLink{target_id, link_type}]`, construire un DAG avec détection de cycle, et exposer l'« arbre de décomposition » comme une simple projection (filtre `link_type=DERIVE`). Aligner sur ReqIF (SpecObject/SpecRelation/SpecType) pour préparer l'import réel. Étendre `Requirement` avec les attributs EN9100 et typer `test_status` en Enum.

---

## B. Justesse du moteur déterministe — des verdicts faux, silencieux (CRITIQUE)

Le moteur est censé être la partie *fiable* (par opposition au LLM). Or l'extraction numérique repose sur des heuristiques fragiles et l'allocation somme des grandeurs hétérogènes sans contrôle de cohérence. Conséquence : sur corpus réel, des BLOQUANTS fictifs apparaissent et de vrais dépassements sont masqués — **sans aucune trace de l'incertitude**.

- **Recollage des séparateurs de milliers trop agressif** (`extract.py:_normalize`, regex `(?<=\d) (?=\d{3}(?:\D|$))`). `_normalize('12 345')` → `'12345'` ; « version 2 014 », « lot 3 500 unités », « 5 200 m » fusionnent deux valeurs ou créent un nombre fantôme qui entre directement dans le calcul d'allocation.
- **Fenêtre de contexte fixe de 45 caractères** (`extract.py:117`) pour classer max/min/measure. Un mot-clé placé au-delà (« …, tous équipements inclus, ne doit pas excéder 150 kg ») tombe hors fenêtre ; la quantité est classée `measure`. Or le défaut retombe **silencieusement** sur `measure` (`extract.py:102`), et un budget non reconnu comme `max` est tout bonnement ignoré par l'allocation : un vrai dépassement passe inaperçu.
- **Plages « entre X et Y » / « de X à Y » mal extraites.** `extract_quantities('entre 5 et 10 kg')` ne renvoie que 10 kg en `measure` : la sémantique d'intervalle est perdue et la borne haute est injectée comme contribution déterministe sommable.
- **Prépositions françaises prises pour des unités.** `_strip_accents` désaccentue *avant* extraction, donc « de 5 **à** 10 » devient « de 5 **a** 10 » où `a` = Ampère (unité mono-lettre du regex `[gtmwvash]`, `extract.py:77`). Sur du français réel, « 3 à 4 », « de 5 à 10 » génèrent des quantités fantômes en Ampères.
- **Roll-up : somme de grandeurs hétérogènes sans cohérence** (`analyzers.py:analyze_allocation`). (1) `primary_quantity` n'en garde qu'une par enfant, par un ordre arbitraire ; (2) on mélange `max` (sous-budgets) et `measure` (valeurs réelles) sans sens physique uniforme ; (3) **aucune conversion d'unité** : un budget en kg ignore un enfant en g ou t (sous-comptage silencieux) ; (4) `min` est exclu, donc un enfant ne déclarant qu'un plancher compte pour zéro.
- **`ALLOCATION_TOLERANCE` quasi morte** (`config.py:29`, fixée à `0.0`). La comparaison `total > budget.value` est stricte sur des float accumulés via `+=` : un budget exactement atteint peut basculer en BLOQUANT pour quelques 1e-12. La constante laisse croire à une marge inexistante. _(Résolu depuis : branchée avec epsilon flottant — `analyzers.py:133-134` `limit_base = budget_base * (1 + ALLOCATION_TOLERANCE)` puis `> limit_base + 1e-9`.)_
- **`concepts_non_couverts` traités comme des IDs** (`analyzers.py:analyze_couverture`) : `_norm_ids` cherche `v['id']`, échoue sur un concept-texte, et le fallback affiche le dict brut (« Concepts non couverts : {'concept': 'tenue au feu'} »).
- **Auto-cycle non rejeté** (`orchestrator.py:build_candidate_tree`, CREATE) : `parent_id == target_id` est accepté ; la structure invalide n'est détectée qu'au prochain audit. _(Résolu depuis : rejeté à la construction — `orchestrator.py:52-54` « Parent introuvable » (l'auto-parent CREATE ne pointe sur aucun nœud existant), et self-link LINK rejeté `:79-80` « ne peut pas être liée à elle-même ».)_

**Direction** : ancrer le recollage des milliers sur un nombre complet suivi d'une unité ; segmenter la phrase par propositions plutôt qu'une fenêtre de N caractères et émettre un Finding INFO « quantité non classée » au lieu de retomber sur `measure` ; détecter explicitement les intervalles ; ne pas désaccentuer avant l'extraction d'unités et traiter à/et comme séparateurs ; convertir vers une unité canonique par dimension et signaler les unités non convertibles ; brancher réellement `ALLOCATION_TOLERANCE` + epsilon ; rejeter l'auto-parent.

---

## C. Confiance IA — verdicts non reproductibles, non sourcés, éval gonflée (CRITIQUE)

Le LLM reste une boîte noire non vérifiable, et le harnais d'éval surestime le rappel — combinaison dangereuse pour un outil de conformité.

- **Non-déterminisme malgré `temperature=0`.** Aucun seed, aucune mise en cache des *réponses* par (skill, payload), aucun re-vote. Le verdict global = pire gravité amplifie : un seul flip INFO→BLOCKING d'un agent fait basculer tout le rapport. Rejouer la même action et obtenir VALIDE puis BLOQUANT détruit la confiance et viole l'exigence de traçabilité EN9100. _(Résolu depuis : cache des réponses `config.py:24-25 LLM_CACHE` + vote self-consistency `LLM_VOTE`/`sample_skill` — `analyzers.py:212-217`.)_
- **La passe fusionnée `audit_exigence` rate la sur-spécification parent/enfant.** Elle n'évalue la redondance que contre les *sœurs* et la couverture que contre les *filles* ; le défaut planté (enfant vs parent) sort du champ. La détection est de plus asymétrique (un doublon A↔B signalé sur A mais pas sur B selon l'aléa, sans réconciliation).
- **Éval `eval_matrix.py` trop indulgente.** Le fallback `loose` compte un défaut comme détecté dès qu'un finding *quelconque* touche un id concerné (même un finding REDACTION sans rapport). Le rappel affiché est artificiellement gonflé : on croit attraper les redondances alors que la passe ne couvre pas le cas et que l'éval ne le teste pas vraiment.
- **Aucune preuve / citation exigée des agents.** Les prompts demandent un verdict + une phrase de synthèse, jamais le verbatim qui le justifie. Les ids retournés (`rupture_avec`, `soeurs_en_conflit`) ne sont **pas validés contre l'arbre** : un id halluciné s'affiche à l'ingénieur, qui chasse une exigence inexistante. _(Preuve résolue depuis : champ `preuve` + `_with_preuve` — `analyzers.py:53-56,220`. **Reste vrai** : les ids ne sont toujours pas validés contre le DAG — `_norm_ids` normalise mais ne vérifie pas l'existence des ids dans l'arbre.)_
- **Défauts silencieux favorables.** Sur parse error ou champ manquant, les analyseurs retombent sur `est_coherent=True` / `est_complet=not gaps` : un échec d'agent devient un faux « tout va bien » — faux négatif dangereux sur l'axe le plus critique.
- **Score de fiabilité non calibré.** `score = 100 − penalty` (9/BLOQUANT, 3/WARNING) : poids arbitraires, non normalisés par la taille du corpus (saturation brutale à 0), double-comptage du même défaut sur plusieurs axes. Un go/no-go sur 67 vs 70/100 n'a aucun sens.
- **Modèle par défaut `mistral-small3.2:latest`** : tag mouvant, non épinglé, jamais tracé dans le rapport ; `set_model` change le modèle à chaud pour tous (cf. thème D).
- **Override trop permissif & synthèse libre.** `force_override` rétrograde *tous* les BLOQUANTS, y compris les invalidités structurelles dures (orphelins, cycles, doublons, dépassement de budget) qui ne devraient jamais être dérogeables par un texte ; la justification vide est acceptée. La synthèse streamée (texte libre) peut paraphraser ou amplifier un constat sans vérification.

**Direction** : cacher les réponses LLM par hash (skill, payload canonique) ; pour les axes bloquants, voter sur 3 appels (abstention → WARNING) ; exiger un champ `preuve` (verbatim cible + ancêtre/sœur) et **valider tout id contre l'arbre** (sinon dropper/rétrograder en « non vérifiable ») ; ne jamais inférer la conformité d'une absence de réponse ; épingler un digest de modèle et le tracer dans le rapport ; durcir `eval_matrix.py` (exiger l'axe attendu, rapporter précision *et* rappel) ; ajouter un axe explicite « sur-spécification vs scope du parent » et réconcilier les findings symétriques ; distinguer BLOQUANTS dérogeables (sémantiques) et non-dérogeables (intégrité structurelle).

---

## D. Concurrence & persistance — corruption silencieuse dès 2 utilisateurs (CRITIQUE)

Streamlit sert plusieurs sessions dans un même process ; le modèle d'état de LynX est intrinsèquement mono-utilisateur.

- **Fichiers globaux partagés** (`src/store.py`) : `working.json` et `history.jsonl` sont des chemins fixes uniques. `save_working` écrase le même fichier pour tous ; deux onglets/utilisateurs s'écrasent mutuellement (last-writer-wins) et leurs deltas s'entremêlent dans l'historique. Aucune notion de tenant, projet, ni utilisateur.
- **Aucune protection de concurrence** : `append_history` ouvre en `a` sans verrou ; `save_corpus` fait un `replace` atomique au niveau OS mais sans verrou applicatif autour du read-modify-write. Deux écritures concurrentes perdent une édition silencieusement.
- **État LLM global** : `_MODEL` et `_CACHE` sont des globals de module lus/écrits sans verrou par les threads des analyseurs (3 dans l'orchestrateur, 8 dans `audit_matrix`). `set_model` fait `_CACHE.clear()` à chaud : un audit en cours peut récupérer `None` ou un client à moitié reconstruit → erreurs intermittentes interprétées comme « LLM indisponible ».
- **Cache de verdict fragile** (`app.py:_sig`) : `hash()` randomisé entre process (PYTHONHASHSEED) → clé non portable, cache jamais survivant à un redémarrage ; pire, le hit applique `_apply` à partir d'un candidate potentiellement obsolète.
- **Historique non maîtrisé** : `get_history` relit et parse l'intégralité de `history.jsonl` à chaque affichage ; le fichier croît sans rotation, sans index, sans identité d'auteur — alors que le qui-a-fait-quoi est une exigence forte du domaine.

**Direction** : scoper modèle/cache/working/history par session ou par workspace ; protéger `_CACHE`/`set_model` par un `threading.Lock` (double-checked locking) ; remplacer le store fichier par un store transactionnel (SQLite WAL ou Postgres) avec versionnage optimiste (révision/ETag) et détection de conflit ; signature stable (sha256 du JSON trié) et séparer « résultat caché » de « application au corpus » ; journal append-only indexé par req_id + horodatage + auteur.

---

## E. UX d'audit & actionnabilité — invendable au-delà de la démo (MAJEUR)

La valeur pour un ingénieur n'est pas le verdict mais sa **justification traçable et actionnable**. Or :

- **La trace multi-agents et le verdict streamé disparaissent au rerun** (`app.py:process_action`/`render_verdict`). L'argument de vente (« voir la trace en direct ») n'existe qu'une fraction de seconde puis est effacé : impossible de relire 30 s plus tard quel agent a flaggé quoi.
- **Verdict non actionnable** : on affiche un mot coloré + une prose LLM libre, jamais les findings structurés (axe, gravité, liste des `impacted_ids`). En revue, un BLOQUANT sans la liste exacte des IDs et l'axe de non-conformité n'est ni actionnable ni défendable.
- **Le graphe `streamlit_agraph` ne passe pas à l'échelle** : tout le corpus rendu en layout hiérarchique fixe, sans recherche par ID/texte, sans filtre (domaine/niveau/KO), sans repli de branches. À 34 nœuds c'est déjà dense ; à des centaines il fige le navigateur. Le clic-graphe est l'unique point d'entrée.
- **Latence opaque pendant l'audit profond** : une barre qui avance d'un cran par exigence (chaque cran = plusieurs secondes Ollama synchrone), sans ETA, sans annulation, app bloquée ; un cold-start modèle donne l'impression d'un figement.
- **Dégradation LLM invisible** : Ollama éteint → bascule silencieuse vers le fallback déterministe ; l'utilisateur en mode « agents IA » croit que les agents ont tourné. La confiance accordée n'est pas la même.
- **Flux d'édition risqué** : pas de diff avant/après, pas d'undo par action (seul « Réinitialiser le corpus » existe), perte de saisie au changement de sélection, suppression sans confirmation.
- **Accessibilité** : la couleur est le seul canal pour verdict/score/état de nœud (impacté orange vs KO rouge foncé indistinguables pour un daltonien — ~8 % des hommes, population sur-représentée), niveaux L0..L5 en dégradé de gris illisible, `unsafe_allow_html` non testé sur petits écrans.
- **Aucun artefact partageable** : pas d'export corpus/rapport (CSV/PDF/ReqIF), pas de vue matrice de traçabilité, pas de journal global, pas d'édition par lot.

**Direction** : persister et re-rendre le rapport complet par agent (expander « Détail de l'analyse ») ; afficher les findings structurés (chips par axe + ids cliquables qui sélectionnent le nœud) ; recherche/filtres pilotant la sélection + vue liste/tableau alternative + rendu par sous-arbre de la sélection ; ETA + annulation + résultats incrémentaux ; badge d'état LLM et marquage explicite du mode dégradé ; diff + undo par action + confirmation de suppression ; canal non-coloré (icône/forme/texte) pour chaque état + contrastes WCAG AA ; exports et vue matrice.

---

## F. Ops & déploiement — non déployable hors poste local (MAJEUR à CRITIQUE)

- **Couplage dur à Ollama localhost** (`src/llm.py`, `config.py`) : un seul backend `ChatOllama` sur `http://localhost:11434`, aucune abstraction de provider, pas de secrets. Un déploiement réel suppose un serveur d'inférence dimensionné ou une API managée.
- **Audit en O(n) appels synchrones** (`audit.py:audit_matrix`, 8 workers) : à des milliers d'exigences, des dizaines de minutes à des heures, dans le thread d'une requête Streamlit, non reprenable, non caché entre runs, exceptions de `fut.result()` avalées (`except Exception: pass`) → échecs partiels invisibles, score artificiellement bon.
- **Pas d'API/service** : toute l'orchestration vit dans les callbacks Streamlit et dépend de `session_state`/`st.rerun()`. Aucune intégration (DOORS/Jira/ReqIF), aucune automatisation CI, aucun test headless possible.
- **Aucune observabilité** : pas de logs structurés, pas de métriques (latence/taux d'erreur/durée d'audit), pas de request-id, pas de `/health`. Les échecs LLM sont avalés silencieusement.
- **Sécurité absente (CRITIQUE)** : aucune authN/authZ — quiconque atteint l'app voit/édite/supprime tout et lit l'historique ; `file_uploader` charge un JSON arbitraire sans limite de taille (DoS mémoire trivial) ; `unsafe_allow_html=True` sur du contenu éditable utilisateur (XSS stocké en contexte multi-utilisateur). Des exigences sont souvent confidentielles (PI, défense).
- **Scaling horizontal impossible** : état en mémoire + globals + FS local ; plusieurs réplicas derrière un load-balancer casseraient la cohérence.

**Direction** : interface `LLMProvider` (Ollama / OpenAI-compatible / API managée) externalisée par config ; déporter l'audit en job asynchrone avec cache par (id, hash texte) et traitement incrémental ; extraire un cœur service FastAPI stateless, Streamlit devenant un client ; logging JSON corrélé + métriques + `/health` + remontée explicite du mode dégradé ; authN/authZ (RBAC par projet), limites/validation d'upload, bannir `unsafe_allow_html` sur tout contenu utilisateur, chiffrement au repos/en transit.

---

## Feuille de route ordonnée (effort vs valeur)

Priorité = valeur/usage réel d'abord, effort ensuite. Les vagues sont à exécuter dans l'ordre : chaque vague conditionne la crédibilité de la suivante.

### Vague 0 — Quick wins « ne pas mentir » (effort faible, valeur élevée)
Rendre l'outil **honnête sur ses limites** avant même de le rendre plus juste.
1. Ne plus défaulter vers le verdict favorable : sur quantité non classée, parse error LLM, champ manquant, unité non convertible → Finding INFO visible « indéterminé / à revoir ». (B, C)
2. Badge d'état LLM + marquage explicite « mode dégradé » quand le fallback déterministe est utilisé. (E)
3. Brancher `ALLOCATION_TOLERANCE` + epsilon flottant ; rejeter `parent_id == target_id`. (B)
4. Cesser de désaccentuer avant l'extraction d'unités ; exclure `a`/`et` isolés. (B)
5. Compter et afficher les exigences en échec d'audit au lieu du `except: pass`. (C/F)
6. Épingler un digest de modèle (plus de `latest`) et le tracer dans le rapport. (C)
7. Signature de cache stable (sha256) à la place de `hash()`. (D)

### Vague 1 — Fiabilité du déterministe (effort moyen, valeur élevée)
Le cœur « fiable » doit l'être réellement.
8. Conversion d'unités canonique par dimension + signalement des non-convertibles. (B)
9. Segmentation par propositions pour la classification max/min/measure ; recollage des milliers ancré sur un nombre complet ; détection explicite des intervalles. (B)
10. Distinguer somme-de-mesures vs somme-de-budgets ; émettre « roll-up partiel : N enfants sans quantité comparable ». (B)
11. Verrou sur `_CACHE`/`set_model` ; cache des réponses LLM par (skill, payload). (C/D)

### Vague 2 — Confiance & auditabilité IA (effort moyen-élevé, valeur élevée)
12. Exiger un champ `preuve` (verbatim) dans chaque prompt + validation de tout id retourné contre l'arbre. (C)
13. Durcir `eval_matrix.py` (exiger l'axe attendu, rapporter précision + rappel) ; ajouter l'axe « sur-spécification vs parent » ; réconcilier les findings symétriques. (C)
14. Vote majoritaire 3 appels sur axes bloquants ; remplacer le score composite par des indicateurs lisibles dédupliqués (nb bloquants / nb warnings / % exigences saines). (C)
15. Distinguer BLOQUANTS dérogeables / non-dérogeables ; justification non vide ; post-validation de la synthèse streamée (ids cités ⊂ impacted_ids). (C)

### Vague 3 — UX actionnable & traçable (effort moyen, valeur élevée)
16. Persister et re-rendre le rapport par agent (trace non éphémère) + findings structurés sous le verdict (chips d'axe + ids cliquables). (E)
17. Recherche/filtres + vue liste/tableau + rendu par sous-arbre de la sélection. (E)
18. ETA + annulation + résultats d'audit incrémentaux. (E)
19. Diff + undo par action + confirmation de suppression ; préservation du brouillon. (E)
20. Canal non-coloré pour chaque état + contrastes WCAG AA. (E)
21. Exports (CSV/PDF/ReqIF) + vue matrice de traçabilité. (E)

### Vague 4 — Refonte du modèle de données EN9100 (effort élevé, valeur structurante)
La plus coûteuse mais celle qui débloque la véritable fidélité métier ; à mener idéalement avant ou avec la Vague 5.
22. `parent_id` → `links: List[TraceLink{target_id, link_type}]` (DAG + détection de cycle) ; « arbre de décomposition » comme projection. (A)
23. Modéliser les essais L5 comme liens VERIFIES, non comme enfants ; liens ALLOCATES_TO/CONTRIBUTES_TO pour les budgets transverses. (A)
24. Attributs EN9100 : IADT, source, rationale, criticité, version ; `test_status` en Enum + état de maturité distinct ; alignement ReqIF (Document/Module, segment). (A)
25. Baseline / configuration (snapshot gelé à une revue) + ChangeRequest horodatées. (A)
26. Détection explicite des orphelins (références pendantes) à la suppression. (A)

### Vague 5 — Industrialisation & multi-utilisateur (effort élevé, valeur déploiement)
27. Store transactionnel par projet/utilisateur avec versionnage optimiste ; journal indexé avec auteur. (D)
28. Cœur service FastAPI stateless ; Streamlit en client ; interface `LLMProvider` configurable ; audit en job asynchrone caché/incrémental. (F)
29. AuthN/authZ (RBAC par projet), limites d'upload, suppression de `unsafe_allow_html` sur contenu utilisateur, chiffrement. (F)
30. Observabilité (logs JSON corrélés, métriques, `/health`). (F)

---

## Conclusion honnête

LynX réussit la démonstration : l'idée (déterministe d'abord, IA vérifiable ensuite, traçabilité au centre) est juste et l'exécution est soignée. Mais en l'état il **affirme une fiabilité qu'il ne tient pas** : le moteur déterministe se trompe en silence, les verdicts IA ne sont ni reproductibles ni sourcés, le modèle ne sait pas représenter une matrice de traçabilité, et l'état se corrompt à plusieurs. La priorité absolue n'est pas d'ajouter des fonctionnalités mais de rendre l'outil **honnête sur son incertitude** (Vague 0), puis **réellement juste** sur son cœur déterministe et sémantique (Vagues 1-2), avant d'investir la refonte du modèle de données et l'industrialisation qui le transformeront de démonstrateur en système.