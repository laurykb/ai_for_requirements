"""File d'ingestion SÉQUENTIELLE multi-documents, sans dépendance UI.

Version backend de la file de `app/ingestion.py` (qui reste la file du
Streamlit) : mêmes jobs, même worker unique, mais importable par l'API
FastAPI. Les deux UIs tournent dans des process séparés — chacune a sa file.

Statut d'un job : "queued" -> "running" -> "success" | "error".
"""
import threading
import time
import shutil
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
DOCS_OUT = _ROOT / "docs" / "out"
DOCS_PDF = _ROOT / "docs" / "PDF"
DOCS_STAGING = _ROOT / "docs" / ".ingest-staging"

# Types de documents acceptés au dépôt (convertis via Docling).
UPLOAD_TYPES = ("pdf", "docx", "pptx", "xlsx", "html", "md")

_LOCK = threading.Lock()
_JOBS: list[dict] = []      # jobs dans l'ordre de lancement (file + historique)
_SEQ = 0                    # identifiant croissant
_WORKER_ALIVE = False       # un worker traite-t-il la file ?
_RESTORED = False


def _jobs_col():
    from utils.mongo import get_db
    return get_db()["ingest_jobs"]


def _versions_col():
    from utils.mongo import get_db
    return get_db()["document_versions"]


def _serializable_job(job: dict) -> dict:
    return {key: value for key, value in job.items()
            if key not in {"run", "_last_persist"}}


def _persist(job: dict) -> bool:
    try:
        job.pop("persistence_error", None)
        _jobs_col().replace_one({"id": job["id"]}, _serializable_job(job), upsert=True)
        return True
    except Exception as exc:
        job["persistence_error"] = str(exc)[:500]
        return False


def _restore() -> None:
    """Recharge les jobs après redémarrage et remet les jobs interrompus en file."""
    global _RESTORED, _SEQ
    if _RESTORED:
        return
    _RESTORED = True
    try:
        rows = list(_jobs_col().find().sort("id", 1).limit(200))
    except Exception:
        return
    with _LOCK:
        if _JOBS:
            return
        for row in rows:
            row.pop("_id", None)
            row["run"] = None
            if row.get("status") in {"queued", "running"}:
                if row.get("path") and Path(row["path"]).exists():
                    row["status"] = "queued"
                    row["step"] = "Reprise après redémarrage..."
                else:
                    row["status"] = "error"
                    row["result"] = {"status": "error", "message": "Fichier temporaire absent après redémarrage."}
            _JOBS.append(row)
            _SEQ = max(_SEQ, int(row.get("id", 0)))


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
        now = time.time()
        if now - job.get("_last_persist", 0) >= 0.75:
            job["_last_persist"] = now
            _persist(job)

    try:
        if job.get("job_type") == "lynx_baseline":
            # Job sérialisable/reprenable : le snapshot vit sur disque et le
            # type permet de reconstruire l'exécuteur après un redémarrage.
            job["source_name"] = job["name"]
            corpus = json.loads(Path(job["path"]).read_text(encoding="utf-8"))
            from core.lynx_baseline_ingest import ingest_baseline
            stats = ingest_baseline(corpus, cb, source=job["name"],
                                    version=job.get("version_id"))
            if stats.get("status") == "success":
                try:
                    from api.lynx_chat import activate_baseline_version
                    activate_baseline_version(job, stats)
                except Exception as exc:
                    stats = {**stats, "status": "error",
                             "message": f"Activation impossible : {exc}"}
            if stats.get("status") != "success":
                try:
                    from api.lynx_chat import fail_baseline_version
                    fail_baseline_version(job, stats.get("message") or "Échec inconnu")
                except Exception:
                    pass
            job["result"] = stats
            if stats.get("status") == "success":
                from core.ask import clear_retrieval_caches
                clear_retrieval_caches()
            return
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
            version_dir = DOCS_STAGING / str(job.get("version_id") or job["id"]) / "converted"
            version_dir.mkdir(parents=True, exist_ok=True)
            md_path = convert_and_clean(str(path), out_dir=str(version_dir))
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
                                progress_callback=cb, ingest_version=job.get("version_id"),
                                content_hash=job.get("content_hash"))
        job["result"] = stats
        if stats.get("status") == "success":
            # Le fichier visible n'est promu qu'après publication des index.
            DOCS_OUT.mkdir(parents=True, exist_ok=True)
            shutil.copy2(md_path, DOCS_OUT / Path(md_path).name)
            try:
                _versions_col().update_many(
                    {"source_name": job["source_name"], "active": True},
                    {"$set": {"active": False}},
                )
                _versions_col().update_one(
                    {"name": job["name"], "version_id": job.get("version_id")},
                    {"$set": {"name": job["name"], "source_name": job["source_name"],
                              "version_id": job.get("version_id"),
                              "content_hash": job.get("content_hash"), "params": p,
                              "active": True, "activated_at": time.time()}},
                    upsert=True,
                )
            except Exception:
                pass
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
        _persist(job)


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
    _restore()
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
                "content_hash": it.get("content_hash"),
                "version_id": it.get("version_id"),
                "job_type": it.get("job_type", "document"),
                "markdown_path": it.get("markdown_path"),
            })
            if not _persist(_JOBS[-1]) and it.get("job_type") == "lynx_baseline":
                failed = _JOBS.pop()
                raise RuntimeError(
                    "Le job LynX ne peut pas être garanti après redémarrage : "
                    + failed.get("persistence_error", "persistance indisponible")
                )
            added += 1
    if added:
        _ensure_worker()
    return added


def snapshot() -> list[dict]:
    """Copie cohérente de la file (évite les races avec le worker)."""
    _restore()
    if any(j.get("status") == "queued" for j in _JOBS):
        _ensure_worker()
    with _LOCK:
        return [dict(j) for j in _JOBS]


def active() -> bool:
    with _LOCK:
        return any(j["status"] in ("queued", "running") for j in _JOBS)


def clear_finished() -> None:
    """Retire les jobs terminés (succès/échec), garde ceux en file/en cours."""
    with _LOCK:
        removed = [j["id"] for j in _JOBS if j["status"] not in ("queued", "running")]
        _JOBS[:] = [j for j in _JOBS if j["status"] in ("queued", "running")]
    if removed:
        try:
            _jobs_col().delete_many({"id": {"$in": removed}})
        except Exception:
            pass


def version_exists(name: str, version_id: str) -> bool:
    """Vrai si cette combinaison contenu+paramètres est déjà active ou en file."""
    _restore()
    if any(j.get("name") == name and j.get("version_id") == version_id
           and j.get("status") in {"queued", "running", "success"} for j in _JOBS):
        return True
    try:
        return _versions_col().find_one(
            {"name": name, "version_id": version_id, "active": True}, {"_id": 1}
        ) is not None
    except Exception:
        return False
