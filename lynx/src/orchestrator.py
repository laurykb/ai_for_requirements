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
    analyze_allocation, analyze_coreference, analyze_couverture, analyze_downstream,
    analyze_impact_latent, analyze_pertinence, analyze_pertinence_aval, analyze_redondance,
)
from .config import MAX_NIVEAU
from .models import Action, ActionType, Finding, ImpactReport, LinkType, Scope, Severity
from .orchestration_config import active_analyzers
from .tree import RequirementTree, _DECOMP

# Libellé lisible de chaque agent, pour la trace dans l'UI.
AGENT_LABELS = {
    analyze_allocation: "Allocation budgétaire",
    analyze_downstream: "Propagation aval",
    analyze_pertinence: "Pertinence amont",
    analyze_couverture: "Couverture du parent",
    analyze_redondance: "Redondance / sur-spécification",
    analyze_pertinence_aval: "Pertinence aval",
    analyze_impact_latent: "Impact latent",
    analyze_coreference: "Cohérence des co-références",
}


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
    elif action.action_type == ActionType.DELETE:
        if action.target_id not in tree:
            raise ActionError(f"Exigence cible introuvable : {action.target_id}")
        return tree.with_deleted(action.target_id)
    elif action.action_type == ActionType.CREATE:
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
    elif action.action_type == ActionType.LINK:
        child, parent = action.target_id, action.link_target
        ltype = action.link_type or LinkType.DERIVE
        if child not in tree:
            raise ActionError(f"Exigence source du lien introuvable : {child}")
        if not parent or parent not in tree:
            raise ActionError(f"Exigence cible du lien introuvable : {parent}")
        if parent == child:
            raise ActionError("Une exigence ne peut pas être liée à elle-même.")
        node = tree.get(child)
        if any(lk.target == parent and lk.type == ltype for lk in node.links):
            raise ActionError(f"Lien {ltype.value} → {parent} déjà présent sur {child}.")
        # Anti-cycle : seuls les liens de décomposition créent une arête amont ;
        # rattacher à un descendant fermerait une boucle.
        if ltype in _DECOMP and parent in {d.id for d in tree.descendants(child)}:
            raise ActionError(f"Lien impossible : {parent} est déjà en aval de {child} (cycle).")
        # Déclinaison = niveaux adjacents : la mère est exactement un niveau au-dessus
        # de la fille (parent N-1 → fille N). On interdit tout saut de niveau (ex. L1 → L3).
        parent_node = tree.get(parent)
        if ltype in _DECOMP and parent_node and node.niveau != parent_node.niveau + 1:
            raise ActionError(
                f"Déclinaison invalide : {parent} (L{parent_node.niveau}) et {child} "
                f"(L{node.niveau}) ne sont pas à des niveaux adjacents. Un lien de "
                f"décomposition relie N → N+1 ; le saut de niveau est interdit.")
        return tree.with_link(child, parent, ltype)
    elif action.action_type == ActionType.UNLINK:
        child, parent, ltype = action.target_id, action.link_target, action.link_type
        if child not in tree:
            raise ActionError(f"Exigence source du lien introuvable : {child}")
        node = tree.get(child)
        if not any(lk.target == parent and (ltype is None or lk.type == ltype) for lk in node.links):
            raise ActionError(f"Aucun lien vers {parent} à retirer sur {child}.")
        return tree.with_unlink(child, parent, ltype)
    else:
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


# Titres de section affichés dans la narration, dans l'ordre du pipeline d'analyse.
_SCOPE_TITLES = {
    "STRUCTURE": "Validité structurelle",
    "ALLOCATION": "Allocation / budget",
    "AMONT": "T1 — Pertinence / cohérence amont",
    "COUVERTURE": "T2 — Couverture du parent (complétude)",
    "HORIZONTAL": "T3 — Redondance / sur-spécification",
    "PERTINENCE_AVAL": "T4 — Pertinence / cohérence aval",
    "IMPACT_LATENT": "Impact latent (exigences non reliées)",
    "COHERENCE_REF": "Cohérence des co-références",
    "AVAL": "Propagation aval (descendants)",
}
_SEVERITY_MARK = {Severity.INFO: "", Severity.WARNING: "attention", Severity.BLOCKING: "bloquant"}


def _narrate(report: ImpactReport) -> str:
    lines = [f"**Statut global : {report.global_status.value}** "
             f"pour {report.action_type.value} sur `{report.target_id}`.", ""]
    by_scope: dict[str, List[Finding]] = {}
    for f in report.findings:
        by_scope.setdefault(f.scope.value, []).append(f)
    for scope, title in _SCOPE_TITLES.items():
        items = by_scope.get(scope)
        if not items:
            continue
        lines.append(f"### {title}")
        for f in items:
            mark = _SEVERITY_MARK[f.severity]
            prefix = f"{mark} : " if mark else ""
            lines.append(f"- {prefix}{f.message}")
        lines.append("")
    return "\n".join(lines).strip()


_VERDICT = {Severity.INFO: "VALIDE", Severity.WARNING: "ATTENTION", Severity.BLOCKING: "BLOQUANT"}
_ACTION_VERB = {ActionType.CREATE: "L'ajout", ActionType.UPDATE: "La modification",
                ActionType.DELETE: "La suppression", ActionType.LINK: "Le rattachement",
                ActionType.UNLINK: "Le détachement"}


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
    for piece in llm.stream_agent(system, _synthesis_payload(report, action),
                                  label="synthese_message"):
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
    # Ordre et activation PILOTABLES depuis l'UI (corpus/orchestration.json).
    det_analyzers, sem_analyzers = active_analyzers()

    for analyzer in det_analyzers:
        label = AGENT_LABELS.get(analyzer, "analyseur")
        emit("start", label)
        findings += _safe(analyzer, ctx)
        emit("done", label)
    # Les agents LLM (pertinence, couverture, redondance) sont indépendants :
    # on les lance en parallèle et on remonte chaque résultat dès qu'il arrive.
    if semantic:
        for analyzer in sem_analyzers:
            emit("start", AGENT_LABELS.get(analyzer, "agent"))
        with ThreadPoolExecutor(max_workers=max(1, len(sem_analyzers))) as pool:
            futures = {pool.submit(_safe, a, ctx): a for a in sem_analyzers}
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
        report.narrative = (f"**Intégration forcée** (justification : "
                            f"{action.override_rationale or 'non précisée'}).\n\n") + _narrate(report)
    else:
        report.narrative = _narrate(report)
    return report
