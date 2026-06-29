"""
Le RAG exposé comme un outil (tool calling).

Contrat d'outil standard : un nom, une description en langage naturel (sur laquelle
le modèle se fonde pour décider quand l'employer), et un schéma JSON des paramètres.
L'exécuteur réutilise le pipeline RAG (core.ask) et renvoie une réponse structurée
(answer + sources + métriques), avec des erreurs informatives.

run_tool() valide le nom et les arguments avant d'exécuter. Ce module est réutilisé
par le serveur MCP et l'agent ReAct, et reste testable indépendamment de l'UI.
"""
from __future__ import annotations

import time

TOOL_NAME = "rag_search"

TOOL_DESCRIPTION = (
    "Recherche dans la base documentaire technique (cibles de sécurité ANSSI / Critères "
    "Communs) et renvoie une réponse sourcée et citée. À utiliser pour toute question "
    "dont la réponse se trouve dans les documents indexés : exigences, fonctions de "
    "sécurité, menaces, hypothèses, identifiants, normes, acronymes du document. "
    "Ne pas utiliser pour des connaissances générales hors de ces documents."
)

# Schéma JSON des paramètres (JSON Schema — format universel du tool calling).
TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "La question, en langage naturel.",
        },
        "document": {
            "type": "string",
            "description": "Nom exact du document pour restreindre la recherche "
                           "(optionnel ; sinon, recherche sur tout l'index).",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}


def tool_spec() -> dict:
    """Spécification d'outil générique (réutilisable pour MCP et agents)."""
    return {"name": TOOL_NAME, "description": TOOL_DESCRIPTION, "parameters": TOOL_PARAMETERS}


def openai_tool_spec() -> dict:
    """Spécification au format function-calling OpenAI."""
    return {"type": "function", "function": tool_spec()}


def rag_search(query: str, document: str = None, system_prompt: str = None,
               mode: str = "answer", max_passages: int = 8) -> dict:
    """
    Exécuteur de l'outil rag_search : interroge le RAG et renvoie un résultat structuré.

    mode="answer" (défaut) : pipeline complet (retrieval + GÉNÉRATION d'une réponse
        rédigée). Retour : { ok, answer, sources, num_chunks, hors_scope, latency_s }.
    mode="passages" : RÉCUPÉRATION SEULE (sans génération) — bien plus rapide. Pour
        l'agent ReAct, qui RAISONNE sur les passages et rédige LUI-MÊME la réponse
        finale (une seule génération en fin de boucle au lieu d'une par appel d'outil).
        Retour : { ok, passages: [{source, section, page, text}], num_chunks,
        hors_scope, latency_s }.
    """
    if not query or not str(query).strip():
        return {"ok": False, "error": "Le paramètre 'query' est requis et ne doit pas être vide."}

    t0 = time.perf_counter()

    if mode == "passages":
        from core.ask import retrieve_only
        try:
            _q, chunks = retrieve_only(str(query), source_filter=document)
        except Exception as e:
            return {"ok": False, "error": f"Échec du retrieval : {e}"}
        kept = (chunks or [])[:max_passages]
        passages = [
            {
                "source": c.get("meta", {}).get("source"),
                "section": c.get("meta", {}).get("heading") or c.get("meta", {}).get("breadcrumb") or None,
                "page": c.get("meta", {}).get("page_number"),
                "text": (c.get("doc", "") or "")[:1000],
            }
            for c in kept
        ]
        return {
            "ok": True,
            "mode": "passages",
            "passages": passages,            # texte TRONQUÉ : contexte vu par le LLM agent
            # Chunks INTÉGRAUX (doc complet + métadonnées enrichies), pour l'UI/persistance —
            # JAMAIS réinjectés au LLM (sinon on noierait son contexte). Voir core.agent.
            "chunks": [
                {"doc": c.get("doc", ""), "ce_score": c.get("ce_score"), "meta": c.get("meta", {})}
                for c in kept
            ],
            "num_chunks": len(chunks or []),
            "hors_scope": not chunks,
            "latency_s": round(time.perf_counter() - t0, 2),
        }

    from core.ask import process_query
    from env_config import OUT_OF_SCOPE_MESSAGE

    try:
        answer, chunks, citations = process_query(
            str(query), source_filter=document, system_prompt=system_prompt
        )
    except Exception as e:
        return {"ok": False, "error": f"Échec du retrieval/génération : {e}"}

    hors_scope = (answer == OUT_OF_SCOPE_MESSAGE) or not chunks
    return {
        "ok": True,
        "answer": answer or "",
        "sources": [
            {
                "idx": c.get("idx"),
                "source": c.get("source"),
                "section": c.get("heading") or c.get("breadcrumb") or None,
                "page": c.get("page"),
            }
            for c in (citations or [])
        ],
        "num_chunks": len(chunks or []),
        "hors_scope": bool(hors_scope),
        "latency_s": round(time.perf_counter() - t0, 2),
    }


# Registre des outils disponibles (un seul pour l'instant).
_TOOLS = {TOOL_NAME: rag_search}


def run_tool(name: str, arguments: dict) -> dict:
    """
    Dispatcher : valide le nom et les arguments, puis exécute l'outil.
    Le code (et non le modèle) reste l'unique exécutant. Renvoie une erreur
    informative en cas de problème.
    """
    fn = _TOOLS.get(name)
    if fn is None:
        return {"ok": False, "error": f"Outil inconnu : '{name}'. Outils disponibles : {list(_TOOLS)}."}
    if not isinstance(arguments, dict):
        return {"ok": False, "error": "Le champ 'arguments' doit être un objet JSON."}
    if "query" not in arguments:
        return {"ok": False, "error": "Paramètre requis manquant : 'query'."}

    allowed = set(TOOL_PARAMETERS["properties"]) | {"system_prompt", "mode", "max_passages"}
    kwargs = {k: v for k, v in arguments.items() if k in allowed}
    return fn(**kwargs)
