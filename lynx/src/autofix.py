"""Correction en lot depuis l'audit de la matrice (boucle corriger → ré-auditer).

Pour chaque exigence signalée par l'audit (BLOQUANT ou WARNING), demande une
réécriture à l'agent de rédaction (``suggest_correction``), l'applique à une
COPIE du corpus, puis ré-audite cette copie. Jusqu'à ``max_passes`` passes :
seules les exigences encore signalées sont retentées. Le corpus réel n'est
JAMAIS muté ici — la validation sélective (``/audit/fix/apply``) applique
ensuite au vrai corpus les seuls textes cochés par l'ingénieur.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional, Set

from .audit import audit_matrix
from .correction import suggest_correction

# Statuts finaux d'une exigence traitée par le lot.
STATUTS = ("corrigee", "amelioree", "recalcitrante", "echec_suggestion", "inchangee")


class BatchCancelled(Exception):
    """Le client a annulé : on abandonne la boucle (la copie est jetée)."""


def _check(cancelled: Optional[threading.Event]) -> None:
    if cancelled is not None and cancelled.is_set():
        raise BatchCancelled()


def _norm(f: Any) -> dict:
    """Constat → dict ``{req_id, axis, severity, message}`` (accepte MatrixFinding)."""
    if isinstance(f, dict):
        return {"req_id": f.get("req_id"), "axis": f.get("axis"),
                "severity": f.get("severity"), "message": f.get("message")}
    return {"req_id": f.req_id, "axis": f.axis,
            "severity": f.severity, "message": f.message}


def _actionable(findings) -> Dict[str, List[dict]]:
    """Constats BLOQUANT/WARNING groupés par exigence (INFO ignoré)."""
    out: Dict[str, List[dict]] = {}
    for f in map(_norm, findings or []):
        if f["severity"] in ("BLOQUANT", "WARNING") and f["req_id"]:
            out.setdefault(f["req_id"], []).append(f)
    return out


def _emit(on_progress: Optional[Callable[[dict], None]], info: dict) -> None:
    if on_progress is not None:
        on_progress(info)


def run_batch_fix(corpus: List[dict], findings, max_passes: int = 3,
                  on_progress: Optional[Callable[[dict], None]] = None,
                  cancelled: Optional[threading.Event] = None,
                  deep: bool = True, scope: Optional[Set[str]] = None) -> dict:
    """Corrige en lot les exigences signalées et renvoie un récapitulatif.

    - ``findings`` : constats de l'audit courant (dicts ou ``MatrixFinding``) ;
      seuls BLOQUANT et WARNING sont traités.
    - ``on_progress(info)`` : ``{phase: correction|audit, passe, done, total, req_id}``.
    - ``cancelled`` : évènement d'annulation (lève ``BatchCancelled``).
    - ``deep`` : profondeur du ré-audit entre les passes (aligner sur l'audit
      d'origine pour que le verdict soit comparable).

    Renvoie ``{recap: [...], compteurs: {...}, passes, score_apres}`` — le
    ``corpus`` d'entrée n'est pas modifié.
    """
    work = [dict(r) for r in corpus]
    by_id = {r.get("id"): r for r in work}
    # Constats courants par exigence (mis à jour à chaque ré-audit).
    courant = {rid: fs for rid, fs in _actionable(findings).items() if rid in by_id}

    etats: Dict[str, dict] = {}
    for rid, fs in courant.items():
        texte = by_id[rid].get("texte", "")
        etats[rid] = {"req_id": rid, "texte_avant": texte, "texte_apres": texte,
                      "findings_avant": fs, "findings_apres": fs,
                      "justification": "", "erreur": "", "statut": "", "passes": 0}

    exclues: set = set()   # échec LLM ou texte identique : on ne retente pas
    passes, score_apres = 0, None
    for passe in range(1, max(1, max_passes) + 1):
        cibles = [rid for rid in etats if rid in courant and rid not in exclues]
        if not cibles:
            break
        passes = passe
        total = len(cibles)
        for i, rid in enumerate(cibles, 1):
            _check(cancelled)
            _emit(on_progress, {"phase": "correction", "passe": passe,
                                "done": i, "total": total, "req_id": rid})
            res = suggest_correction(work, rid,
                                     [f["message"] for f in courant.get(rid, [])])
            et = etats[rid]
            et["passes"] = passe
            if res.get("error"):
                # Le lot continue : l'échec est consigné, l'exigence n'est plus retentée.
                et["erreur"] = str(res["error"])
                exclues.add(rid)
                continue
            texte = res["texte"]
            if texte == (by_id[rid].get("texte") or ""):
                exclues.add(rid)  # l'agent n'a rien de mieux à proposer
                continue
            by_id[rid]["texte"] = texte
            et["texte_apres"] = texte
            et["justification"] = res.get("justification", "")
        _check(cancelled)
        # Ré-audit de la copie : mêmes juges que l'audit d'origine.
        rep = audit_matrix(work, deep=deep, scope=scope, on_event=lambda done, tot, p=passe: _emit(
            on_progress, {"phase": "audit", "passe": p,
                          "done": done, "total": tot, "req_id": None}))
        _check(cancelled)
        score_apres = rep.score
        courant = _actionable(rep.findings)
        for rid, et in etats.items():
            et["findings_apres"] = courant.get(rid, [])
        if not any(rid in courant and rid not in exclues for rid in etats):
            break  # plus rien à retenter : convergence (ou tout est exclu)

    # Statuts finaux — priorité : rien proposé (échec/identique), puis verdict du ré-audit.
    for et in etats.values():
        apres = et["findings_apres"]
        if et["texte_apres"] == et["texte_avant"]:
            et["statut"] = "echec_suggestion" if et["erreur"] else "inchangee"
        elif any(f["severity"] == "BLOQUANT" for f in apres):
            et["statut"] = "recalcitrante"
        elif any(f["severity"] == "WARNING" for f in apres):
            et["statut"] = "amelioree"
        else:
            et["statut"] = "corrigee"

    recap = sorted(etats.values(), key=lambda e: e["req_id"])
    compteurs = {"total": len(recap),
                 "corrigees": sum(1 for e in recap if e["statut"] == "corrigee"),
                 "ameliorees": sum(1 for e in recap if e["statut"] == "amelioree"),
                 "recalcitrantes": sum(1 for e in recap if e["statut"] == "recalcitrante"),
                 "echecs": sum(1 for e in recap if e["statut"] == "echec_suggestion"),
                 "inchangees": sum(1 for e in recap if e["statut"] == "inchangee")}
    return {"recap": recap, "compteurs": compteurs,
            "passes": passes, "score_apres": score_apres}
