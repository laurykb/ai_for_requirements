"""Analyseurs d'impact.

Chaque analyseur reçoit un ``Ctx`` (arbre courant, arbre candidat = après action,
et l'action) et renvoie une liste de ``Finding``.

Déterministes :
  - allocation : roll-up budgétaire (somme des enfants vs plafond du parent)
  - aval       : descendants impactés (orphelins, re-test)
Sémantiques (agents LLM, qwen3.5) :
  - pertinence (T1) : la cible reste-t-elle cohérente/pertinente vs ses ancêtres ?
  - couverture (T2) : le parent reste-t-il entièrement couvert par ses filles ?
  - redondance (T3) : la cible est-elle redondante / sur-spécifiée vs ses sœurs ?
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from . import llm
from .config import ALLOCATION_TOLERANCE, EMBED_DISTINCT_THRESHOLD, EMBED_DUP_THRESHOLD, LLM_VOTE
from .extract import allocation_rollup, from_base
from .models import Action, ActionType, Finding, Scope, Severity
from .tree import RequirementTree

_SEV = {"INFO": Severity.INFO, "WARNING": Severity.WARNING, "BLOCKING": Severity.BLOCKING}


@dataclass
class Ctx:
    """Contexte d'analyse : avant/après l'action + l'action elle-même."""

    current: RequirementTree    # arbre avant l'action
    candidate: RequirementTree  # arbre après l'action
    action: Action

    @property
    def target_pre(self):
        return self.current.get(self.action.target_id)

    @property
    def parent_id(self) -> Optional[str]:
        node = self.target_pre
        if node and node.parent_id:
            return node.parent_id
        return self.action.parent_id  # cas CREATE


def _sev_from(resp: dict, default: Severity = Severity.WARNING) -> Severity:
    return _SEV.get(str(resp.get("niveau_gravite", "")).upper(), default)


def _with_preuve(message: str, resp: dict) -> str:
    """Ajoute la citation (preuve) au message si l'agent en a fourni une."""
    preuve = (resp.get("preuve") or "").strip()
    return f"{message} Preuve : « {preuve} »" if preuve else message


def _norm_ids(value) -> List[str]:
    """Normalise une liste hétérogène (str ou {id:...}) en liste d'ids non vides."""
    out: List[str] = []
    for v in value or []:
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            ident = v.get("id") or v.get("id_soeur") or v.get("id_ancetre")
            if ident:
                out.append(ident)
    return [i for i in out if i]


def _skip(scope: Scope, analyzer: str, ids: List[str], err: str) -> Finding:
    return Finding(analyzer=analyzer, scope=scope, severity=Severity.INFO,
                   message=f"Analyse {analyzer} ignorée (LLM indisponible : {err}).",
                   impacted_ids=ids)


# --------------------------------------------------------------------------
# Déterministe : allocation / budget
# --------------------------------------------------------------------------
def analyze_allocation(ctx: Ctx) -> List[Finding]:
    findings: List[Finding] = []
    tree = ctx.candidate

    # Parents à recontrôler : la cible (si présente) + sa chaîne d'ancêtres.
    suspects: dict[str, None] = {}
    if ctx.action.target_id in tree:
        suspects.setdefault(ctx.action.target_id, None)
    # ancêtres connus via l'arbre courant (robuste même au DELETE)
    chain_src = ctx.action.target_id if ctx.action.target_id in ctx.current else None
    if chain_src:
        for anc in ctx.current.ancestors(chain_src):
            if anc.id in tree:
                suspects.setdefault(anc.id, None)
    if ctx.parent_id and ctx.parent_id in tree:
        suspects.setdefault(ctx.parent_id, None)
    # LINK : la fille contribue désormais au nouveau parent -> recontrôler ce
    # parent et toute sa chaîne amont (le roll-up budgétaire remonte).
    if ctx.action.action_type == ActionType.LINK and ctx.action.link_target:
        if ctx.action.link_target in tree:
            suspects.setdefault(ctx.action.link_target, None)
        for anc in tree.ancestors(ctx.action.link_target):
            if anc.id in tree:
                suspects.setdefault(anc.id, None)

    for parent_id in suspects:
        parent = tree.get(parent_id)
        if not parent:
            continue
        roll = allocation_rollup(parent.texte, [(c.id, c.texte) for c in tree.children(parent_id)])
        if roll is None:
            continue
        findings.extend(_allocation_findings(parent.id, roll))
    return findings


def _allocation_findings(parent_id: str, roll: dict) -> List[Finding]:
    """Transforme un roll-up en constats (déterministe, avec tolérance + epsilon)."""
    budget = roll["budget"]
    unit = budget.unit
    out: List[Finding] = []
    # Enfants non comparables (autre famille d'unité) : on le dit explicitement.
    if roll["non_comparables"]:
        out.append(Finding(
            analyzer="allocation", scope=Scope.ALLOCATION, severity=Severity.INFO,
            message=(f"Roll-up partiel sur {parent_id} : {len(roll['non_comparables'])} enfant(s) "
                     f"d'unité non comparable (≠ {unit}) non pris en compte."),
            impacted_ids=[parent_id, *roll["non_comparables"]],
            details={"non_comparables": roll["non_comparables"]}))
    if not roll["contributions"]:
        return out
    total = round(from_base(roll["total_base"], unit), 3)
    limit_base = roll["budget_base"] * (1.0 + ALLOCATION_TOLERANCE)
    if roll["total_base"] > limit_base + 1e-9:
        overflow = round(from_base(roll["total_base"] - roll["budget_base"], unit), 3)
        out.append(Finding(
            analyzer="allocation", scope=Scope.ALLOCATION, severity=Severity.BLOCKING,
            message=(f"Dépassement de budget sur {parent_id} : somme des enfants = {total} {unit} "
                     f"> plafond {budget.value} {unit} (excès {overflow} {unit})."),
            impacted_ids=[parent_id, *[c for c, _ in roll["contributions"]]],
            details={"parent_id": parent_id, "budget": budget.value, "unit": unit,
                     "sum_children": total,
                     "contributions": [{"id": c, "value": v} for c, v in roll["contributions"]]}))
    else:
        out.append(Finding(
            analyzer="allocation", scope=Scope.ALLOCATION, severity=Severity.INFO,
            message=(f"Budget respecté sur {parent_id} : {total}/{budget.value} {unit} "
                     f"(marge {round(budget.value - total, 3)} {unit})."),
            impacted_ids=[parent_id],
            details={"parent_id": parent_id, "sum_children": total, "budget": budget.value, "unit": unit}))
    return out


# --------------------------------------------------------------------------
# Déterministe : propagation aval
# --------------------------------------------------------------------------
def analyze_downstream(ctx: Ctx) -> List[Finding]:
    action, tree = ctx.action, ctx.candidate
    if action.action_type == ActionType.DELETE:
        from .tree import _DECOMP
        tid = action.target_id
        orphans = []
        for r in tree.all():
            refs = ([r.parent_id] if r.parent_id else []) + [lk.target for lk in r.links if lk.type in _DECOMP]
            if tid in refs and tid not in tree:
                orphans.append(r.id)
        if orphans:
            return [Finding(
                analyzer="downstream", scope=Scope.AVAL, severity=Severity.BLOCKING,
                message=(f"Suppression de {action.target_id} : {len(orphans)} exigence(s) "
                         f"deviennent orphelines et doivent être réaffectées ou supprimées."),
                impacted_ids=orphans, details={"orphans": orphans})]
        return []
    if action.action_type == ActionType.UPDATE:
        desc = [d.id for d in tree.descendants(action.target_id)]
        if desc:
            return [Finding(
                analyzer="downstream", scope=Scope.AVAL, severity=Severity.WARNING,
                message=(f"Modification de {action.target_id} : {len(desc)} exigence(s) en aval "
                         f"doivent être revérifiées (déclinaison/test à reconfirmer)."),
                impacted_ids=desc, details={"a_revalider": desc})]
    return []


# --------------------------------------------------------------------------
# T1 — Pertinence / cohérence vs ancêtres (LLM)
# --------------------------------------------------------------------------
def analyze_pertinence(ctx: Ctx) -> List[Finding]:
    action, tree = ctx.action, ctx.candidate
    if action.action_type == ActionType.DELETE:
        return []
    target = tree.get(action.target_id)
    if not target:
        return []
    ancestors = tree.ancestors(action.target_id)
    if not ancestors:
        return [Finding(analyzer="pertinence", scope=Scope.AMONT, severity=Severity.INFO,
                        message="Exigence racine : pas de traçabilité ascendante à vérifier.",
                        impacted_ids=[target.id])]
    payload = {"exigence_cible": target.short(),
               "chaine_amont": [a.short() for a in reversed(ancestors)]}
    resp = llm.call_skill("coherence_pertinence", payload)
    if resp.get("error"):
        return [_skip(Scope.AMONT, "pertinence", [a.id for a in ancestors], resp["error"])]
    coherent = resp.get("est_coherent", True)
    sev = _sev_from(resp, Severity.INFO if coherent else Severity.WARNING)
    rupture = _norm_ids(resp.get("rupture_avec"))
    base = resp.get("synthese") or ("Traçabilité cohérente." if coherent else "Rupture de pertinence.")

    # Vote self-consistency : un verdict BLOQUANT à fort enjeu est re-tiré N fois
    # (température > 0) et conservé seulement si la majorité confirme l'incohérence.
    if sev == Severity.BLOCKING and LLM_VOTE > 1:
        votes = llm.sample_skill("coherence_pertinence", payload, n=LLM_VOTE)
        incoh = sum(1 for v in votes if v.get("est_coherent") is False)
        if votes and incoh <= len(votes) // 2:
            sev = Severity.WARNING
            base += f" (rétrogradé : incohérence non confirmée par vote {incoh}/{len(votes)})"
    return [Finding(
        analyzer="pertinence", scope=Scope.AMONT, severity=sev,
        message=_with_preuve(base, resp),
        impacted_ids=[target.id, *rupture], details={"preuve": resp.get("preuve", ""), "raw": resp})]


# --------------------------------------------------------------------------
# T2 — Couverture amont / complétude (LLM)
# --------------------------------------------------------------------------
def analyze_couverture(ctx: Ctx) -> List[Finding]:
    # La couverture d'un parent n'est réellement menacée que par une SUPPRESSION
    # (un coverer disparaît). Un UPDATE/CREATE n'introduit pas de lacune de
    # couverture du parent — l'évaluer dans ces cas génère surtout des faux
    # positifs (le LLM juge le parent « incomplet » de façon instable).
    if ctx.action.action_type != ActionType.DELETE:
        return []
    parent_id = ctx.parent_id
    if not parent_id:
        return []
    tree = ctx.candidate
    parent = tree.get(parent_id)
    if not parent:
        return []
    children = tree.children(parent_id)
    if not children:
        sev = Severity.BLOCKING if ctx.action.action_type == ActionType.DELETE else Severity.WARNING
        return [Finding(analyzer="couverture", scope=Scope.COUVERTURE, severity=sev,
                        message=f"{parent.id} n'a plus aucune fille : déclinaison perdue.",
                        impacted_ids=[parent.id])]
    payload = {"exigence_parent": parent.short(),
               "exigences_filles": [c.short() for c in children]}
    resp = llm.call_skill("couverture_amont", payload)
    if resp.get("error"):
        return [_skip(Scope.COUVERTURE, "couverture", [parent_id], resp["error"])]
    gaps = _norm_ids(resp.get("concepts_non_couverts")) or list(resp.get("concepts_non_couverts") or [])
    complete = resp.get("est_complet", not gaps)
    if complete:
        return [Finding(analyzer="couverture", scope=Scope.COUVERTURE, severity=Severity.INFO,
                        message=resp.get("synthese") or f"{parent.id} reste entièrement couvert par ses filles.",
                        impacted_ids=[parent.id], details={"raw": resp})]

    # Delta-aware : si la lacune existait DÉJÀ avant l'action, on ne l'impute pas
    # à cette édition (sinon faux positif systématique). On vérifie l'état d'avant.
    before = ctx.current.get(parent_id)
    if before:
        children_before = ctx.current.children(parent_id)
        if children_before:
            resp_b = llm.call_skill("couverture_amont", {
                "exigence_parent": before.short(),
                "exigences_filles": [c.short() for c in children_before]})
            if not resp_b.get("error") and resp_b.get("est_complet") is False:
                return [Finding(
                    analyzer="couverture", scope=Scope.COUVERTURE, severity=Severity.INFO,
                    message=f"Lacune de couverture préexistante sur {parent.id} (non aggravée par cette action).",
                    impacted_ids=[parent.id], details={"preexistante": True, "raw": resp})]

    sev = Severity.BLOCKING if ctx.action.action_type == ActionType.DELETE else Severity.WARNING
    base = (resp.get("synthese") or f"{parent.id} n'est plus entièrement couvert.") \
        + (f" Concepts non couverts : {', '.join(map(str, gaps))}." if gaps else "")
    return [Finding(
        analyzer="couverture", scope=Scope.COUVERTURE, severity=sev,
        message=_with_preuve(base, resp),
        impacted_ids=[parent.id], details={"concepts_non_couverts": gaps,
                                            "preuve": resp.get("preuve", ""), "raw": resp})]


# --------------------------------------------------------------------------
# T3 — Redondance / sur-spécification (LLM)
# --------------------------------------------------------------------------
def analyze_redondance(ctx: Ctx) -> List[Finding]:
    action, tree = ctx.action, ctx.candidate
    if action.action_type == ActionType.DELETE:
        return []
    target = tree.get(action.target_id)
    if not target:
        return []
    siblings = tree.siblings(action.target_id)
    if not siblings:
        return [Finding(analyzer="redondance", scope=Scope.HORIZONTAL, severity=Severity.INFO,
                        message="Aucune sœur : pas de redondance possible.", impacted_ids=[target.id])]

    # Routeur embeddings (déterministe, rapide) : on ne déclenche le LLM que dans
    # la zone ambiguë. Doublon clair -> BLOQUANT ; clairement distinct -> INFO.
    from . import embeddings
    if embeddings.embeddings_available():
        top = embeddings.most_similar(target.texte, [(s.id, s.texte) for s in siblings])
        if top:
            sib_id, score = top

            def _route(decision: str) -> None:
                llm.trace_event("routeur_embeddings",
                                {"cible": target.id, "n_soeurs": len(siblings)},
                                {"plus_proche": sib_id, "similarite": round(score, 3),
                                 "decision": decision})

            if score >= EMBED_DUP_THRESHOLD:
                _route("doublon clair → BLOQUANT (tranché sans LLM)")
                return [Finding(
                    analyzer="redondance", scope=Scope.HORIZONTAL, severity=Severity.BLOCKING,
                    message=f"Redondante avec {sib_id} : énoncés quasi identiques (similarité {score:.2f}).",
                    impacted_ids=[target.id, sib_id],
                    details={"method": "embedding", "similarity": round(score, 3), "sibling": sib_id})]
            if score < EMBED_DISTINCT_THRESHOLD:
                _route("clairement distinct → INFO (tranché sans LLM)")
                return [Finding(
                    analyzer="redondance", scope=Scope.HORIZONTAL, severity=Severity.INFO,
                    message=f"Apporte une couverture nouvelle (distincte des sœurs, similarité max {score:.2f}).",
                    impacted_ids=[target.id],
                    details={"method": "embedding", "similarity": round(score, 3)})]
            # zone ambiguë -> on laisse le LLM trancher ci-dessous
            _route("zone ambiguë → escalade à l'agent LLM")

    parent = tree.get(target.parent_id) if target.parent_id else None
    payload = {"exigence_cible": target.short(),
               "exigences_soeurs": [s.short() for s in siblings],
               "exigence_parent": parent.short() if parent else None}
    resp = llm.call_skill("redondance_surspec", payload)
    if resp.get("error"):
        return [_skip(Scope.HORIZONTAL, "redondance", [target.id], resp["error"])]
    redundant = bool(resp.get("est_redondante"))
    overspec = bool(resp.get("est_sur_specifiee"))
    default = Severity.BLOCKING if redundant else (Severity.WARNING if overspec else Severity.INFO)
    sev = _sev_from(resp, default)
    conflicts = _norm_ids(resp.get("soeurs_en_conflit"))
    base = resp.get("synthese") or ("Redondante avec une sœur." if redundant
            else "Sur-spécification (aucun aspect nouveau)." if overspec
            else "Apporte une couverture nouvelle, pas de redondance.")
    return [Finding(
        analyzer="redondance", scope=Scope.HORIZONTAL, severity=sev,
        message=_with_preuve(base, resp),
        impacted_ids=[target.id, *conflicts],
        details={"est_redondante": redundant, "est_sur_specifiee": overspec,
                 "preuve": resp.get("preuve", ""), "raw": resp})]


SEMANTIC_ANALYZERS = [analyze_pertinence, analyze_couverture, analyze_redondance]
DETERMINISTIC_ANALYZERS = [analyze_allocation, analyze_downstream]
