"""Routeur FastAPI de LynX (AI for Requirements).

Wrappe le backend `lynx/src` (orchestrateur, audit, correction, trace) pour le
front Next.js — même contrat que l'UI Streamlit `lynx/app.py`. Mono-poste : le
corpus de travail est un état de process (comme la session Streamlit), et les
appels LLM sont sérialisés (le buffer de trace de `src.llm` est global).
"""
from __future__ import annotations

import hashlib
import os
import json
import time
import queue
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.common import _sse, read_upload_limited
# lynx/ utilise des imports `from src import ...` : on l'ajoute au path.
_LYNX_DIR = str(Path(__file__).resolve().parent.parent / "lynx")
if _LYNX_DIR not in sys.path:
    sys.path.insert(0, _LYNX_DIR)

from src import audit as lynx_audit          # noqa: E402
from src import autofix as lynx_autofix      # noqa: E402
from src import correction as lynx_correction  # noqa: E402
from src import feedback, llm, roi, store, trace  # noqa: E402
from src.models import Action                 # noqa: E402
from src.orchestrator import (                # noqa: E402
    run_impact_analysis, stream_synthesis, verdict_label,
)
from api.lynx_corpus import (                 # noqa: E402
    corpus_diff as _corpus_diff,
    corpus_health as _corpus_health,
    corpus_issues as _corpus_issues,
    corpus_facets as _corpus_facets,
    query_requirements as _query_requirements,
    requirement_relations as _requirement_relations,
    parse_corpus_payloads as _parse_corpus_payloads,
    prepare_activation as _prepare_activation,
    merge_requirements as _merge_requirements,
)

router = APIRouter(prefix="/api/lynx")

_MIB = 1024 * 1024
_MAX_IMPORT_FILES = int(os.environ.get("LYNX_MAX_IMPORT_FILES", "100"))
_MAX_IMPORT_FILE_BYTES = int(os.environ.get("LYNX_MAX_IMPORT_FILE_MB", "100")) * _MIB
_MAX_IMPORT_TOTAL_BYTES = int(os.environ.get("LYNX_MAX_IMPORT_TOTAL_MB", "500")) * _MIB




@dataclass
class LynxApiState:
    corpus: list[dict] | None = None
    drafts: dict[str, dict] = field(default_factory=dict)
    last_activation_diff: dict | None = None
    llm_lock: threading.Lock = field(default_factory=threading.Lock)


_state = LynxApiState()
# Compatibilité des extensions et tests historiques ; la prochaine frontière DI peut fournir LynxApiState.
_corpus: list[dict] | None = None
_drafts: dict[str, dict] = {}
_last_activation_diff: dict | None = None


def _get_corpus() -> list[dict]:
    global _corpus
    if _corpus is None:
        _corpus = store.load_initial()
    return _corpus


def _corpus_revision(corpus: list[dict] | None = None) -> str:
    """Empreinte stable utilisée pour détecter une activation concurrente."""
    payload = corpus if corpus is not None else _get_corpus()
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _ui_findings(report) -> list[dict]:
    """Constats au format UI (même reshape que lynx/app.py)."""
    return [{"scope": f.scope.value, "sev": f.severity.value, "analyzer": f.analyzer,
             "method": (f.details or {}).get("method", ""),
             "sim": (f.details or {}).get("similarity"), "msg": f.message,
             "debate": f.debate}
            for f in report.findings]


# ─────────────── Corpus ───────────────

@router.get("/corpus")
def get_corpus() -> dict:
    corpus = _get_corpus()
    # Catalogue de niveaux porté par le corpus XL s'il existe (libellés sémantiques
    # côté UI) ; sinon [] et le front retombe sur les libellés L0–L5 d'origine.
    import json as _json
    from src.config import DATA_DIR as _DD
    niveaux: list = []
    xl = _DD / "corpus_xl.json"
    if xl.exists():
        try:
            niveaux = _json.loads(xl.read_text(encoding="utf-8")).get("niveaux", [])
        except Exception:
            niveaux = []
    return {"n": len(corpus), "exigences": corpus, "niveaux": niveaux,
            "llm": {"available": llm.llm_available(), "model": llm.current_model()}}


@router.delete("/corpus")
def delete_active_corpus() -> dict:
    """Vide la baseline active après sauvegarde restaurable."""
    global _corpus, _last_activation_diff
    previous = [dict(req) for req in _get_corpus()]
    version_id = store.save_version(previous, "Avant suppression manuelle de la baseline")
    _corpus = []
    _last_activation_diff = _corpus_diff(previous, [])
    store.save_working([])
    try:
        from api.lynx_chat import _purge_baseline_index
        _purge_baseline_index()
        chat_sync = {"purged": True}
    except Exception as exc:
        chat_sync = {"purged": False, "error": str(exc)}
    return {"deleted": True, "n": 0, "previous_version_id": version_id,
            "diff": _last_activation_diff, "chat_sync": chat_sync}


@router.get("/requirements/facets")
def requirement_facets() -> dict:
    return _corpus_facets(_get_corpus())


@router.get("/corpus/status")
def corpus_status() -> dict:
    """État léger de la baseline, sans transférer ses exigences."""
    return {"n": len(_get_corpus()), "revision": _corpus_revision(),
            "llm": {"available": llm.llm_available(), "model": llm.current_model()}}


@router.get("/requirements")
def requirement_catalog(query: str = "", level: int | None = None,
                        source: str | None = None, domain: str | None = None,
                        status: str | None = None,
                        page: int = 1, page_size: int = 100) -> dict:
    impacted = (_last_activation_diff or {}).get("impacted", [])
    return _query_requirements(_get_corpus(), query, level, source, domain, status,
                               impacted, page, page_size)


@router.get("/requirements/{req_id}/relations")
def requirement_relation_view(req_id: str) -> dict:
    result = _requirement_relations(_get_corpus(), req_id)
    if not result:
        raise HTTPException(404, "Exigence inconnue.")
    return result


@router.get("/requirements/{req_id}")
def requirement_detail(req_id: str) -> dict:
    requirement = next((req for req in _get_corpus() if req["id"] == req_id), None)
    if not requirement:
        raise HTTPException(404, "Exigence inconnue.")
    return requirement


def _make_draft(corpus: list[dict], warnings: list[str], source_names: list[str],
                source_batches: list[dict] | None = None, draft_id: str | None = None) -> dict:
    draft_id = draft_id or uuid4().hex
    draft = {"draft_id": draft_id, "created_at": time.time(),
             "source_names": source_names, "source_batches": source_batches or [],
             "exigences": corpus,
             "warnings": warnings[:100], "health": _corpus_health(corpus),
             "diff": _corpus_diff(_get_corpus(), corpus),
             "base_revision": _corpus_revision()}
    _drafts[draft_id] = draft
    store.save_draft(draft)
    return draft


@router.post("/corpus/preview/stream")
async def preview_corpus_stream(files: list[UploadFile] = File(...),
                                draft_id: str | None = Form(None)) -> StreamingResponse:
    """Conversion en tâche de fond avec progression SSE."""
    if not files or len(files) > _MAX_IMPORT_FILES:
        raise HTTPException(413, f"Un import LynX accepte entre 1 et "
                                f"{_MAX_IMPORT_FILES} fichiers.")
    payloads = []
    total_size = 0
    for upload in files:
        data = await read_upload_limited(upload, _MAX_IMPORT_FILE_BYTES)
        total_size += len(data)
        if total_size > _MAX_IMPORT_TOTAL_BYTES:
            raise HTTPException(413, f"L'import LynX dépasse la limite totale de "
                                    f"{_MAX_IMPORT_TOTAL_BYTES // _MIB} Mo.")
        if not data:
            raise HTTPException(400, f"{upload.filename or 'Fichier'} est vide.")
        payloads.append((upload.filename or "matrice", data))

    def run(emit, cancelled):
        try:
            emit({"type": "progress", "stage": "normalisation", "pct": 10,
                  "message": "Lecture et normalisation des feuilles…"})
            corpus, warnings = _parse_corpus_payloads(payloads)
            if cancelled.is_set():
                return
            emit({"type": "progress", "stage": "quality", "pct": 70,
                  "message": f"Contrôle de {len(corpus)} exigences…"})
            names = [name for name, _ in payloads]
            batch = {"batch_id": uuid4().hex, "source_names": names,
                     "exigences": corpus, "warnings": warnings[:100], "created_at": time.time()}
            previous = (_drafts.get(draft_id) or store.load_draft(draft_id)) if draft_id else None
            if previous:
                batches = list(previous.get("source_batches") or [])
                if not batches:
                    batches.append({"batch_id": "legacy",
                                    "source_names": previous.get("source_names") or ["Import existant"],
                                    "exigences": previous.get("exigences") or [],
                                    "warnings": previous.get("warnings") or []})
                batches.append(batch)
                corpus, merge_warnings = _merge_requirements([item["exigences"] for item in batches])
                warnings = [warning for item in batches for warning in item.get("warnings", [])] + merge_warnings
                names = [name for item in batches for name in item.get("source_names", [])]
                draft = _make_draft(corpus, warnings, names, batches, draft_id)
            else:
                draft = _make_draft(corpus, warnings, names, [batch])
            emit({"type": "progress", "stage": "persist", "pct": 95,
                  "message": "Brouillon persisté et diff calculé…"})
            emit({"type": "result", "draft": draft})
        except Exception as exc:
            emit({"type": "error", "message": str(exc)})
        emit({"type": "done"})

    return _sse_stream(run)


@router.get("/corpus/drafts")
def list_corpus_drafts() -> dict:
    return {"drafts": store.list_drafts()}


@router.get("/corpus/drafts/{draft_id}")
def get_corpus_draft(draft_id: str) -> dict:
    draft = _drafts.get(draft_id) or store.load_draft(draft_id)
    if not draft:
        raise HTTPException(404, "Brouillon inconnu.")
    _drafts[draft_id] = draft
    return draft


@router.delete("/corpus/drafts/{draft_id}/sources/{batch_id}")
def delete_draft_source(draft_id: str, batch_id: str) -> dict:
    """Retire un dépôt du brouillon et recalcule la fusion."""
    draft = _drafts.get(draft_id) or store.load_draft(draft_id)
    if not draft:
        raise HTTPException(404, "Brouillon inconnu.")
    original = draft.get("source_batches", [])
    batches = [item for item in original if item.get("batch_id") != batch_id]
    if len(batches) == len(original):
        raise HTTPException(404, "Dépôt inconnu.")
    if not batches:
        _drafts.pop(draft_id, None)
        store.delete_draft(draft_id)
        return {"deleted": True, "draft": None}
    corpus, merge_warnings = _merge_requirements([item.get("exigences", []) for item in batches])
    warnings = [warning for item in batches for warning in item.get("warnings", [])] + merge_warnings
    names = [name for item in batches for name in item.get("source_names", [])]
    updated = _make_draft(corpus, warnings, names, batches, draft_id)
    return {"deleted": True, "draft": updated}


@router.delete("/corpus/drafts/{draft_id}")
def delete_corpus_draft(draft_id: str) -> dict:
    _drafts.pop(draft_id, None)
    store.delete_draft(draft_id)
    return {"deleted": True}


class RootStatusBody(BaseModel):
    declared: bool
    rationale: str = ""


@router.post("/requirements/{req_id}/root-status")
def set_requirement_root_status(req_id: str, body: RootStatusBody) -> dict:
    """Déclare explicitement une racine métier ou réouvre son rattachement."""
    corpus = _get_corpus()
    requirement = next((req for req in corpus if req["id"] == req_id), None)
    if requirement is None:
        raise HTTPException(404, "Exigence inconnue.")
    requirement["root_declared"] = body.declared
    store.save_working(corpus)
    store.append_history("ROOT_STATUS", req_id, str(not body.declared), str(body.declared), body.rationale or "Qualification de la racine")
    return {"requirement": requirement, "health": _corpus_health(corpus)}


class ResolveCollisionBody(BaseModel):
    texte: str
    source: str | None = None
    approver: str = "Ingénieur RPP"
    rationale: str = "Choix de la formulation de référence"


@router.post("/corpus/drafts/{draft_id}/collisions/{req_id}")
def resolve_corpus_collision(draft_id: str, req_id: str,
                             body: ResolveCollisionBody) -> dict:
    """Arbitre une collision en choisissant une formulation déjà sourcée."""
    draft = _drafts.get(draft_id) or store.load_draft(draft_id)
    if not draft:
        raise HTTPException(404, "Brouillon inconnu.")
    requirement = next((req for req in draft["exigences"] if req["id"] == req_id), None)
    if not requirement:
        raise HTTPException(404, "Exigence inconnue dans ce brouillon.")
    variants = requirement.get("collision_variants") or []
    selected = next((variant for variant in variants
                     if variant.get("texte") == body.texte
                     and (body.source is None or variant.get("source") == body.source)), None)
    if selected is None:
        raise HTTPException(400, "La formulation choisie ne fait pas partie des variantes sourcées.")
    requirement["texte"] = selected["texte"]
    requirement["source"] = selected.get("source") or requirement.get("source")
    requirement["collision_variants"] = []
    requirement["arbitration"] = {"decided_at": time.time(), "decided_by": body.approver, "rationale": body.rationale, "selected_source": selected.get("source")}
    draft["health"] = _corpus_health(draft["exigences"])
    draft["diff"] = _corpus_diff(_get_corpus(), draft["exigences"])
    _drafts[draft_id] = draft
    store.save_draft(draft)
    return draft


@router.get("/corpus/health")
def corpus_health() -> dict:
    return _corpus_health(_get_corpus())


@router.get("/corpus/impact")
def corpus_impact() -> dict:
    return _last_activation_diff or {"added": [], "removed": [], "modified": [],
                                     "changed": [], "impacted": []}


@router.get("/corpus/issues")
def corpus_issues() -> dict:
    impacted = (_last_activation_diff or {}).get("impacted", [])
    return _corpus_issues(_get_corpus(), impacted)


class ActivateDraftBody(BaseModel):
    included_ids: list[str] | None = None
    expected_revision: str | None = None
    activation_id: str | None = None
    approver: str = "Utilisateur local"
    rationale: str = "Validation de la revue RPP"


def _sync_chat_after_baseline_change() -> dict:
    """Programme la reconstruction de l’index RAG sans bloquer la baseline."""
    try:
        from api.lynx_chat import sync_baseline
        result = sync_baseline()
        return {"scheduled": True, **result}
    except Exception as exc:
        return {"scheduled": False, "error": str(exc)}


@router.post("/corpus/drafts/{draft_id}/activate")
def activate_draft(draft_id: str, body: ActivateDraftBody) -> dict:
    global _corpus, _last_activation_diff
    """Seul geste qui fait entrer un brouillon dans la baseline LynX."""
    activation_id = body.activation_id or uuid4().hex
    if body.activation_id:
        receipt = store.load_activation_receipt(body.activation_id)
        if receipt is not None:
            return {**receipt, "idempotent_replay": True}
    draft = _drafts.get(draft_id) or store.load_draft(draft_id)
    if not draft:
        raise HTTPException(404, "Brouillon expiré ou inconnu.")
    try:
        selected = _prepare_activation(draft["exigences"], body.included_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    current_revision = _corpus_revision()
    expected_revision = body.expected_revision or draft.get("base_revision")
    if expected_revision and expected_revision != current_revision:
        raise HTTPException(409, {"code": "BASELINE_CONFLICT", "message": "La baseline active a changé depuis la création du brouillon.", "expected_revision": expected_revision, "current_revision": current_revision, "action": "Recharger et comparer à nouveau avant activation."})
    previous = _get_corpus()
    version_id = store.save_version(previous, f"Avant activation du brouillon {draft_id} · {body.approver} · {body.rationale}")
    store.save_working(selected)
    _corpus = selected
    _last_activation_diff = _corpus_diff(previous, selected)
    store.delete_draft(draft_id)
    _drafts.pop(draft_id, None)
    result = {"n": len(selected), "activation_id": activation_id, "revision": _corpus_revision(selected), "approved_by": body.approver, "rationale": body.rationale, "previous_version_id": version_id,
              "diff": _last_activation_diff, "health": _corpus_health(selected),
              "chat_sync": _sync_chat_after_baseline_change()}
    store.save_active_manifest(selected, activation_id, version_id)
    store.save_activation_receipt(activation_id, result)
    return result


@router.get("/corpus/versions")
def list_corpus_versions() -> dict:
    return {"versions": store.list_versions()}


@router.post("/corpus/versions/{version_id}/restore")
def restore_corpus_version(version_id: str) -> dict:
    global _corpus, _last_activation_diff
    restored = store.load_version(version_id)
    if not restored:
        raise HTTPException(404, "Version inconnue ou vide.")
    current = _get_corpus()
    backup_id = store.save_version(current, f"Avant restauration de {version_id}")
    _last_activation_diff = _corpus_diff(current, restored)
    _corpus = restored
    store.save_working(restored)
    return {"n": len(restored), "backup_version_id": backup_id,
            "diff": _last_activation_diff, "health": _corpus_health(restored),
            "chat_sync": _sync_chat_after_baseline_change()}


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


def _save_run(kind: str, summary: dict, exchanges: list) -> str | None:
    """Traçabilité des runs multi-agents (doctrine : chemin de pensée, appels
    LLM, coût/latence). Meilleur-effort : Mongo down ne casse jamais un run.
    Collection bornée aux 200 derniers runs."""
    try:
        from utils.mongo import get_db
        col = get_db()["lynx_runs"]
        run_id = uuid4().hex[:12]
        col.insert_one({"run_id": run_id, "kind": kind, "t": time.time(),
                        **summary, "exchanges": exchanges})
        excess = col.count_documents({}) - 200
        if excess > 0:
            for doc in col.find({}, {"_id": 1}).sort("t", 1).limit(excess):
                col.delete_one({"_id": doc["_id"]})
        return run_id
    except Exception:
        return None


@router.get("/runs")
def list_runs(limit: int = 50) -> dict:
    """Historique des runs multi-agents (analyses, audits) — résumés seuls."""
    try:
        from utils.mongo import get_db
        rows = list(get_db()["lynx_runs"].find({}, {"_id": 0, "exchanges": 0})
                    .sort("t", -1).limit(max(1, min(limit, 200))))
        return {"available": True, "runs": rows}
    except Exception:
        return {"available": False, "runs": []}


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    """Un run complet : résumé + chemin de pensée (échanges LLM par agent)."""
    try:
        from utils.mongo import get_db
        doc = get_db()["lynx_runs"].find_one({"run_id": run_id}, {"_id": 0})
    except Exception:
        raise HTTPException(503, "Mongo injoignable.")
    if not doc:
        raise HTTPException(404, "Run introuvable.")
    return doc


class _Cancelled(Exception):
    """Le client SSE a disparu : on interrompt le worker au plus tôt (sinon il
    poursuivrait ses appels LLM en tenant _state.llm_lock — UI « pendue » ensuite)."""


def _sse_error_stream(message: str) -> StreamingResponse:
    """Erreur de validation AVANT le flux : une trame `error` propre (un 500
    JSON serait affiché « API injoignable » par le front)."""
    def gen():
        yield _sse({"type": "error", "message": message})
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store"})


def _sse_stream(run: Callable[[Callable[[dict], None], threading.Event], None],
                *, on_cancel: Callable[[], None] | None = None,
                cancel_excs: tuple[type[BaseException], ...] = ()) -> StreamingResponse:
    """Ossature SSE partagée par les endpoints d'analyse (analyze, audit,
    audit/fix, generate, eval), qui ne diffèrent que par leur travail.

    `run(emit, cancelled)` fait le travail (typiquement sous `_state.llm_lock`) et
    publie ses events métier via `emit(...)`, y compris son `done` final. Le
    helper fournit tout le reste, identique partout : une file, un thread démon,
    l'annulation quand le client SSE se déconnecte (`emit` lève alors
    `_Cancelled` au prochain appel), la trame `error` générique et le `None` de
    fin de flux.

    `on_cancel` nettoie à l'annulation (ex. `llm.stop_trace`, idempotent) ;
    `cancel_excs` déclare les exceptions d'annulation propres à l'appelant
    (ex. `BatchCancelled`) à traiter comme un `_Cancelled`.
    """
    q: queue.Queue = queue.Queue()
    cancelled = threading.Event()

    def emit(item: dict) -> None:
        if cancelled.is_set():
            raise _Cancelled()
        q.put(item)

    def worker() -> None:
        try:
            run(emit, cancelled)
        except (_Cancelled, *cancel_excs):
            if on_cancel is not None:
                on_cancel()
        except Exception as e:
            q.put({"type": "error", "message": f"{type(e).__name__}: {str(e)[:200]}"})
        q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        try:
            while True:
                item = q.get()
                if item is None:
                    return
                yield _sse(item)
        finally:
            cancelled.set()  # client parti : le worker s'arrête au prochain emit

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store"})


@router.post("/analyze")
def analyze(body: AnalyzeBody) -> StreamingResponse:
    """Analyse d'impact d'UNE action, en boîte de verre SSE : `agent`
    (start/done par agent), `report` (verdict + constats), `token` (synthèse
    streamée), `exchanges` (timeline humanisée), `done` / `error`."""
    corpus = [dict(r) for r in _get_corpus()]
    try:
        action = _build_action(body.action)
    except Exception as e:
        return _sse_error_stream(f"Action invalide : {str(e)[:200]}")

    def run(emit, cancelled):
        with _state.llm_lock:
            # Total d'agents prévus (déterministes + sémantiques si activés) :
            # permet à l'UI d'afficher une barre de progression, comme l'audit.
            try:
                from src import orchestration_config as oc
                cfg = oc.load_config()
                total = sum(1 for e in cfg["deterministic"] if e.get("enabled", True))
                if body.semantic:
                    total += sum(1 for e in cfg["semantic"] if e.get("enabled", True))
                emit({"type": "agents_total", "n": total})
            except Exception:
                pass  # sans total, l'UI garde les pastilles par agent
            llm.start_trace()
            t0 = time.time()
            agents_done = {"n": 0}

            def _on_agent(kind, label):
                if kind == "done":
                    agents_done["n"] += 1
                emit({"type": "agent", "kind": kind, "label": label})

            try:
                report = run_impact_analysis(
                    corpus, action, semantic=body.semantic, on_event=_on_agent)
                findings = _ui_findings(report)
                emit({"type": "report", "verdict": verdict_label(report),
                      "findings": findings, "impacted": report.impacted_ids,
                      "narrative": report.narrative})
                for piece in stream_synthesis(report, action, use_llm=body.semantic):
                    emit({"type": "token", "text": piece})
            except llm.LynxBudgetExceeded as exc:
                # Garde-fou coût : abandon PROPRE, boîte de verre conservée.
                findings = []
                emit({"type": "error", "message": str(exc)})
            records = llm.stop_trace()
            timeline = trace.build_timeline(records, findings)
            emit({"type": "exchanges", "exchanges": timeline})
            # Doctrine multi-agent : complétude, appels LLM, coût vs latence.
            metrics = {"agents_done": agents_done["n"],
                       "llm_calls": llm.llm_calls_in_trace(),
                       "wall_s": round(time.time() - t0, 1)}
            emit({"type": "metrics", **metrics})
            _save_run("analyse", {
                "action": f"{action.action_type.value} {action.target_id}",
                "verdict": verdict_label(report) if findings is not None and 'report' in dir() else None,
                **metrics}, timeline)
            # ROI : défauts captés tôt (shift-left), comme le Streamlit.
            try:
                roi.record_catches("edition", action.action_type.value,
                                   action.target_id, findings)
            except Exception:
                pass
        emit({"type": "done"})

    return _sse_stream(run, on_cancel=llm.stop_trace)  # purge le buffer de trace


class ApplyBody(BaseModel):
    action: ActionBody
    rationale: str = ""               # renseigné si passage en force (BLOQUANT)


_LINK_TYPES = {"DERIVE", "SATISFIES", "VERIFIES", "REFINES", "ALLOCATES_TO"}
_ACTION_TYPES = {"CREATE", "UPDATE", "DELETE", "LINK", "UNLINK"}


@router.post("/apply")
def apply_action(body: ApplyBody) -> dict:
    global _corpus
    """Applique une action à la matrice de travail (après verdict côté front) :
    état process + working.json + journal d'historique.

    Validation stricte AVANT écriture : un link_type hors enum persisterait
    dans working.json et casserait ensuite CHAQUE analyse (RequirementTree
    valide les liens) jusqu'au reset du corpus."""
    corpus = _get_corpus()
    a = body.action
    if a.action_type not in _ACTION_TYPES:
        raise HTTPException(400, f"action_type invalide : {a.action_type}")
    if a.action_type in ("LINK", "UNLINK") and a.link_type is not None \
            and a.link_type not in _LINK_TYPES:
        raise HTTPException(400, f"link_type invalide : {a.link_type}")
    old = next((r.get("texte", "") for r in corpus if r["id"] == a.target_id), "")
    _corpus = _candidate(corpus, a)
    store.save_working(_corpus)
    store.append_history(a.action_type, a.target_id, old, a.new_text,
                         body.rationale or "")
    return {"n": len(_corpus)}


# ─────────────── Audit de la matrice (SSE) ───────────────

class AuditBody(BaseModel):
    deep: bool = True                 # False = règles déterministes seules
    req_ids: list[str] | None = None  # diff ciblé + voisinage à un bond


@router.post("/audit")
def audit(body: AuditBody) -> StreamingResponse:
    """Audit complet : `progress` (exigences auditées / total), puis `report`
    (score /100, constats par axe, exigences signalées) + `exchanges`."""
    corpus = [dict(r) for r in _get_corpus()]
    if body.req_ids:
        wanted = set(body.req_ids)
        neighbours: dict[str, set[str]] = {req["id"]: set() for req in corpus}
        for req in corpus:
            targets = {req.get("parent_id")} | {
                lk.get("target") for lk in req.get("links", []) if isinstance(lk, dict)}
            targets.discard(None)
            for target in targets:
                if target in neighbours:
                    neighbours[req["id"]].add(target)
                    neighbours[target].add(req["id"])
        # Extension à un bond calculée depuis un snapshot : résultat stable
        # quel que soit l'ordre des exigences dans le fichier.
        wanted |= set().union(*(neighbours.get(req_id, set()) for req_id in tuple(wanted)))
        corpus = [req for req in corpus if req["id"] in wanted]

    def run(emit, cancelled):
        with _state.llm_lock:
            llm.start_trace()
            t0 = time.time()
            rep = lynx_audit.audit_matrix(
                corpus, deep=body.deep,
                on_event=lambda done, total: emit(
                    {"type": "progress", "done": done, "total": total}))
            records = llm.stop_trace()
        exchanges = trace.humanize_audit(records)
        metrics = {"llm_calls": llm.llm_calls_in_trace(),
                   "wall_s": round(time.time() - t0, 1)}
        non_audited_ids = sorted({f.req_id for f in rep.findings if f.axis == "NON_AUDITE"})
        audited_ids = ([req["id"] for req in corpus if req["id"] not in non_audited_ids]
                       if body.deep else [])
        emit({"type": "report", "n": rep.n, "score": rep.score,
              "counts": rep.counts, "flagged_ids": rep.flagged_ids,
              "n_non_audite": rep.n_non_audite,
              "requested_n": rep.requested_n, "audited_n": rep.audited_n,
              "coverage": rep.coverage, "mode": rep.mode,
              "degraded_reasons": rep.degraded_reasons,
              "score_meaningful": rep.score_meaningful,
              "audited_ids": audited_ids, "non_audited_ids": non_audited_ids,
              "findings": [vars(f) for f in rep.findings],
              "exchanges": exchanges})
        emit({"type": "metrics", **metrics})
        _save_run("audit", {"score": rep.score, "n": rep.n, **metrics}, exchanges)
        emit({"type": "done"})

    return _sse_stream(run, on_cancel=llm.stop_trace)


# ─────────────── Correction en lot depuis l'audit (SSE) ───────────────

class FixBody(BaseModel):
    findings: list[dict]              # constats de l'audit courant (req_id, severity, message…)
    deep: bool = True                 # profondeur du ré-audit entre les passes


@router.post("/audit/fix")
def audit_fix(body: FixBody) -> StreamingResponse:
    """Correction en lot : pour chaque exigence signalée (BLOQUANT/WARNING),
    l'agent de rédaction propose une réécriture appliquée à une COPIE du
    corpus, puis la copie est ré-auditée — jusqu'à 3 passes. Events
    `progress` {phase, passe, done, total, req_id} puis `result` (récap par
    exigence + compteurs). Le corpus réel n'est PAS modifié : la validation
    sélective passe par /audit/fix/apply."""
    corpus = [dict(r) for r in _get_corpus()]

    def run(emit, cancelled):
        with _state.llm_lock:
            out = lynx_autofix.run_batch_fix(
                corpus, body.findings, max_passes=3, deep=body.deep,
                on_progress=lambda info: emit({"type": "progress", **info}),
                cancelled=cancelled)
        emit({"type": "result", **out})
        emit({"type": "done"})

    # Pas de nettoyage à l'annulation : la copie de travail est simplement jetée.
    return _sse_stream(run, cancel_excs=(lynx_autofix.BatchCancelled,))


class FixApplyItem(BaseModel):
    req_id: str
    texte: str


class FixApplyBody(BaseModel):
    items: list[FixApplyItem]


@router.post("/audit/fix/apply")
def audit_fix_apply(body: FixApplyBody) -> dict:
    """Applique les corrections COCHÉES du récap à la matrice réelle, comme
    une série d'UPDATE : état process + working.json + journal d'historique.
    Renvoie le corpus à jour."""
    corpus = _get_corpus()
    by_id = {r["id"]: r for r in corpus}
    if not body.items:
        raise HTTPException(400, "Aucune correction sélectionnée.")
    inconnues = [it.req_id for it in body.items if it.req_id not in by_id]
    if inconnues:
        raise HTTPException(400, f"Exigences inconnues : {', '.join(inconnues[:5])}")
    for it in body.items:
        if not it.texte.strip():
            raise HTTPException(400, f"Texte vide pour {it.req_id}.")
    for it in body.items:
        old = by_id[it.req_id].get("texte", "")
        by_id[it.req_id]["texte"] = it.texte
        store.append_history("UPDATE", it.req_id, old, it.texte,
                             "Correction en lot (audit)")
    store.save_working(corpus)
    return {"n": len(corpus), "exigences": corpus}


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


# ─────────────── Pilotage : prompts des agents + orchestration ───────────────

@router.get("/skills")
def list_skills() -> dict:
    """Les prompts (markdown) de TOUS les agents — la matière première de
    LynX, éditable depuis l'onglet Informations. Rechargés du disque à chaque
    appel LLM : une sauvegarde prend effet dès l'analyse suivante."""
    from src.config import SKILLS_DIR
    skills = []
    for path in sorted(Path(SKILLS_DIR).glob("*.md")):
        skills.append({"name": path.stem, "content": path.read_text(encoding="utf-8")})
    return {"skills": skills}


class SkillBody(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)


@router.put("/skills/{name}")
def save_skill(name: str, body: SkillBody) -> dict:
    """Écrit le prompt d'un agent (uniquement un skill EXISTANT — pas de
    création de fichier arbitraire)."""
    from src.config import SKILLS_DIR
    safe = name.replace("/", "").replace("\\", "").replace("..", "")
    path = Path(SKILLS_DIR) / f"{safe}.md"
    if not path.exists():
        raise HTTPException(404, f"Skill inconnu : {safe}")
    if not body.content.strip():
        raise HTTPException(400, "Le prompt ne peut pas être vide.")
    path.write_text(body.content, encoding="utf-8")
    return {"ok": True, "name": safe}


@router.get("/orchestration")
def get_orchestration() -> dict:
    """Ordre + activation des agents d'analyse (déterministes séquentiels,
    sémantiques parallèles), avec leurs libellés humains."""
    from src import orchestration_config as oc
    from src.orchestrator import AGENT_LABELS
    labels = {f.__name__: lbl for f, lbl in AGENT_LABELS.items()}
    cfg = oc.load_config()
    # `model` : LLM résolu par agent (routage LYNX_MODEL_<SKILL>, cf. src.llm).
    # Les déterministes n'appellent pas de LLM -> None.
    return {g: [{**e, "label": labels.get(e["name"], e["name"]),
                 "model": llm.model_for(e["name"]) if g == "semantic" else None}
                for e in cfg[g]]
            for g in ("deterministic", "semantic")}


class OrchestrationEntryBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = True


class OrchestrationBody(BaseModel):
    deterministic: list[OrchestrationEntryBody] = Field(max_length=64)
    semantic: list[OrchestrationEntryBody] = Field(max_length=64)


@router.put("/orchestration")
def put_orchestration(body: OrchestrationBody) -> dict:
    """Persiste l'ordre/activation — appliqué dès la PROCHAINE analyse."""
    from src import orchestration_config as oc
    oc.save_config({"deterministic": [entry.model_dump() for entry in body.deterministic],
                    "semantic": [entry.model_dump() for entry in body.semantic]})
    return get_orchestration()


class CorrectBody(BaseModel):
    req_id: str
    problems: list[str] = []


@router.post("/correct")
def correct(body: CorrectBody) -> dict:
    """Suggestion de correction d'UNE exigence (1 appel LLM)."""
    with _state.llm_lock:
        return lynx_correction.suggest_correction(
            _get_corpus(), body.req_id, body.problems or None)
