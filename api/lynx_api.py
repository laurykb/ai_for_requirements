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
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.common import _sse

# lynx/ utilise des imports `from src import ...` : on l'ajoute au path.
_LYNX_DIR = str(Path(__file__).resolve().parent.parent / "lynx")
if _LYNX_DIR not in sys.path:
    sys.path.insert(0, _LYNX_DIR)

from eval.run_eval import run_golden_eval    # noqa: E402
from src import audit as lynx_audit          # noqa: E402
from src import autofix as lynx_autofix      # noqa: E402
from src import correction as lynx_correction  # noqa: E402
from src import generation as lynx_generation  # noqa: E402
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
        # _unwrap : accepte le format enveloppé {"meta":…, "exigences":[…]}
        # (celui que LynX exporte lui-même) comme la liste nue.
        valides, errs = corpus_io.validate_corpus(corpus_io._unwrap(raw))
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


class _Cancelled(Exception):
    """Le client SSE a disparu : on interrompt le worker au plus tôt (sinon il
    poursuivrait ses appels LLM en tenant _LLM_LOCK — UI « pendue » ensuite)."""


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

    `run(emit, cancelled)` fait le travail (typiquement sous `_LLM_LOCK`) et
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
        with _LLM_LOCK:
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
            report = run_impact_analysis(
                corpus, action, semantic=body.semantic,
                on_event=lambda kind, label: emit(
                    {"type": "agent", "kind": kind, "label": label}))
            findings = _ui_findings(report)
            emit({"type": "report", "verdict": verdict_label(report),
                  "findings": findings, "impacted": report.impacted_ids,
                  "narrative": report.narrative})
            for piece in stream_synthesis(report, action, use_llm=body.semantic):
                emit({"type": "token", "text": piece})
            records = llm.stop_trace()
            emit({"type": "exchanges",
                  "exchanges": trace.build_timeline(records, findings)})
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
    """Applique une action à la matrice de travail (après verdict côté front) :
    état process + working.json + journal d'historique.

    Validation stricte AVANT écriture : un link_type hors enum persisterait
    dans working.json et casserait ensuite CHAQUE analyse (RequirementTree
    valide les liens) jusqu'au reset du corpus."""
    global _corpus
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


@router.post("/audit")
def audit(body: AuditBody) -> StreamingResponse:
    """Audit complet : `progress` (exigences auditées / total), puis `report`
    (score /100, constats par axe, exigences signalées) + `exchanges`."""
    corpus = [dict(r) for r in _get_corpus()]

    def run(emit, cancelled):
        with _LLM_LOCK:
            llm.start_trace()
            rep = lynx_audit.audit_matrix(
                corpus, deep=body.deep,
                on_event=lambda done, total: emit(
                    {"type": "progress", "done": done, "total": total}))
            records = llm.stop_trace()
        emit({"type": "report", "n": rep.n, "score": rep.score,
              "counts": rep.counts, "flagged_ids": rep.flagged_ids,
              "n_non_audite": rep.n_non_audite,
              "findings": [vars(f) for f in rep.findings],
              "exchanges": trace.humanize_audit(records)})
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
        with _LLM_LOCK:
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


# ─────────────── Génération descendante de filles (SSE) ───────────────

class GenerateChildrenBody(BaseModel):
    req_id: str


@router.post("/generate/children")
def generate_children(body: GenerateChildrenBody) -> StreamingResponse:
    """Propose des exigences filles L(n+1) pour la mère sélectionnée :
    `progress` {phase: generation|audit|reecriture, ...} puis `result`
    (récap sélectif — rien n'est créé sans /generate/children/apply)."""
    corpus = [dict(r) for r in _get_corpus()]

    def run(emit, cancelled):
        with _LLM_LOCK:
            llm.start_trace()  # boîte de verre : proposition + audit + débat + réécriture
            out = lynx_generation.generate_children(
                corpus, body.req_id,
                on_progress=lambda info: emit({"type": "progress", **info}),
                cancelled=cancelled)
            records = llm.stop_trace()
        if out.get("error"):
            emit({"type": "error", "message": str(out["error"])[:300]})
        else:
            child_ids = [f["id_propose"] for f in out.get("filles", [])]
            out["exchanges"] = trace.build_generation_timeline(records, child_ids)
            emit({"type": "result", **out})
            emit({"type": "done"})

    return _sse_stream(run, on_cancel=llm.stop_trace,  # purge le buffer, copie jetée
                       cancel_excs=(lynx_generation.BatchCancelled,))


class ChildItem(BaseModel):
    texte: str
    niveau: int
    id: str | None = None             # id proposé au récap (repli auto si pris)


class GenerateApplyBody(BaseModel):
    parent_id: str
    items: list[ChildItem]


@router.post("/generate/children/apply")
def generate_children_apply(body: GenerateApplyBody) -> dict:
    """Crée réellement les filles cochées du récap (lien DERIVE via parent_id),
    par le même chemin que la création manuelle (CREATE + persistance)."""
    corpus = _get_corpus()
    ids = {r["id"] for r in corpus}
    if body.parent_id not in ids:
        raise HTTPException(400, f"Mère inconnue : {body.parent_id}")
    if not body.items:
        raise HTTPException(400, "Aucune fille sélectionnée.")
    for it in body.items:
        if not it.texte.strip():
            raise HTTPException(400, "Texte de fille vide.")
    for it in body.items:
        ids = {r["id"] for r in _get_corpus()}
        cid = it.id if it.id and it.id not in ids else ""  # "" -> id auto
        apply_action(ApplyBody(action=ActionBody(
            action_type="CREATE", target_id=cid, new_text=it.texte.strip(),
            parent_id=body.parent_id, niveau=it.niveau),
            rationale="Génération descendante (validée au récap)"))
    return {"n": len(_get_corpus()), "exigences": _get_corpus()}


# ─────────────── Éval du golden set (SSE) ───────────────

class EvalBody(BaseModel):
    fast: bool = True                 # True = agents déterministes seuls (immédiat)


@router.post("/eval")
def run_eval(body: EvalBody) -> StreamingResponse:
    """Harnais d'évaluation (lynx/eval) sur le golden set : `progress`
    (cas évalués / total), puis `result` (P/R/F1 micro + par axe). Les
    prompts étant rechargés du disque à chaque appel LLM, un prompt
    sauvegardé depuis l'onglet Informations est évalué tel quel."""
    def run(emit, cancelled):
        with _LLM_LOCK:
            out = run_golden_eval(
                semantic=not body.fast,
                on_progress=lambda done, total: emit(
                    {"type": "progress", "done": done, "total": total}))
        emit({"type": "result", **out})
        emit({"type": "done"})

    # Rien à nettoyer à l'annulation (last_eval.json n'est pas écrit).
    return _sse_stream(run)


@router.get("/eval/last")
def get_last_eval() -> dict:
    """Dernier résumé écrit par le harnais (baseline affichée par l'UI)."""
    path = Path(_LYNX_DIR) / "eval" / "last_eval.json"
    if not path.exists():
        return {"exists": False}
    try:
        return {"exists": True, **json.loads(path.read_text(encoding="utf-8"))}
    except Exception:
        return {"exists": False}


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
    content: str


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
    return {g: [{**e, "label": labels.get(e["name"], e["name"])} for e in cfg[g]]
            for g in ("deterministic", "semantic")}


class OrchestrationBody(BaseModel):
    deterministic: list[dict]
    semantic: list[dict]


@router.put("/orchestration")
def put_orchestration(body: OrchestrationBody) -> dict:
    """Persiste l'ordre/activation — appliqué dès la PROCHAINE analyse."""
    from src import orchestration_config as oc
    oc.save_config({"deterministic": body.deterministic, "semantic": body.semantic})
    return get_orchestration()


class CorrectBody(BaseModel):
    req_id: str
    problems: list[str] = []


@router.post("/correct")
def correct(body: CorrectBody) -> dict:
    """Suggestion de correction d'UNE exigence (1 appel LLM)."""
    with _LLM_LOCK:
        return lynx_correction.suggest_correction(
            _get_corpus(), body.req_id, body.problems or None)
