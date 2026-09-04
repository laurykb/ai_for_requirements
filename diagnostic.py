#!/usr/bin/env python3
"""
diagnostic.py - Vérifier la portabilité et la config du projet RAG
Utile pour troubleshooting avant de lancer l'app
"""

import sys
from pathlib import Path
from env_config import get_config, CONFIG

def print_header(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")

def print_status(label, value, ok=True):
    symbol = "" if ok else ""
    color_start = "\033[92m" if ok else "\033[91m"  # Green or Red
    color_end = "\033[0m"
    print(f"  {color_start}{symbol}{color_end} {label:<40} {value}")

def main():
    print_header("RAG Project - Diagnostic Portal")
    
    cfg = get_config()
    
    # ----------------------------------------------------------
    # 1. PROJET & CHEMINS
    # ----------------------------------------------------------
    print_header("1. Projet & Chemins")
    
    project_root = Path(cfg["PROJECT_ROOT"])
    print_status("   Project root", str(project_root), project_root.exists())
    print_status("   Chroma DB", cfg["CHROMA_PATH"], Path(cfg["CHROMA_PATH"]).exists())
    print_status("   Vocab", cfg["VOCAB_JSON_PATH"], Path(cfg["VOCAB_JSON_PATH"]).exists())
    
    # ----------------------------------------------------------
    # 2. GPU & RESSOURCES
    # ----------------------------------------------------------
    print_header("2. GPU & Ressources")
    
    num_gpus = cfg["NUM_GPUS"]
    print_status(f" GPUs detected", f"{num_gpus} GPU(s)", num_gpus > 0 or num_gpus == 0)
    print_status("   CUDA available", "Yes" if cfg["CUDA_AVAILABLE"] else "No (CPU mode)", 
                 cfg["CUDA_AVAILABLE"])
    print_status("   CE Device", cfg["CE_DEVICE"], True)
    
    # ----------------------------------------------------------
    # 3. OLLAMA
    # ----------------------------------------------------------
    print_header("3. Ollama Services")
    
    ollama_ok = cfg["OLLAMA_AVAILABLE"]
    print_status(" Ollama", 
                f"{' Online' if ollama_ok else ' Offline'} ({cfg['OLLAMA_HOST']})", 
                ollama_ok)
    print_status("   Embed model", cfg["EMBED_MODEL"], True)
    print_status("   Embed timeout", f"{cfg['EMBED_TIMEOUT_S']}s (tolère le swap à froid)", True)
    print_status("   Rewriter model", cfg["REWRITER_MODEL"], True)
    print_status("   Generator model", cfg["GEN_MODEL"], True)
    print_status("   Agent model", f"{cfg['AGENT_MODEL']} (max {cfg['AGENT_MAX_ITERATIONS']} étapes)", True)
    fast = cfg["RAG_FAST_MODE"]
    print_status("   Mode rapide", ("ON (prompt épuré)" if fast else "OFF (prompt détaillé)"), True)

    if not ollama_ok:
        print(f"    Ollama not reachable. Start it or update OLLAMA_HOST in .env")

    # ----------------------------------------------------------
    # 3b. ROUTAGE DE MODÈLES
    # ----------------------------------------------------------
    print_header("3b. Routage de modèles")
    try:
        from core.model_router import routing_table
        table = routing_table()
        distinct = set(table.values())
        for role, model in table.items():
            print_status(f"   rôle '{role}'", model, True)
        note = "1 modèle (uniforme)" if len(distinct) == 1 else f"{len(distinct)} modèles distincts"
        print_status("   Politique", note + " - cycle de vie laissé à OLLAMA_KEEP_ALIVE", True)
    except Exception as e:
        print_status("   Routage", f"introspection indisponible : {e}", False)
    
    # ----------------------------------------------------------
    # 4. MONGODB
    # ----------------------------------------------------------
    print_header("4. MongoDB")
    
    mongo_ok = cfg["MONGO_AVAILABLE"]
    print_status("  MongoDB",
                f"{'Online' if mongo_ok else ' Offline'} ({cfg['MONGO_HOST']}:{cfg['MONGO_PORT']})",
                mongo_ok)
    print_status("   DB name", cfg["MONGO_DB"], True)
    print_status("   Full URI", cfg["MONGO_URI"], True)
    
    if not mongo_ok:
        print(f"    MongoDB not reachable. Start it or update MONGO_HOST in .env")
    
    # ----------------------------------------------------------
    # 5. CROSS-ENCODER
    # ----------------------------------------------------------
    print_header("5. Cross-Encoder Reranking")
    
    ce_enabled = cfg["USE_CROSS_ENCODER"]
    ce_path = Path(cfg["CROSS_ENCODER_LOCAL_PATH"])
    ce_exists = ce_path.exists()
    
    print_status("  Enabled", "Yes" if ce_enabled else "No", ce_enabled)
    print_status("   Model path", str(ce_path), ce_exists)
    
    if ce_enabled and not ce_exists:
        print(f"    Model not found at {ce_path}")
        print(f"       To download: cd /path/to/project/models")
        print(f"                   huggingface-cli download BAAI/bge-reranker-v2-m3")
    
    # ----------------------------------------------------------
    # 6. RETRIEVAL CONFIG
    # ----------------------------------------------------------
    print_header("6. Retrieval Configuration")
    
    print_status("   Num chunks", str(cfg["NUM_CHUNKS"]), True)
    print_status("   RRF K", str(cfg["RRF_K"]), True)
    print_status("   Semantic weight", f"{cfg['WEIGHT_SEMANTIC']:.1%}", True)
    print_status("   BM25 weight", f"{cfg['WEIGHT_BM25']:.1%}", True)
    print_status("   Max chunk length", f"{cfg['MAX_CHUNK_LENGTH']:,}" + " chars", True)

    # ----------------------------------------------------------
    # 6b. MAGASIN VECTORIEL
    # ----------------------------------------------------------
    print_header("6b. Magasin vectoriel (ChromaDB)")
    rag_consistency = None
    try:
        from retrieval.vector_store import get_vector_store
        n = get_vector_store().count()
        print_status("   Vecteurs indexés", f"{n:,}", n > 0)
        if n == 0:
            print(f"    Collection vide -> lancez une ingestion (onglet Documents).")
        from core.index_consistency import rag_index_consistency
        rag_consistency = rag_index_consistency()
        if rag_consistency.get("available"):
            totals = rag_consistency.get("totals", {})
            bad = [row for row in rag_consistency["sources"] if not row["in_sync"]]
            print_status(
                "   Cohérence RAG Mongo/BM25/Chroma",
                f"{len(rag_consistency['sources']) - len(bad)}/{len(rag_consistency['sources'])} sources complètes",
                rag_consistency["in_sync"],
            )
            print_status("   Chunks RAG indexables / vecteurs",
                         f"{totals.get('indexable', 0):,} / {totals.get('vectors', 0):,}",
                         totals.get("vectors", 0) >= totals.get("indexable", 0))
            print_status("   Vecteurs orphelins", str(totals.get("orphan_vectors", 0)),
                         totals.get("orphan_vectors", 0) == 0)
    except Exception as e:
        print_status("   Vecteurs indexés", f"indisponible : {e}", False)

    # ----------------------------------------------------------
    # 7. FEATURES
    # ----------------------------------------------------------
    print_header("7. Advanced Features")
    
    print_status(" Parent-Child", "Enabled" if cfg["PARENT_CHILD_ENABLED"] else "Disabled", True)
    print_status(" Self-RAG", "Enabled" if cfg["SELF_RAG_ENABLED"] else "Disabled", True)
    print_status(" Chunking mode", cfg["CHUNKING_MODE"], True)
    print_status(" Raptor summaries", "Enabled" if cfg["RAPTOR_SUMMARIES"] else "Disabled", True)
    
    # ----------------------------------------------------------
    # SUMMARY
    # ----------------------------------------------------------
    print_header("Summary")
    
    issues = []
    if not ollama_ok:
        issues.append("Ollama offline")
    if not mongo_ok:
        issues.append("MongoDB offline")
    if ce_enabled and not ce_exists:
        issues.append("CE model missing")
    if rag_consistency is not None and not rag_consistency.get("in_sync", False):
        issues.append("RAG indexes inconsistent (Mongo/BM25/Chroma)")
    
    if issues:
        print(f"   {len(issues)} issue(s) detected:")
        for issue in issues:
            print(f"     - {issue}")
    else:
        print("  All systems operational!")
        print("  Ready to run: python serve.py  (front Next.js + API FastAPI)")
    
    print(f"\n  Edit .env to customize, or use defaults (auto-detection)")
    print(f"  See SETUP_PORTABLE.md for detailed guide\n")
    
    return 0 if not issues else 1

if __name__ == "__main__":
    sys.exit(main())
