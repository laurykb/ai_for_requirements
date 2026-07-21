# Comparaison de modèles LLM — choix par la donnée

Quel modèle local (open-source, via Ollama) pour les agents de jugement
(pertinence, couverture, redondance) ? Réponse mesurée avec le harnais d'éval
(`eval/run_eval.py`) : même jeu de 6 cas contrôlés, **précision / rappel / F1**
par axe, cache LLM désactivé (vrais appels à chaque fois).

## Résultats

| Modèle | Précision | Rappel | F1 | Temps (6 cas) |
|---|---|---|---|---|
| **mistral-small3.2** | 1.00 | 1.00 | **1.00** | **21 s** |
| gpt-oss:20b | 1.00 | 1.00 | **1.00** | 73 s |
| qwen3.5 | 1.00 | 0.75 | 0.86 | 345 s |
| magistral (raisonnant) | 1.00 | 0.50 | 0.67 | 50 s |

## Lecture

- **mistral-small3.2 gagne** : F1 maximal (1.00) **et** le plus rapide. L'hypothèse
  « il manque de puissance » n'est pas confirmée.
- **gpt-oss:20b** : même F1 (1.00) mais ~3,5× plus lent. Le raisonnement n'apporte
  pas de gain de qualité ici.
- **qwen3.5** : plus lent (×16) **et** moins bon (rappel 0.75). Mauvais compromis.
- **magistral** (modèle à chaîne de pensée) : le pire (F1 0.67, rate la moitié des
  cas). Les modèles raisonnants délibèrent mais dégradent le JSON strict et la
  détection nette sur une tâche aussi cadrée.

## Pourquoi le modèle rapide suffit

L'architecture **décharge le difficile** sur le déterministe (allocation) et les
embeddings (redondance) ; le LLM ne reçoit que des **jugements bien cadrés**
(avec few-shot). Pour ça, un modèle rapide et capable est idéal ; un modèle
raisonnant ajoute de la latence sans valeur. La rapidité n'est donc pas un signe
de non-raisonnement — l'éval prouve que la qualité reste maximale.

## Recommandation

- **Défaut : mistral-small3.2** (meilleur F1, plus rapide).
- Ne pas utiliser qwen3.5 / magistral pour ce projet (plus lents et/ou moins bons).
- Réserve crédible : **gpt-oss:20b** en 2ᵉ étage (vérification des verdicts BLOQUANT
  via `LLM_VOTE`), puisqu'il égale la qualité — mais non justifié par les données
  actuelles.

## Limite méthodologique

L'éval ne comporte que **6 cas** : mistral-small3.2 et gpt-oss:20b y sont
indistinguables. Pour départager finement et confirmer sur des cas durs réels, il
faut **élargir le jeu d'éval** puis relancer la comparaison.

## Reproduire

```bash
for m in mistral-small3.2:latest gpt-oss:20b qwen3.5:latest magistral:latest; do
  echo "=== $m ==="
  OLLAMA_MODEL="$m" LLM_CACHE=0 python -m eval.run_eval | grep -E "micro|F1 micro"
done
```
