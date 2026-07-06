"""Routeur FastAPI de LynX (AI for Requirements).

Wrappe le backend `lynx/src` (orchestrateur, audit, correction, trace) pour le
front Next.js — même contrat que l'UI Streamlit `lynx/app.py`. Mono-poste : le
corpus de travail est un état de process (comme la session Streamlit), et les
appels LLM sont sérialisés (le buffer de trace de `src.llm` est global).
"""
from __future__ import annotations

import json
import queue
import sys
import threading
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# lynx/ utilise des imports `from src import ...` : on l'ajoute au path.
_LYNX_DIR = str(Path(__file__).resolve().parent.parent / "lynx")
if _LYNX_DIR not in sys.path:
    sys.path.insert(0, _LYNX_DIR)

from src import audit as lynx_audit          # noqa: E402
from src import correction as lynx_correction  # noqa: E402
from src import corpus_io, feedback, llm, roi, store, trace  # noqa: E402
from src.models import Action                 # noqa: E402
from src.orchestrator import (                # noqa: E402
    run_impact_analysis, stream_synthesis, verdict_label,
)

router = APIRouter(prefix="/api/lynx")

_LLM_LOCK = threading.Lock()   # sérialise analyze/audit (trace globale de src.llm)
_corpus: list[dict] | None = None


def _get_corpus() -> list[dict]:
    global _corpus
    if _corpus is None:
        _corpus = store.load_initial()
    return _corpus


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _ui_findings(report) -> list[dict]:
    """Constats au format UI (même reshape que lynx/app.py)."""
    return [{"scope": f.scope.value, "sev": f.severity.value, "analyzer": f.analyzer,
             "method": (f.details or {}).get("method", ""),
             "sim": (f.details or {}).get("similarity"), "msg": f.message}
            for f in report.findings]


# ─────────────── Corpus ───────────────

@router.get("/corpus")
def get_corpus() -> dict:
    corpus = _get_corpus()
    return {"n": len(corpus), "exigences": corpus,
            "llm": {"available": llm.llm_available(), "model": llm.current_model()}}


@router.post("/corpus/upload")
async def upload_corpus(files: list[UploadFile] = File(...)) -> dict:
    """Importe une ou plusieurs matrices JSON (validation + fusion + dédup)."""
    global _corpus
    payloads = []
    for up in files:
        try:
            payloads.append(json.loads(await up.read()))
        except Exception:
            raise HTTPException(400, f"JSON invalide : {up.filename}")
    merged, errors = [], []
    seen = set()
    for raw in payloads:
        valides, errs = corpus_io.validate_corpus(raw)
        errors.extend(errs)
        for r in valides:
            if r["id"] not in seen:
                seen.add(r["id"])
                merged.append(r)
    if not merged:
        raise HTTPException(400, "Aucune exigence valide dans les fichiers fournis.")
    _corpus = merged
    return {"n": len(merged), "errors": errors[:20]}


@router.post("/corpus/reset")
def reset_corpus() -> dict:
    """Abandonne la matrice de travail et recharge la matrice d'origine."""
    global _corpus
    store.reset_working()
    _corpus = store.load_initial()
    return {"n": len(_corpus)}


# ─────────────── Analyse d'une action (SSE) ───────────────

class ActionBody(BaseModel):
    action_type: str                  # CREATE|UPDATE|DELETE|LINK|UNLINK
    target_id: str
    new_text: str = ""
    parent_id: str | None = None
    niveau: int | None = None
    domaine: str | None = None
    link_target: str | None = None
    link_type: str | None = None
    force_override: bool = False
    override_rationale: str = ""


class AnalyzeBody(BaseModel):
    action: ActionBody
    semantic: bool = True             # False = agents déterministes seuls (instantané)


def _build_action(a: ActionBody) -> Action:
    fields = a.model_dump(exclude_none=True)
    return Action(**fields)


def _candidate(corpus: list[dict], a: ActionBody) -> list[dict]:
    """Matrice candidate après action — même logique que les callbacks Streamlit."""
    cand = [dict(r) for r in corpus]
    t = a.action_type
    if t == "UPDATE":
        for r in cand:
            if r["id"] == a.target_id:
                r["texte"] = a.new_text
    elif t == "DELETE":
        cand = [r for r in cand if r["id"] != a.target_id]
    elif t == "CREATE":
        parent = next((r for r in cand if r["id"] == a.parent_id), {})
        cand.append({
            "id": a.target_id or f"REQ-NEW-{uuid4().hex[:6].upper()}",
            "niveau": a.niveau if a.niveau is not None
            else min(int(parent.get("niveau", 0)) + 1, 5),
            "type": parent.get("type", "Exigence"),
            "domaine": a.domaine or parent.get("domaine") or "Général",
            "texte": a.new_text, "parent_id": a.parent_id, "test_status": "PENDING"})
    elif t == "LINK":
        for r in cand:
            if r["id"] == a.target_id:
                r["links"] = list(r.get("links") or []) + [
                    {"type": a.link_type or "DERIVE", "target": a.link_target}]
    elif t == "UNLINK":
        for r in cand:
            if r["id"] == a.target_id:
                r["links"] = [lk for lk in (r.get("links") or [])
                              if not (lk.get("target") == a.link_target
                                      and (a.link_type is None
                                           or lk.get("type") == a.link_type))]
    return cand


@router.post("/analyze")
def analyze(body: AnalyzeBody) -> StreamingResponse:
    """Analyse d'impact d'UNE action, en boîte de verre SSE : `agent`
    (start/done par agent), `report` (verdict + constats), `token` (synthèse
    streamée), `exchanges` (timeline humanisée), `done` / `error`."""
    corpus = [dict(r) for r in _get_corpus()]
    action = _build_action(body.action)

    q: queue.Queue = queue.Queue()

    def worker():
        try:
            with _LLM_LOCK:
                llm.start_trace()
                report = run_impact_analysis(
                    corpus, action, semantic=body.semantic,
                    on_event=lambda kind, label: q.put(
                        {"type": "agent", "kind": kind, "label": label}))
                findings = _ui_findings(report)
                q.put({"type": "report", "verdict": verdict_label(report),
                       "findings": findings, "impacted": report.impacted_ids,
                       "narrative": report.narrative})
                for piece in stream_synthesis(report, action, use_llm=body.semantic):
                    q.put({"type": "token", "text": piece})
                records = llm.stop_trace()
                q.put({"type": "exchanges",
                       "exchanges": trace.build_timeline(records, findings)})
                # ROI : défauts captés tôt (shift-left), comme le Streamlit.
                try:
                    roi.record_catches("edition", action.action_type.value,
                                       action.target_id, findings)
                except Exception:
                    pass
            q.put({"type": "done"})
        except Exception as e:
            q.put({"type": "error", "message": f"{type(e).__name__}: {str(e)[:200]}"})
        q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        while True:
            item = q.get()
            if item is None:
                return
            yield _sse(item)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store"})


class ApplyBody(BaseModel):
    action: ActionBody
    rationale: str = ""               # renseigné si passage en force (BLOQUANT)


@router.post("/apply")
def apply_action(body: ApplyBody) -> dict:
    """Applique une action à la matrice de travail (après verdict côté front) :
    état process + working.json + journal d'historique."""
    global _corpus
    corpus = _get_corpus()
    a = body.action
    old = next((r.get("texte", "") for r in corpus if r["id"] == a.target_id), "")
    _corpus = _candidate(corpus, a)
    store.save_working(_corpus)
    store.append_history(a.action_type, a.target_id, old, a.new_text,
                         body.rationale or "")
    return {"n": len(_corpus)}


# ─────────────── Audit de la matrice (SSE) ───────────────

class AuditBody(BaseModel):
    deep: bool = True                 # False = règles déterministes seules


@router.post("/audit")
def audit(body: AuditBody) -> StreamingResponse:
    """Audit complet : `progress` (exigences auditées / total), puis `report`
    (score /100, constats par axe, exigences signalées) + `exchanges`."""
    corpus = [dict(r) for r in _get_corpus()]
    q: queue.Queue = queue.Queue()

    def worker():
        try:
            with _LLM_LOCK:
                llm.start_trace()
                rep = lynx_audit.audit_matrix(
                    corpus, deep=body.deep,
                    on_event=lambda done, total: q.put(
                        {"type": "progress", "done": done, "total": total}))
                records = llm.stop_trace()
            q.put({"type": "report", "n": rep.n, "score": rep.score,
                   "counts": rep.counts, "flagged_ids": rep.flagged_ids,
                   "n_non_audite": rep.n_non_audite,
                   "findings": [vars(f) for f in rep.findings],
                   "exchanges": trace.humanize_audit(records)})
            q.put({"type": "done"})
        except Exception as e:
            q.put({"type": "error", "message": f"{type(e).__name__}: {str(e)[:200]}"})
        q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        while True:
            item = q.get()
            if item is None:
                return
            yield _sse(item)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store"})


# ─────────────── Correction ───────────────

class FeedbackBody(BaseModel):
    action_type: str
    target_id: str
    verdict: str
    message: str
    correct: bool


@router.post("/feedback")
def record_feedback(body: FeedbackBody) -> dict:
    """Pouce haut/bas sur un verdict (journal d'apprentissage + stats)."""
    feedback.record(body.action_type, body.target_id, body.verdict,
                    body.message, body.correct)
    return {"ok": True, "stats": feedback.stats()}


class ModelBody(BaseModel):
    model: str


@router.post("/model")
def set_model(body: ModelBody) -> dict:
    """Changement à chaud du modèle des agents LynX."""
    llm.set_model(body.model)
    return {"ok": True, "model": llm.current_model()}


class CorrectBody(BaseModel):
    req_id: str
    problems: list[str] = []


@router.post("/correct")
def correct(body: CorrectBody) -> dict:
    """Suggestion de correction d'UNE exigence (1 appel LLM)."""
    with _LLM_LOCK:
        return lynx_correction.suggest_correction(
            _get_corpus(), body.req_id, body.problems or None)
