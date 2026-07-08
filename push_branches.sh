#!/usr/bin/env bash
# Pousse les deux branches du jour (te demandera ton identifiant GitHub + PAT).
set -e
cd "$(dirname "$0")"
git push -u origin feat/web-ui-foundations
git push -u origin feat/rag-plan-execute
echo "OK : les deux branches sont sur GitHub."
