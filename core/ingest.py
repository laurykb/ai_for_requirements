# ingest.py
from __future__ import annotations

from pathlib import Path

from indexing.chunking import decoupe_semantic_md
from indexing.embedding import index_chroma, build_embeddings
from indexing.keyword_index import build_bm25_index, save_bm25_to_mongo
from env_config import (COLLECTION_NAME, AUTO_KEYWORDS, AUTO_QUESTIONS,
                    ENHANCEMENT_MODEL, CHUNKING_MODE,
                    RAPTOR_SUMMARIES, RAPTOR_MIN_CHUNKS, RAPTOR_MAX_INPUT_CHUNKS,
                    VOCAB_SAVE_DIR)

from nlp.vocab_builder import save_vocab, build_vocab
from nlp.chunk_enhancer import enhance_chunks, build_raptor_summaries
from indexing.store_mongo import replace_source_chunks, save_chunks_to_mongo
from utils.logging_config import get_logger

logger = get_logger("rag.ingest")


def print_chunk_stats(docs):
    # Affiche un résumé par section si présent
    section_counts = {}
    for d in docs:
        sec = d.metadata.get("section_idx", "?")
        section_counts[sec] = section_counts.get(sec, 0) + 1
    logger.debug("Chunks par section Markdown :")
    for sec, count in sorted(section_counts.items()):
        logger.debug("  Section %s: %d chunks", sec, count)


def _resolve_ingest_options(
    output_dir: str | None,
    num_keywords: int | None,
    num_questions: int | None,
    enhancement_model: str | None,
    chunking_mode: str | None,
    raptor_summaries: bool | None,
) -> dict:
    """Centralise les valeurs par défaut d'ingestion."""
    return {
        "output_dir": output_dir or str(VOCAB_SAVE_DIR),
        "num_keywords": AUTO_KEYWORDS if num_keywords is None else num_keywords,
        "num_questions": AUTO_QUESTIONS if num_questions is None else num_questions,
        "enhancement_model": enhancement_model or ENHANCEMENT_MODEL,
        "chunking_mode": chunking_mode or CHUNKING_MODE,
        "raptor_summaries": RAPTOR_SUMMARIES if raptor_summaries is None else raptor_summaries,
    }


def _notify_progress(progress_callback, message: str, pct: int):
    """Appelle le callback de progression s'il est fourni."""
    if progress_callback:
        progress_callback(message, pct)


def ingest_markdown(md_path: str, output_dir: str | None = None,
                    num_keywords: int = None, num_questions: int = None,
                    enhancement_model: str = None, chunking_mode: str = None,
                    raptor_summaries: bool = None,
                    progress_callback=None, ingest_version: str | None = None,
                    content_hash: str | None = None):
    """
    Pipeline d'ingestion complet :
    - Découpe en chunks (mode naive ou technical)
    - Enrichissement LLM (keywords + questions) si activé
    - Résumés RAPTOR par section si activé
    - Construction du vocabulaire
    - Embeddings avec Ollama
    - Indexation Chroma
    - Sauvegarde MongoDB
    - Sauvegarde BM25
    
    Args:
        md_path: chemin du fichier .md
        output_dir: dossier de sortie vocabulaire (défaut: data/vocab_save à la racine du projet)
        num_keywords: nombre de mots-clés par chunk (None = config AUTO_KEYWORDS)
        num_questions: nombre de questions par chunk (None = config AUTO_QUESTIONS)
        enhancement_model: modèle LLM pour l'enrichissement (None = config)
        chunking_mode: mode de chunking "naive" ou "technical" (None = config CHUNKING_MODE)
        raptor_summaries: activer les résumés RAPTOR (None = config RAPTOR_SUMMARIES)
        progress_callback: callable(step_name, progress_pct) pour UI
    
    Retourne un dictionnaire avec les stats d'ingestion.
    """
    options = _resolve_ingest_options(
        output_dir=output_dir,
        num_keywords=num_keywords,
        num_questions=num_questions,
        enhancement_model=enhancement_model,
        chunking_mode=chunking_mode,
        raptor_summaries=raptor_summaries,
    )
    output_dir = options["output_dir"]
    num_keywords = options["num_keywords"]
    num_questions = options["num_questions"]
    enhancement_model = options["enhancement_model"]
    chunking_mode = options["chunking_mode"]
    raptor_summaries = options["raptor_summaries"]

    stats = {}
    
    try:
        # 1) Vérifier que le fichier existe
        if not Path(md_path).exists():
            raise FileNotFoundError(f"Fichier {md_path} introuvable")
        
        _notify_progress(progress_callback, "Découpe en chunks...", 5)
        
        # 2) Découper en chunks
        docs = decoupe_semantic_md(md_path, max_characters=1000, mode=chunking_mode)
        if not docs:
            # Garde-fou : document vide ou conversion échouée. On s'arrête AVANT
            # de toucher aux index (sinon : reset Chroma puis add([]) -> crash
            # « Expected Embeddings to be non-empty list » + perte des vecteurs).
            raise ValueError(
                f"Aucun chunk extrait de {Path(md_path).name} : document vide ou "
                "conversion échouée. Les index existants n'ont pas été modifiés."
            )
        stats["num_chunks"] = len(docs)
        source_name = Path(md_path).name
        if ingest_version:
            for doc in docs:
                original_id = str(doc.metadata.get("id") or doc.metadata.get("chunk_idx"))
                doc.metadata["id"] = f"{ingest_version}:{original_id}"
                doc.metadata["ingest_version"] = ingest_version
                doc.metadata["content_hash"] = content_hash or ""
            stats["version"] = ingest_version
            stats["content_hash"] = content_hash
        stats["chunking_mode"] = chunking_mode
        logger.info("%d chunks générés à partir de %s (mode: %s)", len(docs), md_path, chunking_mode)
        print_chunk_stats(docs)
        stats["chunks_per_section"] = {}
        for d in docs:
            sec = d.metadata.get("section_idx", "?")
            stats["chunks_per_section"][sec] = stats["chunks_per_section"].get(sec, 0) + 1
        
        # 3) Enrichissement LLM (keywords + questions)
        if num_keywords > 0 or num_questions > 0:
            _notify_progress(progress_callback, "Enrichissement LLM (keywords + questions)...", 15)
            
            def _enhance_progress(current, total):
                # Progression de 15% à 50% pendant l'enrichissement
                pct = 15 + int(35 * current / total)
                _notify_progress(progress_callback, f"Enrichissement chunk {current}/{total}...", pct)
            
            docs = enhance_chunks(
                docs,
                num_keywords=num_keywords,
                num_questions=num_questions,
                model=enhancement_model,
                progress_callback=_enhance_progress
            )
            chunks_with_table_desc = len([d for d in docs if d.metadata.get("table_description")])
            chunks_with_entities = len([d for d in docs if d.metadata.get("entities_str")])
            stats["enhancement"] = {
                "num_keywords": num_keywords,
                "num_questions": num_questions,
                "chunks_enhanced": len([d for d in docs if d.metadata.get("keywords")]),
                "table_descriptions": chunks_with_table_desc,
                "chunks_with_entities": chunks_with_entities
            }
            logger.info("Enrichissement terminé : %d chunks enrichis", stats["enhancement"]["chunks_enhanced"])
            if chunks_with_table_desc:
                logger.info("  -> %d descriptions de tableaux générées", chunks_with_table_desc)
            logger.info("  -> %d chunks avec entités nommées", chunks_with_entities)
        else:
            stats["enhancement"] = {"num_keywords": 0, "num_questions": 0, "chunks_enhanced": 0}
            # S'assurer que les métadonnées existent même sans enrichissement
            for d in docs:
                d.metadata.setdefault("keywords", [])
                d.metadata.setdefault("keywords_str", "")
                d.metadata.setdefault("questions", [])
                d.metadata.setdefault("questions_str", "")

        # NER sur tous les chunks (même si enrichissement LLM désactivé)
        # Le NER spaCy est rapide (~0.5ms/chunk) donc on le fait toujours
        from nlp.ner_extractor import extract_entities, entities_to_str, entities_to_flat_list
        for d in docs:
            if not d.metadata.get("entities_str"):
                ner_dict = extract_entities(d.page_content)
                d.metadata.setdefault("entities", ner_dict)
                d.metadata.setdefault("entities_flat", entities_to_flat_list(ner_dict))
                d.metadata.setdefault("entities_str", entities_to_str(ner_dict))
        
        # Marquer tous les chunks comme type "chunk" (par défaut)
        for d in docs:
            d.metadata.setdefault("chunk_type", "chunk")
        
        # 3b) Résumés RAPTOR par section
        if raptor_summaries:
            _notify_progress(progress_callback, "Génération des résumés RAPTOR par section...", 50)
            
            def _raptor_progress(current, total):
                pct = 50 + int(5 * current / max(total, 1))
                _notify_progress(progress_callback, f"Résumé RAPTOR {current}/{total}...", pct)
            
            summary_docs = build_raptor_summaries(
                docs,
                min_chunks=RAPTOR_MIN_CHUNKS,
                max_input_chunks=RAPTOR_MAX_INPUT_CHUNKS,
                model=enhancement_model,
                progress_callback=_raptor_progress
            )
            stats["raptor"] = {
                "enabled": True,
                "summaries_generated": len(summary_docs),
            }
            # Ajouter les résumés à la liste des documents
            docs.extend(summary_docs)
            logger.info("RAPTOR : %d résumés ajoutés -> %d docs total", len(summary_docs), len(docs))
        else:
            stats["raptor"] = {"enabled": False, "summaries_generated": 0}

        # Filtrer les chunks vides : ils polluent BM25 (token vide) et le pool de
        # candidats sémantique sans jamais rien apporter à une réponse.
        _before = len(docs)
        docs = [d for d in docs if (getattr(d, "page_content", "") or "").strip()]
        _removed = _before - len(docs)
        if _removed:
            logger.info("Chunks vides écartés avant indexation : %d", _removed)
        stats["empty_chunks_removed"] = _removed

        from indexing.chunk_quality import qualify_documents
        stats["quality"] = qualify_documents(docs)
        indexable_docs = [d for d in docs if d.metadata.get("quality_status") != "quarantined"]
        if not indexable_docs:
            raise ValueError("Tous les chunks ont été mis en quarantaine par le contrôle qualité")
        stats["quality"]["indexed"] = len(indexable_docs)
        _notify_progress(progress_callback, f"Contrôle qualité : {len(indexable_docs)} indexables sur {len(docs)}...", 54)

        _notify_progress(progress_callback, "Construction du vocabulaire...", 55)
        
        # 4) Construction du vocabulaire
        vocab, acronyms = build_vocab(indexable_docs, top_k_terms=3000)
        output_dir_path = Path(output_dir)
        output_dir_path.mkdir(parents=True, exist_ok=True)
        save_vocab(vocab, acronyms, path=str(output_dir_path / "vocab.json"))
        stats["vocab_size"] = len(vocab)
        stats["acronyms_count"] = len(acronyms)
        logger.info("Vocabulaire corpus sauvegardé")
        
        _notify_progress(progress_callback, "Génération des embeddings...", 60)
        
        # 5) Embeddings via Ollama
        texts, embeddings, metadatas, ids = build_embeddings(indexable_docs)
        stats["embeddings_count"] = len(embeddings)
        logger.info("Embeddings générés")
        
        _notify_progress(progress_callback, "Indexation ChromaDB...", 75)

        # 6) Indexation Chroma — PAR DOCUMENT : on ne purge que les vecteurs de
        # CE document (anti-doublons) puis on ajoute les nouveaux. L'ancien
        # clean_collection=True vidait TOUTE la collection à chaque ingestion :
        # les autres documents perdaient silencieusement leur recherche
        # sémantique (le multi-document ne tenait que par BM25).
        _ = index_chroma(ids, texts, metadatas, embeddings,
                         collection_name=COLLECTION_NAME,
                         clean_collection=False,
                         replace_source=source_name)
        logger.info("Indexation vector store terminée")
        
        _notify_progress(progress_callback, "Sauvegarde MongoDB...", 85)
        
        # 7) Sauvegarde MongoDB
        if ingest_version:
            replace_source_chunks(docs, source_name, ingest_version)
        else:
            save_chunks_to_mongo(docs)
        logger.info("Chunks sauvegardés dans MongoDB")
        
        _notify_progress(progress_callback, "Construction index BM25...", 88)
        
        # 8) Index BM25 (enrichi avec keywords+questions)
        bm25_tuple = build_bm25_index(indexable_docs)
        # Un index par document est conservé dans MongoDB ; le chargement les
        # fusionne. Une seconde copie pickle serait ambiguë et non atomique avec
        # la base, notamment après plusieurs ingestions.
        save_bm25_to_mongo(bm25_tuple, source_doc=source_name)
        logger.info("Index BM25 sauvegardé dans MongoDB")

        _notify_progress(progress_callback, "Termine", 100)
        
        stats["status"] = "success"
        stats["message"] = f"Ingestion terminée : {len(docs)} chunks indexés"
        
    except Exception as e:
        stats["status"] = "error"
        stats["message"] = str(e)
        # Trace complète au lieu d'avaler silencieusement l'erreur.
        logger.exception("Échec de l'ingestion de %s : %s", md_path, e)

    return stats

if __name__ == "__main__":
    md_file = input("Nom du fichier .md nettoyé à utiliser : ").strip()
    if not Path(md_file).exists():
        print(f"Erreur : fichier {md_file} introuvable")
        exit(1)

    ingest_markdown(md_file)
