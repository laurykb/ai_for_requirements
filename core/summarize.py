"""
Résumé global d'un document.

Réutilise les **résumés de section RAPTOR** calculés à l'ingestion
(`chunk_type='summary'`) plutôt que de re-traiter tout le document : on agrège les
résumés de section puis on demande au modèle de génération une synthèse globale. À
défaut de résumés RAPTOR (doc ingéré sans l'option), on retombe sur un échantillon
ordonné de chunks.

Passe par le rôle 'generate' (cf. core.model_router) et rédige dans la langue du
document. Factuel, neutre, sourcé sur le seul matériau fourni (zéro invention).
"""
from __future__ import annotations

from utils.logging_config import get_logger

logger = get_logger("rag.summarize")

# Garde-fous fenêtre de contexte (generate = num_ctx 16k ~ 50-60k chars).
_MAX_SECTION_SUMMARIES = 50
_MAX_FALLBACK_CHUNKS = 60
_MAX_MATERIAL_CHARS = 24000

_SUMMARY_PROMPT = """[RÔLE] Tu produis le RÉSUMÉ d'un document technique.

[CONSIGNES]
- Appuie-toi UNIQUEMENT sur le MATÉRIAU fourni (résumés de section / extraits). Zéro invention.
- Rédige IMPÉRATIVEMENT dans la LANGUE du document.
- Structure de sortie :
  **Objet du document** : 1 à 2 phrases.
  **Points clés** : 5 à 10 puces (périmètre, exigences, niveaux, valeurs, entités importantes).
  **Synthèse** : 1 à 2 phrases de conclusion.
- Factuel, neutre, concis. Conserve les identifiants/niveaux/valeurs exacts (ex: EAL4, ALC_FLR.3).
- Si le matériau est insuffisant pour résumer, dis-le clairement.

[MATÉRIAU - {n} élément(s) du document « {source} »]
{material}

[RÉSUMÉ]"""


def _chunks_collection():
    """Collection `chunks` via le client Mongo singleton partagé (voir utils.mongo)."""
    from utils.mongo import get_db
    return get_db()["chunks"]


def _gather_material(source: str) -> tuple[str, int, str]:
    """Retourne (matériau, nb_éléments, base). Privilégie les résumés RAPTOR."""
    col = _chunks_collection()
    sums = list(
        col.find({"source": source, "chunk_type": "summary"},
                 {"content": 1, "heading": 1, "breadcrumb": 1, "section_idx": 1})
           .sort("section_idx", 1).limit(_MAX_SECTION_SUMMARIES)
    )
    if sums:
        parts = []
        for s in sums:
            title = (s.get("heading") or s.get("breadcrumb")
                     or f"Section {s.get('section_idx')}")
            parts.append(f"## {title}\n{(s.get('content') or '').strip()}")
        return "\n\n".join(parts), len(sums), "résumés de section RAPTOR"

    # Fallback : échantillon ordonné de chunks de contenu (hors résumés).
    chs = list(
        col.find({"source": source, "chunk_type": {"$ne": "summary"}},
                 {"content": 1, "section_idx": 1, "chunk_idx": 1})
           .sort([("section_idx", 1), ("chunk_idx", 1)]).limit(_MAX_FALLBACK_CHUNKS)
    )
    material = "\n\n".join((c.get("content") or "").strip() for c in chs)
    return material, len(chs), "échantillon de chunks"


def summarize_document(source: str) -> dict:
    """Résumé global d'un document indexé.

    Retourne {status, summary, n, basis} :
      - status : 'success' | 'empty' | 'error'
      - n      : nombre d'éléments agrégés ; basis : leur nature (résumés / chunks)
    """
    try:
        material, n, basis = _gather_material(source)
        if not material.strip():
            return {"status": "empty", "summary": "", "n": 0, "basis": basis}
        prompt = _SUMMARY_PROMPT.format(n=n, source=source,
                                        material=material[:_MAX_MATERIAL_CHARS])
        from core.model_router import build_llm
        summary = build_llm("generate").invoke(prompt)
        logger.info("Résumé de « %s » : %d %s agrégés", source, n, basis)
        return {"status": "success", "summary": summary, "n": n, "basis": basis}
    except Exception as e:
        logger.exception("Échec du résumé de %s : %s", source, e)
        return {"status": "error", "summary": str(e), "n": 0, "basis": ""}
