"""MAP du pipeline de synthèse corpus : extraction d'un aspect depuis UN document,
à partir de son matériau (résumés RAPTOR d'abord). LLM injectable (tests offline)."""
from __future__ import annotations

_EXTRACT_PROMPT = """[RÔLE] Tu EXTRAIS d'un document technique tous les éléments correspondant à : {aspect}.

[CONSIGNES]
- Appuie-toi UNIQUEMENT sur le MATÉRIAU fourni. Zéro invention.
- Renvoie une LISTE : un élément par ligne, préfixé « - ». Rien d'autre (pas d'intro, pas de conclusion).
- Chaque élément est factuel, concis, dans la LANGUE du document. Conserve les identifiants/valeurs exacts.
- Si le document ne contient AUCUN élément pour « {aspect} », renvoie une liste vide (aucune ligne).

[MATÉRIAU - document « {document} »]
{material}

[ÉLÉMENTS EXTRAITS pour « {aspect} »]"""


def _parse_items(text: str) -> list[str]:
    items = []
    for line in (text or "").splitlines():
        s = line.strip().lstrip("-*•").strip()
        if s:
            items.append(s)
    return items


def extract_from_document(document, aspect, llm=None, gather=None):
    """Extrait `aspect` du document. Retourne {document, items, raw}."""
    if gather is None:
        from core.summarize import _gather_material as gather
    if llm is None:
        from core.model_router import build_llm
        llm = build_llm("extract").invoke

    material, _n, _basis = gather(document)
    if not (material or "").strip():
        return {"document": document, "items": [], "raw": ""}
    from core.summarize import _MAX_MATERIAL_CHARS
    from core.prompt_registry import get_prompt
    prompt = get_prompt("extract.map", _EXTRACT_PROMPT).format(
        aspect=aspect, document=document, material=material[:_MAX_MATERIAL_CHARS])
    raw = llm(prompt)
    return {"document": document, "items": _parse_items(raw), "raw": raw}

def _raw_document_chunks(document: str) -> list[dict]:
    """Chunks de preuve d un document, selon la politique qualité commune."""
    from utils.mongo import get_db
    from core.evidence_policy import select_primary_evidence_chunks
    chunks = list(get_db()["chunks"].find(
        {"source": document, "chunk_type": {"$ne": "summary"}},
        {"content": 1, "section_idx": 1, "chunk_idx": 1,
         "quality_status": 1, "quality_reasons": 1, "content_provenance": 1},
    ).sort([("section_idx", 1), ("chunk_idx", 1)]))
    selected, policy = select_primary_evidence_chunks(chunks)
    for chunk in selected:
        chunk["evidence_policy"] = policy
    return selected


def _content_batches(chunks: list[dict], limit: int = 24_000,
                     overlap: int = 1_500) -> list[str]:
    """Couvre tout le document sans tronquer, avec léger recouvrement."""
    text = "\n\n".join(str(chunk.get("content") or "").strip()
                       for chunk in chunks if str(chunk.get("content") or "").strip())
    if not text:
        return []
    batches = []
    start = 0
    while start < len(text):
        end = min(len(text), start + limit)
        if end < len(text):
            boundary = text.rfind("\n\n", start + limit // 2, end)
            if boundary > start:
                end = boundary
        batches.append(text[start:end])
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return batches


def extract_from_document_exhaustive(document, aspect, llm=None, gather=None,
                                     batch_chars: int = 24_000,
                                     batch_overlap: int = 1_500):
    """MAP exhaustif : le LLM filtre chaque lot, sans échantillonnage de chunks."""
    if gather is None:
        gather = _raw_document_chunks
    if llm is None:
        from core.model_router import build_llm
        llm = build_llm("extract").invoke
    chunks = gather(document)
    batches = _content_batches(chunks, batch_chars, batch_overlap)
    from core.prompt_registry import get_prompt
    items = []
    raw_parts = []
    seen = set()
    for index, material in enumerate(batches, 1):
        prompt = get_prompt("extract.map", _EXTRACT_PROMPT).format(
            aspect=aspect, document=f"{document} — lot {index}/{len(batches)}",
            material=material)
        raw = llm(prompt)
        raw_parts.append(raw)
        for item in _parse_items(raw):
            key = " ".join(item.casefold().split())
            if key not in seen:
                seen.add(key)
                items.append(item)
    from core.evidence_registry import parse_evidence_item
    source_text = "\n".join(str(chunk.get("content") or "") for chunk in chunks)
    candidates = [parse_evidence_item(item, document, source_text) for item in items]
    return {"document": document, "items": items, "candidates": candidates,
            "raw": "\n".join(raw_parts), "batch_count": len(batches),
            "chunks_scanned": len(chunks)}
