# Phase 4 — Migration vLLM + analyse de performance rigoureuse — PLAN DE REPRISE

Date : 2026-07-08 (soir). **Statut : capturé, à exécuter DEMAIN.** Rien n'a été installé
ni téléchargé ce soir. On reprend ici.

Contexte : Phases 0-2 livrées (numpy, cache embeddings SQLite, `audit_matrix(scope)`).
Phase 3 (async/snapshot) écartée (l'async/SSE existe déjà). Phase 4 = remplacer **Ollama
par vLLM** — brique d'infra **partagée LynX + RAG** (le RAG migrera aussi, cf.
[[rag-scaling-requirement]]).

---

## A. Décision modèle — CONTRAINTE MATÉRIELLE (à ne pas oublier)

Matériel : **2× NVIDIA RTX 6000 Ada, 48 Go chacun = 96 Go total**, bande passante
mémoire ≈ **960 Go/s** par carte. Ada Lovelace → **FP8 natif** (pas de NVFP4 natif =
c'est du Blackwell).

| Modèle | Taille | FP8 poids | Tient sur 96 Go Ada ? |
|---|---|---|---|
| **Mistral-Small-3.2-24B** (actuel) | 24B dense | ~24 Go | ✅ largement (1 GPU, l'autre libre) |
| Mistral-Small-4 (`…-119B-2603`) | 119B MoE (6.5B actifs) | ~119 Go | ❌ **NON** (prévu 2×H200 141 Go) |
| Mistral-Large-3 (`…-675B-2512`) | 675B MoE | énorme | ❌ **NON** (prévu 8×H200) |

**Conclusion** : le fit réaliste pour cette machine est **Mistral-Small-3.2-24B en FP8**.
Small-4 (119B) et Large-3 (675B) réclament du H200 — les télécharger sur cette config
serait une perte de temps (119 Go+ pour un modèle qui ne se charge pas, ou offload CPU
inutilisable). **À rediscuter demain** : soit on reste sur Small-3.2-24B FP8, soit on
évalue Small-4 seulement si un hébergement H200 est envisagé (cloud souverain ?), soit
un dense ~70B en 4-bit (~40 Go) comme intermédiaire. Repos FP8 de Small-3.2 :
`stelterlab/…-FP8`, `RedHatAI/…-FP8`, `unsloth/…-FP8`.

## B. Stack vLLM cible (Small-3.2-24B FP8, à valider demain)

- `vllm serve <repo-FP8> --tokenizer-mode mistral --config-format mistral --load-format mistral --tool-call-parser mistral --enable-auto-tool-choice --gpu-memory-utilization 0.9 --max-model-len 32768`
- TP=1 (FP8 tient sur 1 GPU) → 2e GPU libre pour bge-m3 / RAG.
- **Embeddings** : garder un Ollama minimal pour `bge-m3` (1,4 Go) → `EMBED_BASE_URL`
  reste sur Ollama, `LLM_BASE_URL` → vLLM. Unification totale (2e serveur vLLM
  `--task embed`) possible plus tard.
- LynX : `.env` `LLM_BASE_URL=http://localhost:8001/v1`, `LLM_MODEL=<id vLLM>`, monter
  `LLM_MAX_CONCURRENCY` à 32-64. La clé de cache LLM inclut le nom du modèle → les
  verdicts Q4 ne polluent pas les FP8 (invalidation auto, propre).
- ⚠️ **Parité** : FP8 ≠ Q4 → **verdicts différents** (probablement meilleurs). Ce n'est
  plus de la parité stricte : on **valide l'équivalence-qualité** (échantillon Q4 vs FP8).

## C. Analyse de performance — MESURES RÉELLES (exigence : être précis)

Objectif : justifier vLLM vs Ollama **sur cette machine**, chiffres à l'appui, sur le
corpus XXL, **toute la chaîne d'appels LLM**.

### Opérations à couvrir (chacune = un profil d'appels LLM différent)
1. **Audit** de la matrice complète (le gros : N appels `audit_exigence` + débats + coréf).
2. **Modification** d'une exigence (impact : ~6 analyseurs + votes + débats).
3. **Ajout** (CREATE) d'une exigence.
4. **Suppression** (DELETE) — surtout déterministe, peu de LLM.
5. **Correction** en lot (`run_batch_fix` : réécritures + ré-audits scopés).
6. **Génération de filles** (proposition + auto-audit scopé + réécritures).

### Métriques par appel LLM (à instrumenter)
- Nom de l'agent/skill (audit_exigence, coherence_pertinence, defense, juge, synthese…).
- `prompt_tokens`, `completion_tokens` (déjà dans `telemetry.py`).
- **TTFT** (time-to-first-token) — nécessite le streaming OU les métriques vLLM (`/metrics`).
- Latence totale (`latency_ms`, déjà là).
- **TPS** = `completion_tokens / temps_de_décodage`.
- `cached` (hit cache LLM disque/mémoire — à ne PAS compter comme appel modèle).
- Concurrence effective au moment de l'appel.

### Agrégats par opération
- Nombre total d'appels LLM (hors cache) + nombre servis par le cache.
- Parallélisme max réellement atteint (Ollama `NUM_PARALLEL=4` vs vLLM batch).
- Tokens totaux (prompt + completion).
- Wall-clock de l'opération.
- TPS agrégé (somme des completion_tokens / wall-clock).

### Cohérence avec le matériel (le check que « on » te demande)
- Bande passante RTX 6000 Ada ≈ **960 Go/s**. En décodage (memory-bound), TPS mono-flux
  ≈ bande_passante / octets_poids_actifs. Pour 24B FP8 (~1 octet/param, mais seuls les
  poids parcourus comptent) ≈ **~40 tok/s/flux** en ordre de grandeur.
- Le batching (vLLM) fait passer en régime **compute-bound** → TPS **agrégé** bien plus
  haut que 40 × (nb flux naïf). Comparer mesuré vs plafond théorique = mesure du
  « combien de perf je laisse sur la table » et donc du gain vLLM.
- Ollama à `NUM_PARALLEL=4` sature à ~4 flux → plafonne bien en-dessous.

### Protocole comparatif
- Rejouer **les 6 opérations** sur le corpus XXL, **cache LLM vidé** (mesurer le vrai
  coût à froid), sur : (a) **Ollama** actuel (Q4, NUM_PARALLEL=4), (b) **vLLM** (FP8,
  batch). Tabuler wall-clock + TPS agrégé + gain ×.
- Isoler embeddings (bge-m3) du comptage LLM génératif.

## D. Onglet Observabilité LynX (comme le RAG)

But : voir en direct, par appel d'agent, les stats de latence pour **identifier le
goulot**. Réutiliser l'existant :
- `lynx/src/telemetry.py` enregistre déjà `{model, ok, latency_ms, prompt_tokens,
  completion_tokens, total_tokens}` par appel.
- Le front Next.js a **déjà une route `/observability`** (RAG) → mirrorer pour LynX.
- Ajouter : agrégation par **skill/agent** (moyenne/p50/p95 latence, TTFT, TPS, tokens,
  taux de cache), et si vLLM : brancher `GET http://localhost:8001/metrics` (Prometheus :
  `vllm:time_to_first_token_seconds`, `vllm:time_per_output_token_seconds`,
  `vllm:num_requests_running/waiting`, taille de batch) pour la vue système.
- Livrable : un onglet qui, pour une opération donnée, montre la timeline des appels +
  le tableau par agent → on voit *quel agent* (audit_exigence ? débat ? synthèse ?)
  domine, et si c'est la queue (Ollama) ou le décodage (modèle) le vrai goulot.

## E. Ordre d'exécution DEMAIN
1. **Trancher le modèle** (Small-3.2-24B FP8 recommandé vu le matériel ; décider si on
   évalue quand même Small-4 via un plan H200).
2. Instrumenter : enrichir `telemetry.py` (TTFT via streaming ou /metrics, TPS, cache) +
   un petit harnais qui rejoue les 6 opérations et exporte un JSON de mesures.
3. **Baseline Ollama** : lancer le harnais sur Ollama actuel (cache vidé) → mesures de
   référence AVANT de toucher au serving.
4. Installer vLLM (venv), télécharger le FP8, décharger le mistral d'Ollama (garder
   bge-m3), lancer vLLM, brancher `.env`.
5. Rejouer le harnais sur vLLM → comparatif.
6. Construire l'onglet Observabilité LynX (telemetry + /metrics vLLM).
7. Valider l'équivalence-qualité (échantillon d'audit Q4 vs FP8).

## Notes
- Ne PAS committer les binaires (modèles HF vont dans `~/.cache/huggingface`, hors repo).
- vLLM PAS encore installé (`.venv` Python 3.12.3, driver 595.71.05 / CUDA 13.2, 3,2 To
  libres, cache HF déjà 6,8 Go).
- Branche `feat/web-ui-foundations`, non poussée.
