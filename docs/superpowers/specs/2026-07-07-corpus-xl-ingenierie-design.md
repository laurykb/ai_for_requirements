# Corpus XL réaliste + hiérarchie dynamique — design

Date : 2026-07-07
Statut : validé (brainstorming), en attente de relecture avant plan d'implémentation

## Contexte

Retour d'un ingénieur système sur LynX : « la solution semble intéressante sur le
papier, mais les exemples ne reflètent qu'un sous-ensemble des exigences manipulées
sur les projets. La déclinaison d'exigences s'appuie sur de la conception ou de
l'analyse système selon la phase projet ; il faut intégrer ces éléments aux données
pour pouvoir vérifier la déclinaison. Les limitations montrent aussi le besoin
d'alimenter les agents avec plus de données — d'où une réflexion à mener sur la
formalisation de l'ensemble des données d'ingénierie manipulées. »

Le corpus de démo actuel : 34 exigences, un drone de surveillance optronique,
niveaux L0–L5, énoncés courts. Les agents ne reçoivent que `{id, niveau, texte}`
(via `Requirement.short()`) : ils raisonnent sur du texte et un arbre, pas sur la
donnée d'ingénierie (architecture, allocation, modes opérationnels, grandeurs).

## Objectif de CETTE étape

Produire un **nouvel input** — un corpus réaliste, volumineux (~500 exigences),
verbeux et profond, qui suit le modèle d'architecture — puis **lancer LynX tel quel
dessus** pour faire ressortir ses limites. On ne câble aucune dimension d'analyse.

Le but est empirique : stresser le système existant sur une donnée réaliste, mesurer
sa tenue (échelle, verbosité, profondeur) et documenter les limites — matière
première de la prochaine itération (câblage des dimensions d'ingénierie).

## Périmètre

**Dans le périmètre**
- Un générateur de corpus (`scripts/gen_corpus_xl.py`) : structure déterministe + prose LLM.
- Un fichier `corpus/corpus_xl.json` (~500 exigences), **sans toucher** `corpus.json`/`working.json` de démo.
- Rendre la hiérarchie de niveaux **dynamique** (détectée depuis le JSON) au lieu de figée à 6.
- Un protocole de test du système actuel sur ce corpus + une note de limites.

**Hors périmètre (étapes futures)**
- Câbler les champs enrichis (modes, grandeurs, architecture, allocation) dans les
  analyzers / `short()` / le débat.
- Faire évoluer le golden set / les évals sur le nouveau modèle.
- Persistance des champs enrichis dans `working.json` (le loader les laisse tomber, cf. ci-dessous).

## Décisions (issues du brainstorming)

| Sujet | Décision |
|---|---|
| Forme de l'arbre | Plus profond ET plus large (~500 exigences, plusieurs branches complètes) |
| Ambition | Corpus seul ; on teste le système existant. Pas de câblage de dimension. |
| Profondeur | Autorisée au-delà de L5 ; le nombre de niveaux devient une donnée du JSON |
| Hiérarchie UI | **Dynamique** : détectée depuis le corpus, pas de niveau codé en dur |
| Production | Générateur : structure déterministe + prose LLM local (repli gabarit) |
| Domaine | Univers du drone de surveillance actuel, étendu à l'échelle programme |

## Le modèle de données enrichi (dans le fichier corpus)

Le fichier `corpus_xl.json` est un objet avec des **catalogues top-level** + les exigences :

```json
{
  "meta": { "systeme": "Drone de surveillance ...", "genere_le": "...", "n": 500 },
  "niveaux": [ { "niveau": 0, "label": "Mission / Besoin" }, ... ],
  "modes_operationnels": [ { "id": "croisiere", "label": "Vol de croisière", "description": "..." }, ... ],
  "architecture": [ { "id": "AE-PROP", "type": "sous-systeme", "label": "Propulsion", "parent": "AE-SYS", "fonction": "Fournir la poussée" }, ... ],
  "exigences": [ { ...voir ci-dessous... }, ... ]
}
```

Chaque exigence porte les champs actuels **plus** les champs enrichis :

```json
{
  "id": "REQ-L3-PROP-004",
  "niveau": 3,
  "type": "Exigence",
  "domaine": "Propulsion",
  "texte": "<énoncé verbeux, plusieurs phrases, conditions + grandeur quantifiée + contexte>",
  "parent_id": "REQ-L2-PROP-001",
  "test_status": "PENDING",
  "rationale": "<justification du besoin>",
  "verification": "A",

  "contexte_operationnel": ["croisiere", "montee"],
  "grandeurs": [ { "grandeur": "masse", "operateur": "<=", "valeur": 2.0, "unite": "kg", "mode": null, "tolerance": 0.1 } ],
  "alloue_a": ["AE-PROP"],
  "base_derivation": { "phase": "analyse_fonctionnelle", "justification": "...", "ref": "AF-012" }
}
```

**Ce que le système consomme aujourd'hui vs ignore — c'est un RÉSULTAT attendu, pas un défaut.**
- Le loader Pydantic (`Requirement(**item)` → `model_dump()`) **laisse tomber silencieusement** les champs inconnus (`contexte_operationnel`, `grandeurs`, `alloue_a`, `base_derivation`).
- `_unwrap` n'extrait que la clé `exigences` : les catalogues `niveaux`/`modes_operationnels`/`architecture` **n'atteignent pas** le moteur via le loader actuel.
- Conséquence : lancer LynX sur `corpus_xl.json` l'exerce sur **texte + arbre + niveau** uniquement. Cela **démontre concrètement l'aveuglement du système à la donnée d'ingénierie** — constat n°1 qui justifiera le câblage futur. Les champs enrichis restent dans le fichier (réalisme + prêts pour l'étape suivante).

## Changement système (minimal) : rendre la hiérarchie dynamique

Motivation (retour utilisateur) : l'UI ne doit pas être figée sur un nombre de
niveaux ; elle doit détecter la hiérarchisation directement depuis le JSON.

### `lynx/src/models.py`
- `niveau: int = Field(..., ge=0, le=5, ...)` → `Field(..., ge=0, ...)` : suppression du
  plafond. Le modèle accepte la profondeur présente dans le JSON.

### Points qui codent « 5 » en dur (audit)
- `lynx/src/config.py::MAX_NIVEAU` : découplé du plafond de validation. Reste un
  paramètre produit (plancher de génération de filles), inchangé fonctionnellement
  pour cette étape ; documenté comme n'étant plus une borne de schéma.
- `web/.../panels.tsx` (`sel.niveau >= 5`, plancher génération) et
  `web/.../req-graph-3d.tsx` (`Math.min(5, …)`) : voir UI ci-dessous.

### UI — `web/src/components/req-graph.tsx` et `req-graph-3d.tsx`
Remplacer les constantes figées `NIVEAU_COLORS` (6 hex) et `LEVEL_LABELS` (6 str) par
une dérivation depuis les données :
- **Niveaux présents** : `nMax = max(niveau)` sur les exigences chargées.
- **Couleurs** : fonction `couleurNiveau(n, nMax)` — échelle de teintes déterministe
  (HSL réparti sur `nMax`), valable pour 6, 8 ou 12 niveaux. Les hex actuels servent
  d'ancrage visuel (mêmes teintes L0–L5 quand `nMax = 5`, continuité de la démo).
- **Libellés** : générique `L{n}` par défaut (dérivé de la donnée) ; si le corpus
  fournit un catalogue `niveaux`, l'UI l'utilise pour les libellés sémantiques.
  Repli sur les libellés actuels (L0 Besoin…) pour `n ≤ 5` afin de préserver la démo.
- **Vue 3D** : espacement des couches calculé sur `nMax` détecté (au lieu du clamp `5`).

### API — `api/lynx_api.py`
- Optionnel (libellés sémantiques) : `/corpus` passe le catalogue `niveaux` du fichier
  au front. Cœur du besoin (niveaux + couleurs dynamiques) satisfait sans ce passthrough.

### Ce qu'on NE touche pas
Analyzers, débat, glass box, prompts, orchestration, golden set : intacts.

## Le générateur — `scripts/gen_corpus_xl.py`

Principe : **structure d'abord, prose ensuite.** Garantit la validité structurelle à
l'échelle 500 (parents valides, allocations cohérentes, grandeurs qui se somment),
le LLM ne faisant que rédiger l'énoncé verbeux.

Étapes :
1. **Arbre d'architecture** : construit top-down (système → sous-systèmes → ensembles
   → équipements → composants → …) dans l'univers drone étendu (véhicule aérien,
   station sol, liaison de données, charge utile optronique, soutien). Catalogue
   `architecture` + catalogue `modes_operationnels` posés.
2. **Dérivation des exigences** niveau par niveau, une ou plusieurs par élément
   d'architecture, avec `id`/`parent_id`/`alloue_a` **garantis cohérents** et
   convention d'ids `REQ-L{n}-{CODE}-{num}`.
3. **Grandeurs typées avec roll-up** : les budgets d'un parent = somme des budgets
   des enfants (allocation qui « boucle ») ; modes tirés du catalogue.
4. **Rédaction verbeuse** : chaque énoncé produit par le **LLM local (mistral)** à
   partir de la fiche structurée de l'exigence, avec **repli gabarit déterministe**
   si le LLM est indisponible (générateur reproductible, resservira pour le golden set).
5. **Écriture** de `corpus/corpus_xl.json`. Le corpus produit est **cohérent** (pas
   de défauts injectés à ce stade — l'injection de défauts étiquetés relève du golden
   set, étape future) : cela permet de mesurer le taux de **faux positifs** du système
   à l'échelle et sur des énoncés longs.

Paramètres (CLI) : nombre cible d'exigences (~500), profondeur, activation LLM
on/off (repli gabarit), graine de reproductibilité.

## Protocole de test (le vrai but)

1. Charger `corpus_xl.json` dans le système (via reset/upload).
2. Lancer l'**audit profond** ; mesurer : temps total, latence par exigence sur ~500
   appels, score de santé, nombre d'exigences signalées.
3. **Inspecter qualitativement** un échantillon des constats : faux positifs induits
   par les énoncés longs ? Le débat contradictoire tient-il ?
4. Exercer l'**analyse d'impact** et la **génération de filles** sur quelques nœuds.
5. Observer la **tenue du graphe 2D/3D** à ~500 nœuds (lisibilité, perf de rendu).
6. Rédiger `docs/superpowers/specs/corpus-xl-findings.md` : limites observées,
   classées (échelle / verbosité / aveuglement à l'enrichissement / UI), priorisées.

## Livrables

- `scripts/gen_corpus_xl.py` — le générateur.
- `corpus/corpus_xl.json` — le corpus (~500 exigences).
- Changements système minimaux : `models.py` (plafond), UI dynamique (2 composants +
  éventuel passthrough API).
- `docs/…/corpus-xl-findings.md` — note de limites.

## Critères de succès

- Le corpus se charge et s'audite **sans erreur** de validation (hors champs enrichis
  volontairement ignorés).
- L'UI **s'adapte** à la profondeur du JSON (6, 8, 10 niveaux) sans changement de code.
- Le corpus de démo actuel (34 exigences, L0–L5) **rend à l'identique** (non-régression
  visuelle : couleurs/libellés inchangés quand `nMax = 5`).
- La note de limites identifie **au moins** : (a) l'aveuglement à la donnée
  d'ingénierie, (b) le comportement à l'échelle 500, (c) la tenue sur énoncés verbeux.

## Risques et mitigations

- **Coût LLM de génération** (~500 appels pour rédiger) : long. Mitigation — repli
  gabarit déterministe ; génération en arrière-plan ; graine pour reproductibilité.
- **Perf de l'audit à 500** (500 appels LLM) : lent. C'est un objet de mesure, pas un
  bug ; prévoir un mode échantillonné pour l'inspection qualitative.
- **Rendu du graphe à 500 nœuds** : possible dégradation. C'est aussi un constat
  attendu (entrée de la note de limites).
- **Incohérence structurelle du générateur** : mitigée par la construction
  structure-d'abord + validation via `corpus_io.validate_corpus` avant écriture.

## Non-objectifs (YAGNI)

- Pas d'injection de défauts étiquetés (golden set = étape future).
- Pas de câblage des dimensions enrichies dans les analyzers.
- Pas de persistance des champs enrichis dans `working.json`.
- Pas de nouveau domaine métier : on étend l'univers drone existant.
