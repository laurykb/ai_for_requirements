"""Tests unitaires du RAG exposé comme outil (tool calling)."""
from tools.rag_tool import (
    tool_spec, openai_tool_spec, run_tool, rag_search, TOOL_NAME,
)


def test_tool_spec_structure():
    s = tool_spec()
    assert s["name"] == "rag_search"
    assert isinstance(s["description"], str) and s["description"]
    params = s["parameters"]
    assert params["required"] == ["query"]
    assert "query" in params["properties"]
    assert params["additionalProperties"] is False


def test_openai_tool_spec_format():
    s = openai_tool_spec()
    assert s["type"] == "function"
    assert s["function"]["name"] == "rag_search"


def test_run_tool_unknown_name():
    r = run_tool("does_not_exist", {"query": "x"})
    assert r["ok"] is False
    assert "inconnu" in r["error"].lower()


def test_run_tool_rejects_bad_arguments():
    assert run_tool(TOOL_NAME, {})["ok"] is False           # query manquant
    assert run_tool(TOOL_NAME, "pas un dict")["ok"] is False  # mauvais type


def test_rag_search_empty_query_is_informative_error():
    r = rag_search("")
    assert r["ok"] is False
    assert "query" in r["error"].lower()
