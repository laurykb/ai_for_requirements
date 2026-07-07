"""Débat contradictoire sur les verdicts BLOQUANT sémantiques.

Avant de rendre un BLOQUANT issu d'un agent LLM, un « avocat de la défense »
tente de le réfuter à partir du contexte de traçabilité, puis un « juge »
tranche : MAINTENU ou RETROGRADE (le constat passe alors en WARNING — il n'est
JAMAIS supprimé). Fail-safe : toute erreur LLM pendant le débat conserve le
BLOQUANT initial.

Ne concerne QUE les verdicts sémantiques : les constats factuels (DOUBLON,
LIEN, CYCLE, ALLOCATION, doublons par embeddings) ne se plaident pas — les
appelants ne soumettent au débat que les constats issus d'un appel LLM.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from . import llm
from .config import DEBATE_ENABLED
from .tree import RequirementTree


def build_context(tree: RequirementTree, req_id: str) -> Dict[str, Any]:
    """Contexte d'arbre de l'exigence accusée (pattern de ``suggest_correction``)."""
    req = tree.get(req_id)
    if req is None:
        return {"exigence": {"id": req_id}, "parent": None,
                "ancetres": [], "soeurs": [], "filles": []}
    parent = tree.get(req.parent_id) if req.parent_id else None
    return {
        "exigence": req.short(),
        "parent": parent.short() if parent else None,
        "ancetres": [a.short() for a in tree.ancestors(req_id)],
        "soeurs": [s.short() for s in tree.siblings(req_id)],
        "filles": [c.short() for c in tree.children(req_id)],
    }


def contest_blocking(finding_msg: str, req_context: Dict[str, Any]) -> Dict[str, Any]:
    """Débat avocat → juge sur une accusation BLOQUANT.

    Renvoie ``{statut: "MAINTENU"|"RETROGRADE", plaidoyer, jugement}``
    (+ ``erreur`` si un fail-safe a joué). Les deux appels passent par
    ``llm.call_skill`` : le débat est tracé dans la boîte de verre.
    """
    defense = llm.call_skill("defense_exigence",
                             {**req_context, "accusation": finding_msg})
    if defense.get("error"):
        return {"statut": "MAINTENU", "plaidoyer": "", "jugement": "",
                "erreur": f"avocat : {defense['error']}"}
    plaidoyer = (defense.get("plaidoyer") or "").strip()
    if not defense.get("refutation_possible", False):
        # L'avocat lui-même ne voit pas de réfutation fondée : inutile de
        # déranger le juge, le verdict est maintenu (trace conservée).
        return {"statut": "MAINTENU", "plaidoyer": plaidoyer,
                "jugement": "L'avocat ne conteste pas l'accusation."}
    jugement = llm.call_skill("juge_verdict", {
        "accusation": finding_msg,
        "plaidoyer": plaidoyer,
        "arguments": defense.get("arguments") or [],
        "elements_contexte": defense.get("elements_contexte") or [],
    })
    if jugement.get("error"):
        return {"statut": "MAINTENU", "plaidoyer": plaidoyer, "jugement": "",
                "erreur": f"juge : {jugement['error']}"}
    statut = "RETROGRADE" if jugement.get("verdict") == "RETROGRADE" else "MAINTENU"
    return {"statut": statut, "plaidoyer": plaidoyer,
            "jugement": (jugement.get("motivation") or "").strip()}


def contest(finding, tree: Optional[RequirementTree], req_id: str):
    """Soumet un constat BLOQUANT sémantique au débat, IN PLACE.

    - ``finding`` : ``models.Finding`` ou ``audit.MatrixFinding`` (duck typing :
      champs ``severity``, ``message``, ``debate``) ;
    - RETROGRADE -> sévérité WARNING (``BLOCKING``/``BLOQUANT`` selon l'objet) ;
    - le champ ``debate`` {statut, plaidoyer, jugement} est attaché dans tous
      les cas (badge « contesté » dans l'UI). Sans arbre ou débat désactivé :
      constat rendu tel quel.
    """
    sev = getattr(finding.severity, "value", finding.severity)
    if not DEBATE_ENABLED or tree is None or sev not in ("BLOCKING", "BLOQUANT"):
        return finding
    outcome = contest_blocking(finding.message, build_context(tree, req_id))
    if outcome["statut"] == "RETROGRADE":
        # Severity (Enum, analyzers) ou str FR (audit) : on reste dans le type.
        finding.severity = type(finding.severity)("WARNING")
    finding.debate = outcome
    return finding
