"""Tests unitaires du serveur MCP (enregistrement de l'outil + schéma)."""
import asyncio

import rag_mcp_server as srv


def test_server_name():
    assert srv.mcp.name == "rag_mcp"


def test_rag_search_tool_registered_with_schema():
    tools = asyncio.run(srv.mcp.list_tools())
    by_name = {t.name: t for t in tools}
    assert "rag_search" in by_name
    tool = by_name["rag_search"]
    assert tool.description and "documentaire" in tool.description.lower()
    props = (tool.inputSchema or {}).get("properties", {})
    assert "query" in props
    assert "document" in props
