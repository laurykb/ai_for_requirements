#!/usr/bin/env bash
# Applique les valeurs calibrées du Chantier 1a au fichier .env (qui shadow les
# défauts de env_config.py via load_dotenv). À lancer UNE fois :
#     bash scripts/apply_env_calibration.sh
# Idempotent : réexécutable sans effet de bord. Sauvegarde .env en .env.bak.
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE=".env"

if [ ! -f "$ENV_FILE" ]; then
  echo "Pas de .env — rien à faire (les défauts env_config.py 1a s'appliquent déjà)."
  exit 0
fi

cp "$ENV_FILE" "$ENV_FILE.bak"
echo "Sauvegarde : $ENV_FILE.bak"

# Clés calibrées Chantier 1a (clé=valeur).
declare -A KV=(
  [CE_RELEVANCE_THRESHOLD]=0.505
  [WEIGHT_SEMANTIC]=0.5
  [WEIGHT_BM25]=0.5
  [CANDIDATE_POOL_MIN]=40
  [CANDIDATE_POOL_MAX]=120
  [CANDIDATE_POOL_PER_DOC]=3
  [PER_DOC_FLOOR]=1
  [MAX_CHUNKS]=24
)

for key in "${!KV[@]}"; do
  val="${KV[$key]}"
  if grep -qE "^[[:space:]]*${key}=" "$ENV_FILE"; then
    # Remplace la ligne existante (préserve un éventuel commentaire de fin ? non : on écrase).
    sed -i -E "s|^[[:space:]]*${key}=.*|${key}=${val}|" "$ENV_FILE"
    echo "  màj  ${key}=${val}"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
    echo "  add  ${key}=${val}"
  fi
done

echo
echo "Terminé. Redémarre l'app (python serve.py) pour charger les nouvelles valeurs."
echo "Vérif rapide : .venv/bin/python -m scripts.probe_retrieval"
