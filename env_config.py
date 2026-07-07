"""
env_config.py - Gestion centralisée et portable des variables d'environnement.

Ce module remplace les hardcoded paths/URLs par un système flexible :
- Charge les vars depuis .env
- Détecte les ressources disponibles (GPU, services)
- Fournit des fallbacks intelligents
- Vérifie la disponibilité des services

Utilisation :
    from env_config import get_config, CONFIG
    cfg = get_config()
    print(cfg['MONGO_URI'], cfg['CE_DEVICE'])
"""

import os
import sys
from pathlib import Path
from typing import Dict, Any
from dotenv import load_dotenv
import logging

logger = logging.getLogger(__name__)

# -- Robustesse encodage (Windows) --------------------------------------------
# Le code émet des caractères Unicode dans ses print() (, , ->...). Sur une
# console Windows cp1252 sans PYTHONUTF8, cela lève UnicodeEncodeError et casse
# une requête. On force UTF-8 (errors="replace" en filet) sur stdout/stderr,
# indépendamment des variables d'environnement - fix définitif et portable.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# ============================================================================
# 1. CHARGEMENT DES VARIABLES D'ENVIRONNEMENT
# ============================================================================
_PROJECT_ROOT = Path(__file__).resolve().parent
_ENV_FILE = _PROJECT_ROOT / ".env"
_ENV_EXAMPLE_FILE = _PROJECT_ROOT / ".env.example"

if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE, verbose=False)
    logger.debug(f" Chargé .env depuis {_ENV_FILE}")
elif _ENV_EXAMPLE_FILE.exists():
    # Fallback utile pour un clone GitHub sans .env local.
    load_dotenv(_ENV_EXAMPLE_FILE, verbose=False)
    logger.warning(
        ".env absent: fallback sur .env.example. "
        "Copiez .env.example vers .env pour personnaliser votre configuration."
    )
else:
    logger.warning(
        ".env et .env.example absents. "
        "Le projet utilisera uniquement les valeurs par défaut codées dans env_config.py."
    )


# ============================================================================
# 2. AUTO-DÉTECTION RESSOURCES
# ============================================================================

def _detect_available_gpus() -> int:
    """Détecte le nombre de GPUs disponibles."""
    try:
        import torch
        return torch.cuda.device_count()
    except (ImportError, RuntimeError):
        return 0


def _detect_optimal_device(num_gpus: int) -> str:
    """Sélectionne le device en fonction des GPUs disponibles."""
    if num_gpus == 0:
        return "cpu"
    elif num_gpus == 1:
        return "cuda:0"
    elif num_gpus >= 2:
        # 2+ GPUs : use "cuda:0" for LLM, "cuda:1" for CE (default split)
        return "cuda:0"
    else:
        return "cpu"


def _check_service_available(host: str, timeout: float = 1.0) -> bool:
    """Vérifie si un service est accessible."""
    try:
        import socket
        hostname, port_str = host.rsplit(":", 1)
        port = int(port_str)
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((hostname, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def _resolve_project_path(path_value: str) -> str:
    """
    Résout un chemin de config en absolu.
    - absolu : conservé tel quel
    - relatif : interprété depuis la racine projet
    """
    p = Path(path_value).expanduser()
    if p.is_absolute():
        return str(p)
    return str((_PROJECT_ROOT / p).resolve())


# ============================================================================
# 3. CONFIGURATION UNIFIÉE
# ============================================================================

def get_config() -> Dict[str, Any]:
    """
    Retourne la configuration complète du projet.
    
    Priorité :
      1. Variables d'environnement (depuis .env ou OS)
      2. Auto-détection (GPU, services)
      3. Fallbacks par défaut
    
    Returns:
        Dict avec toutes les clés de configuration
    """
    
    num_gpus = _detect_available_gpus()
    
    # --------------------- PATHS (relatifs au projet) -------------------
    paths_config = {
        "PROJECT_ROOT": str(_PROJECT_ROOT),
        "DATA_DIR": str(_PROJECT_ROOT / "data"),
        "CHROMA_PATH": str(_PROJECT_ROOT / "data" / "chroma_db"),
        "VOCAB_SAVE_DIR": str(_PROJECT_ROOT / "data" / "vocab_save"),
        "VOCAB_JSON_PATH": str(_PROJECT_ROOT / "data" / "vocab_save" / "vocab.json"),
        "MODELS_DIR": str(_PROJECT_ROOT / "models"),
    }
    
    # --------------------- DATABASE -------------------
    # MongoDB - tous les défauts viennent de .env.example
    mongo_host = os.environ.get("MONGO_HOST", "localhost")
    mongo_port = os.environ.get("MONGO_PORT", "27017")
    mongo_user = os.environ.get("MONGO_USER", "").strip()
    mongo_pass = os.environ.get("MONGO_PASSWORD", "").strip()
    
    if mongo_user and mongo_pass:
        mongo_uri = f"mongodb://{mongo_user}:{mongo_pass}@{mongo_host}:{mongo_port}"
    else:
        mongo_uri = f"mongodb://{mongo_host}:{mongo_port}"
    
    mongo_uri = os.environ.get("MONGO_URI", mongo_uri)
    mongo_db = os.environ.get("MONGO_DB", "ragdb")
    
    # Vérifier la disponibilité
    mongo_available = _check_service_available(f"{mongo_host}:{mongo_port}")
    
    db_config = {
        "MONGO_URI": mongo_uri,
        "MONGO_HOST": mongo_host,
        "MONGO_PORT": mongo_port,
        "MONGO_DB": mongo_db,
        "MONGO_AVAILABLE": mongo_available,
    }
    if mongo_user:
        db_config["MONGO_USER"] = mongo_user
    if mongo_pass:
        db_config["MONGO_PASSWORD"] = mongo_pass
    
    # --------------------- EMBEDDING & LLM (Ollama) -------------------
    ollama_host = os.environ.get("OLLAMA_HOST", "").strip().rstrip("/")
    if not ollama_host:
        logger.warning("OLLAMA_HOST manquant en .env - utilisation du fallback interne")
        ollama_host = "http://localhost:11434"

    ollama_available = _check_service_available(ollama_host.replace("http://", ""))

    embed_model = os.environ.get("EMBED_MODEL", "")
    if not embed_model:
        logger.warning("EMBED_MODEL manquant en .env - utilisation du fallback interne")
        embed_model = "bge-m3:567m"

    # Timeout HTTP des embeddings. Sur VRAM contrainte (8 Go), le 1er appel à bge-m3
    # déclenche un swap de modèle (décharge llama, charge bge-m3) qui peut dépasser
    # 60 s -> l'ancien timeout court retombait silencieusement sur un vecteur nul
    # (jambe sémantique morte). 180 s couvre le swap à froid documenté.
    embed_timeout_s = int(os.environ.get("EMBED_TIMEOUT_S", "180"))

    rewriter_model = os.environ.get("REWRITER_MODEL", "")
    if not rewriter_model:
        logger.warning("REWRITER_MODEL manquant en .env - fallback interne utilisé")
        rewriter_model = "llama3.1:latest"

    gen_model = os.environ.get("GEN_MODEL", "")
    if not gen_model:
        logger.warning("GEN_MODEL manquant en .env - fallback interne utilisé")
        gen_model = "llama3.1:latest"
    
    # Nombre de couches offloadées sur GPU, injecté dans TOUTES les requêtes
    # Ollama (LLM + embeddings) quand il est défini. 0 = tout sur CPU : levier
    # mono-poste quand le GPU est occupé par un autre travail (sans ça, un
    # rechargement de modèle retente le GPU saturé et échoue en 500).
    # Vide (défaut) = laisser Ollama décider.
    ollama_num_gpu_raw = os.environ.get("OLLAMA_NUM_GPU", "").strip()
    ollama_num_gpu = int(ollama_num_gpu_raw) if ollama_num_gpu_raw else None

    llm_config = {
        "OLLAMA_HOST": ollama_host,
        "OLLAMA_AVAILABLE": ollama_available,
        "OLLAMA_NUM_GPU": ollama_num_gpu,
        "EMBED_MODEL": embed_model,
        "EMBED_TIMEOUT_S": embed_timeout_s,
        "REWRITER_MODEL": rewriter_model,
        "GEN_MODEL": gen_model,
        "LLM_NUM_CTX": int(os.environ.get("LLM_NUM_CTX", "16384")), # Nombre de tokens maximum que le modèle peut gérer
        # Contexte de l'enrichissement d'ingestion (rôle 'enhance'). Petit par design :
        # 1 chunk + consignes. Évite que le modèle charge son contexte par défaut
        # (131072 pour llama3.1 = ~30 Go de KV-cache) qui fige l'ingestion.
        "ENHANCE_NUM_CTX": int(os.environ.get("ENHANCE_NUM_CTX", "4096")),
    }
    
    # --------------------- CROSS-ENCODER (GPU) -------------------
    use_cross_encoder = os.environ.get("USE_CROSS_ENCODER", "true").lower() in ("true", "1", "yes")
    
    # Fallback pour le device du cross-encoder
    if num_gpus >= 2:
        default_ce_device = "cuda:1"  # Si 2+ GPUs, utiliser GPU 1 pour CE
    else:
        default_ce_device = "cuda:0" if num_gpus == 1 else "cpu"
    
    ce_device = os.environ.get("CE_DEVICE", default_ce_device)
    cross_encoder_model_raw = os.environ.get(
        "CROSS_ENCODER_LOCAL_PATH",
        str(_PROJECT_ROOT / "models" / "bge-reranker-v2-m3"),
    )
    cross_encoder_model = _resolve_project_path(cross_encoder_model_raw)
    
    ce_config = {
        "USE_CROSS_ENCODER": use_cross_encoder,
        "CE_DEVICE": ce_device,
        "CROSS_ENCODER_LOCAL_PATH": cross_encoder_model,
        # Seuil hors-scope : le cross-encoder renvoie ~0.500 (neutre) pour le hors-sujet
        # et juste au-dessus pour l'in-domain. 0.505 les sépare (calibré).
        "CE_RELEVANCE_THRESHOLD": float(os.environ.get("CE_RELEVANCE_THRESHOLD", "0.505")),
    }
    
    # --------------------- RETRIEVAL & RANKING -------------------
    retrieval_config = {
        "NUM_CHUNKS": int(os.environ.get("NUM_CHUNKS", "15")),
        "RRF_K": int(os.environ.get("RRF_K", "60")),
        "WEIGHT_SEMANTIC": float(os.environ.get("WEIGHT_SEMANTIC", "0.3")),
        "WEIGHT_BM25": float(os.environ.get("WEIGHT_BM25", "0.7")),
        "MAX_CHUNK_LENGTH": int(os.environ.get("MAX_CHUNK_LENGTH", "25000")),
        "MAX_QUERY_CHARS": int(os.environ.get("MAX_QUERY_CHARS", "512")),
    }
    
    # --------------------- CHUNKING & ENHANCEMENT -------------------
    enhancement_config = {
        "CHUNKING_MODE": os.environ.get("CHUNKING_MODE", "technical"),
        "AUTO_KEYWORDS": int(os.environ.get("AUTO_KEYWORDS", "5")),
        "AUTO_QUESTIONS": int(os.environ.get("AUTO_QUESTIONS", "3")),
        "ENHANCEMENT_MODEL": os.environ.get("ENHANCEMENT_MODEL", None) or None,
        # Parallélisme de l'enrichissement LLM (mots-clés/questions). Aligné sur
        # OLLAMA_NUM_PARALLEL côté serveur ; au-delà les requêtes sont juste mises en file.
        "ENHANCE_MAX_WORKERS": int(os.environ.get("ENHANCE_MAX_WORKERS", "3")),
        # Fusionner mots-clés + questions en un seul appel LLM (JSON). true = 1 appel
        # (rapide) ; false = 2 appels dédiés (historique). À comparer via le harnais d'éval.
        "ENHANCE_COMBINED": os.environ.get("ENHANCE_COMBINED", "true").lower() in ("true", "1", "yes"),
        "RAPTOR_SUMMARIES": os.environ.get("RAPTOR_SUMMARIES", "true").lower() in ("true", "1", "yes"),
        "RAPTOR_MIN_CHUNKS": int(os.environ.get("RAPTOR_MIN_CHUNKS", "3")),
        "RAPTOR_MAX_INPUT_CHUNKS": int(os.environ.get("RAPTOR_MAX_INPUT_CHUNKS", "15")),
    }
    
    # --------------------- AGENT ReAct -------------------
    # Agent ReAct : modèle de raisonnement, séparable du modèle de génération.
    # Par défaut le même que GEN_MODEL, mais surchargeable indépendamment.
    agent_model = os.environ.get("AGENT_MODEL", "").strip() or gen_model
    agent_config = {
        "AGENT_MODEL": agent_model,
        "AGENT_MAX_ITERATIONS": int(os.environ.get("AGENT_MAX_ITERATIONS", "4")),
        # Planificateur multi-hop (rôle « planner ») : produit le plan JSON du mode
        # agent (sous-questions) et le révise en cours de route. Par défaut le même
        # modèle que l'agent, surchargeable indépendamment via .env.
        "PLANNER_MODEL": os.environ.get("PLANNER_MODEL", "").strip() or agent_model,
        # Attribution par affirmation (passe post-hoc, core/attribution.py) :
        # budget TOTAL de la passe (appel LLM + validation + retry compris).
        # Dépassé -> la réponse garde ses marqueurs inline et l'event
        # `attribution` porte un statut d'échec — jamais bloquant.
        "ATTRIBUTION_TIMEOUT_S": float(os.environ.get("ATTRIBUTION_TIMEOUT_S", "60")),
    }

    # --------------------- MODE RAPIDE (latence) -------------------
    # Levier de latence : sur petit GPU, la GÉNÉRATION = ~85 % du temps,
    # car un modèle 8B + contexte 16k déborde la VRAM -> offload CPU lent. Activer
    # RAG_FAST_MODE bascule sur un prompt système épuré (un 3B se noie dans le prompt
    # détaillé tuné pour le 8B). Recette « rapide » dans .env : RAG_FAST_MODE=true +
    # GEN_MODEL=llama3.2:3b -> ~5-6x plus rapide (mesuré 27 s vs 150 s).
    # Compromis : un 3B est moins fiable qu'un 8B sur l'extraction critique (peut se
    # tromper de niveau EAL p.ex.) -> mode pour l'exploratoire ; 8B (défaut) pour
    # l'autoritatif. NB mesuré : plafonner les chunks ne gagne rien en vitesse (la
    # latence vient de la taille du modèle) et risque d'éjecter la bonne info -> on
    # garde tous les chunks par défaut ; GEN_NUM_CHUNKS reste réglable manuellement.
    fast_mode = os.environ.get("RAG_FAST_MODE", "false").lower() in ("true", "1", "yes")
    fast_config = {
        "RAG_FAST_MODE": fast_mode,
        "GEN_NUM_CHUNKS": int(os.environ.get("GEN_NUM_CHUNKS", str(retrieval_config["NUM_CHUNKS"]))),
    }

    # --------------------- AFFINAGE DU CONTEXTE (précision -> fidélité) -----------
    # Passes déterministes (sans LLM) sur les passages AVANT la génération, pour
    # réduire le bruit envoyé au modèle (maillon faible mesuré = génération/precision).
    # Activables pour un A/B (faithfulness/precision) via le harnais d'éval.
    context_refine_config = {
        # Déduplication des passages quasi-redondants (parent-child + RAPTOR se recouvrent).
        "CONTEXT_DEDUP": os.environ.get("CONTEXT_DEDUP", "true").lower() in ("true", "1", "yes"),
        "CONTEXT_DEDUP_THRESHOLD": float(os.environ.get("CONTEXT_DEDUP_THRESHOLD", "0.85")),
        # Réordonnancement « lost-in-the-middle » : meilleurs passages aux extrémités.
        "CONTEXT_REORDER": os.environ.get("CONTEXT_REORDER", "true").lower() in ("true", "1", "yes"),
    }

    # --------------------- SELF-RAG -------------------
    self_rag_config = {
        "SELF_RAG_ENABLED": os.environ.get("SELF_RAG_ENABLED", "false").lower() in ("true", "1", "yes"),
        "SELF_RAG_THRESHOLD": float(os.environ.get("SELF_RAG_THRESHOLD", "0.55")),
        "SELF_RAG_MAX_RETRIES": int(os.environ.get("SELF_RAG_MAX_RETRIES", "1")),
    }
    
    # --------------------- PARENT-CHILD -------------------
    parent_child_config = {
        "PARENT_CHILD_ENABLED": os.environ.get("PARENT_CHILD_ENABLED", "false").lower() in ("true", "1", "yes"),
        "PARENT_CHILD_MAX_CHARS": int(os.environ.get("PARENT_CHILD_MAX_CHARS", "4000")),
        "NUM_CHUNKS_PARENT_CHILD": int(os.environ.get("NUM_CHUNKS_PARENT_CHILD", "8")),
    }
    
    # --------------------- COLLECTION CHROMA DB -------------------
    chroma_config = {
        "COLLECTION_NAME": os.environ.get("COLLECTION_NAME", "test_rag"),
    }
    
    # --------------------- MESSAGES -------------------
    messages_config = {
        "OUT_OF_SCOPE_MESSAGE": os.environ.get(
            "OUT_OF_SCOPE_MESSAGE",
            "Je n'ai pas trouvé d'information sur ce sujet dans vos documents. "
            "Essayez de reformuler la question, ou choisissez d'autres documents à interroger."
        ),
    }
    
    # --------------------- MERGE ALL -------------------
    full_config = {
        **paths_config,
        **db_config,
        **llm_config,
        **agent_config,
        **fast_config,
        **context_refine_config,
        **ce_config,
        **retrieval_config,
        **enhancement_config,
        **self_rag_config,
        **parent_child_config,
        **chroma_config,
        **messages_config,
        # Infos système
        "NUM_GPUS": num_gpus,
        "CUDA_AVAILABLE": num_gpus > 0,
        "LOG_LEVEL": os.environ.get("LOG_LEVEL", "INFO"),
    }

    return full_config


# ============================================================================
# EXPORTS DIRECTS POUR LA CONFIG
# ============================================================================
CONFIG = get_config()

PROJECT_ROOT = Path(CONFIG["PROJECT_ROOT"])
DATA_DIR = Path(CONFIG["DATA_DIR"])
CHROMA_PATH = Path(CONFIG["CHROMA_PATH"])
VOCAB_SAVE_DIR = Path(CONFIG["VOCAB_SAVE_DIR"])
VOCAB_JSON_PATH = Path(CONFIG["VOCAB_JSON_PATH"])
MODELS_DIR = Path(CONFIG["MODELS_DIR"])

MONGO_URI = CONFIG["MONGO_URI"]
MONGO_HOST = CONFIG["MONGO_HOST"]
MONGO_PORT = CONFIG["MONGO_PORT"]
MONGO_DB = CONFIG["MONGO_DB"]
MONGO_AVAILABLE = CONFIG["MONGO_AVAILABLE"]

OLLAMA_HOST = CONFIG["OLLAMA_HOST"]
OLLAMA_AVAILABLE = CONFIG["OLLAMA_AVAILABLE"]
OLLAMA_NUM_GPU = CONFIG["OLLAMA_NUM_GPU"]
EMBED_MODEL = CONFIG["EMBED_MODEL"]
EMBED_TIMEOUT_S = CONFIG["EMBED_TIMEOUT_S"]
REWRITER_MODEL = CONFIG["REWRITER_MODEL"]
GEN_MODEL = CONFIG["GEN_MODEL"]
LLM_NUM_CTX = CONFIG["LLM_NUM_CTX"]
ENHANCE_NUM_CTX = CONFIG["ENHANCE_NUM_CTX"]

AGENT_MODEL = CONFIG["AGENT_MODEL"]
AGENT_MAX_ITERATIONS = CONFIG["AGENT_MAX_ITERATIONS"]
PLANNER_MODEL = CONFIG["PLANNER_MODEL"]
ATTRIBUTION_TIMEOUT_S = CONFIG["ATTRIBUTION_TIMEOUT_S"]
RAG_FAST_MODE = CONFIG["RAG_FAST_MODE"]
GEN_NUM_CHUNKS = CONFIG["GEN_NUM_CHUNKS"]
CONTEXT_DEDUP = CONFIG["CONTEXT_DEDUP"]
CONTEXT_DEDUP_THRESHOLD = CONFIG["CONTEXT_DEDUP_THRESHOLD"]
CONTEXT_REORDER = CONFIG["CONTEXT_REORDER"]

USE_CROSS_ENCODER = CONFIG["USE_CROSS_ENCODER"]
CROSS_ENCODER_LOCAL_PATH = CONFIG["CROSS_ENCODER_LOCAL_PATH"]
CE_DEVICE = CONFIG["CE_DEVICE"]
CE_RELEVANCE_THRESHOLD = CONFIG["CE_RELEVANCE_THRESHOLD"]

NUM_CHUNKS = CONFIG["NUM_CHUNKS"]
RRF_K = CONFIG["RRF_K"]
WEIGHT_SEMANTIC = CONFIG["WEIGHT_SEMANTIC"]
WEIGHT_BM25 = CONFIG["WEIGHT_BM25"]
MAX_CHUNK_LENGTH = CONFIG["MAX_CHUNK_LENGTH"]
MAX_QUERY_CHARS = CONFIG["MAX_QUERY_CHARS"]
#N_EXPANSIONS = CONFIG["N_EXPANSIONS"]

AUTO_KEYWORDS = CONFIG["AUTO_KEYWORDS"]
AUTO_QUESTIONS = CONFIG["AUTO_QUESTIONS"]
ENHANCEMENT_MODEL = CONFIG["ENHANCEMENT_MODEL"]
ENHANCE_MAX_WORKERS = CONFIG["ENHANCE_MAX_WORKERS"]
ENHANCE_COMBINED = CONFIG["ENHANCE_COMBINED"]
CHUNKING_MODE = CONFIG["CHUNKING_MODE"]
RAPTOR_SUMMARIES = CONFIG["RAPTOR_SUMMARIES"]
RAPTOR_MIN_CHUNKS = CONFIG["RAPTOR_MIN_CHUNKS"]
RAPTOR_MAX_INPUT_CHUNKS = CONFIG["RAPTOR_MAX_INPUT_CHUNKS"]

SELF_RAG_ENABLED = CONFIG["SELF_RAG_ENABLED"]
SELF_RAG_THRESHOLD = CONFIG["SELF_RAG_THRESHOLD"]
SELF_RAG_MAX_RETRIES = CONFIG["SELF_RAG_MAX_RETRIES"]

PARENT_CHILD_ENABLED = CONFIG["PARENT_CHILD_ENABLED"]
PARENT_CHILD_MAX_CHARS = CONFIG["PARENT_CHILD_MAX_CHARS"]
NUM_CHUNKS_PARENT_CHILD = CONFIG["NUM_CHUNKS_PARENT_CHILD"]

COLLECTION_NAME = CONFIG["COLLECTION_NAME"]

OUT_OF_SCOPE_MESSAGE = CONFIG["OUT_OF_SCOPE_MESSAGE"]
NUM_GPUS = CONFIG["NUM_GPUS"]
CUDA_AVAILABLE = CONFIG["CUDA_AVAILABLE"]
LOG_LEVEL = CONFIG["LOG_LEVEL"]

# Configure le logging structuré dès le chargement de la config (niveau via LOG_LEVEL).
try:
    from utils.logging_config import setup_logging
    setup_logging(LOG_LEVEL)
except Exception:  # pragma: no cover - le logging ne doit jamais bloquer le démarrage
    pass


# ============================================================================
# LOGGING & VALIDATION
# ============================================================================

def log_config_status():
    """Log le statut des services et ressources."""
    logger.info("=" * 70)
    logger.info("Configuration RAG chargée")
    logger.info("=" * 70)
    logger.info(f"Project root: {CONFIG['PROJECT_ROOT']}")
    logger.info(f"GPU(s) detected: {CONFIG['NUM_GPUS']}")
    logger.info(f"CE device: {CONFIG['CE_DEVICE']}")
    logger.info(f"Ollama: {'Online' if CONFIG['OLLAMA_AVAILABLE'] else 'Offline'} ({CONFIG['OLLAMA_HOST']})")
    logger.info(f"MongoDB: {'Online' if CONFIG['MONGO_AVAILABLE'] else 'Offline'} ({CONFIG['MONGO_HOST']}:{CONFIG['MONGO_PORT']})")
    logger.info("=" * 70)
