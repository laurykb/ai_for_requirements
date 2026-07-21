"""Évaluation de l'audit contre la vérité terrain (défauts plantés du corpus).

Le corpus généré documente ses défauts dans meta.defauts_plantes. On lance
l'audit et on mesure le rappel : chaque défaut planté est-il détecté ?

Usage :
    python eval_matrix.py                # audit complet (LLM)
    python eval_matrix.py --fast         # déterministe seul (structure + alloc)
    OLLAMA_MODEL=qwen3.5:latest python eval_matrix.py
"""

import json
import sys
import time

from src.audit import audit_matrix
from src.config import DEFAULT_CORPUS

# Type de défaut planté -> axes d'audit qui valent détection.
DEFECT_TO_AXES = {
    "lacune_couverture": {"COUVERTURE"},
    "redondance": {"REDONDANCE", "COUVERTURE"},
    "sur_specification": {"REDONDANCE", "COUVERTURE", "PERTINENCE"},
    "incoherence": {"PERTINENCE"},
    "depassement_budget": {"ALLOCATION"},
    "rupture_tracabilite": {"PERTINENCE", "LIEN"},
}


def main():
    fast = "--fast" in sys.argv
    try:
        raw = json.loads(DEFAULT_CORPUS.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"Corpus illisible ({DEFAULT_CORPUS}) : {exc}")
        return
    corpus = raw.get("exigences", [])
    planted = raw.get("meta", {}).get("defauts_plantes", [])
    if not planted:
        print("Aucun défaut planté documenté dans le corpus.")
        return

    print(f"Corpus : {len(corpus)} exigences · {len(planted)} défauts plantés · "
          f"mode {'rapide' if fast else 'complet (LLM)'}\n")
    t = time.time()
    rep = audit_matrix(corpus, deep=not fast)
    dt = time.time() - t

    # Index des findings par id et par axe.
    by_id: dict[str, set] = {}
    for f in rep.findings:
        by_id.setdefault(f.req_id, set()).add(f.axis)

    detected = 0
    print(f"{'DÉFAUT PLANTÉ':24} {'DÉTECTÉ':9} AXE(S) ATTENDU(S)")
    print("-" * 70)
    for d in planted:
        dtype = d.get("type", "")
        wanted_axes = DEFECT_TO_AXES.get(dtype, set())
        ids = d.get("ids", [])
        hit = any(wanted_axes & by_id.get(i, set()) for i in ids)
        # détection plus lenient : un finding quelconque sur un id concerné
        loose = any(i in by_id for i in ids)
        ok = hit or (loose and dtype in {"redondance", "sur_specification"})
        detected += int(ok)
        mark = "oui" if ok else ("partiel" if loose else "NON")
        print(f"{dtype:24} {mark:9} {','.join(sorted(wanted_axes))}")

    recall = detected / len(planted)
    print("-" * 70)
    print(f"\nRappel : {detected}/{len(planted)} défauts détectés ({recall:.0%})")
    print(f"Score de fiabilité audit : {rep.score}/100 · {len(rep.findings)} constats · "
          f"audit en {dt:.0f}s")
    print("Répartition :", rep.counts)


if __name__ == "__main__":
    main()
