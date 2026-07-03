# Roadmap — ce qui reste à faire

État actuel : démonstrateur fonctionnel, mesuré (**précision 0.99 · rappel 0.91 ·
F1 0.95** sur 196 cas), local/open-source. Ce qui sépare ce démonstrateur d'un
produit adopté par des ingénieurs système experts, par ordre de valeur.

## Réalisé — session 2026-07-01

Axe directeur : **transparence (boîte de verre) + remédiation**, avec un rendu
épuré et professionnel (pas d'emoji décoratif ; seuls guident : pastilles de
gravité, badges de rôle, flèches typographiques). Voir mémoire `lynx-ux-principles`.

- **Boîte de verre du raisonnement** (`src/trace.py`, `app._render_glassbox`).
  Capture thread-safe des échanges agent↔LLM (`llm.start_trace`/`stop_trace`),
  rendus en langage naturel (« Reçu / Répondu » par agent), avec un **bandeau
  pipeline** (gravité par agent). Couvre l'**édition** *et* l'**audit** — côté
  audit, on montre les trois contrôles déterministes (Structure, Allocation,
  Vectoriel) **et** les audits IA par exigence (seuls les signalés en détail +
  décompte des conformes, pour ne pas exploser en N blocs). Le routeur embeddings
  de la redondance apparaît aussi (décision « sans LLM » vs « escalade »).
- **Remap — liens typés entre exigences existantes** (DAG). Nouvelles actions
  `LINK`/`UNLINK` (`models`, `tree.with_link/with_unlink`, `orchestrator`) avec
  anti-cycle, anti-doublon et passage par l'analyse d'impact. UI : panneau
  « Remapper » (sens amont/aval, type de lien, bulles d'aide EN9100).
- **Suggestion de correction** (`src/correction.py`, skill `suggest_correction.md`).
  Sur une exigence **signalée** (WARNING/BLOQUANT), réécriture conforme guidée par
  les règles EN9100 + le contexte (parent, ancêtres, sœurs, **filles**, problèmes
  détectés), appliquée en un clic → re-vérifiée par l'analyse d'impact. **À la
  demande uniquement** (jamais pendant l'audit) → latence maîtrisée.
- **Bulles d'aide** (`help=`) : bouton d'audit (ce qu'il produit), types de liens,
  sens du remap ; menu déroulant auto-explicite + rappel dynamique.
- **Tests** : 40 tests déterministes verts (LINK/UNLINK, cycle, humanisation de la
  trace, humanisation de l'audit, suggestion de correction avec LLM simulé).
- **Non-régression** : éval LLM re-passée après ces changements — **précision 0.99 ·
  rappel 0.91 · F1 0.95**, identique à la référence (`eval/last_eval.json`).

## 1. Confiance (en grande partie fait, à consolider)
- [x] Jeu d'éval réaliste labellisé par construction (196 cas, 16 domaines).
- [x] Précision/rappel/F1 par axe, posture **précision d'abord** (F1 0.95).
- [x] Métriques affichées dans l'UI (panneau « Valeur »).
- [ ] **Élargir l'éval** (plus de cas, sous-ensemble labellisé par un humain) pour
      départager les modèles et détecter les biais résiduels.
- [ ] **Confiance par constat** : calibrer un niveau de confiance par finding.
- [ ] Redondance : rappel 0.87 (précision 1.00). Tester `EMBED_DUP_THRESHOLD=0.93`
      pour récupérer du rappel sans perdre la précision.

## 2. Durcissement (5 critiques corrigés, backlog restant)
- [x] 5 bugs critiques (insécables FR, batch embeddings, JSONL, label d'éval).
- [ ] **39 constats en backlog** (`HARDENING.md`), à traiter par sévérité :
      majeurs (couverture delta-aware, vote, intégrité liens audit, atomicité
      d'écriture) puis mineurs.

## 3. Alignement consigne — couverture à la déclinaison
- [ ] La couverture ne se déclenche aujourd'hui qu'à la **suppression** (pour la
      précision). La consigne vise « détecter tôt, quand on décline » : réactiver
      la couverture à l'**ajout (CREATE)** d'une fille, avec garde-fous de précision.
- [ ] Modéliser le **côté droit du V** (vérification) : méthode IADT par exigence,
      matrice de traçabilité test ↔ exigence — là où l'incomplétude remonte.

## 4. Données & échelle
- [ ] **Import réel ReqIF + Excel** (on reste en JSON aujourd'hui) — le pont vers
      DOORS / Polarion / Jama.
- [ ] **Graphe focalisé** (ego-graph autour d'une exigence) + recherche/filtre,
      pour des matrices de milliers d'exigences.
- [ ] **Audit incrémental** (ne ré-analyser que ce qui a changé).
- [ ] **vLLM** en production (continuous batching) — voir `HARDWARE.md`.

## 5. Industrialisation
- [ ] **Multi-utilisateur** réel : base de données + authentification + collaboration
      (aujourd'hui : verrou fichier + workspaces).
- [ ] Conteneurisation, l'API (`src/api.py`) déployée en service.
- [ ] Observabilité production (logs structurés, traçabilité des verdicts pour
      certification EN9100).

## 6. Boucle d'amélioration
- [ ] Exploiter le **feedback** (`corpus/feedback.jsonl`) : `feedback.export_dataset()`
      -> jeu d'entraînement -> **fine-tuning d'un petit modèle** spécialisé
      (meilleur + moins cher que le généraliste à terme).

## Prochain pas recommandé
Soit **(3)** réactiver la couverture à la déclinaison (cœur de la consigne), soit
**(4)** l'import ReqIF/Excel (cœur de l'usabilité). Les deux transforment le
démonstrateur en outil réel.
