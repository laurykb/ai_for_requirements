#!/usr/bin/env bash
# Lance le front Next.js en dev (http://localhost:3000).
# Charge nvm si présent pour garantir Node >= 20.
set -e
cd "$(dirname "$0")"
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" && nvm use default >/dev/null
[ -d node_modules ] || npm install
exec npm run dev
