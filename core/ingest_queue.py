"""File d'ingestion SÉQUENTIELLE multi-documents, sans dépendance UI.

Version backend de la file de `app/ingestion.py` (qui reste la file du
Streamlit) : mêmes jobs, même worker unique, mais importable par l'API
FastAPI. Les deux UIs tournent dans des process séparés — chacune a sa file.

Statut d'un job : "queued" -> "running" -> "success" | "error".
"""
import threading
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
DOCS_OUT = _ROOT / "docs" / "out"
DOCS_PDF = _ROOT / "docs" / "PDF"

# Types de documents acceptés au dépôt (convertis via Docling).
UPLOAD_TYPES = ("pdf", "docx", "pptx", "xlsx", "html", "md")

_LOCK = threading.Lock()
_JOBS: list[dict] = []      # jobs dans l'ordre de lancement (file + historique)
_SEQ = 0                    # identifiant croissant
_WORKER_ALIVE = False       # un worker traite-t-il la file ?


def default_params() -> dict:
    """Options d'ingestion par défaut (valeurs .env)."""
    from env_config import AUTO_KEYWORDS, AUTO_QUESTIONS, CHUNKING_MODE, RAPTOR_SUMMARIES
    return {
        "nkw": AUTO_KEYWORDS, "nq": AUTO_QUESTIONS,
        "mode": CHUNKING_MODE if CHUNKING_MODE in ("technical", "naive") else "technical",
        "raptor": RAPTOR_SUMMARIES, "enh_model": "",
    }


def _process_job(job: dict) -> None:
    """Convertit (si besoin) puis ingère le document d'un job. Met à jour sa
    progression. Tourne dans le thread worker. Ne lève pas : renseigne job['result']."""
    p = job["params"]

    def cb(msg, pct):
        job["pct"] = min(100, int(pct))
        job["step"] = str(msg)

    try:
        if job.get("run") is not None:
            # Exécuteur personnalisé (ex. baseline LynX : chunks pré-construits,
            # pas de conversion ni de découpage markdown). Contrat : le callable
            # reçoit le progress_callback et retourne les stats d'ingestion.
            job["source_name"] = job["name"]
            stats = job["run"](cb)
            job["result"] = stats
            if stats.get("status") == "success":
                from core.ask import clear_retrieval_caches
                clear_retrieval_caches()
            return
        DOCS_OUT.mkdir(parents=True, exist_ok=True)
        path = job["path"]
        if not str(path).lower().endswith(".md"):
            job["step"] = "Conversion document -> Markdown (Docling)..."
            from preprocessing.pdf_to_markdown import convert_and_clean
            md_path = convert_and_clean(str(path), out_dir=str(DOCS_OUT))
        else:
            md_path = str(path)
        # Nom de SOURCE réel (celui enregistré en base) : pour un PDF converti,
        # c'est le nom du markdown produit (ex. rapport-clean.md), PAS le nom
        # du fichier déposé — l'UI en a besoin pour cibler le bon document.
        job["source_name"] = Path(md_path).name
        from core.ingest import ingest_markdown
        stats = ingest_markdown(md_path, num_keywords=p["nkw"], num_questions=p["nq"],
                                enhancement_model=p["enh_model"] or None,
                                chunking_mode=p["mode"], raptor_summaries=p["raptor"],
                                progress_callback=cb)
        job["result"] = stats
        if stats.get("status") == "success":
            from core.ask import clear_retrieval_caches
            clear_retrieval_caches()      # le nouveau document devient interrogeable
    except Exception as e:
        job["result"] = {"status": "error", "message": str(e)}


def _worker() -> None:
    """Boucle worker : prend le prochain job 'queued', l'absorbe, recommence.
    S'arrête quand la file est vide (relancé à la prochaine mise en file)."""
    global _WORKER_ALIVE
    while True:
        job = None
        with _LOCK:
            for j in _JOBS:
                if j["status"] == "queued":
                    job = j
                    j["status"] = "running"
                    j["t0"] = time.time()
                    j["step"] = "Démarrage..."
                    break
            if job is None:
                _WORKER_ALIVE = False   # plus rien à faire -> le worker s'éteint
                return
        _process_job(job)
        job["t_end"] = time.time()
        job["status"] = "success" if (job["result"] or {}).get("status") == "success" else "error"


def _ensure_worker() -> None:
    global _WORKER_ALIVE
    with _LOCK:
        if _WORKER_ALIVE:
            return
        _WORKER_ALIVE = True
    threading.Thread(target=_worker, daemon=True).start()


def enqueue(items: list[dict], params: dict) -> int:
    """Met en file un LOT de documents. `items` = liste de {name, path} déjà
    écrits sur le disque (+ optionnellement `run`, un exécuteur personnalisé
    callable(progress_cb) -> stats) ; `params` = options PARTAGÉES du lot."""
    global _SEQ
    added = 0
    with _LOCK:
        for it in items:
            _SEQ += 1
            _JOBS.append({
                "id": _SEQ, "name": it["name"], "path": it.get("path"),
                "run": it.get("run"),
                "params": dict(params), "status": "queued", "pct": 0,
                "step": "En file d'attente...", "result": None, "t0": 0.0, "t_end": 0.0,
                "source_name": None,
            })
            added += 1
    if added:
        _ensure_worker()
    return added


def snapshot() -> list[dict]:
    """Copie cohérente de la file (évite les races avec le worker)."""
    with _LOCK:
        return [dict(j) for j in _JOBS]


def active() -> bool:
    with _LOCK:
        return any(j["status"] in ("queued", "running") for j in _JOBS)


def clear_finished() -> None:
    """Retire les jobs terminés (succès/échec), garde ceux en file/en cours."""
    with _LOCK:
        _JOBS[:] = [j for j in _JOBS if j["status"] in ("queued", "running")]
