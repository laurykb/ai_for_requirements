"""Orchestrateur d'analyse d'impact (in-process, temps réel).

Construit l'arbre *candidat* correspondant à l'action demandée, exécute les
quatre analyseurs et agrège leurs constats dans un ``ImpactReport``.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, Iterator, List, Optional

from . import llm
from .analyzers import (
    Ctx, DETERMINISTIC_ANALYZERS, SEMANTIC_ANALYZERS,
    analyze_allocation, analyze_couverture, analyze_downstream,
    analyze_pertinence, analyze_redondance,
)
from .config import MAX_NIVEAU

# Libellé lisible de chaque agent, pour la trace dans l'UI.
AGENT_LABELS = {
    analyze_allocation: "Allocation budgétaire",
    analyze_downstream: "Propagation aval",
    analyze_pertinence: "Pertinence amont",
    analyze_couverture: "Couverture du parent",
    analyze_redondance: "Redondance / sur-spécification",
}
from .models import Action, ActionType, Finding, ImpactReport, Scope, Severity
from .tree import RequirementTree


class ActionError(ValueError):
    """Action structurellement invalide (collision d'ID, niveau hors borne…)."""


def build_candidate_tree(tree: RequirementTree, action: Action) -> RequirementTree:
    """Applique l'action à une copie de l'arbre et renvoie l'arbre candidat."""
    if action.action_type == ActionType.UPDATE:
        if action.target_id not in tree:
            raise ActionError(f"Exigence cible introuvable : {action.target_id}")
        changes: dict = {"texte": action.new_text}
        if action.test_status is not None:
            changes["test_status"] = action.test_status
        return tree.with_updated(action.target_id, changes)
    if action.action_type == ActionType.DELETE:
        if action.target_id not in tree:
            raise ActionError(f"Exigence cible introuvable : {action.target_id}")
        return tree.with_deleted(action.target_id)
    if action.action_type == ActionType.CREATE:
        if action.target_id in tree:
            raise ActionError(f"Identifiant déjà utilisé : {action.target_id}")
        parent = tree.get(action.parent_id) if action.parent_id else None
        if action.parent_id and parent is None:
            raise ActionError(f"Parent introuvable : {action.parent_id}")
        niveau = action.niveau
        if niveau is None:
            niveau = (parent.niveau + 1) if parent else 0
        if niveau > MAX_NIVEAU:
            raise ActionError(
                f"Niveau {niveau} au-delà du maximum L{MAX_NIVEAU} : impossible de "
                f"décliner sous une exigence de niveau maximal.")
        new_req = {
            "id": action.target_id,
            "niveau": niveau,
            "type": (parent.type if parent else "Exigence"),
            "domaine": action.domaine or (parent.domaine if parent else "Général"),
            "texte": action.new_text or "",
            "parent_id": action.parent_id,
            "test_status": action.test_status or "PENDING",
        }
        return tree.with_added(new_req)
    raise ValueError(f"Action inconnue : {action.action_type}")


def _safe(analyzer, ctx) -> List[Finding]:
    """Exécute un analyseur en isolant ses erreurs (un échec ne bloque pas le reste)."""
    try:
        return analyzer(ctx)
    except Exception as exc:  # noqa: BLE001
        return [Finding(
            analyzer=getattr(analyzer, "__name__", "analyzer"),
            scope=Scope.STRUCTURE, severity=Severity.INFO,
            message=f"Analyseur {getattr(analyzer, '__name__', '?')} en erreur : {exc}",
            impacted_ids=[ctx.action.target_id])]


def _narrate(report: ImpactReport) -> str:
    icon = {Severity.INFO: "🟢", Severity.WARNING: "🟡", Severity.BLOCKING: "🔴"}
    lines = [f"{icon[report.global_status]} **Statut global : {report.global_status.value}** "
             f"pour {report.action_type.value} sur `{report.target_id}`.", ""]
    by_scope: dict[str, List[Finding]] = {}
    for f in report.findings:
        by_scope.setdefault(f.scope.value, []).append(f)
    titles = {
        "STRUCTURE": "🧱 Validité structurelle",
        "ALLOCATION": "📐 Allocation / budget",
        "AMONT": "⬆️ T1 — Pertinence / cohérence amont",
        "COUVERTURE": "🧩 T2 — Couverture du parent (complétude)",
        "HORIZONTAL": "🤝 T3 — Redondance / sur-spécification",
        "AVAL": "⬇️ Propagation aval (descendants)",
    }
    for scope, title in titles.items():
        items = by_scope.get(scope)
        if not items:
            continue
        lines.append(f"### {title}")
        for f in items:
            mark = {Severity.INFO: "•", Severity.WARNING: "⚠️", Severity.BLOCKING: "⛔"}[f.severity]
            lines.append(f"- {mark} {f.message}")
        lines.append("")
    return "\n".join(lines).strip()


_VERDICT = {Severity.INFO: "VALIDE", Severity.WARNING: "ATTENTION", Severity.BLOCKING: "BLOQUANT"}
_ACTION_VERB = {ActionType.CREATE: "L'ajout", ActionType.UPDATE: "La modification",
                ActionType.DELETE: "La suppression"}


def _fallback_message(report: ImpactReport, action: Action) -> str:
    verb = _ACTION_VERB.get(action.action_type, "L'action")
    notable = [f for f in report.findings if f.severity != Severity.INFO]
    if not notable:
        return f"{verb} de {action.target_id} est cohérente avec l'arborescence. Aucun impact problématique détecté."
    lines = [f"{verb} de {action.target_id} appelle votre attention :"]
    for f in notable:
        lines.append(f"- {f.message}")
    return "\n".join(lines)


def verdict_label(report: ImpactReport) -> str:
    """Verdict déterministe (VALIDE / ATTENTION / BLOQUANT) tiré du statut global."""
    return _VERDICT[report.global_status]


def _synthesis_payload(report: ImpactReport, action: Action) -> dict:
    return {
        "action": {"type": action.action_type.value, "exigence": action.target_id,
                   "nouveau_texte": action.new_text},
        "statut_global": report.global_status.value,
        "constats": [{"axe": f.scope.value, "gravite": f.severity.value, "message": f.message}
                     for f in report.findings],
    }


def synthesize_verdict(report: ImpactReport, action: Action, use_llm: bool = True) -> Dict[str, str]:
    """Verdict + message complet (non streamé). Repli déterministe si pas de LLM."""
    fallback = {"verdict": verdict_label(report), "message": _fallback_message(report, action)}
    if not use_llm:
        return fallback
    resp = llm.call_skill("synthese_impact", _synthesis_payload(report, action))
    if resp.get("error") or not resp.get("message"):
        return fallback
    return {"verdict": verdict_label(report), "message": resp["message"]}


def stream_synthesis(report: ImpactReport, action: Action, use_llm: bool = True) -> Iterator[str]:
    """Produit le message de synthèse token par token (pour l'affichage live)."""
    if not use_llm:
        yield _fallback_message(report, action)
        return
    try:
        system = llm.load_skill_prompt("synthese_message")
    except Exception:
        yield _fallback_message(report, action)
        return
    emitted = False
    for piece in llm.stream_agent(system, _synthesis_payload(report, action)):
        emitted = True
        yield piece
    if not emitted:  # LLM indisponible ou flux vide
        yield _fallback_message(report, action)


def run_impact_analysis(corpus: List[dict], action: Action, semantic: bool = True,
                        on_event: Optional[Callable[[str, str], None]] = None) -> ImpactReport:
    """Point d'entrée : analyse l'impact d'``action`` sur ``corpus``.

    ``semantic`` : si False, on ne lance que les analyseurs déterministes
    (allocation, aval) — instantané, sans appel LLM.
    ``on_event(kind, label)`` : callback de trace ; kind ∈ {"start","done"}.
    Renvoie un ``ImpactReport`` agrégé. Ne mute pas ``corpus``.
    """
    def emit(kind: str, label: str) -> None:
        if on_event:
            try:
                on_event(kind, label)
            except Exception:
                pass

    current = RequirementTree(corpus)
    try:
        candidate = build_candidate_tree(current, action)
    except ActionError as exc:
        report = ImpactReport(
            action_type=action.action_type,
            target_id=action.target_id,
            findings=[Finding(
                analyzer="structure", scope=Scope.STRUCTURE, severity=Severity.BLOCKING,
                message=f"Action invalide : {exc}", impacted_ids=[action.target_id],
            )],
        ).recompute_status()
        report.narrative = _narrate(report)
        return report

    ctx = Ctx(current=current, candidate=candidate, action=action)
    findings: List[Finding] = []
    for analyzer in DETERMINISTIC_ANALYZERS:
        label = AGENT_LABELS.get(analyzer, "analyseur")
        emit("start", label)
        findings += _safe(analyzer, ctx)
        emit("done", label)
    # Les agents LLM (pertinence, couverture, redondance) sont indépendants :
    # on les lance en parallèle et on remonte chaque résultat dès qu'il arrive.
    if semantic:
        for analyzer in SEMANTIC_ANALYZERS:
            emit("start", AGENT_LABELS.get(analyzer, "agent"))
        with ThreadPoolExecutor(max_workers=len(SEMANTIC_ANALYZERS)) as pool:
            futures = {pool.submit(_safe, a, ctx): a for a in SEMANTIC_ANALYZERS}
            for fut in as_completed(futures):
                findings += fut.result()
                emit("done", AGENT_LABELS.get(futures[fut], "agent"))

    report = ImpactReport(
        action_type=action.action_type,
        target_id=action.target_id,
        findings=findings,
    ).recompute_status()

    # Dérogation : si l'ingénieur force et fournit une justification, on
    # rétrograde les constats bloquants en avertissements consignés.
    if action.force_override and report.global_status == Severity.BLOCKING:
        for f in report.findings:
            if f.severity == Severity.BLOCKING:
                f.severity = Severity.WARNING
                f.details["overridden"] = True
                f.details["override_rationale"] = action.override_rationale
        report.recompute_status()
        report.narrative = (f"🛡️ **Intégration forcée** (justification : "
                            f"{action.override_rationale or 'non précisée'}).\n\n") + _narrate(report)
    else:
        report.narrative = _narrate(report)
    return report
