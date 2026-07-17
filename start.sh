#!/usr/bin/env bash
# Point d'entree unique de "AI for SSH" (RAG documentaire + AI for Requirements).
# Demarre MongoDB + Ollama (si besoin) puis l'app : par defaut le front Next.js
# + API FastAPI (serve.py). Pour l'ancienne UI Streamlit : `serve.py --streamlit`.
#
# Usage :  bash start.sh        (ou ./start.sh apres chmod +x)
#
# On invoque le python du venv directement : les scripts du venv ont des shebangs
# herites du projet d'origine, mais "python serve.py" relance les sous-process
# via sys.executable, donc tout reste coherent.
set -e
cd "$(dirname "$0")"
exec .venv/bin/python serve.py
