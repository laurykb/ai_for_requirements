"""Constantes et helpers bas niveau partagés par les vues de l'application."""
from pathlib import Path

import streamlit as st
from pymongo import MongoClient

from env_config import MONGO_URI, MONGO_DB, OLLAMA_HOST

# legacy/app/common.py → racine du dépôt = trois niveaux au-dessus (…/legacy/app/).
_ROOT = Path(__file__).resolve().parent.parent.parent
ALL_DOCS = "Tous les documents"
DOCS_OUT = _ROOT / "docs" / "out"
DOCS_PDF = _ROOT / "docs" / "PDF"


@st.cache_resource
def _chunks_col():
    return MongoClient(MONGO_URI)[MONGO_DB]["chunks"]


def list_sources() -> list[str]:
    try:
        return sorted(s for s in _chunks_col().distinct("source") if s)
    except Exception:
        return []


def _ollama_set_keep_alive(model: str, keep_alive) -> tuple[bool, str]:
    """keep_alive=0 décharge le modèle de la VRAM ; -1 le garde résident."""
    import requests
    try:
        requests.post(f"{OLLAMA_HOST}/api/generate",
                      json={"model": model, "keep_alive": keep_alive}, timeout=30)
        return True, "OK"
    except Exception as e:
        return False, str(e)


@st.cache_data(ttl=20)
def _ollama_models() -> list[str]:
    import subprocess
    try:
        out = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=5).stdout
        names = []
        for line in out.splitlines():
            line = line.strip()
            if not line or line.lower().startswith("name"):
                continue
            names.append(line.split()[0])
        return list(dict.fromkeys(names))
    except Exception:
        return []
