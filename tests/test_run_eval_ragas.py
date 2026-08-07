"""Test du mode --mode ragas de evals/run_eval.py (offline, métriques RAGAS stubées)."""


def test_ragas_mode_item(monkeypatch):
    import evals.run_eval as R
    from core import evaluation as E
    # stub retrieval+génération et métriques pour rester offline
    monkeypatch.setattr("core.ask.process_query",
                        lambda q, source_filter=None: ("réponse", [{"doc": "ctx"}], []))
    monkeypatch.setattr(E, "faithfulness_ragas", lambda g, c, llm=None: 0.9)
    monkeypatch.setattr(E, "context_recall_ragas", lambda r, c, llm=None: 0.8)
    monkeypatch.setattr(E, "context_precision_ragas", lambda q, r, c, topk=5, llm=None: 0.7)
    monkeypatch.setattr(E, "answer_relevancy_ragas", lambda g, q, llm=None, embed=None, n=3: 0.85)
    item = {"question": "Q ?", "answer": "ref", "expected_keywords": []}
    m = R._evaluate_item(item, mode="ragas", source_filter=None, use_judge=True, judge=None)
    assert m["status"] == "ok"
    assert m["faithfulness"] == 0.9 and m["context_recall"] == 0.8
    assert m["context_precision"] == 0.7 and m["answer_relevancy"] == 0.85


def test_write_last_eval_persists_aggregate(tmp_path):
    """Régression : run_eval doit écrire evals/last_eval.json avec l'agrégat
    complet (dont answer_relevancy) — le mode ragas ne l'écrivait pas."""
    import json
    import evals.run_eval as R
    path = tmp_path / "last_eval.json"
    agg = {"context_recall": 0.53, "answer_relevancy": 0.6, "faithfulness": 0.5}
    R._write_last_eval(path, dataset_name="golden", mode="ragas",
                       run_name="ragas-x", aggregate=agg, num_questions=30)
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["mode"] == "ragas"
    assert data["run_name"] == "ragas-x"
    assert data["num_questions"] == 30
    assert data["aggregate"]["answer_relevancy"] == 0.6


def test_breakdown_groups_metrics_by_query_type():
    from evals.run_eval import _breakdown
    rows = [
        {"status": "ok", "query_type": "pointed", "strategy_mode": "rag",
         "strategy_profile": "hybrid_precise", "keyword_hit_rate": 1.0},
        {"status": "ok", "query_type": "pointed", "strategy_mode": "rag",
         "strategy_profile": "hybrid_precise", "keyword_hit_rate": 0.0},
        {"status": "ok", "query_type": "aggregate", "strategy_mode": "synth",
         "strategy_profile": "corpus_coverage", "keyword_hit_rate": 0.75},
    ]
    out = _breakdown(rows)
    assert out["by_query_type"]["pointed"]["num_questions"] == 2
    assert out["by_query_type"]["pointed"]["aggregate"]["keyword_hit_rate"] == 0.5
    assert out["by_strategy_mode"]["synth"]["num_questions"] == 1


def test_spatial_candidate_dataset_has_evidence_and_categories():
    import json
    from pathlib import Path
    path = Path(__file__).parent.parent / "evals" / "golden_space_candidates_v1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "human_validated_provisional"
    assert {i["query_type"] for i in data["items"]} >= {"pointed", "aggregate", "multi_hop"}
    assert all(i["status"] == "human_validated_provisional" and i["evidence"] for i in data["items"])
    structured = [i for i in data["items"] if i["query_type"] == "structured_aggregate"]
    assert len(structured) > len(data["items"]) / 2
    assert all(i.get("expected_axes") for i in structured)


def test_eval_run_persists_dataset_mode_and_breakdown(monkeypatch):
    from core import evaluation as E
    captured = {}
    class Col:
        def insert_one(self, doc):
            captured.update(doc)
            return type("R", (), {"inserted_id": "x"})()
    class DB:
        def __getitem__(self, _): return Col()
    class Client:
        def __getitem__(self, _): return DB()
    monkeypatch.setattr(E, "get_client", lambda: Client())
    E.save_eval_run_to_mongo([], "r", dataset="space", mode="retrieval",
                             breakdown={"by_query_type": {}})
    assert captured["dataset"] == "space"
    assert captured["mode"] == "retrieval"
    assert "by_query_type" in captured["breakdown"]


def test_execute_adaptive_runs_synthesis_for_structured_query(monkeypatch):
    import evals.run_eval as R
    def fake_synth(question):
        yield {"type": "done", "result": {"answer": "table",
               "chunks": [{"doc": "preuve"}]}}
    monkeypatch.setattr("core.synthesize_corpus.synthesize_corpus", fake_synth)
    answer, chunks, citations = R._execute_adaptive(
        "q", {"mode": "synth"}, source_filter=None)
    assert answer == "table" and chunks == [{"doc": "preuve"}]
    assert citations == []

def test_execute_adaptive_runs_rag_for_pointed_query(monkeypatch):
    import evals.run_eval as R
    monkeypatch.setattr("core.ask.process_query",
                        lambda q, source_filter=None: ("answer", ["chunk"], ["citation"]))
    out = R._execute_adaptive("q", {"mode": "rag"}, source_filter="a.md")
    assert out == ("answer", ["chunk"], ["citation"])


def test_execute_deep_uses_deep_model_and_unloading_wrapper(monkeypatch):
    import evals.run_eval as R
    calls = {}

    class LLM:
        invoke = staticmethod(lambda prompt: "deep")

    def fake_build(role, **kwargs):
        calls["build"] = (role, kwargs)
        return LLM()

    def fake_events(question, model, **kwargs):
        calls["events"] = (question, model, kwargs)
        yield {"type": "done", "result": {
            "answer": "deep answer", "chunks": [{"doc": "proof"}],
        }}

    monkeypatch.setattr("core.model_router.build_llm", fake_build)
    monkeypatch.setattr("api.rag._deep_synthesis_events", fake_events)
    monkeypatch.setattr("env_config.DEEP_RESEARCH_MODEL", "deep-model")

    answer, chunks, citations = R._execute_deep("question")

    assert answer == "deep answer"
    assert chunks == [{"doc": "proof"}]
    assert citations == []
    assert calls["build"] == ("synthesize", {
        "model": "deep-model", "think": True, "num_predict": 4096,
    })
    assert calls["events"][0:2] == ("question", "deep-model")
    assert calls["events"][2]["llm"] is LLM.invoke


def test_deep_ragas_forces_deep_strategy(monkeypatch):
    import evals.run_eval as R
    from core import evaluation as E

    monkeypatch.setattr(R, "_execute_deep",
                        lambda q: ("answer", [{"doc": "ctx"}], []))
    monkeypatch.setattr(E, "faithfulness_ragas", lambda *a, **k: 0.9)
    monkeypatch.setattr(E, "context_recall_ragas", lambda *a, **k: 0.8)
    monkeypatch.setattr(E, "context_precision_ragas", lambda *a, **k: 0.7)
    monkeypatch.setattr(E, "answer_relevancy_ragas", lambda *a, **k: 0.85)
    row = R._evaluate_item(
        {"question": "Q", "answer": "ref", "expected_keywords": []},
        mode="deep_ragas", source_filter=None, use_judge=True, judge=object(),
    )
    assert row["status"] == "ok"
    assert row["strategy_mode"] == "synth"
    assert row["strategy_profile"] == "deep_research"


def test_adaptive_ragas_measures_selected_pipeline(monkeypatch):
    import evals.run_eval as R
    from core import evaluation as E
    monkeypatch.setattr(R, "_execute_adaptive",
                        lambda q, strategy, source_filter=None: ("réponse", [{"doc": "ctx"}], []))
    monkeypatch.setattr(E, "faithfulness_ragas", lambda g, c, llm=None: 0.91)
    monkeypatch.setattr(E, "context_recall_ragas", lambda r, c, llm=None: 0.81)
    monkeypatch.setattr(E, "context_precision_ragas", lambda q, r, c, topk=5, llm=None: 0.71)
    monkeypatch.setattr(E, "answer_relevancy_ragas", lambda g, q, llm=None, embed=None, n=3: 0.86)
    item = {"question": "Q ?", "answer": "ref", "mode": "agent"}
    out = R._evaluate_item(item, mode="adaptive_ragas", source_filter=None, use_judge=True, judge=None)
    assert out["status"] == "ok"
    assert out["strategy_mode"] == "agent"
    assert out["faithfulness"] == 0.91
