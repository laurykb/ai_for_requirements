#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OFFLINE_INSTALLED="$ROOT_DIR/.offline-installed"
ACTION="${1:-help}"
shift || true

usage() {
  cat <<'EOF'
AI for SSH — déploiement hors ligne

Machine connectée :
  deploy/offline.sh prepare DESTINATION

Machine hors ligne, depuis DESTINATION :
  python3 serve.py

Exploitation : status | logs | stop | diagnose | smoke | reinstall
EOF
}

require_docker() {
  command -v docker >/dev/null || { echo "Docker Engine est requis." >&2; exit 1; }
  docker compose version >/dev/null 2>&1 || { echo "Docker Compose est requis." >&2; exit 1; }
  docker info >/dev/null 2>&1 || { echo "Le daemon Docker est inaccessible." >&2; exit 1; }
}

ensure_image() {
  docker image inspect "$1" >/dev/null 2>&1 || docker pull "$1"
}

wait_service() {
  local service="$1" limit="${2:-180}" elapsed=0 id state
  while (( elapsed < limit )); do
    id="$(docker compose ps -q "$service")"
    state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id" 2>/dev/null || true)"
    case "$state" in
      healthy|running) return 0 ;;
      unhealthy|exited|dead) docker compose logs --tail=100 "$service"; return 1 ;;
    esac
    sleep 2
    elapsed=$((elapsed + 2))
  done
  echo "$service n'est pas prêt après ${limit}s." >&2
  return 1
}

find_ollama_store() {
  local first_model from_path candidate
  if [[ -n "${OLLAMA_MODELS_DIR:-}" ]]; then
    candidate="$OLLAMA_MODELS_DIR"
  else
    first_model="$(ollama list | awk 'NR == 2 {print $1}')"
    [[ -n "$first_model" ]] || { echo "Aucun modèle Ollama à exporter." >&2; return 1; }
    from_path="$(ollama show "$first_model" --modelfile | awk '$1 == "FROM" && $2 ~ /^\// {print $2; exit}')"
    candidate="${from_path%/blobs/*}"
  fi
  [[ -d "$candidate/blobs" && -d "$candidate/manifests" ]] || {
    echo "Magasin Ollama introuvable. Définissez OLLAMA_MODELS_DIR." >&2
    return 1
  }
  printf '%s\n' "$candidate"
}

validate_bundle() {
  local item
  for item in images.tar compose.yaml .env.compose.example deploy/offline.sh \
    docs/INSTALLATION_HORS_LIGNE.md serve.py models/ollama-models.tsv data/mongo.archive SHA256SUMS; do
    [[ -s "$ROOT_DIR/$item" ]] || { echo "Fichier obligatoire absent : $item" >&2; return 1; }
  done
}

verify() {
  require_docker
  validate_bundle
  [[ -s "$ROOT_DIR/models/bge-reranker-v2-m3/model.safetensors" ]] || { echo "Cross-encoder absent du bundle." >&2; return 1; }
  (cd "$ROOT_DIR" && sha256sum -c --quiet SHA256SUMS)
  echo "Paquet complet et sommes SHA-256 valides."
}

prepare() {
  local destination="${1:-}" model image_archive ollama_store mongo_db
  [[ -n "$destination" ]] || { echo "Destination requise." >&2; usage; exit 2; }
  [[ "$(uname -s)/$(uname -m)" == "Linux/x86_64" ]] || { echo "Linux x86_64 requis." >&2; exit 1; }
  [[ ! -e "$destination" ]] || { echo "La destination existe déjà : $destination" >&2; exit 1; }
  require_docker
  command -v ollama >/dev/null || { echo "Ollama est requis sur la machine connectée." >&2; exit 1; }
  [[ -s "$ROOT_DIR/models/bge-reranker-v2-m3/model.safetensors" ]] || { echo "Cross-encoder local absent de la source." >&2; exit 1; }

  echo "[1/5] Validation de l'application"
  "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/scripts/validate_export.py"

  echo "[2/5] Construction des images runtime"
  docker build -t ai-for-ssh-api:latest "$ROOT_DIR"
  docker build -t ai-for-ssh-web:latest "$ROOT_DIR/web"
  ensure_image mongo:7.0
  ensure_image ollama/ollama:latest

  echo "[3/5] Création du dossier transférable"
  mkdir -p "$destination/deploy" "$destination/docs/PDF" "$destination/docs/out" \
    "$destination/data" "$destination/lynx/corpus" "$destination/models"
  cp "$ROOT_DIR/compose.yaml" "$ROOT_DIR/compose.host-ollama.yaml" "$ROOT_DIR/.env.compose.example" "$ROOT_DIR/serve.py" "$destination/"
  cp "$ROOT_DIR/deploy/offline.sh" "$destination/deploy/"
  cp "$ROOT_DIR/docs/INSTALLATION_HORS_LIGNE.md" "$destination/docs/"
  find "$ROOT_DIR/data" -mindepth 1 -maxdepth 1 ! -name mongodb \
    -exec cp -a {} "$destination/data/" \;
  mongo_db="$(awk -F= '$1 == "MONGO_DB" {value=$2} END {print value}' "$ROOT_DIR/.env")"
  [[ -n "$mongo_db" ]] || { echo "MONGO_DB absent du fichier .env source." >&2; return 1; }
  echo "Sauvegarde MongoDB : $mongo_db"
  docker run --rm --network host mongo:7.0 mongodump \
    --host "${MONGO_SOURCE_HOST:-127.0.0.1}" \
    --port "${MONGO_SOURCE_PORT:-27017}" \
    --db "$mongo_db" --archive > "$destination/data/mongo.archive"
  [[ -s "$destination/data/mongo.archive" ]] || { echo "Sauvegarde Mongo vide." >&2; return 1; }

  cp -a "$ROOT_DIR/docs/PDF/." "$destination/docs/PDF/" 2>/dev/null || true
  cp -a "$ROOT_DIR/docs/out/." "$destination/docs/out/" 2>/dev/null || true
  cp -a "$ROOT_DIR/lynx/corpus/." "$destination/lynx/corpus/" 2>/dev/null || true
  mkdir -p "$ROOT_DIR/dist"
  image_archive="$(mktemp "$ROOT_DIR/dist/.offline-images.XXXXXX.tar")"
  trap 'rm -f "$image_archive"' RETURN
  docker save -o "$image_archive" \
    ai-for-ssh-api:latest ai-for-ssh-web:latest mongo:7.0 ollama/ollama:latest
  cp "$image_archive" "$destination/images.tar"

  echo "[4/5] Copie fidèle de tous les modèles Ollama"
  ollama_store="$(find_ollama_store)"
  : > "$destination/models/ollama-models.tsv"
  while IFS= read -r model; do
    [[ -n "$model" ]] && printf '%s\n' "$model" >> "$destination/models/ollama-models.tsv"
  done < <(ollama list | awk 'NR > 1 {print $1}')
  cp -a "$ROOT_DIR/models/bge-reranker-v2-m3" "$destination/models/"
  mkdir -p "$destination/models/ollama-store"
  cp -a "$ollama_store/." "$destination/models/ollama-store/"

  echo "[5/5] Contrôle d'intégrité"
  (cd "$destination" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
  "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/scripts/audit_export.py" "$destination" --report "$destination/OFFLINE_AUDIT.json"
  (cd "$destination" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
  echo "Export prêt : $destination ($(du -sh "$destination" | awk '{print $1}'))"
}

verify_gpu_host() {
  docker info --format '{{json .Runtimes}}' | grep -q '"nvidia"' || {
    echo "Runtime Docker NVIDIA absent : les deux RTX 6000 ne seraient pas utilisées." >&2
    return 1
  }
}

verify_gpu_runtime() {
  if [[ "${OLLAMA_DOCKER_RUNTIME:-runc}" == "nvidia" ]]; then
    docker compose exec -T ollama nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader
  else
    echo "Ollama Docker fonctionne en mode CPU (runtime NVIDIA absent ou désactivé)."
  fi
}

verify_models() {
  local name installed expected count=0
  installed="$(docker compose exec -T ollama ollama list </dev/null | awk 'NR > 1 {print $1}')"
  while IFS= read -r name; do
    [[ -z "$name" ]] || grep -Fxq "$name" <<<"$installed" || {
      echo "Modèle absent après initialisation : $name" >&2; return 1;
    }
    [[ -z "$name" ]] || count=$((count + 1))
  done < "$ROOT_DIR/models/ollama-models.tsv"
  for expected in "${EMBED_MODEL:-}" "${GEN_MODEL:-}" "${LYNX_MODEL:-}"; do
    [[ -z "$expected" ]] || grep -Fxq "$expected" <<<"$installed" || {
      echo "Modèle requis par .env absent : $expected" >&2; return 1;
    }
  done
  echo "Modèles Ollama prêts : $count modèle(s) embarqué(s), aucun téléchargement requis."
}

show_embedded_models() {
  local count model
  count="$(awk "NF {count++} END {print count+0}" "$ROOT_DIR/models/ollama-models.tsv")"
  [[ "$count" -gt 0 ]] || { echo "Aucun modèle Ollama dans le paquet." >&2; return 1; }
  [[ -d "$ROOT_DIR/models/ollama-store/blobs" && -d "$ROOT_DIR/models/ollama-store/manifests" ]] || {
    echo "Magasin Ollama embarqué incomplet." >&2; return 1;
  }
  echo "Installation locale des modèles embarqués : $count modèle(s), aucun accès Internet."
  while IFS= read -r model; do [[ -z "$model" ]] || echo "  - $model"; done < "$ROOT_DIR/models/ollama-models.tsv"
}

bundle_mongo_db() {
  awk -F= '$1 == "MONGO_DB" {value=$2} END {print value}' "$ROOT_DIR/.env"
}

restore_mongo() {
  local mongo_db
  mongo_db="$(bundle_mongo_db)"
  [[ -n "$mongo_db" ]] || { echo "MONGO_DB absent du fichier .env." >&2; return 1; }
  docker compose exec -T mongo mongorestore --archive --drop < "$ROOT_DIR/data/mongo.archive"
}

verify_mongo() {
  local mongo_db collections
  mongo_db="$(bundle_mongo_db)"
  collections="$(docker compose exec -T mongo mongosh --quiet "$mongo_db" \
    --eval 'db.getCollectionNames().length' </dev/null)"
  [[ "$collections" =~ ^[1-9][0-9]*$ ]] || {
    echo "MongoDB restauré sans collection ($mongo_db)." >&2; return 1;
  }
}

reset_installation_state() {
  echo "Initialisation du volume MongoDB 7.0 propre..."
  docker compose down --volumes --remove-orphans
}

published_web_port() {
  local published
  published="$(docker compose port web 3000)"
  printf "%s\n" "${published##*:}"
}

verify_host_http() {
  local port="$1"
  TEST_WEB_PORT="$port" python3 - <<PY
import os
import urllib.request

port = os.environ["TEST_WEB_PORT"]
for path in ("/", "/backend/health", "/backend/api/models", "/backend/api/settings"):

    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            if response.status != 200:
                raise SystemExit(f"{url}: HTTP {response.status}")
    except Exception as exc:
        raise SystemExit(
            "Le service web est sain dans Docker mais ne répond pas depuis "
            f"hôte ({url}) : {exc}"
        ) from exc
print(f"Interface web prête : http://localhost:{port}")
PY
}

verify_model_runtime() {
  docker compose exec -T api python - <<'PY'
from env_config import CE_DEVICE, CROSS_ENCODER_LOCAL_PATH
from retrieval.cross_encoder import _load_cross_encoder_local

model = _load_cross_encoder_local(CROSS_ENCODER_LOCAL_PATH, CE_DEVICE)
print(f"Cross-encoder prêt : {CROSS_ENCODER_LOCAL_PATH} ({CE_DEVICE}).")
del model
PY
  docker compose exec -T api python - <<PY
import os
import requests

host = os.environ["OLLAMA_HOST"].rstrip("/")
checks = (
    ("embedding", "/api/embed", {"model": os.environ["EMBED_MODEL"], "input": "test", "keep_alive": 0}),
    ("génération", "/api/generate", {"model": os.environ["GEN_MODEL"], "prompt": "Réponds OK", "stream": False, "keep_alive": 0, "options": {"num_ctx": 2048, "num_predict": 1}}),
)
for label, path, payload in checks:
    response = requests.post(host + path, json=payload, timeout=600)
    response.raise_for_status()
    print(f"Test Ollama {label} réussi ({payload['model']}).")
PY
}

ensure_local_install_path() {
  case "$ROOT_DIR/" in
    /media/*|/mnt/*)
      echo "Installation refusée depuis un support monté : $ROOT_DIR" >&2
      echo "Copiez le paquet sur le disque local puis relancez :" >&2
      echo '  cp -a "/chemin/du/support/bundle" "$HOME/ai-for-ssh-offline"' >&2
      echo '  cd "$HOME/ai-for-ssh-offline" && python3 serve.py' >&2
      return 1
      ;;
  esac
}

install_bundle() {
  local requested_project="${COMPOSE_PROJECT_NAME:-}" requested_web_port="${WEB_PORT:-}"
  local requested_mode="${OLLAMA_DEPLOYMENT_MODE:-}" requested_runtime="${OLLAMA_DOCKER_RUNTIME:-}"
  ensure_local_install_path
  [[ ! -f "$OFFLINE_INSTALLED" ]] || {
    echo "Installation déjà effectuée. Utilisez start, ou reinstall pour restaurer le dump initial." >&2
    return 1
  }
  verify
  show_embedded_models
  cd "$ROOT_DIR"
  # Bash ouvre le fichier puis l'envoie à Docker. Cela fonctionne aussi lorsque
  # Docker est installé via Snap et ne voit pas directement /media ou /tmp.
  docker load < images.tar
  local image
  for image in ai-for-ssh-api:latest ai-for-ssh-web:latest mongo:7.0 ollama/ollama:latest; do
    docker image inspect "$image" >/dev/null
  done
  [[ -f .env ]] || cp .env.compose.example .env
  chmod 0644 .env
  set -a
  source .env
  set +a
  [[ -z "$requested_project" ]] || export COMPOSE_PROJECT_NAME="$requested_project"
  [[ -z "$requested_web_port" ]] || export WEB_PORT="$requested_web_port"
  [[ -z "$requested_mode" ]] || export OLLAMA_DEPLOYMENT_MODE="$requested_mode"
  [[ -z "$requested_runtime" ]] || export OLLAMA_DOCKER_RUNTIME="$requested_runtime"
  if [[ -z "${OLLAMA_DOCKER_RUNTIME:-}" ]]; then
    if verify_gpu_host; then
      export OLLAMA_DOCKER_RUNTIME=nvidia
    else
      export OLLAMA_DOCKER_RUNTIME=runc
    fi
  fi
  reset_installation_state
  docker compose up -d mongo ollama
  wait_service mongo
  restore_mongo
  verify_mongo
  wait_service ollama
  verify_gpu_runtime
  verify_models
  touch .offline-installed
  echo "Installation terminée. Lancez : deploy/offline.sh start"
}

start_bundle() {
  local port
  require_docker
  cd "$ROOT_DIR"
  [[ -f .offline-installed ]] || { echo "Lancez d'abord deploy/offline.sh install." >&2; exit 1; }
  docker compose up -d --no-build
  wait_service web 240
  port="$(published_web_port)"
  verify_host_http "$port"
}

smoke_bundle() {
  local published port
  require_docker
  cd "$ROOT_DIR"
  [[ -f .offline-installed ]] || { echo "Installation hors ligne non effectuée." >&2; return 1; }
  docker compose up -d --no-build
  wait_service mongo
  wait_service ollama
  verify_gpu_runtime
  wait_service api 240
  wait_service web 240
  verify_models
  verify_model_runtime
  verify_mongo
  docker compose exec -T web node -e \
    "Promise.all(['http://api:8000/health','http://127.0.0.1:3000/backend/health'].map(async u=>{const r=await fetch(u);if(!r.ok)throw new Error(u+' HTTP '+r.status)}))"
  published="$(docker compose port web 3000)"
  port="${published##*:}"
  TEST_WEB_PORT="$port" python3 - <<'PY'
import os
import urllib.request
port = os.environ["TEST_WEB_PORT"]
for path in ("/", "/backend/health"):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=15) as response:
        if response.status != 200:
            raise SystemExit(f"{path}: HTTP {response.status}")
print(f"Recette HTTP réussie sur http://localhost:{port}")
PY
  verify_host_http "$port"
}

reinstall_bundle() {
  cd "$ROOT_DIR"
  rm -f .offline-installed
  install_bundle
  start_bundle
}

case "$ACTION" in
  prepare) prepare "${1:-}" ;;
  verify) verify ;;
  install) install_bundle ;;
  start) start_bundle ;;
  smoke) smoke_bundle ;;
  reinstall) reinstall_bundle ;;
  status) cd "$ROOT_DIR"; docker compose ps ;;
  logs) cd "$ROOT_DIR"; docker compose logs -f "$@" ;;
  stop) cd "$ROOT_DIR"; docker compose down ;;
  diagnose) cd "$ROOT_DIR"; docker compose ps; docker compose exec -T ollama ollama list; docker compose port web 3000 ;;
  help|-h|--help) usage ;;
  *) echo "Action inconnue : $ACTION" >&2; usage >&2; exit 2 ;;
esac
