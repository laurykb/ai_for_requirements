"""
Routage de modèles : un modèle par rôle, pas un seul pour tout.

On n'emploie pas le même LLM pour toutes les tâches : un modèle léger peut suffire
aux travaux mécaniques (réécriture de requête, raisonnement de l'agent, jugement
d'éval), tandis que la génération de la réponse finale peut utiliser un modèle plus
fort. Ce module centralise le choix « quel modèle pour quel rôle » et la construction
des clients LLM, au lieu de disperser des Ollama(model=...) dans le code. Chaque rôle
est surchargeable via .env (REWRITER_MODEL / AGENT_MODEL / GEN_MODEL).

Rôles :
    rewrite   réécriture/condensation de requête, réécriture Self-RAG (REWRITER_MODEL)
    agent     raisonnement de l'agent ReAct (AGENT_MODEL)
    generate  réponse finale ancrée et citée (GEN_MODEL)
    judge     LLM-as-judge du harnais d'éval (REWRITER_MODEL par défaut)
    enhance   enrichissement d'ingestion : mots-clés/questions/résumés/descriptions
              de tables (ENHANCEMENT_MODEL si défini, sinon REWRITER_MODEL)

NB : le rôle `enhance` n'est PAS construit via `build_llm` - l'enrichissement parle
directement à l'API Ollama `/api/chat` (pour désactiver le « thinking » des modèles
qwen3 et plafonner `num_predict`). Le routeur reste néanmoins la SOURCE UNIQUE du choix
de modèle ET des hyperparamètres de ce rôle, exposés via `ollama_options("enhance")`.
Le routage = « quel modèle/quels paramètres pour quel rôle », indépendamment du client.
"""
from __future__ import annotations

from utils.logging_config import get_logger
from env_config import (
    REWRITER_MODEL, GEN_MODEL, AGENT_MODEL, ENHANCEMENT_MODEL,
    NUM_CHUNKS, LLM_NUM_CTX, ENHANCE_NUM_CTX,
)

logger = get_logger("rag.router")

# Override RUNTIME du modèle de génération : permet de changer le modèle « à chaud »
# depuis l'UI (bouton « Charger le modèle » du chat), sans réécrire .env ni redémarrer.
# None -> on retombe sur GEN_MODEL (.env). Le résolveur paresseux ci-dessous le lit à
# chaque génération, donc le changement est immédiat pour les requêtes suivantes.
_GENERATE_OVERRIDE: str | None = None


def set_generate_model(name: str | None) -> None:
    """Fixe (ou efface si None/"") le modèle de génération pour le process courant."""
    global _GENERATE_OVERRIDE
    _GENERATE_OVERRIDE = (name or "").strip() or None


def get_generate_model() -> str:
    """Modèle de génération effectif (override runtime, sinon GEN_MODEL de .env)."""
    return _GENERATE_OVERRIDE or GEN_MODEL


# Table de routage : rôle -> modèle. Résolveurs paresseux (lambda) pour refléter la
# config courante au moment de l'appel plutôt que figer à l'import.
_ROLE_MODELS = {
    "rewrite":  lambda: REWRITER_MODEL,
    "agent":    lambda: AGENT_MODEL,
    "generate": lambda: _GENERATE_OVERRIDE or GEN_MODEL,
    "judge":    lambda: REWRITER_MODEL,
    # ENHANCEMENT_MODEL est l'override explicite (historique, .env) ; à défaut on
    # réutilise le modèle de réécriture (léger, sans « thinking »), inchangé.
    "enhance":  lambda: ENHANCEMENT_MODEL or REWRITER_MODEL,
}

# Hyperparamètres par défaut par rôle - reproduisent le tuning qui était dispersé
# dans ask.py / llm_answer.py / agent.py / evaluation.py.
#
# NB : on NE force PLUS `keep_alive=-1` (Forever). Sur un GPU unique 8 Go, llama et
# bge-m3 ne co-résident pas : pinner un modèle « Forever » n'évite aucun swap mais
# WEDGE le scheduler Ollama (le modèle reste coincé en « Stopping... » et bloque le
# chargement de l'embedder -> embeddings nuls). Le cycle de vie des modèles est
# désormais laissé au serveur (variable OLLAMA_KEEP_ALIVE), là où il doit vivre.
_ROLE_PARAMS = {
    # num_ctx plafonné sur TOUS les rôles : sans cela Ollama charge le contexte par
    # défaut du modèle (131072 pour llama3.1). Avec OLLAMA_NUM_PARALLEL>1, le KV-cache
    # est multiplié par le nb de slots -> débordement VRAM/CPU (148 Go observés). Les
    # tâches légères (rewrite/judge) n'ont besoin que de quelques k tokens.
    "rewrite":  {"temperature": 0.2, "num_ctx": 8192},
    "agent":    {"temperature": 0.1, "num_ctx": LLM_NUM_CTX},
    "generate": {"temperature": 0.3, "top_k": NUM_CHUNKS, "top_p": 0.8,
                 "repeat_penalty": 1.5, "num_ctx": LLM_NUM_CTX},
    "judge":    {"temperature": 0.0, "num_ctx": 8192},
    # Enrichissement : très factuel, réponse courte plafonnée (anciennement codé en
    # dur dans nlp/chunk_enhancer._call_ollama). Consommé par ollama_options().
    # num_ctx plafonné : les prompts d'enrichissement = 1 chunk (~1 Ko) + consignes.
    # Sans ce plafond, Ollama charge le contexte par défaut du modèle (131072 pour
    # llama3.1 -> 30 Go de KV-cache) qui thrashe la VRAM et FIGE l'ingestion.
    "enhance":  {"temperature": 0.1, "top_p": 0.9, "num_predict": 300,
                 "num_ctx": ENHANCE_NUM_CTX},
}


def model_for(role: str) -> str:
    """Nom du modèle assigné à un rôle (ex: 'generate' -> 'llama3.1:8b')."""
    resolver = _ROLE_MODELS.get(role)
    if resolver is None:
        raise ValueError(f"Rôle LLM inconnu : '{role}'. Connus : {sorted(_ROLE_MODELS)}.")
    return resolver()


def routing_table() -> dict:
    """Vue {rôle: modèle} - introspection pour l'UI (Paramètres/Observabilité) et la démo."""
    return {role: resolver() for role, resolver in _ROLE_MODELS.items()}


def llm_kwargs(role: str, **overrides) -> dict:
    """Construit le dict d'arguments du LLM pour un rôle (PUR, donc testable hors-ligne).

    `overrides` ajuste ponctuellement (ex: `keep_alive=-1` pour le streaming, ou
    `model=...` pour forcer un modèle). Les valeurs None sont ignorées (= « garder le
    défaut du rôle »).
    """
    params = dict(_ROLE_PARAMS.get(role, {}))
    model_override = overrides.pop("model", None)
    params.update({k: v for k, v in overrides.items() if v is not None})
    params["model"] = model_override or model_for(role)
    return params


def ollama_options(role: str, **overrides) -> dict:
    """Options de génération Ollama natives d'un rôle, SANS le nom du modèle.

    Pour les chemins qui parlent à l'API HTTP Ollama directement (ex: l'enrichissement
    d'ingestion via `/api/chat`) plutôt que via `OllamaClient`. La table de rôle reste
    la source unique du tuning (`temperature`, `top_p`, `num_predict`...). PUR/testable.
    """
    opts = llm_kwargs(role, **overrides)
    opts.pop("model", None)
    return opts


def build_llm(role: str, **overrides):
    """Construit le client LLM d'un rôle. UNIQUE point de construction du pipeline.

    Renvoie un `OllamaClient` (HTTP direct, sans langchain). Les hyperparamètres du
    rôle (temperature, num_ctx, top_k...) deviennent les `options` Ollama ; `model` et
    `keep_alive` sont passés au niveau supérieur.
    """
    from core.llm_client import OllamaClient
    kw = llm_kwargs(role, **overrides)
    model = kw.pop("model")
    keep_alive = kw.pop("keep_alive", None)
    logger.debug("LLM rôle=%s -> modèle=%s", role, model)
    return OllamaClient(model=model, options=kw, keep_alive=keep_alive)
