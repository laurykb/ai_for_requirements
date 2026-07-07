"""Génération descendante d'exigences filles L(n) -> L(n+1).

Depuis une exigence mère sélectionnée, l'agent ``generation_filles`` propose
2 à 7 filles (bornes aussi côté code), insérées sur une COPIE du corpus puis
auto-auditées (débat contradictoire inclus) ; les filles encore signalées sont
réécrites via la boucle de correction en lot (max 2 passes). Le corpus réel
n'est JAMAIS muté ici : la validation sélective (``/generate/children/apply``)
crée ensuite réellement les filles cochées, avec leur lien DERIVE (parent_id).
Un seul niveau à la fois — pas de cascade.
"""

from __future__ import annotations

import re
import threading
from typing import Callable, List, Optional

from . import llm
from .autofix import BatchCancelled, run_batch_fix
from .config import MAX_NIVEAU, SKILLS_DIR
from .redaction import _rules_text  # même référentiel que l'agent de rédaction
from .tree import RequirementTree

# Bornes du nombre de filles proposées (le prompt les annonce aussi).
MIN_FILLES, MAX_FILLES = 2, 7

# Convention d'ids du corpus de démo : REQ-L<niveau>-<DOMAINE>-<numéro>.
_ID_CONVENTION = re.compile(r"^(?P<prefixe>.+-)L(?P<niveau>\d)-(?P<code>[A-Z0-9]+)-(?P<num>\d+)$")


def _prompt() -> str:
    template = (SKILLS_DIR / "generation_filles.md").read_text(encoding="utf-8")
    return template.replace("<<REGLES>>", _rules_text())


def _child_ids(mother_id: str, niveau_fille: int, n: int, existants: set) -> List[str]:
    """Ids des filles selon la convention du corpus courant (repli : GEN)."""
    m = _ID_CONVENTION.match(mother_id or "")
    if m:
        base = f"{m.group('prefixe')}L{niveau_fille}-{m.group('code')}-"
        width = len(m.group("num"))
    else:
        base, width = f"REQ-L{niveau_fille}-GEN-", 3
    out: List[str] = []
    num = 1
    while len(out) < n:
        cand = f"{base}{num:0{width}d}"
        if cand not in existants:
            out.append(cand)
            existants.add(cand)
        num += 1
    return out


def generate_children(corpus: List[dict], req_id: str,
                      on_progress: Optional[Callable[[dict], None]] = None,
                      cancelled: Optional[threading.Event] = None) -> dict:
    """Propose des filles auto-auditées pour ``req_id`` (sans muter ``corpus``).

    ``on_progress(info)`` : ``{phase: generation|audit|reecriture, ...}``.
    Renvoie ``{filles: [{id_propose, texte, justification, aspect_couvert,
    findings_restants, statut}], aspects_non_couverts, niveau_filles}``
    ou ``{error}`` (mère introuvable, niveau plancher L5, réponse inexploitable).
    """
    def emit(info: dict) -> None:
        if cancelled is not None and cancelled.is_set():
            raise BatchCancelled()
        if on_progress is not None:
            on_progress(info)

    try:
        tree = RequirementTree([dict(r) for r in corpus])
    except ValueError as exc:
        return {"error": f"Corpus invalide : {exc}"}
    mere = tree.get(req_id)
    if mere is None:
        return {"error": f"Exigence introuvable : {req_id}"}
    if mere.niveau >= MAX_NIVEAU:
        return {"error": f"Niveau plancher atteint (L{MAX_NIVEAU}) : "
                         f"une exigence L{MAX_NIVEAU} ne se décline pas."}
    niveau_fille = mere.niveau + 1

    filles_existantes = tree.children(req_id)
    payload = {
        "exigence_mere": mere.short(),
        "niveau_filles": niveau_fille,
        "ancetres": [a.short() for a in tree.ancestors(req_id)],
        "soeurs_de_la_mere": [s.short() for s in tree.siblings(req_id)],
        # Les filles existantes sont fournies pour éviter toute redondance.
        "filles_existantes": [c.short() for c in filles_existantes],
    }
    emit({"phase": "generation", "req_id": req_id})
    resp = llm.call_agent(_prompt(), payload, label="generation_filles")
    if resp.get("error"):
        return {"error": resp["error"], "detail": resp.get("detail", "")}
    proposees = [f for f in (resp.get("filles") or [])
                 if (f.get("texte") or "").strip()][:MAX_FILLES]
    if len(proposees) < MIN_FILLES:
        return {"error": f"L'agent n'a proposé que {len(proposees)} fille(s) "
                         f"exploitables (minimum {MIN_FILLES})."}

    # Insertion sur une COPIE : le corpus réel reste intact.
    existants = {r.get("id") for r in corpus}
    ids = _child_ids(mere.id, niveau_fille, len(proposees), existants)
    copie = [dict(r) for r in corpus]
    par_id = {}
    for fid, f in zip(ids, proposees):
        par_id[fid] = f
        copie.append({
            "id": fid, "niveau": niveau_fille, "type": "Exigence",
            "domaine": getattr(mere, "domaine", None) or "Général",
            "texte": f["texte"].strip(), "parent_id": req_id,
            "test_status": "PENDING"})

    # Auto-audit de la copie (débat contradictoire inclus), puis réécriture
    # des filles signalées — même boucle que la correction en lot, bornée à
    # 2 passes et restreinte aux filles générées.
    from .audit import audit_matrix
    emit({"phase": "audit", "passe": 0, "done": 0, "total": len(copie)})
    rapport = audit_matrix(copie, deep=True, on_event=lambda done, total: emit(
        {"phase": "audit", "passe": 0, "done": done, "total": total}))
    a_corriger = [f for f in rapport.findings
                  if f.req_id in par_id and f.severity in ("BLOQUANT", "WARNING")]
    recap_fix = {}
    if a_corriger:
        out = run_batch_fix(
            copie, a_corriger, max_passes=2, deep=True,
            on_progress=lambda info: emit(
                {**info, "phase": "reecriture" if info.get("phase") == "correction"
                 else "audit"}),
            cancelled=cancelled)
        recap_fix = {it["req_id"]: it for it in out["recap"]}

    filles = []
    for fid, f in par_id.items():
        it = recap_fix.get(fid)
        if it is None:
            restants, statut, texte = [], "conforme", f["texte"].strip()
        else:
            restants, statut, texte = it["findings_apres"], it["statut"], it["texte_apres"]
        filles.append({
            "id_propose": fid, "texte": texte,
            "justification": f.get("justification", ""),
            "aspect_couvert": f.get("aspect_couvert", ""),
            "findings_restants": restants, "statut": statut})
    return {"filles": filles,
            "aspects_non_couverts": [str(a) for a in (resp.get("aspects_non_couverts") or [])],
            "niveau_filles": niveau_fille, "mere": mere.short()}
