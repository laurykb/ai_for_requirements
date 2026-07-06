#!/usr/bin/env python3
"""
Lance tout d'un coup : MongoDB + Ollama, puis l'application.

    python serve.py          # interface actuelle (Streamlit)
    python serve.py --web    # nouvelle interface (API FastAPI :8000 + front Next.js :3000)

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
           "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_KEEP_ALIVE": "5m"}
    print("Ollama : démarrage...")
    _spawn([ollama, "serve"], env=env)


def _wait(port: int, name: str, timeout: int = 30) -> None:
    for _ in range(timeout):
        if _port_open(port):
            print(f"  {name} prêt.")
            return
        time.sleep(1)
    print(f"  {name} : pas prêt après {timeout}s (l'app démarre quand même).")


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
    print("\nLancement de l'application...\n")
    if "--web" in sys.argv:
        run_web()
    else:
        subprocess.run([sys.executable, "-m", "streamlit", "run", str(ROOT / "app" / "main.py")])


if __name__ == "__main__":
    main()
