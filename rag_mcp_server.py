#!/usr/bin/env python3
"""
Serveur MCP (Model Context Protocol) exposant le RAG documentaire.

Annonce l'outil `rag_search` et l'exécute à la demande via le transport stdio (local).
Réutilise le contrat d'outil de tools.rag_tool (validation + exécution du pipeline RAG).

Branchement dans un hôte MCP (ex. Claude Desktop) :
    {
      "mcpServers": {
        "rag": {
          "command": "/chemin/vers/.venv/bin/python",
          "args": ["/chemin/vers/rag_mcp_server.py"]
        }
      }
    }

Prérequis : MongoDB et Ollama démarrés.
"""
import sys
import json
import asyncio
import contextlib
from pathlib import Path
from typing import Annotated, Optional

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pydantic import Field
from mcp.server.fastmcp import FastMCP

from tools.rag_tool import run_tool

mcp = FastMCP("rag_mcp")


@mcp.tool(
    name="rag_search",
    annotations={
        "title": "Recherche RAG documentaire (ANSSI / Critères Communs)",
        "readOnlyHint": True,       # ne modifie rien
        "destructiveHint": False,
        "idempotentHint": False,    # la génération LLM peut varier
        "openWorldHint": False,     # corpus local fixe
    },
)
async def rag_search(
    query: Annotated[str, Field(
        description="La question, en langage naturel (ex: 'Quel est le niveau EAL de la TOE ?').",
        min_length=2, max_length=2000)],
    document: Annotated[Optional[str], Field(
        description="Nom exact du document pour restreindre la recherche "
                    "(optionnel ; sinon recherche sur tout l'index).")] = None,
) -> str:
    """Recherche dans la base documentaire technique (cibles de sécurité ANSSI / Critères
    Communs) et renvoie une réponse sourcée et citée.

    À utiliser pour toute question dont la réponse se trouve dans les documents indexés :
    exigences, fonctions de sécurité, menaces, hypothèses, identifiants, normes, acronymes
    du document. Ne PAS utiliser pour des connaissances générales hors de ces documents.

    Args:
        query (str) : la question en langage naturel (2..2000 caractères).
        document (str, optionnel) : nom exact du document pour restreindre la recherche.

    Returns:
        str : JSON. En cas de succès :
            {
              "ok": true,
              "answer": str,                 # réponse rédigée, avec citations [1], [2]…
              "sources": [ {"idx": int, "source": str, "section": str|null, "page": int|null} ],
              "num_chunks": int,
              "hors_scope": bool,            # true si rien de pertinent dans le corpus
              "latency_s": float
            }
        En cas d'erreur : { "ok": false, "error": str }.

    Exemples :
        - « Quel est le niveau EAL de la TOE Mistral ? » → query="Quel est le niveau EAL de la TOE Mistral ?"
        - « Quelles sont les fonctions de sécurité ? » (sur un doc précis) → document="ANSSI-CC-cible_2011-1-20.md"
    """
    def _work():
        # stdout est RÉSERVÉ au JSON-RPC en transport stdio : on redirige les print()
        # du pipeline (rewrite/graph/ner…) vers stderr pour ne pas corrompre le protocole.
        with contextlib.redirect_stdout(sys.stderr):
            return run_tool(
                "rag_search",
                {"query": query, "document": (document.strip() if document else None)},
            )

    # Exécution dans un thread : le pipeline RAG est bloquant (embeddings, rerank, LLM).
    result = await asyncio.to_thread(_work)
    return json.dumps(result, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run()
