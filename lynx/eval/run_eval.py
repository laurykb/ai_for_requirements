"""Évaluation rigoureuse des analyseurs d'impact (jeu élargi, multi-corpus).

Deux sources de cas :
- 6 cas de référence sur le corpus propre ``eval/corpus_eval.json`` ;
- les cas générés (par construction, label connu) dans ``eval/cases_generated.json``,
  chacun portant son propre corpus.

Chaque cas applique une action et porte un label ``expected`` (axes devant
produire WARNING/BLOQUANT). On mesure précision / rappel / F1 par axe — la
PRÉCISION d'abord (un faux positif détruit la confiance).

Usage :
    python -m eval.run_eval            # complet (LLM)
    python -m eval.run_eval --fast     # déterministe seul
"""

import json
import sys
from pathlib import Path

from src.models import Action, ActionType, Severity
from src.orchestrator import run_impact_analysis

_DIR = Path(__file__).parent
DEFAULT_CORPUS = json.loads((_DIR / "corpus_eval.json").read_text(encoding="utf-8"))["exigences"]
AXES = ["ALLOCATION", "AMONT", "COUVERTURE", "HORIZONTAL"]

# Cas de référence (sur le corpus propre par défaut).
REFERENCE_CASES = [
    {"name": "clean_update", "expected": [],
     "action": {"action_type": "UPDATE", "target_id": "E-L2-CAM",
                "new_text": "La charge utile doit fournir une caméra de jour d'une résolution d'au moins 4 mégapixels."}},
    {"name": "alloc_overflow", "expected": ["ALLOCATION"],
     "action": {"action_type": "UPDATE", "target_id": "E-L3-CELL",
                "new_text": "Le pack de cellules lithium-ion a une masse mesurée à 4.5 kg."}},
    {"name": "redondance", "expected": ["HORIZONTAL"],
     "action": {"action_type": "CREATE", "target_id": "E-NEW-DUP", "parent_id": "E-L1-PAY", "niveau": 2,
                "new_text": "La charge utile doit fournir une caméra de jour d'au moins 4 mégapixels."}},
    {"name": "incoherence_amont", "expected": ["AMONT"],
     "action": {"action_type": "UPDATE", "target_id": "E-L1-END",
                "new_text": "Le drone doit limiter son endurance de vol à 30 minutes maximum."}},
    {"name": "clean_create", "expected": [],
     "action": {"action_type": "CREATE", "target_id": "E-NEW-LRF", "parent_id": "E-L1-PAY", "niveau": 2,
                "new_text": "La charge utile doit intégrer un télémètre laser pour mesurer la distance à la cible."}},
    {"name": "couverture_delete", "expected": ["COUVERTURE"],
     "action": {"action_type": "DELETE", "target_id": "E-L2-IR"}},
]


def validate_case(case) -> bool:
    """Garde-fou structurel : un cas mal formé est écarté (label non fiable)."""
    exp = case.get("expected")
    if not isinstance(exp, list) or any(x not in AXES for x in exp):
        return False
    corpus = case.get("corpus") or DEFAULT_CORPUS
    ids = {r.get("id") for r in corpus}
    if len(ids) != len(corpus):
        return False
    for r in corpus:
        pid = r.get("parent_id")
        if pid and pid not in ids:
            return False
        if not isinstance(r.get("niveau"), int) or not (0 <= r["niveau"] <= 5):
            return False
    a = case.get("action") or {}
    at, tid = a.get("action_type"), a.get("target_id")
    if at in ("UPDATE", "DELETE"):
        return tid in ids
    if at == "CREATE":
        return tid not in ids and (a.get("parent_id") in ids if a.get("parent_id") else True)
    return False


def _flagged_axes(report) -> set:
    return {f.scope.value for f in report.findings
            if f.severity in (Severity.WARNING, Severity.BLOCKING)} & set(AXES)


def load_cases():
    cases = list(REFERENCE_CASES)
    gen = _DIR / "cases_generated.json"
    if gen.exists():
        data = json.loads(gen.read_text(encoding="utf-8"))
        cases += data.get("cases", data if isinstance(data, list) else [])
    valid = [c for c in cases if validate_case(c)]
    return valid, len(cases) - len(valid)


def main():
    semantic = "--fast" not in sys.argv
    cases, dropped = load_cases()
    tp = {a: 0 for a in AXES}; fp = {a: 0 for a in AXES}; fn = {a: 0 for a in AXES}

    print(f"Éval sur {len(cases)} cas valides ({dropped} écartés) · "
          f"mode {'complet (LLM)' if semantic else 'rapide'}\n")
    for case in cases:
        corpus = [dict(r) for r in (case.get("corpus") or DEFAULT_CORPUS)]
        action = Action(**case["action"])
        try:
            report = run_impact_analysis(corpus, action, semantic=semantic)
        except Exception as exc:
            print(f"  ! {case['name']}: erreur {exc}")
            continue
        got, exp = _flagged_axes(report), set(case["expected"]) & set(AXES)
        for a in AXES:
            if a in exp and a in got:
                tp[a] += 1
            elif a in exp:
                fn[a] += 1
            elif a in got:
                fp[a] += 1

    print("{:12} {:>5} {:>5} {:>5} {:>7} {:>7} {:>6}".format("AXE", "TP", "FP", "FN", "Préc.", "Rappel", "F1"))
    print("-" * 52)
    mtp = mfp = mfn = 0
    for a in AXES:
        p = tp[a] / (tp[a] + fp[a]) if (tp[a] + fp[a]) else 1.0
        r = tp[a] / (tp[a] + fn[a]) if (tp[a] + fn[a]) else 1.0
        f1 = 2 * p * r / (p + r) if (p + r) else 0.0
        mtp += tp[a]; mfp += fp[a]; mfn += fn[a]
        print(f"{a:12} {tp[a]:>5} {fp[a]:>5} {fn[a]:>5} {p:>7.2f} {r:>7.2f} {f1:>6.2f}")
    P = mtp / (mtp + mfp) if (mtp + mfp) else 1.0
    R = mtp / (mtp + mfn) if (mtp + mfn) else 1.0
    F1 = 2 * P * R / (P + R) if (P + R) else 0.0
    print("-" * 52)
    print(f"{'micro':12} {mtp:>5} {mfp:>5} {mfn:>5} {P:>7.2f} {R:>7.2f} {F1:>6.2f}")
    print(f"\nPrécision micro = {P:.2f} · Rappel micro = {R:.2f} · F1 = {F1:.2f}")
    # Écrit un résumé exploitable par l'UI (taux de justesse affiché).
    (_DIR / "last_eval.json").write_text(json.dumps({
        "cases": len(cases), "precision": round(P, 3), "recall": round(R, 3), "f1": round(F1, 3),
        "per_axis": {a: {"precision": round(tp[a] / (tp[a] + fp[a]), 3) if (tp[a] + fp[a]) else 1.0,
                         "recall": round(tp[a] / (tp[a] + fn[a]), 3) if (tp[a] + fn[a]) else 1.0}
                     for a in AXES}}, ensure_ascii=False, indent=2), encoding="utf-8")
    if semantic and P < 0.85:
        print("ATTENTION : précision sous 0.85 (priorité : réduire les faux positifs).")
        sys.exit(1)
    print("OK.")


if __name__ == "__main__":
    main()
