"""Suggestion de correction d'exigence (détection → proposition).

À partir d'une exigence signalée, de son contexte de traçabilité (parent, ancêtres,
sœurs) et des problèmes détectés, propose une réécriture conforme et cohérente.
L'ingénieur peut l'appliquer en un clic : elle repasse alors par l'analyse d'impact
comme une action UPDATE, ce qui vérifie que la correction ne casse rien en aval.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import llm
from .config import SKILLS_DIR
from .redaction import _rules_text  # règles EN9100 distillées, injectées au prompt
from .tree import RequirementTree


def _prompt() -> str:
    template = (SKILLS_DIR / "suggest_correction.md").read_text(encoding="utf-8")
    return template.replace("<<REGLES>>", _rules_text())


def suggest_correction(corpus: List[dict], req_id: str,
                       problems: Optional[List[str]] = None) -> Dict[str, Any]:
    """Propose une réécriture de ``req_id`` corrigeant ``problems``.

    Renvoie ``{texte, justification, changements, corrige_tout}`` ou ``{error}``.
    Ne mute pas ``corpus``.
    """
    try:
        tree = RequirementTree(corpus)
    except ValueError as exc:
        return {"error": f"Corpus invalide : {exc}"}
    req = tree.get(req_id)
    if req is None:
        return {"error": f"Exigence introuvable : {req_id}"}
    parent = tree.get(req.parent_id) if req.parent_id else None
    payload = {
        "exigence": req.short(),
        "parent": parent.short() if parent else None,
        "ancetres": [a.short() for a in tree.ancestors(req_id)],
        "soeurs": [s.short() for s in tree.siblings(req_id)],
        "filles": [c.short() for c in tree.children(req_id)],
        "problemes_detectes": list(problems or []),
    }
    resp = llm.call_agent(_prompt(), payload, label="suggest_correction")
    if resp.get("error"):
        return {"error": resp.get("error"), "detail": resp.get("detail", "")}
    texte = (resp.get("texte_propose") or "").strip()
    if not texte:
        return {"error": "Réponse vide de l'agent."}
    return {
        "texte": texte,
        "justification": resp.get("justification", ""),
        "changements": resp.get("changements") or [],
        "corrige_tout": bool(resp.get("corrige_tout", False)),
        "raw": resp,
    }
