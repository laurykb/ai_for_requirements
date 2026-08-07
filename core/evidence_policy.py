"""Politique commune d'autorité des preuves pour Chat et SRA."""
from __future__ import annotations


_CHAT_AUTHORITY = {
    ("accepted", "raw"): 1.00,
    ("degraded", "raw"): 0.82,
    ("accepted", "derived"): 0.78,
    ("degraded", "derived"): 0.64,
    ("accepted", "generated"): 0.55,
    ("degraded", "generated"): 0.40,
}


def evidence_authority(metadata: dict | None) -> float:
    """Score d'autorité stable; les anciens chunks sont traités comme raw/accepted."""
    metadata = metadata or {}
    quality = str(metadata.get("quality_status") or "accepted")
    provenance = str(metadata.get("content_provenance") or "raw")
    if quality == "quarantined":
        return 0.0
    return _CHAT_AUTHORITY.get((quality, provenance), 0.50)


def rank_chat_candidates(items: list[dict]) -> list[dict]:
    """Filtre la quarantaine et reclasse pertinence × autorité pour le Chat."""
    ranked = []
    for item in items or []:
        meta = item.get("meta") or {}
        authority = evidence_authority(meta)
        if authority <= 0:
            continue
        base_score = item.get("ce_score")
        if base_score is None:
            base_score = item.get("score_global", 0.0)
        enriched = dict(item)
        enriched_meta = dict(meta)
        enriched_meta["evidence_authority"] = authority
        enriched["meta"] = enriched_meta
        enriched["evidence_score"] = float(base_score or 0.0) * authority
        ranked.append(enriched)
    return sorted(
        ranked,
        key=lambda row: (
            row["evidence_score"],
            row["meta"]["evidence_authority"],
        ),
        reverse=True,
    )


def select_primary_evidence_chunks(chunks: list[dict]) -> tuple[list[dict], str]:
    """Sélectionne les preuves métier sans promouvoir du contenu généré/dérivé.

    Les preuves primaires accepted/raw sont utilisées en priorité. Le repli
    degraded/raw n'est permis que lorsque le document ne contient aucun chunk
    primaire, afin de ne pas rendre un document entièrement muet.
    """
    raw = [
        chunk for chunk in (chunks or [])
        if str(chunk.get("quality_status") or "accepted") != "quarantined"
        and str(chunk.get("content_provenance") or "raw") == "raw"
    ]
    accepted = [
        chunk for chunk in raw
        if str(chunk.get("quality_status") or "accepted") == "accepted"
    ]
    if accepted:
        return accepted, "primary"
    degraded = [
        chunk for chunk in raw
        if str(chunk.get("quality_status") or "accepted") == "degraded"
    ]
    return degraded, "degraded_fallback" if degraded else "no_evidence"
