#!/usr/bin/env python3
"""
Lance tout d'un coup : MongoDB + Ollama, puis l'application.

    python serve.py            # interface Next.js (API FastAPI :8000 + front :3000)

- Démarre les services seulement s'ils ne tournent pas déjà.
- Multi-OS : utilise `mongod` / `ollama` du PATH, ou les chemins MONGO_BIN /
  OLLAMA_BIN du .env (utile pour une installation portable).
- Variables :
    MONGO_BIN      chemin de mongod (défaut : `mongod` du PATH)
    MONGO_DBPATH   dossier de données Mongo (défaut : ./data/mongodb)
    OLLAMA_BIN     chemin d'ollama (défaut : `ollama` du PATH)
"""
import os
import sys
import time
import socket
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    s = socket.socket()
    s.settimeout(0.5)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def _resolve(name: str, env_var: str) -> str | None:
    return os.environ.get(env_var) or shutil.which(name)


def _spawn(cmd: list[str], env: dict | None = None) -> None:
    subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def start_mongo() -> None:
    if _port_open(27017):
        print("MongoDB : déjà lancé.")
        return
    mongod = _resolve("mongod", "MONGO_BIN")
    if not mongod:
        print("MongoDB : `mongod` introuvable (PATH ou MONGO_BIN dans .env) - ignoré.")
        return
    dbpath = Path(os.environ.get("MONGO_DBPATH", ROOT / "data" / "mongodb"))
    dbpath.mkdir(parents=True, exist_ok=True)
    print(f"MongoDB : démarrage ({dbpath})...")
    _spawn([mongod, "--dbpath", str(dbpath), "--port", "27017", "--bind_ip", "127.0.0.1"])


def start_ollama() -> None:
    if _port_open(11434):
        print("Ollama : déjà lancé.")
        return
    ollama = _resolve("ollama", "OLLAMA_BIN")
    if not ollama:
        print("Ollama : `ollama` introuvable (PATH ou OLLAMA_BIN dans .env) - ignoré.")
        return
    # FLASH_ATTENTION=0 : évite des NaN de bge-m3 sur certains GPU. NUM_PARALLEL=1
    # et KEEP_ALIVE=5m : adaptés à une VRAM contrainte (évite de pinner un modèle).
    env = {**os.environ, "OLLAMA_FLASH_ATTENTION": "0",
           "OLLAMA_NUM_PARALLEL": os.environ.get("OLLAMA_NUM_PARALLEL", "4"),
           "OLLAMA_MAX_LOADED_MODELS": os.environ.get("OLLAMA_MAX_LOADED_MODELS", "4"),
           "OLLAMA_SCHED_SPREAD": os.environ.get("OLLAMA_SCHED_SPREAD", "1"),
           "OLLAMA_KEEP_ALIVE": os.environ.get("OLLAMA_KEEP_ALIVE", "15m")}
    print("Ollama : démarrage...")
    _spawn([ollama, "serve"], env=env)


def _wait(port: int, name: str, timeout: int = 30) -> None:
    for _ in range(timeout):
        if _port_open(port):
            print(f"  {name} prêt.")
            return
        time.sleep(1)
    print(f"  {name} : pas prêt après {timeout}s (l'app démarre quand même).")


# ─────────────────────────── Pre-flight (portabilité) ───────────────────────────
# Vérifications RAPIDES (filesystem + HTTP, aucun import lourd) de ce qui bloque
# un nouveau venu. Non bloquant : l'app démarre quand même, comme pour Mongo/
# Ollama absents. L'audit approfondi reste `python diagnostic.py`.

def collect_missing(root, env, spacy_ok, ollama_tags, node_ok):
    """Pure : liste [(prérequis manquant, commande pour corriger)].

    ollama_tags : modèles Ollama installés, ou None si le service est injoignable
    (dans ce cas on ne signale rien — pas de faux positif).
    """
    missing = []
    if not spacy_ok:
        missing.append(("modèle spaCy fr_core_news_sm",
                        "python -m spacy download fr_core_news_sm"))
    ce_dir = Path(env.get("CROSS_ENCODER_LOCAL_PATH")
                  or root / "models" / "bge-reranker-v2-m3")
    try:
        ce_ok = ce_dir.is_dir() and any(ce_dir.iterdir())
    except OSError:
        ce_ok = False
    if not ce_ok:
        missing.append(("cross-encoder de reranking (models/bge-reranker-v2-m3)",
                        "huggingface-cli download BAAI/bge-reranker-v2-m3 "
                        "--local-dir models/bge-reranker-v2-m3"))
    if ollama_tags is not None:
        bases = {t.split(":")[0] for t in ollama_tags}
        for var in ("EMBED_MODEL", "GEN_MODEL"):
            model = env.get(var, "")
            if model and model.split(":")[0] not in bases:
                missing.append((f"modèle Ollama {model} ({var})",
                                f"ollama pull {model}"))
    if not node_ok:
        missing.append(("Node.js >= 20 (front Next.js)",
                        "voir SETUP_PORTABLE.md § Prérequis"))
    return missing


def _spacy_model_ok():
    import importlib.util
    return importlib.util.find_spec("fr_core_news_sm") is not None


def _ollama_tags():
    if not _port_open(11434):
        return None
    try:
        import json
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as r:
            data = json.load(r)
        return [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        return None


def _node_ok():
    # dev.sh charge nvm lui-même : nvm présent suffit.
    return bool(shutil.which("node")) or (Path.home() / ".nvm").is_dir()


def check_setup():
    try:
        missing = collect_missing(ROOT, os.environ, _spacy_model_ok(),
                                  _ollama_tags(), _node_ok())
        if not missing:
            return
        print("Prérequis manquants (l'app démarre quand même) :")
        for label, cmd in missing:
            print(f"  ✗ {label}\n    → {cmd}")
        print("  (réseau restreint : voir SETUP_PORTABLE.md § Réseau restreint)\n")
    except Exception:
        # Garantie non bloquante : aucune erreur ne doit arrêter le démarrage.
        return


def run_web() -> None:
    """Nouvelle interface : API FastAPI (:8000) + front Next.js (:3000).

    L'API tourne en arrière-plan ; le front reste au premier plan pour que
    Ctrl+C arrête tout (l'API est tuée à la sortie).
    """
    api = subprocess.Popen([sys.executable, "-m", "uvicorn", "api.main:app",
                            "--port", "8000"], cwd=ROOT)
    _wait(8000, "API")
    print("\nFront Next.js : http://localhost:3000 (API : http://127.0.0.1:8000)\n")
    try:
        # dev.sh charge nvm (Node >= 20) et fait `npm install` au premier lancement.
        subprocess.run(["bash", str(ROOT / "web" / "dev.sh")])
    finally:
        api.terminate()


def main() -> None:
    start_mongo()
    start_ollama()
    _wait(27017, "MongoDB")
    _wait(11434, "Ollama")
    check_setup()
    print("\nLancement de l'application...\n")
    run_web()


if __name__ == "__main__":
    main()
