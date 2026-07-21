"""Tests unitaires de la couche de tracing (observabilité)."""
import utils.tracing as tracing
from utils.tracing import start_trace, span, Trace, _Span


def test_span_tree_to_dict():
    tr = Trace("root", {"a": 1})
    s = _Span("child", {})
    s.duration_ms = 5.0
    tr.root.children.append(s)
    d = tr.to_dict()
    assert d["name"] == "root"
    assert d["metadata"]["a"] == 1
    assert d["children"][0]["name"] == "child"
    assert "timestamp" in d


def test_span_noop_without_active_trace():
    # Hors de toute trace, span() ne fait rien et retourne None.
    with span("orphan") as s:
        assert s is None


def test_start_trace_nesting_and_metadata(monkeypatch):
    captured = {}
    monkeypatch.setattr(tracing, "_persist", lambda tr: captured.update(d=tr.to_dict()))

    with start_trace("rag.query", query="q") as tr:
        with span("retrieve") as s:
            with span("rerank"):
                pass
            s.set("num_chunks", 3)
        tr.set("answer_len", 10)

    d = captured["d"]
    assert d["name"] == "rag.query"
    assert d["metadata"]["answer_len"] == 10
    assert d["duration_ms"] is not None
    retrieve = d["children"][0]
    assert retrieve["name"] == "retrieve"
    assert retrieve["metadata"]["num_chunks"] == 3
    assert retrieve["duration_ms"] is not None
    assert retrieve["children"][0]["name"] == "rerank"


def test_recent_traces_returns_list():
    assert isinstance(tracing.recent_traces(), list)
