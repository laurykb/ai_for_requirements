"""Sauvegarde / restauration de l'espace de travail (souverain, un seul zip).

L'espace de travail = ce qui n'est reconstructible nulle part ailleurs :
  - la baseline d'exigences DE TRAVAIL (l'arbre, lynx/corpus/working.json)
  - son journal d'historique (history.jsonl)
  - les conversations persistées (Mongo, RAG et chat baseline confondus)

Export : GET /api/workspace/export -> zip téléchargé.
Import : POST /api/workspace/import (zip) -> restaure le tout, après avoir
sauvegardé l'état courant en .bak (jamais de perte silencieuse). L'index de
chat de la baseline n'est PAS embarqué : il se reconstruit d'un clic
(Synchroniser) — le zip reste petit et portable.
"""
from __future__ import annotations

import io
import json
import time
import zipfile

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from utils.mongo import get_db

router = APIRouter(prefix="/api/workspace")

_SESSIONS_COLLECTION = "chat_sessions"
_MAX_ZIP_MEMBER = 50 * 1024 * 1024  # 50 Mo par membre : garde-fou zip-bomb


def _lynx_store():
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    if str(root / "lynx") not in sys.path:
        sys.path.insert(0, str(root / "lynx"))
    from src import store  # noqa: E402
    return store


@router.get("/export")
def export_workspace() -> StreamingResponse:
    """Zip de l'espace de travail : baseline + historique + conversations."""
    store = _lynx_store()
    corpus = store.load_initial()

    try:
        sessions = list(get_db()[_SESSIONS_COLLECTION].find({}))
    except Exception:
        sessions = []  # Mongo down : on exporte au moins la baseline

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta.json", json.dumps({
            "app": "AI for SSH", "kind": "workspace",
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "n_exigences": len(corpus), "n_sessions": len(sessions),
        }, ensure_ascii=False, indent=2))
        zf.writestr("lynx/working.json", json.dumps(corpus, ensure_ascii=False, indent=2))
        if store.HISTORY.exists():
            zf.writestr("lynx/history.jsonl", store.HISTORY.read_text(encoding="utf-8"))
        zf.writestr("mongo/chat_sessions.json",
                    json.dumps(sessions, ensure_ascii=False, default=str))
    buf.seek(0)
    stamp = time.strftime("%Y%m%d-%H%M")
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition":
                 f'attachment; filename="ai-for-ssh-workspace-{stamp}.zip"'})


def _read_member(zf: zipfile.ZipFile, name: str) -> str | None:
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > _MAX_ZIP_MEMBER:
        raise HTTPException(400, f"Membre trop volumineux : {name}")
    return zf.read(name).decode("utf-8")


@router.post("/import")
async def import_workspace(file: UploadFile = File(...)) -> dict:
    """Restaure un espace de travail exporté. L'état courant est d'abord
    sauvegardé (working.json.bak, history.jsonl.bak) — jamais de perte
    silencieuse. Les sessions sont restaurées par upsert (id conservé)."""
    raw = await file.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise HTTPException(400, "Fichier invalide : un zip d'espace de travail est attendu.")

    meta_txt = _read_member(zf, "meta.json")
    working_txt = _read_member(zf, "lynx/working.json")
    if not meta_txt or not working_txt:
        raise HTTPException(400, "Zip incomplet : meta.json ou lynx/working.json manquant.")
    try:
        meta = json.loads(meta_txt)
        assert meta.get("kind") == "workspace"
    except Exception:
        raise HTTPException(400, "meta.json invalide : pas un export d'espace de travail.")

    # Validation de la baseline AVANT toute écriture.
    store = _lynx_store()
    from src import corpus_io  # noqa: E402 (sys.path posé par _lynx_store)
    corpus, errors = corpus_io.validate_corpus(corpus_io._unwrap(json.loads(working_txt)))
    if not corpus:
        raise HTTPException(400, "Baseline du zip invalide : aucune exigence valide.")

    # Sauvegarde de l'état courant, puis restauration.
    report: dict = {"n_exigences": len(corpus), "validation_errors": errors[:10]}
    if store.WORKING.exists():
        store.WORKING.replace(store.WORKING.with_suffix(".json.bak"))
        report["backup"] = str(store.WORKING.with_suffix(".json.bak").name)
    store.save_working(corpus)
    import api.lynx_api
    api.lynx_api._corpus = corpus  # l'arbre affiché suit immédiatement

    history_txt = _read_member(zf, "lynx/history.jsonl")
    if history_txt is not None:
        if store.HISTORY.exists():
            store.HISTORY.replace(store.HISTORY.with_suffix(".jsonl.bak"))
        store.HISTORY.write_text(history_txt, encoding="utf-8")
        report["history"] = "restauré"

    sessions_txt = _read_member(zf, "mongo/chat_sessions.json")
    n_sessions = 0
    if sessions_txt:
        try:
            col = get_db()[_SESSIONS_COLLECTION]
            for doc in json.loads(sessions_txt):
                doc.pop("_id", None)  # l'_id Mongo exporté est une chaîne : clé métier = session_id
                if doc.get("session_id"):
                    col.replace_one({"session_id": doc["session_id"]}, doc, upsert=True)
                    n_sessions += 1
        except Exception as e:
            report["sessions_error"] = str(e)[:200]
    report["n_sessions"] = n_sessions
    return report
