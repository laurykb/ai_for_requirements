"""Ingestion dédiée de la baseline d'exigences LynX : 1 chunk = 1 exigence.

Contrairement au découpage markdown générique (qui produisait des chunks
d'en-tête vides et diluait les identifiants), chaque exigence devient UN chunk
avec ses attributs en métadonnées (`req_id`, `req_niveau`, `req_domaine`,
`req_parent_id`) : les citations du chat pointent des exigences exactes et le
front peut ouvrir l'arbre sur le bon nœud.

HyPE élastique : les exigences sont formulées en langage contractuel, les
questions utilisateur en langage courant — le décalage de vocabulaire est le
principal mode d'échec du retrieval sur une baseline. Des questions
hypothétiques sont donc générées par exigence (vecteurs HyPE pointant vers le
parent), tant que la baseline reste de taille raisonnable :

    LYNX_CHAT_HYPE          activer la génération (défaut : true)
    LYNX_CHAT_HYPE_NQ       questions par exigence (défaut : 2)
    LYNX_CHAT_HYPE_MAX_REQS taille de baseline au-delà de laquelle HyPE est
                            coupé — coût LLM linéaire (défaut : 300)

Volontairement PAS touchés (ils appartiennent au monde RAG) : le vocabulaire
global (`data/vocab_save`), le pickle BM25 global de secours, les résumés
RAPTOR. L'index BM25 de la baseline est stocké en Mongo sous sa source.
"""
from __future__ import annotations

import logging
import os

from core.document import Document
from core.reserved_sources import LYNX_BASELINE_SOURCE

logger = logging.getLogger(__name__)


def hype_policy(n_reqs: int) -> tuple[bool, int]:
    """(hype_actif, questions_par_exigence) — élastique selon la taille."""
    enabled = os.environ.get("LYNX_CHAT_HYPE", "true").lower() in ("true", "1", "yes")
    nq = max(0, int(os.environ.get("LYNX_CHAT_HYPE_NQ", "2")))
    max_reqs = int(os.environ.get("LYNX_CHAT_HYPE_MAX_REQS", "300"))
    if not enabled or nq == 0 or n_reqs > max_reqs:
        return False, 0
    return True, nq


def _requirement_text(req: dict) -> str:
    """Contenu du chunk : l'énoncé d'abord, les attributs de traçabilité ensuite."""
    head = (f"{req.get('id')} ({req.get('type') or 'Exigence'}, "
            f"niveau L{req.get('niveau', 0)}, domaine {req.get('domaine') or 'Général'})")
    texte = str(req.get("texte") or "").strip() or "(énoncé vide)"
    lines = [f"{head} : {texte}"]
    if req.get("parent_id"):
        lines.append(f"Dérivée de : {req['parent_id']}")
    for link in req.get("links") or []:
        if isinstance(link, dict) and link.get("target_id"):
            lines.append(f"Lien {link.get('type', 'REFERENCE')} : {link['target_id']}")
    if req.get("verification"):
        lines.append(f"Vérification (IADT) : {req['verification']}")
    if req.get("source"):
        lines.append(f"Origine : {req['source']}")
    if req.get("rationale"):
        lines.append(f"Justification : {req['rationale']}")
    return "\n".join(lines)


def build_requirement_documents(corpus: list[dict],
                                source: str = LYNX_BASELINE_SOURCE) -> list[Document]:
    """Un Document par exigence, trié (domaine, niveau, id) — ordre stable.
    `source` : source réservée cible (baseline vivante par défaut ; les
    harnais d'éval/stress utilisent leur propre source réservée)."""
    domains = sorted({str(r.get("domaine") or "Général") for r in corpus})
    section_of = {d: i for i, d in enumerate(domains)}
    ordered = sorted(corpus, key=lambda r: (str(r.get("domaine") or "Général"),
                                            int(r.get("niveau") or 0), str(r.get("id"))))
    docs = []
    for idx, req in enumerate(ordered):
        domaine = str(req.get("domaine") or "Général")
        req_id = str(req.get("id"))
        docs.append(Document(
            page_content=_requirement_text(req),
            metadata={
                "id": f"{source}::{req_id}",
                "source": source,
                "chunk_idx": idx,
                "section_idx": section_of[domaine],
                "chunk_type": "requirement",
                "heading": f"{req_id} · L{req.get('niveau', 0)}",
                "breadcrumb": f"Baseline d'exigences › {domaine}",
                "req_id": req_id,
                "req_niveau": int(req.get("niveau") or 0),
                "req_domaine": domaine,
                "req_parent_id": req.get("parent_id") or "",
            },
        ))
    return docs


def ingest_baseline(corpus: list[dict], progress_callback=None,
                    source: str = LYNX_BASELINE_SOURCE) -> dict:
    """Pipeline complet baseline -> index (embeddings + HyPE + BM25 + Mongo).

    Même contrat de stats que `core.ingest.ingest_markdown` (status/message/
    num_chunks/quality) : la file d'ingestion et l'UI de progression les lisent
    à l'identique."""
    def _notify(msg: str, pct: int) -> None:
        if progress_callback:
            try:
                progress_callback(msg, pct)
            except Exception:
                pass

    stats: dict = {"n_exigences": len(corpus)}
    try:
        _notify("Construction des chunks d'exigences...", 5)
        docs = build_requirement_documents(corpus, source=source)
        if not docs:
            raise ValueError("Baseline vide : aucune exigence à indexer.")
        stats["num_chunks"] = len(docs)

        hype_on, hype_nq = hype_policy(len(docs))
        if hype_on:
            _notify(f"Questions HyPE ({hype_nq}/exigence, LLM)...", 10)
            from nlp.chunk_enhancer import enhance_chunks

            def _enh_progress(current, total):
                _notify(f"Questions HyPE {current}/{total}...", 10 + int(40 * current / max(total, 1)))

            docs = enhance_chunks(docs, num_keywords=0, num_questions=hype_nq,
                                  progress_callback=_enh_progress)
        stats["hype"] = {"enabled": hype_on, "questions_per_req": hype_nq}
        for d in docs:
            d.metadata.setdefault("keywords", [])
            d.metadata.setdefault("keywords_str", "")
            d.metadata.setdefault("questions", [])
            d.metadata.setdefault("questions_str", "")

        _notify("Entités nommées (NER)...", 52)
        from nlp.ner_extractor import extract_entities, entities_to_str, entities_to_flat_list
        for d in docs:
            if not d.metadata.get("entities_str"):
                ner = extract_entities(d.page_content)
                d.metadata.setdefault("entities", ner)
                d.metadata.setdefault("entities_flat", entities_to_flat_list(ner))
                d.metadata.setdefault("entities_str", entities_to_str(ner))

        _notify("Contrôle qualité...", 56)
        from indexing.chunk_quality import qualify_documents
        stats["quality"] = qualify_documents(docs)
        indexable = [d for d in docs if d.metadata.get("quality_status") != "quarantined"]
        if not indexable:
            raise ValueError("Toutes les exigences ont été mises en quarantaine par le contrôle qualité.")
        stats["quality"]["indexed"] = len(indexable)

        _notify("Embeddings (contenu + questions HyPE)...", 60)
        from indexing.embedding import build_embeddings, index_chroma
        from env_config import COLLECTION_NAME, HYPE_MAX_QUESTIONS
        texts, vecs, metadatas, ids = build_embeddings(
            indexable, hype_enabled=hype_on,
            hype_max_questions=max(hype_nq, HYPE_MAX_QUESTIONS) if hype_on else None)
        stats["embeddings_count"] = len(vecs)

        _notify("Indexation vector store...", 82)
        index_chroma(ids, texts, metadatas, vecs, collection_name=COLLECTION_NAME,
                     clean_collection=False, replace_source=source)

        _notify("Sauvegarde MongoDB...", 90)
        from indexing.store_mongo import save_chunks_to_mongo
        save_chunks_to_mongo(docs)

        _notify("Index BM25 de la baseline...", 95)
        from indexing.keyword_index import build_bm25_index, save_bm25_to_mongo
        save_bm25_to_mongo(build_bm25_index(indexable), source_doc=source)

        _notify("Terminé", 100)
        stats["status"] = "success"
        stats["message"] = (f"Baseline indexée : {len(indexable)} exigence(s), "
                            f"HyPE {'activé' if hype_on else 'coupé'}")
    except Exception as e:
        stats["status"] = "error"
        stats["message"] = str(e)
        logger.exception("Échec de l'ingestion de la baseline : %s", e)
    return stats
