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

RESERVED_SOURCES: tuple[str, ...] = (LYNX_BASELINE_SOURCE,)
