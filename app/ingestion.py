"""File d'ingestion SÉQUENTIELLE multi-documents (worker en arrière-plan).

Plusieurs documents peuvent être mis en file ; un UNIQUE worker les absorbe l'un
après l'autre (l'ingestion est lourde : conversion + enrichissement LLM + embeddings
+ RAPTOR). Chaque job porte son propre état (statut + barre de progression), ce qui
permet d'afficher PLUSIEURS barres, dans l'ordre de lancement.
"""
import threading
import time

import streamlit as st

from app.common import DOCS_OUT
from core.chat_sessions import create_session
from env_config import AUTO_KEYWORDS, AUTO_QUESTIONS, CHUNKING_MODE, RAPTOR_SUMMARIES

_INGEST_LOCK = threading.Lock()
_INGEST_JOBS: list[dict] = []     # jobs dans l'ordre de lancement (file + historique)
_INGEST_SEQ = 0                   # identifiant croissant
_INGEST_WORKER_ALIVE = False      # un worker traite-t-il la file ?

# Statut d'un job : "queued" -> "running" -> "success" | "error".

# Types de documents acceptés au dépôt (convertis via Docling).
_UPLOAD_TYPES = ["pdf", "docx", "pptx", "xlsx", "html", "md"]


def _process_job(job: dict) -> None:
    """Convertit (si besoin) puis ingère le document d'un job. Met à jour sa progression.
    Tourne dans le thread worker. Ne lève pas : renseigne job['result']."""
    p = job["params"]

    def cb(msg, pct):
        job["pct"] = min(100, int(pct))
        job["step"] = str(msg)

    try:
        DOCS_OUT.mkdir(parents=True, exist_ok=True)
        path = job["path"]
        if not str(path).lower().endswith(".md"):
            job["step"] = "Conversion document -> Markdown (Docling)..."
            from preprocessing.pdf_to_markdown import convert_and_clean
            md_path = convert_and_clean(str(path), out_dir=str(DOCS_OUT))
        else:
            md_path = str(path)
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


def _ingest_worker() -> None:
    """Boucle worker : prend le prochain job 'queued', l'absorbe, recommence. S'arrête
    quand la file est vide (relancé à la prochaine mise en file)."""
    global _INGEST_WORKER_ALIVE
    while True:
        job = None
        with _INGEST_LOCK:
            for j in _INGEST_JOBS:
                if j["status"] == "queued":
                    job = j
                    j["status"] = "running"
                    j["t0"] = time.time()
                    j["step"] = "Démarrage..."
                    break
            if job is None:
                _INGEST_WORKER_ALIVE = False   # plus rien à faire -> le worker s'éteint
                return
        _process_job(job)
        job["t_end"] = time.time()
        job["status"] = "success" if (job["result"] or {}).get("status") == "success" else "error"


def _ensure_worker() -> None:
    """Démarre le worker s'il n'est pas déjà en train de tourner (un seul à la fois)."""
    global _INGEST_WORKER_ALIVE
    with _INGEST_LOCK:
        if _INGEST_WORKER_ALIVE:
            return
        _INGEST_WORKER_ALIVE = True
    threading.Thread(target=_ingest_worker, daemon=True).start()


def _remember_session_doc(name: str):
    """Associe un document déposé à la session courante (vue « Documents en mémoire »)."""
    sid = st.session_state.get("active_sid")
    if not sid:
        sid = st.session_state.active_sid = create_session(source_filter=None)
    try:
        from core.chat_sessions import add_session_document
        add_session_document(sid, name)
    except Exception:
        pass


def _enqueue_jobs(items: list[dict], params: dict) -> int:
    """Met en file un LOT de documents (ingestion séquentielle). `items` = liste de
    {name, path} déjà écrits sur le disque ; `params` = options PARTAGÉES du lot.
    Retourne le nombre de jobs ajoutés et démarre le worker si besoin."""
    global _INGEST_SEQ
    added = 0
    with _INGEST_LOCK:
        for it in items:
            _INGEST_SEQ += 1
            _INGEST_JOBS.append({
                "id": _INGEST_SEQ, "name": it["name"], "path": it["path"],
                "params": dict(params), "status": "queued", "pct": 0,
                "step": "En file d'attente...", "result": None, "t0": 0.0, "t_end": 0.0,
            })
            added += 1
    # Trace les documents dans la session courante (vue « Documents en mémoire »).
    for it in items:
        _remember_session_doc(it["name"])
    if added:
        _ensure_worker()
    return added


def _ingest_jobs_snapshot() -> list[dict]:
    """Copie cohérente de la file pour le rendu (évite les races avec le worker)."""
    with _INGEST_LOCK:
        return [dict(j) for j in _INGEST_JOBS]


def _ingest_active() -> bool:
    """Vrai si au moins un job est en file ou en cours."""
    with _INGEST_LOCK:
        return any(j["status"] in ("queued", "running") for j in _INGEST_JOBS)


def _clear_finished_jobs() -> None:
    """Retire les jobs terminés (succès/échec), garde ceux en file/en cours."""
    with _INGEST_LOCK:
        _INGEST_JOBS[:] = [j for j in _INGEST_JOBS if j["status"] in ("queued", "running")]


_JOB_STATUS_META = {
    "queued":  (":material/schedule:", "En file"),
    "running": (":material/sync:", "En cours"),
    "success": (":material/check_circle:", "Indexé"),
    "error":   (":material/error:", "Échec"),
}


def _render_ingest_queue(compact: bool = False, key: str = "ingest") -> None:
    """Affiche la file d'ingestion : UNE barre par document, dans l'ordre de lancement.
    `compact=True` (barre latérale) : ne montre que les jobs actifs, en réduit. Sinon
    (onglet Documents) : tout l'historique + bouton de nettoyage. Auto-refresh tant
    qu'un job est actif."""
    jobs = _ingest_jobs_snapshot()
    if not jobs:
        return
    active = any(j["status"] in ("queued", "running") for j in jobs)
    if active:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=1500, key=f"{key}_poll")

    shown = [j for j in jobs if j["status"] in ("queued", "running")] if compact else jobs
    if not shown:
        return

    n_run = sum(1 for j in jobs if j["status"] == "running")
    n_q = sum(1 for j in jobs if j["status"] == "queued")
    if compact:
        st.caption(f":material/sync: **Ingestion** - {n_run} en cours - {n_q} en file")

    for j in shown:
        icon, lbl = _JOB_STATUS_META.get(j["status"], (":material/help:", j["status"]))
        if j["status"] == "running":
            elapsed = int(time.time() - (j["t0"] or time.time()))
            txt = f"{j['name']} - {j['step']}" if not compact else f"{j['name']} ({j['pct']}%)"
            st.progress(min(100, j["pct"]) / 100.0, text=txt)
            if not compact:
                st.caption(f"{icon} {lbl} - {elapsed}s écoulées")
        elif j["status"] == "queued":
            st.progress(0.0, text=f"{j['name']} - en file d'attente")
        elif j["status"] == "success":
            res = j["result"] or {}
            st.progress(1.0, text=f"{j['name']} - indexé")
            if not compact:
                enh = res.get("enhancement", {}) or {}
                st.caption(f"{icon} {res.get('num_chunks', '?')} chunks - "
                           f"enrichis {enh.get('chunks_enhanced', '-')}")
        else:  # error
            res = j["result"] or {}
            st.progress(1.0, text=f"{j['name']} - échec")
            if not compact:
                st.caption(f"{icon} {res.get('message', 'Erreur ingestion.')}")

    if not compact and any(j["status"] in ("success", "error") for j in jobs):
        if st.button("Effacer les terminés", key=f"{key}_clear", icon=":material/clear_all:"):
            _clear_finished_jobs()
            st.rerun()


def _default_ingest_params() -> dict:
    """Options d'ingestion par défaut (valeurs .env), utilisées pour le dépôt rapide dans
    le chat sans rien demander à l'utilisateur."""
    return {
        "nkw": AUTO_KEYWORDS, "nq": AUTO_QUESTIONS,
        "mode": CHUNKING_MODE if CHUNKING_MODE in ("technical", "naive") else "technical",
        "raptor": RAPTOR_SUMMARIES, "enh_model": "",
    }
