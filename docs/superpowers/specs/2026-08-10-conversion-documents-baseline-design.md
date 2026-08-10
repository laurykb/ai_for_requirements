# Conversion de documents d'exigences → baseline JSON LynX

Date : 2026-08-10 · Statut : validé (brainstorming avec Laury)

## Objet

Les ingénieurs arrivent avec des documents d'exigences (Word, PDF, tableurs),
pas avec un JSON. Cette fonctionnalité ajoute une page « Conversion » à LynX
qui transforme un lot de documents en une baseline JSON conforme au schéma
`Requirement` (`lynx/src/models.py`), inspectable puis exportable ou envoyée
directement au corpus LynX.

Cas de test réel : `docs/Référentiel_système` — 52 fichiers (46 `.doc`
Word 97-2003, 2 `.xls`, 1 `.VSD`, 3 `.zip`, 134 Mo) décrivant un système
complet (STB→SSS→PIDS/IRS/ICD + matrice de traçabilité DJEM).

## Principe non négociable : transversalité

Rien dans le pipeline ne connaît un référentiel particulier :

- **Aucun motif d'identifiant codé en dur** — les familles d'ids sont
  découvertes par analyse du document (tokens répétés de forme stable
  `PREFIXE…numéro`, toute ponctuation : `[SSS-STC-E-REQ-0001]`,
  `EXG_SYS_42`, `STB.1.2.3`). La détection propose, l'utilisateur dispose
  (motif corrigeable par document dans la revue).
- **Mapping niveaux configurable en deux temps** — au dépôt, un niveau par
  document (défaut proposé par regroupement des noms de fichiers, ex. tous
  les `PIDS*` ensemble), ajustable avant lancement ; en revue, une fois les
  familles d'ids connues, le niveau reste corrigeable par famille ou par
  document. Aucun préréglage lié à un référentiel donné.
- **Matrices de traçabilité détectées structurellement** — un tableau dont
  deux colonnes portent des ids de deux familles différentes est une
  matrice, quel que soit son nom ou son format (`.xls` ou tableau Word).
- **Détection LLM comme filet générique** pour tout document textuel sans
  identifiants.
- **Élasticité** ([[rag-elasticity-principle]]) : seuils proportionnels au
  document, pas de constantes figées.

Limite assumée V1 : exigences uniquement en images/schémas, Visio, tableurs
très libres → dégradé ou refusé, mais toujours **signalé explicitement**.

## Décisions de cadrage (validées)

1. **Périmètre d'extraction** : hybride — extraction déterministe des
   exigences marquées ; détection LLM pour les documents sans marqueurs,
   ids générés traçables (`<DOC>-AUTO-001`), provenance conservée.
2. **Hiérarchie** : `niveau` par mapping type-de-document→Ln ajustable ;
   `parent_id` UNIQUEMENT depuis les liens explicites (matrices, références
   d'ids dans le texte). Pas d'inférence sémantique en V1 : les orphelines
   restent orphelines — c'est LynX qui détecte les lacunes de couverture.
3. **Intégration** : page dédiée dans la nav LynX
   (`/requirements?tab=conversion`) ; deux sorties — téléchargement du JSON
   et envoi direct au corpus (logique de `/corpus/upload`).
4. **Additions V1** : revue-édition avant export, éval golden de conversion,
   provenance riche (doc + section), passerelle RAG (case « indexer aussi
   dans le RAG documentaire »). Reportés V1.1 : attributs EN9100
   opportunistes, cache/reprise par document, OCR des scannés, `.zip`/`.VSD`.

## Architecture

```
Upload (.doc .docx .pdf .xls .xlsx .md)
   │  POST /api/conversion/jobs  (lot → 1 job)
   ▼
File séquentielle (conversion/queue.py, pattern ingest_queue, 1 worker)
   ├─ Étage 1 · Normalisation   .doc/.xls → .docx/.xlsx (LibreOffice headless)
   │                            puis Docling → Markdown (réutilise
   │                            preprocessing/pdf_to_markdown) ;
   │                            tableur « matrice » → lignes CSV
   ├─ Étage 2 · Extraction      motif d'ids auto-détecté ; découpage
   │                            id / titre / corps ; section conservée
   ├─ Étage 3 · Détection LLM   docs non marqués uniquement, par section,
   │                            verbatim vérifié, budget élastique
   ├─ Étage 4 · Liens           matrices + références inter-docs → parent_id ;
   │                            niveau via mapping
   ▼
Assemblage + corpus_io.validate_corpus (pydantic, dédup ids)
   ▼
data/conversions/<job_id>/  (baseline.json + report.json, persistés)
   ├─ Télécharger : JSON édité en revue (côté client)
   └─ Envoyer vers LynX : JSON édité → POST /api/lynx/corpus/upload
      existant (geste explicite)
```

- Un job = un lot = une baseline candidate, rechargeable après fermeture de
  l'onglet.
- Rien n'entre dans le corpus LynX sans action explicite de l'utilisateur.
- Progression glass box par SSE (étage, document, compteurs, budget LLM).
- Formats non supportés refusés à l'upload avec motif — jamais ignorés en
  silence.

## Composants backend

Module `conversion/` (cœur métier sans FastAPI, unités testables seules) :

- **`normalize.py`** — `to_markdown(path) -> NormalizedDoc`. Route par
  extension ; `soffice --headless --convert-to` pour `.doc`/`.xls` (timeout,
  erreur explicite si LibreOffice absent) ; Docling ensuite. Tableur
  tabulaire → CSV. Remonte les avertissements (« scanné », « partiel »).
- **`extract_marked.py`** — découverte des familles d'ids (répétition d'un
  motif stable, seuil proportionnel au document) ; découpage id → titre
  optionnel → corps jusqu'au prochain id/titre de section ; `source` =
  document + section (ex. `SSS_61563710 §3.1.4.1`).
- **`extract_llm.py`** — documents non marqués : Markdown découpé par
  sections, prompt d'extraction strict (verbatim, pas de reformulation),
  sortie JSON contrainte par grammaire (module `llm` LynX, modèle routable
  `LYNX_MODEL_CONVERSION`). Garde-fous : (1) texte extrait vérifié présent
  dans la section (normalisation d'espaces près) sinon rejet compté ;
  (2) budget d'appels LLM par job, élastique, affiché ; (3) marquage
  `type: "Exigence (détectée)"`, ids `<DOC>-AUTO-nnn` déterministes (ordre
  d'apparition).
- **`link_builder.py`** — liens depuis (1) matrices : colonnes d'ids de
  familles différentes → paires parent→enfant ; (2) références : id d'une
  autre famille cité dans le texte d'une exigence. `parent_id` posé
  seulement si le parent existe dans le lot ; sinon « référence externe non
  résolue » au rapport. Applique le mapping niveaux.
- **`assemble.py`** — fusion, validation, `baseline.json`
  (`{meta, exigences}`, `meta.genere_par: "conversion"` + mapping + stats)
  et `report.json` par document.
- **`queue.py`** — file séquentielle dédiée (indépendante de la file RAG).

Routeur `api/conversion.py` :

| Endpoint | Rôle |
|---|---|
| `POST /api/conversion/jobs` | upload lot + mapping niveaux + option RAG → job en file |
| `GET /api/conversion/jobs/{id}/events` | SSE progression |
| `GET /api/conversion/jobs/{id}` | état + rapport (rechargeable) |
| `GET /api/conversion/jobs/{id}/baseline` | télécharge le JSON brut (serveur) |
| `POST /api/conversion/jobs/{id}/redo` | ré-extrait UN document (motif d'ids corrigé) |

L'envoi vers LynX ne passe pas par un endpoint dédié : la revue applique les
exclusions/corrections côté client et poste le JSON édité sur
`/api/lynx/corpus/upload` existant — les modifications de revue sont ainsi
toujours prises en compte, et le backend reste plus petit.

Passerelle RAG : si cochée, les Markdown produits par l'étage 1 sont soumis
à la file d'ingestion RAG existante (`ingest_queue`) — un upload nourrit les
deux outils.

## Page « Conversion » (front)

`/requirements?tab=conversion`, thème et principes existants (épuré, glass
box, bulles d'aide). Trois états sur une page :

1. **Dépôt** — drag-and-drop multi-fichiers (types affichés, refus motivé
   immédiat) ; tableau du lot avec niveau proposé modifiable (L0…Ln) ;
   case « Indexer aussi dans le RAG documentaire » ; bouton Convertir.
2. **Progression** (SSE) — une ligne par document : étage, compteurs
   (extraites / détectées LLM / avertissements), budget LLM. Même langage
   visuel que la barre d'analyse LynX.
3. **Revue** — bandeau de synthèse (documents, exigences, liens,
   avertissements) ; table filtrable (document, famille, étage d'origine,
   niveau) avec exclusion par case, édition du niveau, correction du motif
   d'ids par document (relance ce seul document) ; puis Télécharger le JSON
   / Envoyer vers LynX (avertissement de remplacement existant).

La revue recharge son état depuis `GET /jobs/{id}`.

## Erreurs et cas limites

- LibreOffice absent / conversion plantée → document en `échec` avec cause ;
  le job continue sur les autres.
- Document scanné (`_looks_scanned`) → « OCR non tenté en V1 », dégradé.
- Motif ambigu (familles entremêlées) → les deux extraites et signalées ;
  désactivable en revue.
- Ids dupliqués inter-documents (deux révisions) → dédup par la validation,
  doublons nommés au rapport.
- Références externes non résolues → listées au rapport (information
  métier, pas une erreur).
- LLM : JSON invalide → retry par grammaire puis échec de section compté ;
  non-verbatim → rejeté ; budget épuisé → sections « non traitées ».

## Tests et éval

- **Unitaires** : `extract_marked` sur fixtures synthétiques multi-motifs ;
  `link_builder` sur matrices CSV et références croisées ; `assemble` sur
  collisions d'ids. LLM mocké (pattern des tests existants).
- **Intégration API** : cycle job complet sur mini-lot (doc marqué + doc non
  marqué + matrice) → baseline + rapport + push.
- **Éval golden** (`evals/run_conversion_eval.py`) sur le corpus réel :
  SSS → exactement 817 ids, échantillon de textes verbatim ; DJEM → liens
  attendus ; IRS OPSIC → rappel mesuré de la détection LLM vs golden.
- **Smoke Playwright** : dépôt fixture → progression → revue →
  téléchargement.

## Hors périmètre V1 (rappel)

OCR, `.zip`/`.VSD`, inférence LLM de rattachements (`links` ouverts pour une
V2 marquée « inféré »), attributs EN9100, cache/reprise par document,
affichage « voir dans le document » côté LynX (la provenance stockée le
permet plus tard).
