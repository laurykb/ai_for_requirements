# Passage à l'échelle de LynX — audit/impact/génération/correction incrémentaux — design

Date : 2026-07-08
Statut : validé (brainstorming), en attente de relecture avant plan d'implémentation
Suite de : `2026-07-07-corpus-xl-ingenierie-design.md` (le corpus XL servait précisément
à faire ressortir ces limites de tenue à l'échelle).

## Contexte

Retour d'usage sur LynX : l'outil **scale mal**. Sur le corpus XL (~500 exigences,
cible réelle 2000–5000), les opérations LLM-orchestrées prennent un temps
déraisonnable. On est plus proche du corpus XL que du cas d'exemple (34–40
exigences). Le problème est visible surtout sur l'audit de la matrice, mais il
touche **aussi** l'analyse d'impact d'une modification/suppression, la génération
de filles et la correction en lot.

### Cause racine (mesurée dans le code)

`audit.audit_matrix(corpus)` est une passe **O(N) en appels LLM** (un
`audit_exigence` par exigence, `audit.py:256`) **+ O(N²) en cosinus Python pur**
(`_embedding_duplicates` + `embeddings.cosine`, `embeddings.py:80`). C'est la
**sous-routine partagée** de trois opérations qui paraissaient distinctes :

| Opération | Ce qu'elle déclenche réellement |
|---|---|
| Bouton Audit | 1× `audit_matrix` sur N |
| Génération de filles (`generation.py:120`) | 1× `audit_matrix` sur N **+ jusqu'à 2× via `run_batch_fix`** |
| Correction en lot (`autofix.py:113`) | jusqu'à `max_passes` × `audit_matrix` sur N |
| Analyse d'impact (`analyzers.py:460`) | scan du corpus entier (impact latent + co-références) |

Points aggravants, **indépendants du cache LLM disque** (qui, lui, fonctionne déjà
bien : clé = `hash(modèle + prompt + entrée)`, donc les inchangés ne rappellent pas
le LLM) :

- La dédup `_embedding_duplicates` refait **O(N²) cosinus en Python interprété** à
  chaque appel : ~125 k paires à 500, ~12,5 M paires à 5000. Des minutes de pur
  calcul Python que le cache LLM ne couvre pas.
- Le cache d'embeddings est **en mémoire seulement** (`embeddings.py:17`) : au
  redémarrage du process, les N embeddings se recalculent.
- Tout est **synchrone et bloquant** : une requête HTTP unique porte toute la passe,
  aucun résultat progressif, risque de timeout et UI figée.
- `impact_latent` et `coreference` **scannent les N exigences** à chaque analyse
  d'impact.
- Chaque BLOQUANT sémantique déclenche un **débat** (avocat + juge = +2 appels LLM),
  non borné à l'échelle.

## Objectif de CETTE étape

Faire tenir l'outil sur toute la **plage réelle d'exploitation** avec, comme critère
d'acceptation **non négociable** : **parité de comportement**. LynX doit rendre
**exactement les mêmes verdicts** quelle que soit la taille — mêmes constats, mêmes
gravités, mêmes scores — seulement plus vite et en incrémental. Le passage à l'échelle
ne doit **introduire aucune régression de qualité**.

**Curseur d'échelle réel** (précisé par Laury) : le cas **courant** est un **petit
arbre de 50-100 exigences** ; les **gros arbres montent à ~2000 exigences (rare)**.
On ne vise pas 5000+. Conséquence directe : le **chemin par défaut est le petit
corpus synchrone** — il doit rester simple et rapide ; la machinerie lourde
(async/job, priorisation, vLLM) est réservée au cas **rare** des gros arbres. Le duo
**numpy (Phase 0) + store incrémental (Phase 2)** suffit probablement à rendre même
un audit de 2000 acceptable à chaud ; async/vLLM ne se justifient que pour la 1re
passe à froid d'un gros arbre neuf.

## Décisions de cadrage (validées)

1. **Cible** : plage réelle 50-100 exigences (courant) → ~2000 (rare, gros arbres) ;
   pas 5000+. Le petit corpus synchrone est le cas par défaut.
2. **Modèle d'exécution de l'audit** : combiné — déterministe *always-on* + sémantique
   *incrémental / async / persisté*.
3. **Serving LLM** : ajout de **vLLM** (OpenAI-compatible, continuous batching, 100 %
   local/souverain, 2×48 GB VRAM).
4. **Persistance** : **MongoDB** (déjà dans la stack) pour le store de verdicts ;
   embeddings persistés + index ANN in-process (numpy, puis faiss si >10k).

## Principe directeur : de « recalculer » à « indexer + delta »

La matrice devient un **index vivant** : chaque exigence porte un verdict caché et un
embedding persistés ; une édition ne salit qu'un petit *dirty set*. « Auditer » =
réconcilier l'index avec le corpus courant. Le geste architectural central :

> **`audit_matrix` devient une primitive scopée et index-backed, partagée par
> l'audit, l'impact, la génération et la correction.**

## Stratégie adaptative (dynamique selon l'échelle) — principe transversal

Le passage à l'échelle doit être **dynamique** : le système choisit sa stratégie
selon la taille (et le coût estimé) du corpus, au lieu d'un mode figé. Petit corpus →
comportement simple d'aujourd'hui ; gros corpus → machinerie incrémentale/async/batch
activée. C'est une **couche de politique** au-dessus des primitives, pas une bascule
manuelle.

- **Petit corpus** (sous un seuil, ex. l'audit complet tient en quelques secondes) :
  recompute synchrone, pas de job async ni de priorisation — la simplicité prime, la
  latence est déjà bonne. On n'impose pas la surcharge de l'incrémental là où elle ne
  rapporte rien.
- **Gros corpus** : incrémental + async + streaming + batching vLLM + priorisation par
  suspicion. La machinerie ne s'active que quand elle paye.
- **Décision** : sur une métrique de coût estimé (nombre d'appels LLM à faire = taille
  du *dirty set*, pas N brut ; débit LLM courant), pas sur un `N` codé en dur. Un seul
  seuil configurable, avec valeur par défaut mesurée sur le poste. Les primitives
  (index ANN, store de verdicts, `audit_matrix(scope=…)`) sont **les mêmes dans les
  deux modes** — seule la politique d'ordonnancement/async change. Ça garantit la
  parité : le petit et le gros chemin produisent les mêmes verdicts.

Conséquence sur le plan : la Phase 0 (numpy) est *scale-agnostic* (bénéfique partout,
aucun downside) ; la couche de politique adaptative est introduite avec la Phase 3
(async/job), une fois les primitives en place.

## Architecture

### Le geste central — `audit_matrix(corpus, scope=None)`

- `scope=None` → audit complet (bouton), mais **incrémental** : seules les exigences
  au *hash de contexte* périmé (absentes/obsolètes dans le store) passent au LLM ;
  le reste est lu depuis le store de verdicts.
- `scope={ids}` → n'audite sémantiquement que ces exigences **+ leurs voisins dont le
  contexte a changé** (parent, sœurs, filles).
- **Génération** appelle `audit_matrix(copie, scope=ids_des_filles)` → ~5 filles +
  voisins, pas N.
- **Correction** ré-audite `scope=ids_corrigés` entre les passes → les corrigées +
  voisins, pas N.

### Couche 1 — Déterministe, always-on, instantané (zéro LLM)

Tourne sur tout le corpus en **<1 s même à 5000** :

- Structure (IDs dupliqués, parents manquants, liens pendants, cycles) + allocation :
  **existe déjà** (`_structural_findings`), déjà O(N) rapide. Conservé tel quel.
- **Dédup embeddings : remplacer le cosinus Python O(N²) par numpy.** Sur vecteurs
  normalisés, la similarité de tout-contre-tout est une matmul `V @ Vᵀ` ; en pratique
  on n'a besoin que du top-k par exigence via l'index ANN (Couche partagée). numpy
  suffit jusqu'à 5000 ; faiss/hnswlib au-delà de 10k.

### Couche 2 — Sémantique, incrémental, async, persisté (LLM)

- **Clé de hash de contexte** (par exigence) :
  `sha256(texte_exigence ‖ texte_parent ‖ textes_sœurs_triés ‖ textes_filles_triés ‖
  version_prompt_audit ‖ nom_modèle)`.
  C'est le sur-ensemble de la clé du cache LLM existant, promu en identifiant
  requêtable. Éditer une exigence change le hash de : elle-même + parent + sœurs +
  filles ≈ **<15 exigences** — le dirty set.
- **Store de verdicts (Mongo)** : `audit_matrix` lit le store pour tout ce qui n'a pas
  bougé et ne relance le LLM que sur le dirty set. Le rapport est persisté : **rouvrir
  l'audit est instantané**.
- **Job async + résultats progressifs** : worker en tâche de fond qui dépile le dirty
  set, appelle vLLM (batché), écrit les verdicts, pousse la progression en **SSE**. Le
  score se remplit en live.
- **1re passe à froid d'un corpus neuf** (le seul gros calcul restant) : (a) vLLM en
  continuous batching pour le débit ; (b) **priorisation** — file triée par suspicion
  (flags déterministes + heuristiques cheap : mots vagues, absence de « doit », non
  quantifié) pour que les vrais problèmes sortent en premier, la longue traîne
  derrière.
- **Débat/vote** : conservés mais **bornés** — sur les BLOQUANT top-K et/ou à la
  demande (clic sur un constat), plus jamais un débat qui ×3 la passe automatiquement.

### Couche 3 — Analyse d'impact (interactive), rendue constante en N

- `impact_latent` et `coreference` interrogent l'**index ANN partagé** (top-k voisins,
  O(log N)) au lieu de scanner les N.
- Embeddings **persistés** (fini le cold-start). → impact analysis ≈ ~6 appels LLM + 2
  lookups d'index, **constante en N**, interactive.

### Transversal — vLLM

`LLM_BASE_URL` pointe vers vLLM (le code parle déjà l'API OpenAI, cf. `llm.py`).
Continuous batching → la concurrence tourne réellement en parallèle, débit ×5–20 sur
la passe à froid. `LLM_MAX_CONCURRENCY` re-dimensionné sur la capacité de batch vLLM.

## Préservation de la parité (le point critique)

Scoper l'audit **sémantique par-exigence** au dirty set, **tout en gardant les checks
cross-matrice via l'index sur tout N**, produit exactement les verdicts de l'audit
complet :

- La dédup et la co-référence d'une exigence (nouvelle ou éditée) sont évaluées contre
  **l'index entier** via une requête ANN — on garde la détection de doublon/conflit
  lointain, sans jamais faire O(N²).
- Une exigence inchangée dont le voisinage est inchangé a, par construction du hash,
  le **même verdict** qu'un recalcul complet — donc le lire depuis le store est
  équivalent à le recalculer.

**Preuve** : test de parité (golden) — sur un même corpus, `audit_matrix(scope=None)`
version legacy (recompute total) vs version index-backed doit rendre des constats
**identiques** (mêmes `req_id/axis/severity/message`, même score). Idem pour un dirty
set : auditer `scope={id}` après édition doit rendre, pour les exigences touchées, les
mêmes constats qu'un recompute total post-édition.

## Schémas MongoDB (esquisse, à figer au plan)

- `lynx_verdicts` : `{ _id: context_hash, req_id, corpus_id, findings: [...],
  model, prompt_version, created_at }`.
- `lynx_embeddings` : `{ _id: sha256(model ‖ texte), req_id, corpus_id, vector: [...],
  model, created_at }`.
- `lynx_audit_reports` : `{ _id: corpus_id, score, counts, flagged_ids, status,
  updated_at }` (snapshot pour réouverture instantanée).

## Plan de migration (incrémental, chaque phase livrable seule)

- **Phase 0 — numpy dedup** (quick win immédiat, **zéro changement de comportement**) :
  remplacer le cosinus Python O(N²) par numpy. Gain net sur audit/génération/correction
  sans toucher à la sémantique. Gate : test de parité + benchmark.
- **Phase 1 — embeddings persistés + index ANN** : store Mongo `lynx_embeddings` ;
  `impact_latent`/`coreference`/dedup interrogent l'index. Gate : parité + impact
  analysis constante en N.
- **Phase 2 — store de verdicts + `audit_matrix(scope=…)`** : Mongo `lynx_verdicts` ;
  génération/correction passent un `scope`. Gate : **test de parité golden** legacy vs
  scopé.
- **Phase 3 — job async + SSE + score progressif** : audit non bloquant, réouverture
  instantanée via `lynx_audit_reports`.
- **Phase 4 — vLLM + débat borné** : bascule serving, re-dimensionnement concurrence,
  bornage débat/vote.

## Tests & mesures

- **Parité** (bloquant à chaque phase) : constats identiques legacy vs nouveau, sur les
  corpus 34, 500, et un 2000 synthétique.
- **Benchmarks** : temps d'audit complet à froid / à chaud, temps d'analyse d'impact,
  temps de génération de filles, à 500 / 2000 / 5000. Objectif : impact & génération en
  quelques secondes ; audit à chaud instantané ; audit à froid borné et progressif.

## Périmètre

**Dans le périmètre**
- Refactor `audit_matrix` en primitive scopée/incrémentale.
- Index ANN + embeddings persistés (Mongo), numpy pour la dédup.
- Store de verdicts persistant (Mongo) + snapshot de rapport.
- Job async + flux SSE de progression ; câblage UI du score progressif.
- Bascule serving vLLM + bornage débat/vote.
- `generation.py` / `autofix.py` / `analyzers.py` consommant les primitives partagées.

**Hors périmètre (étapes futures)**
- Câblage des dimensions d'ingénierie enrichies (modes, grandeurs, architecture) — objet
  de la lignée corpus-XL, indépendant.
- faiss/hnswlib (seulement si on vise >10k ; numpy suffit à 5000).
- Multi-poste / multi-utilisateur (l'app reste mono-poste souveraine).

## Risques & questions ouvertes

- **Invalidation de cache trans-matrice** : bien vérifier que le hash de contexte
  capture toutes les dépendances qui changent un verdict (parent/sœurs/filles). Les
  checks cross-matrice (dédup/coréf) dépendent de *tout* le corpus — ils restent
  index-backed sur N, jamais mis en cache par exigence. À valider par le test de parité.
- **Seuils ANN vs verdict exact** : l'index ANN sélectionne des candidats ; le verdict
  reste tranché par les mêmes seuils/LLM qu'aujourd'hui. Ne pas laisser l'ANN changer
  une décision (il ne fait que présélectionner, comme le routeur actuel).
- **vLLM et sorties structurées** : vérifier le support `response_format: json_schema`
  (repli `json_object` déjà géré dans `llm.py`).
- **Cohérence Mongo** : `corpus_id` pour isoler démo / working / XL et éviter de mélanger
  les verdicts entre corpus.
