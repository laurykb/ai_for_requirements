"""Analyseurs d'impact.

Chaque analyseur reçoit un ``Ctx`` (arbre courant, arbre candidat = après action,
et l'action) et renvoie une liste de ``Finding``.

Déterministes :
  - allocation : roll-up budgétaire (somme des enfants vs plafond du parent)
  - aval       : descendants impactés (orphelins, re-test)
Sémantiques (agents LLM) :
  - pertinence (T1) : la cible reste-t-elle cohérente/pertinente vs ses ancêtres ?
  - couverture (T2) : le parent reste-t-il entièrement couvert par ses filles ?
  - redondance (T3) : la cible est-elle redondante / sur-spécifiée vs ses sœurs ?
  - pertinence aval (T4) : la cible reste-t-elle cohérente/pertinente vs ses filles ?
  - impact latent : des exigences non reliées sont-elles sémantiquement impactées ?
  - co-références : les exigences partageant un référent concret restent-elles cohérentes ?
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from . import debate, embeddings, llm
from .config import (ALLOCATION_TOLERANCE, EMBED_DISTINCT_THRESHOLD, EMBED_DUP_THRESHOLD,
                     EMBED_LATENT_THRESHOLD, LATENT_TOPK, LLM_VOTE)
from .extract import allocation_rollup, extract_quantities, from_base
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
    # ancêtres connus via l'arbre courant (valable même au DELETE)
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
        # Ne compter que les votes exploitables : un échantillon en erreur
        # (dict {"error":...} sans est_coherent) ne doit pas être compté comme
        # « cohérent » et diluer l'incohérence au point de rétrograder à tort.
        valid = [v for v in votes if isinstance(v, dict) and "error" not in v and "est_coherent" in v]
        incoh = sum(1 for v in valid if v.get("est_coherent") is False)
        if valid and incoh <= len(valid) // 2:
            sev = Severity.WARNING
            base += f" (rétrogradé : incohérence non confirmée par vote {incoh}/{len(valid)})"
    # Débat contradictoire après le vote, sur le verdict consolidé.
    return [debate.contest(Finding(
        analyzer="pertinence", scope=Scope.AMONT, severity=sev,
        message=_with_preuve(base, resp),
        impacted_ids=[target.id, *rupture], details={"preuve": resp.get("preuve", ""), "raw": resp}),
        tree, target.id)]


# --------------------------------------------------------------------------
# T2 — Couverture amont / complétude (LLM)
# --------------------------------------------------------------------------
def analyze_couverture(ctx: Ctx) -> List[Finding]:
    # La couverture d'un parent n'est réellement menacée que par une suppression
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

    # Delta-aware : si la lacune existait déjà avant l'action, on ne l'impute pas
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
    return [debate.contest(Finding(
        analyzer="couverture", scope=Scope.COUVERTURE, severity=sev,
        message=_with_preuve(base, resp),
        impacted_ids=[parent.id], details={"concepts_non_couverts": gaps,
                                            "preuve": resp.get("preuve", ""), "raw": resp}),
        tree, parent.id)]


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
    # Débat sur le verdict LLM uniquement (le routeur embeddings, factuel,
    # rend ses BLOQUANT plus haut sans passer ici).
    return [debate.contest(Finding(
        analyzer="redondance", scope=Scope.HORIZONTAL, severity=sev,
        message=_with_preuve(base, resp),
        impacted_ids=[target.id, *conflicts],
        details={"est_redondante": redundant, "est_sur_specifiee": overspec,
                 "preuve": resp.get("preuve", ""), "raw": resp}),
        tree, target.id)]


# --------------------------------------------------------------------------
# T4 — Pertinence / cohérence vs filles / déclinaison aval (LLM)
# --------------------------------------------------------------------------
def analyze_pertinence_aval(ctx: Ctx) -> List[Finding]:
    action, tree = ctx.action, ctx.candidate
    if action.action_type == ActionType.DELETE:
        return []
    target = tree.get(action.target_id)
    if not target:
        return []
    children = tree.children(action.target_id)
    # Miroir du T1 côté aval : sans fille, il n'y a pas de déclinaison à contrôler.
    if not children:
        return []
    payload = {"exigence_cible": target.short(),
               "exigences_filles": [c.short() for c in children]}
    resp = llm.call_skill("coherence_pertinence_aval", payload)
    if resp.get("error"):
        return [_skip(Scope.PERTINENCE_AVAL, "pertinence_aval", [c.id for c in children], resp["error"])]
    coherent = resp.get("est_coherent", True)
    sev = _sev_from(resp, Severity.INFO if coherent else Severity.WARNING)
    rupture = _norm_ids(resp.get("rupture_avec"))
    base = resp.get("synthese") or ("Déclinaison aval cohérente." if coherent
                                    else "Rupture de pertinence en aval.")

    # Delta-aware (même principe que analyze_couverture) : si la rupture aval
    # existait déjà avant l'action (jugée sur l'ancien texte de la cible), on ne
    # l'impute pas à cette édition — sinon chaque UPDATE re-signale un état
    # ancien (faux positifs mesurés). Ne s'applique que si le texte a changé.
    if not coherent:
        before = ctx.current.get(action.target_id)
        if before and before.texte.strip() and before.texte != target.texte:
            children_before = ctx.current.children(action.target_id)
            if children_before:
                resp_b = llm.call_skill("coherence_pertinence_aval", {
                    "exigence_cible": before.short(),
                    "exigences_filles": [c.short() for c in children_before]})
                if not resp_b.get("error") and resp_b.get("est_coherent") is False:
                    return [Finding(
                        analyzer="pertinence_aval", scope=Scope.PERTINENCE_AVAL,
                        severity=Severity.INFO,
                        message=f"Rupture aval préexistante sur {target.id} "
                                f"(non aggravée par cette action).",
                        impacted_ids=[target.id],
                        details={"preexistante": True, "raw": resp})]

    # Vote self-consistency sur un BLOQUANT à fort enjeu (comme le T1 amont).
    if sev == Severity.BLOCKING and LLM_VOTE > 1:
        votes = llm.sample_skill("coherence_pertinence_aval", payload, n=LLM_VOTE)
        valid = [v for v in votes if isinstance(v, dict) and "error" not in v and "est_coherent" in v]
        incoh = sum(1 for v in valid if v.get("est_coherent") is False)
        if valid and incoh <= len(valid) // 2:
            sev = Severity.WARNING
            base += f" (rétrogradé : incohérence non confirmée par vote {incoh}/{len(valid)})"
    # Débat contradictoire après le vote, sur le verdict consolidé.
    return [debate.contest(Finding(
        analyzer="pertinence_aval", scope=Scope.PERTINENCE_AVAL, severity=sev,
        message=_with_preuve(base, resp),
        impacted_ids=[target.id, *rupture], details={"preuve": resp.get("preuve", ""), "raw": resp}),
        tree, target.id)]


# --------------------------------------------------------------------------
# Impact latent — exigences non reliées mais sémantiquement impactées (embeddings + LLM)
# --------------------------------------------------------------------------
def _neighbourhood(tree: RequirementTree, req_id: str) -> set:
    """Ids déjà couverts par les autres axes (voisinage direct), à exclure du scan trans-matrice."""
    ids = {req_id}
    for grp in (tree.ancestors(req_id), tree.descendants(req_id),
                tree.siblings(req_id), tree.children(req_id)):
        ids |= {r.id for r in grp}
    return ids


def analyze_impact_latent(ctx: Ctx) -> List[Finding]:
    action, tree = ctx.action, ctx.candidate
    if action.action_type == ActionType.DELETE:
        return []
    target = tree.get(action.target_id)
    if not target or not target.texte.strip():
        return []
    # Le routeur est toujours tracé (même à vide) pour rester visible dans la boîte de verre.
    def _route(retenus, sims, note=None):
        out = {"retenus": retenus, "similarites": sims}
        if note:
            out["note"] = note
        llm.trace_event("routeur_impact_latent", {"cible": target.id}, out)

    if not embeddings.embeddings_available():
        _route([], [], note="embeddings indisponibles")
        return []
    excluded = _neighbourhood(tree, target.id)
    candidates = [r for r in tree.all() if r.id not in excluded and r.texte.strip()]
    if not candidates:
        _route([], [], note="aucune exigence non reliée à examiner")
        return []
    # Pré-filtre embeddings : ne garder que la zone « proche mais non reliée ».
    vecs = embeddings.get_embeddings([target.texte] + [c.texte for c in candidates])
    if not vecs or vecs[0] is None:
        _route([], [], note="embeddings indisponibles")
        return []
    sims = embeddings.similarities_to(vecs[0], vecs[1:])
    scored = [(c, s) for c, s in zip(candidates, sims) if s >= EMBED_LATENT_THRESHOLD]
    if not scored:
        _route([], [], note=f"aucune exigence proche parmi {len(candidates)} (seuil {EMBED_LATENT_THRESHOLD})")
        return []
    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[:LATENT_TOPK]
    _route([c.id for c, _ in top], [round(s, 3) for _, s in top])
    payload = {"exigence_modifiee": target.short(),
               "exigences_proches": [c.short() for c, _ in top]}
    resp = llm.call_skill("impact_latent", payload)
    if resp.get("error"):
        return [_skip(Scope.IMPACT_LATENT, "impact_latent", [c.id for c, _ in top], resp["error"])]
    hit_ids = _norm_ids(resp.get("impactees"))
    if not hit_ids:
        return [Finding(analyzer="impact_latent", scope=Scope.IMPACT_LATENT, severity=Severity.INFO,
                        message=(resp.get("synthese")
                                 or f"{len(top)} exigence(s) proche(s) examinée(s) : aucun impact latent."),
                        impacted_ids=[target.id], details={"examinees": [c.id for c, _ in top], "raw": resp})]
    sev = _sev_from(resp, Severity.WARNING)
    base = resp.get("synthese") or f"Impact latent possible sur des exigences non reliées : {', '.join(hit_ids)}."
    return [debate.contest(Finding(
        analyzer="impact_latent", scope=Scope.IMPACT_LATENT, severity=sev,
        message=_with_preuve(base, resp),
        impacted_ids=[target.id, *hit_ids], details={"impactees": resp.get("impactees"), "raw": resp}),
        tree, target.id)]


# --------------------------------------------------------------------------
# Cohérence des co-références — exigences partageant un référent concret (LLM)
# --------------------------------------------------------------------------
# Référent concret = acronyme / code technique (CAN, EMC, RS422, TRC7535, 28V…).
_REF_TOKEN = re.compile(r"\b(?:[A-Z]{2,}[0-9]*|[A-Za-z]*[0-9]+[A-Za-z]+|[A-Za-z]+[0-9]+)\b")


def _referents(text: str):
    """(tokens concrets, unités de grandeur) cités par une exigence."""
    toks = {t for t in _REF_TOKEN.findall(text or "") if len(t) >= 2}
    units = {q.unit for q in extract_quantities(text or "") if q.unit}
    return toks, units


def analyze_coreference(ctx: Ctx) -> List[Finding]:
    action, tree = ctx.action, ctx.candidate
    if action.action_type == ActionType.DELETE:
        return []
    target = tree.get(action.target_id)
    if not target or not target.texte.strip():
        return []
    t_toks, t_units = _referents(target.texte)
    refs = sorted(t_toks | t_units)
    if not refs:
        llm.trace_event("routeur_coreference", {"cible": target.id},
                        {"referents": [], "co_references": [], "note": "aucun référent concret dans l'énoncé"})
        return []
    # La chaîne verticale (ancêtres/descendants) est déjà jugée par les agents
    # amont/aval : la re-signaler ici dupliquerait le même défaut sous un autre
    # axe (faux positifs mesurés). La co-référence cherche les conflits
    # trans-branche — les sœurs et les exigences non reliées restent scannées.
    vertical = ({a.id for a in tree.ancestors(target.id)}
                | {d.id for d in tree.descendants(target.id)})
    shared = []
    for r in tree.all():
        if r.id == target.id or r.id in vertical or not r.texte.strip():
            continue
        r_toks, r_units = _referents(r.texte)
        common = (t_toks & r_toks) | (t_units & r_units)
        if common:
            shared.append((r, common))
    if not shared:
        llm.trace_event("routeur_coreference", {"cible": target.id, "referents": refs},
                        {"co_references": [], "note": "aucune exigence ne partage ces référents"})
        return []
    # Priorité aux référents les plus discriminants (tokens/codes avant unités).
    shared.sort(key=lambda x: (len(x[1] & t_toks), len(x[1])), reverse=True)
    top = shared[:LATENT_TOPK]
    llm.trace_event("routeur_coreference", {"cible": target.id, "referents": refs},
                    {"co_references": [r.id for r, _ in top]})
    payload = {"exigence_cible": target.short(),
               "co_references": [{"id": r.id, "niveau": r.niveau, "texte": r.texte,
                                  "referents_partages": sorted(common)} for r, common in top]}
    resp = llm.call_skill("coherence_coreference", payload)
    if resp.get("error"):
        return [_skip(Scope.COHERENCE_REF, "coreference", [r.id for r, _ in top], resp["error"])]
    if resp.get("coherent", True):
        return [Finding(analyzer="coreference", scope=Scope.COHERENCE_REF, severity=Severity.INFO,
                        message=(resp.get("synthese")
                                 or f"Cohérent avec {len(top)} exigence(s) partageant un référent."),
                        impacted_ids=[target.id], details={"examinees": [r.id for r, _ in top], "raw": resp})]
    conflicts = _norm_ids(resp.get("conflits"))
    sev = _sev_from(resp, Severity.BLOCKING)
    base = resp.get("synthese") or f"Incohérence de co-référence avec {', '.join(conflicts)}."
    return [debate.contest(Finding(
        analyzer="coreference", scope=Scope.COHERENCE_REF, severity=sev,
        message=_with_preuve(base, resp),
        impacted_ids=[target.id, *conflicts], details={"conflits": resp.get("conflits"), "raw": resp}),
        tree, target.id)]


SEMANTIC_ANALYZERS = [analyze_pertinence, analyze_couverture, analyze_redondance,
                      analyze_pertinence_aval, analyze_impact_latent, analyze_coreference]
DETERMINISTIC_ANALYZERS = [analyze_allocation, analyze_downstream]
