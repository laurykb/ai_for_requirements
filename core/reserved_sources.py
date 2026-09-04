"""Sources réservées de l'index documentaire.

Certains documents sont indexés par le pipeline standard mais appartiennent
à un espace dédié (ex. la baseline d'exigences du chat LynX). Ils ne doivent
JAMAIS surgir dans le monde RAG non scopé : ni dans la recherche « Tous les
documents », ni dans la synthèse corpus, ni dans la liste des sources.

Interroger une source réservée reste possible en la nommant explicitement
(`source_filter` exact) — c'est ainsi que son chat dédié la consulte.
"""
from __future__ import annotations

# Baseline d'exigences LynX (api/lynx_chat.py). Nom dupliqué côté front
# (web/src/lib/api.ts, LYNX_BASELINE_SOURCE) : les valeurs doivent rester
# identiques.
LYNX_BASELINE_SOURCE = "baseline-exigences-lynx.md"

# Sources techniques éphémères : harnais d'évaluation du chat baseline
# (evals/run_baseline_eval.py) et stress corpus XL (scripts/). Réservées pour
# ne jamais surgir dans le monde RAG, purgées par leurs harnais respectifs.
LYNX_BASELINE_EVAL_SOURCE = "baseline-eval-lynx.md"
LYNX_BASELINE_STRESS_SOURCE = "baseline-stress-lynx.md"

RESERVED_SOURCES: tuple[str, ...] = (
    LYNX_BASELINE_SOURCE,
    LYNX_BASELINE_EVAL_SOURCE,
    LYNX_BASELINE_STRESS_SOURCE,
)
