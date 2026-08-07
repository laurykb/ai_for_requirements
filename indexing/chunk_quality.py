"""Qualification déterministe des chunks avant leur entrée dans les index."""
from __future__ import annotations

import re
from collections import Counter

_ASSISTANT_BOILERPLATE = re.compile(
    r"\b(je suis désolé|je suis prêt à analyser|pouvez-vous (?:me )?fournir|as an ai|i(?:'|’)m sorry|please provide the table)\b",
    re.IGNORECASE,
)
_TOC = re.compile(r"(?:\.{5,}|\btable des matières\b|\btable of contents\b)", re.IGNORECASE)


def classify_chunk(content: str, metadata: dict | None = None) -> dict:
    metadata = metadata or {}
    text = (content or "").strip()
    reasons: list[str] = []
    kind = metadata.get("chunk_type") or "text"
    provenance = "derived" if kind in {"summary", "hype_question"} else "raw"
    status = "accepted"
    if _ASSISTANT_BOILERPLATE.search(text):
        reasons.append("assistant_boilerplate")
        status = "quarantined"
    if kind in {"table", "mixed"}:
        meaningful = re.sub(r"[\s|:_\-–—.]+", "", text)
        if len(meaningful) < 40:
            reasons.append("empty_or_broken_table")
            status = "quarantined"
    if _TOC.search(text) and len(re.findall(r"\.{3,}\s*\d+", text)) >= 2:
        reasons.append("table_of_contents")
        if status != "quarantined":
            status = "degraded"
    if len(text) < 40:
        reasons.append("very_short")
        if status != "quarantined":
            status = "degraded"
    source = str(metadata.get("source") or "")
    if re.search(r"\b(chatgpt|thalesgpt|conversation|question[-_ ]réponse)\b", source, re.IGNORECASE):
        reasons.append("generated_source")
        provenance = "generated"
        if status == "accepted":
            status = "degraded"
    return {"quality_status": status, "quality_reasons": reasons,
            "content_provenance": provenance}


def qualify_documents(docs) -> dict:
    counts = Counter()
    reasons = Counter()
    for doc in docs:
        result = classify_chunk(doc.page_content, doc.metadata)
        doc.metadata.update(result)
        counts[result["quality_status"]] += 1
        reasons.update(result["quality_reasons"])
    return {"counts": dict(counts), "reasons": dict(reasons), "total": len(docs)}
