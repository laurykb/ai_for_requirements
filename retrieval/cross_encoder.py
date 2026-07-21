
# retrieval/cross_encoder.py
# Module de reranking final avec un Cross-Encoder HuggingFace chargé en local (aucun accès réseau)
# S'utilise après la fusion RRF pour affiner le classement des chunks/documents les plus pertinents.
# Modèle actuel : BAAI/bge-reranker-v2-m3 (multilingue XLM-RoBERTa, supporte français et anglais)
from sentence_transformers import CrossEncoder
import numpy as np
import os
from utils.logging_config import get_logger

logger = get_logger("rag.rerank")


def _is_oom(e: Exception) -> bool:
    """Vrai si l'exception est un manque de mémoire GPU (CUDA out of memory)."""
    return "out of memory" in str(e).lower()

# Variables globales pour stocker le modèle Cross-Encoder et son chemin (singleton)
_CE_MODEL = None  # Instance du modèle CrossEncoder
_CE_PATH = None   # Chemin du modèle actuellement chargé
_CE_DEVICE = None # Device actuellement utilisé

def _sigmoid(x):
    """Normalise les scores bruts XLM-RoBERTa en [0, 1] via sigmoïde."""
    return 1.0 / (1.0 + np.exp(-np.array(x, dtype=np.float32)))

def _load_cross_encoder_local(model_path, device=None):
    """
    Charge le modèle Cross-Encoder HuggingFace depuis un dossier local.
    Utilise un singleton pour éviter de recharger le modèle à chaque appel (optimisation mémoire et temps).
    Args:
        model_path: chemin du dossier contenant le modèle (doit contenir config.json, model.safetensors, tokenizer...)
        device: "cpu", "cuda", "cuda:0", "cuda:1", etc. (optionnel)
    Returns:
        Instance CrossEncoder prête à l'emploi.
    """
    global _CE_MODEL, _CE_PATH, _CE_DEVICE
    if _CE_MODEL is not None and _CE_PATH == model_path and _CE_DEVICE == device:
        # Modèle déjà chargé sur le bon device, on le réutilise
        return _CE_MODEL
    if not os.path.isdir(model_path):
        raise FileNotFoundError(f"[cross_encoder] Dossier modèle introuvable: {model_path}")
    logger.info("[rerank] Chargement Cross-Encoder local: %s (device=%s)", model_path, device or "auto")
    try:
        _CE_MODEL = CrossEncoder(model_path, device=device)
    except RuntimeError as e:
        # Robustesse mono-poste : VRAM saturée (ex. gros modèle Ollama épinglé) ->
        # repli CPU au lieu d'échouer. Le rerank de ~15 chunks reste rapide sur CPU.
        if not _is_oom(e):
            raise
        logger.warning("[rerank] VRAM saturée -> repli du Cross-Encoder sur CPU.")
        device = "cpu"
        _CE_MODEL = CrossEncoder(model_path, device=device)
    _CE_PATH = model_path
    _CE_DEVICE = device
    return _CE_MODEL

def rerank_cross_encoder(query, items, model_path, device=None):
    """
    Rerank une liste d'items (chunks/documents) avec un Cross-Encoder local.
    - Calcule un score de similarité (ce_score) entre la requête et chaque chunk.
    - Pour bge-reranker-v2-m3 (XLM-RoBERTa), applique une sigmoïde pour normaliser en [0, 1].
    - Ajoute ce score à chaque item.
    - Trie la liste par score CE décroissant (plus pertinent en haut), puis par score RRF en cas d'égalité.
    Args:
        query: la requête utilisateur (str)
        items: liste de dicts, chaque dict doit contenir au moins "doc" (texte du chunk)
        model_path: chemin local du modèle Cross-Encoder
        device: "cpu", "cuda", etc. (optionnel)
    Returns:
        Liste triée d'items, chaque item enrichi du score "ce_score"
    """
    if not items:
        return items
    global _CE_MODEL
    ce = _load_cross_encoder_local(model_path, device=device)
    # Prépare les paires (requête, chunk) pour le modèle
    pairs = [(query, it.get("doc", "") or "") for it in items]
    # Prédit les scores bruts de similarité pour chaque paire
    try:
        raw_scores = ce.predict(pairs)
    except RuntimeError as e:
        # OOM à l'inférence (la VRAM s'est remplie APRÈS le chargement) :
        # rechargement sur CPU et nouvelle prédiction, plutôt qu'un échec.
        if not _is_oom(e):
            raise
        logger.warning("[rerank] OOM à l'inférence -> rechargement du Cross-Encoder sur CPU.")
        _CE_MODEL = None
        ce = _load_cross_encoder_local(model_path, device="cpu")
        raw_scores = ce.predict(pairs)
    # bge-reranker-v2-m3 retourne des logits bruts -> normalisation sigmoïde en [0, 1]
    scores = _sigmoid(raw_scores)
    for it, sc in zip(items, scores):
        it["ce_score"] = float(sc)
    # Trie les items par score CE décroissant, puis par score RRF décroissant en cas d'égalité
    items.sort(key=lambda x: (x.get("ce_score", 0.0), x.get("rrf", 0.0)), reverse=True)
    return items
