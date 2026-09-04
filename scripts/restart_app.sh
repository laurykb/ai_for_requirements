#!/usr/bin/env bash
# Redémarre l'application (API 8000 + front 3000) avec le nouveau code.
# Mongo et Ollama restent en place, serve.py les détecte déjà démarrés.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

pkill -f 'uvicorn api.main:app' 2>/dev/null
pkill -f 'next-server'          2>/dev/null
sleep 2

PY="${PYTHON_BIN:-$ROOT/.venv/bin/python3}"
[[ -x "$PY" ]] || { echo "Python introuvable : $PY" >&2; exit 1; }
cd "$ROOT"
nohup "$PY" serve.py > /tmp/serve_export.log 2>&1 &
APP_PID=$!

ready=0
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/health > /dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" -ne 1 ] || ! kill -0 "$APP_PID" 2>/dev/null; then
  echo "Échec du redémarrage : API indisponible. Voir /tmp/serve_export.log" >&2
  exit 1
fi
if ! "$PY" -c 'import json, urllib.request; p=json.load(urllib.request.urlopen("http://127.0.0.1:8000/openapi.json")); assert "delete" in p["paths"]["/api/lynx/corpus"]'; then
  echo "Échec du redémarrage : contrat OpenAPI incomplet (DELETE /api/lynx/corpus)." >&2
  exit 1
fi
echo "serve.py relancé et contrat API vérifié (log : /tmp/serve_export.log)"
