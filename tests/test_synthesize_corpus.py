# tests/test_synthesize_corpus.py
from core.synthesize_corpus import synthesize_corpus, _document_coverage


def test_pipeline_emits_glassbox_events_and_reduces(monkeypatch):
    monkeypatch.setenv("CORPUS_MAP_CONCURRENCY", "1")
    # 2 documents pré-filtrés ; extraction factice par doc ; reduce factice.
    prefilter = lambda aspect: ["a.md", "b.md"]
    def extract(document, aspect):
        data = {"a.md": ["Déni de service", "Rejeu"], "b.md": ["Injection SQL", "Rejeu"]}
        return {"document": document, "items": data[document], "raw": ""}
    captured_prompts = []
    def reduce_llm(prompt):
        captured_prompts.append(prompt)
        return ("**Réseau**\n- Déni de service [src: a.md]\n- Rejeu [src: a.md, src: b.md]\n"
                "**Applicatif**\n- Injection SQL [src: b.md]")

    events = list(synthesize_corpus("catégorise les attaques", aspect="attaques",
                                    llm=reduce_llm, prefilter=prefilter, extract=extract))
    types = [e["type"] for e in events]
    assert types[0] == "stage" and events[0]["stage"] == "prefilter"
    assert events[0]["documents"] == ["a.md", "b.md"]
    map_events = [e for e in events if e["type"] == "map"]
    assert len(map_events) == 2 and map_events[0]["total"] == 2
    assert any(e["type"] == "stage" and e["stage"] == "reduce" for e in events)
    done = events[-1]
    assert done["type"] == "done"
    assert set(done["result"]["documents"]) == {"a.md", "b.md"}
    assert "Injection SQL" in done["result"]["answer"]

    # -- Fix B : attribution inline déterministe dans le matériau REDUCE -------
    # Chaque item du matériau porte son étiquette source verbatim (dérivée de
    # ex["document"], jamais du LLM) - la reformulation en (a.md) est interdite.
    assert len(captured_prompts) == 1
    material = captured_prompts[0]
    assert "Déni de service  [src: a.md]" in material
    assert "Rejeu  [src: a.md]" in material
    assert "Injection SQL  [src: b.md]" in material
    assert "Rejeu  [src: b.md]" in material
    # La synthèse finale préserve bien les étiquettes (pas de citation inventée).
    assert "[src: a.md]" in done["result"]["answer"]
    assert "[src: b.md]" in done["result"]["answer"]


def test_document_coverage_distinguishes_evidence_and_empty_documents():
    coverage = _document_coverage(
        ["a.md", "b.md", "c.md"],
        [{"document": "a.md", "items": ["fait"]},
         {"document": "b.md", "items": []},
         {"document": "c.md", "items": []}],
    )
    assert coverage == {
        "attempted": 3, "with_evidence": 1, "ratio": 1 / 3,
        "documents_with_evidence": ["a.md"],
        "documents_without_evidence": ["b.md", "c.md"],
    }


def test_pipeline_no_relevant_docs(monkeypatch):
    events = list(synthesize_corpus("q", aspect="x", llm=lambda p: "",
                                    prefilter=lambda a: [], extract=lambda d, a: None))
    done = events[-1]
    assert done["type"] == "done"
    assert done["result"]["documents"] == []
    assert done["result"]["n_items"] == 0


def test_map_error_isolated(monkeypatch):
    """Un échec extract() sur UN document ne fait pas échouer tout le run : ce
    document apparaît dans un événement `map` avec n_items=0, les autres docs
    atteignent le reduce normalement, et `done` est bien émis."""
    monkeypatch.setenv("CORPUS_MAP_CONCURRENCY", "1")
    prefilter = lambda aspect: ["broken.md", "b.md"]

    def extract(document, aspect):
        if document == "broken.md":
            raise TimeoutError("Ollama timeout")
        return {"document": document, "items": ["Injection SQL"], "raw": ""}

    def reduce_llm(prompt):
        return "**Applicatif**\n- Injection SQL [src: b.md]"

    events = list(synthesize_corpus("catégorise", aspect="attaques",
                                    llm=reduce_llm, prefilter=prefilter, extract=extract))

    map_events = {e["document"]: e for e in events if e["type"] == "map"}
    assert map_events["broken.md"]["n_items"] == 0
    assert map_events["b.md"]["n_items"] == 1

    done = events[-1]
    assert done["type"] == "done"
    assert set(done["result"]["documents"]) == {"broken.md", "b.md"}
    assert done["result"]["n_items"] == 1
    assert done["result"]["document_coverage"]["ratio"] == 0.5
    assert done["result"]["document_coverage"]["documents_without_evidence"] == ["broken.md"]
    assert "Injection SQL" in done["result"]["answer"]


def test_reduce_batched_when_material_large(monkeypatch):
    """Un matériau MAP qui dépasse _MAX_MATERIAL_CHARS déclenche un REDUCE par lots
    borné (jamais un unique appel LLM qui tronquerait silencieusement)."""
    monkeypatch.setenv("CORPUS_MAP_CONCURRENCY", "1")

    # 10 docs, chacun avec un item très long (~3000 chars) => matériau total > 24000.
    n_docs = 10
    docs = [f"doc{i}.md" for i in range(n_docs)]
    long_item = "X" * 3000

    prefilter = lambda aspect: docs

    def extract(document, aspect):
        return {"document": document, "items": [long_item], "raw": ""}

    calls = []

    def counting_llm(prompt):
        calls.append(prompt)
        return f"synthèse partielle {len(calls)} [src: doc0.md]"

    events = list(synthesize_corpus("catégorise", aspect="attaques",
                                    llm=counting_llm, prefilter=prefilter, extract=extract))

    reduce_stage_events = [e for e in events if e["type"] == "stage" and e["stage"] == "reduce"]
    assert len(reduce_stage_events) == 1
    reduce_stage = reduce_stage_events[0]
    assert reduce_stage.get("batched") is True
    n_batches = reduce_stage["batches"]
    assert n_batches > 1

    # n_batches appels de réduction partielle + 1 appel de fusion finale.
    assert len(calls) == n_batches + 1

    done = events[-1]
    assert done["type"] == "done"
    assert set(done["result"]["documents"]) == set(docs)
    assert done["result"]["n_items"] == n_docs
    assert done["result"]["answer"].strip() != ""


def test_structured_aggregate_decomposes_axes_without_rigid_template(monkeypatch):
    monkeypatch.setenv("CORPUS_MAP_CONCURRENCY", "1")
    question = ("Sur tous les documents, identifie les catégories de sources de risque, "
                "les sources de risque, les catégories d objectif visé et les objectifs "
                "visés. Sois exhaustif et sors une table des types d attaquants.")
    extracted_aspects = []
    prompts = []
    def extract(document, aspect):
        extracted_aspects.append(aspect)
        return {"document": document, "items": [
            "sources de risque | acteur étatique | État hostile | capacités avancées | perturbation du service"], "raw": ""}
    def llm(prompt):
        prompts.append(prompt)
        return "| Axe | Catégorie | Élément | Caractérisation | Objectif lié | Sources |"
    events = list(synthesize_corpus(question, prefilter=lambda _: ["a.md"],
                                    extract=extract, llm=llm,
                                    coverage_check=lambda answer, dimensions: []))
    stage = events[0]
    assert stage["structured"] is True
    assert "sources de risque" in stage["dimensions"]
    assert "objectifs visés" in stage["dimensions"]
    assert "types d attaquants ou acteurs de menace" in stage["dimensions"]
    assert "AXES OBLIGATOIRES" in extracted_aspects[0]
    assert "aucun gabarit exact n est imposé" in prompts[0]
    assert "Distingue clairement une catégorie" in prompts[0]

def test_regular_synthesis_keeps_default_output_contract(monkeypatch):
    monkeypatch.setenv("CORPUS_MAP_CONCURRENCY", "1")
    prompts = []
    events = list(synthesize_corpus(
        "Quelles sont les menaces ?", aspect="menaces",
        prefilter=lambda _: ["a.md"],
        extract=lambda d, a: {"document": d, "items": ["x"], "raw": ""},
        llm=lambda p: prompts.append(p) or "x"))
    assert events[0]["structured"] is False
    assert "TABLEAU MARKDOWN" not in prompts[0]


def test_structured_coverage_repairs_once(monkeypatch):
    import env_config
    monkeypatch.setattr(env_config, "COVERAGE_REPAIR_ENABLED", True)
    monkeypatch.setenv("CORPUS_MAP_CONCURRENCY", "1")
    question = ("Catégorise toutes les sources de risque et tous les objectifs visés "
                "dans une table exhaustive.")
    extract_calls = []
    def extract(document, aspect):
        extract_calls.append(aspect)
        item = ("objectifs visés | service | interruption | impact | INCONNU"
                if aspect.startswith("COMPLÉMENT") else
                "sources de risque | État | acteur hostile | capacité | INCONNU")
        return {"document": document, "items": [item], "raw": ""}
    llm_calls = []
    def llm(prompt):
        llm_calls.append(prompt)
        return "table corrigée [src: a.md]"
    checks = {"n": 0}
    def coverage(answer, dimensions):
        checks["n"] += 1
        return ["objectifs visés"] if checks["n"] == 1 else []
    events = list(synthesize_corpus(question, prefilter=lambda _: ["a.md"],
                                    extract=extract, llm=llm,
                                    coverage_check=coverage))
    repairs = [e for e in events if e["type"] == "stage" and e["stage"] == "repair"]
    assert len(repairs) == 1 and repairs[0]["max_attempts"] == 1
    assert len(extract_calls) == 2
    assert extract_calls[1].startswith("COMPLÉMENT CIBLÉ")
    done = events[-1]["result"]
    assert done["coverage"] == {"checked": True, "missing": [], "repair_attempts": 1}
    assert done["n_items"] == 2

def test_structured_coverage_can_be_disabled(monkeypatch):
    import env_config
    monkeypatch.setattr(env_config, "COVERAGE_REPAIR_ENABLED", False)
    question = "Catégorise toutes les sources de risque et tous les objectifs visés."
    events = list(synthesize_corpus(
        question, prefilter=lambda _: ["a.md"],
        extract=lambda d, a: {"document": d, "items": ["fait"], "raw": ""},
        llm=lambda p: "réponse [src: a.md]",
        coverage_check=lambda *_: (_ for _ in ()).throw(AssertionError("judge appelé")),
    ))
    coverage = events[-1]["result"]["coverage"]
    assert coverage["checked"] is False
    assert coverage["repair_attempts"] == 0
    assert coverage["disabled_reason"] == "configuration"
    assert any(e.get("stage") == "coverage" and e.get("disabled") for e in events)


def test_parse_missing_axes_rejects_unrequested_values():
    from core.synthesize_corpus import _parse_missing_axes
    dims = ["sources de risque", "objectifs visés"]
    raw = "préfixe {\"missing\": [\"objectifs visés\", \"axe inventé\"]} suffixe"
    assert _parse_missing_axes(raw, dims) == ["objectifs visés"]


def test_default_coverage_prompt_formats_and_parses_json():
    from core.synthesize_corpus import _judge_coverage
    prompts = []
    missing = _judge_coverage("Analyse", ["Acteurs", "Objectifs"],
                              llm=lambda prompt: prompts.append(prompt) or "{\"missing\": [\"Objectifs\"]}")
    assert missing == ["Objectifs"]
    assert "{\"missing\"" in prompts[0]

def test_explicit_exhaustive_request_scans_all_sources_without_prefilter(monkeypatch):
    import core.corpus
    import core.corpus_extract
    monkeypatch.setattr(core.corpus, "list_indexed_sources", lambda: ["a.md", "b.md"])
    calls = []

    def exhaustive(document, aspect):
        calls.append((document, aspect))
        return {"document": document, "items": [
            "AXE=catégories de sources de risque | acteur",
            "AXE=sources de risque | service hostile",
            "AXE=catégories d objectifs visés | disponibilité",
            "AXE=objectifs visés | interruption",
            "AXE=relations source de risque vers objectif visé | hostile -> interruption",
        ], "raw": "", "batch_count": 2, "chunks_scanned": 10}

    monkeypatch.setattr(core.corpus_extract, "extract_from_document_exhaustive", exhaustive)
    events = list(synthesize_corpus(
        "Sur tous les documents, catégorise les sources de risque et les objectifs visés de façon exhaustive.",
        llm=lambda _prompt: "réponse [src: a.md]"))
    assert events[0]["documents"] == ["a.md", "b.md"]
    assert {document for document, _aspect in calls} == {"a.md", "b.md"}
    maps = [event for event in events if event["type"] == "map"]
    assert all(event["chunks_scanned"] == 10 for event in maps)
    assert events[-1]["result"]["execution_control"]["expansion"]["attempted"] is False


def test_map_budget_exhaustion_aborts_instead_of_becoming_empty_document():
    from utils.task_metrics import TaskBudgetExceeded

    def exhausted(_document, _aspect):
        raise TaskBudgetExceeded("Budget de tokens atteint")

    try:
        list(synthesize_corpus(
            "question",
            prefilter=lambda _aspect: ["a.md"],
            extract=exhausted,
            map_only=True,
        ))
    except TaskBudgetExceeded as exc:
        assert str(exc) == "Budget de tokens atteint"
    else:
        raise AssertionError("Le dépassement de budget MAP devait interrompre la tâche")


def test_exhaustive_request_expands_missing_axes_once_and_stops(monkeypatch):
    import core.corpus
    import core.corpus_extract
    monkeypatch.setattr(core.corpus, "list_indexed_sources", lambda: ["a.md"])
    calls = []

    def exhaustive(document, aspect):
        calls.append(aspect)
        items = (["AXE=sources de risque | acteur"] if not aspect.startswith("COMPLÉMENT")
                 else ["AXE=objectifs visés | interruption"])
        return {"document": document, "items": items, "raw": "",
                "batch_count": 1, "chunks_scanned": 4}

    monkeypatch.setattr(core.corpus_extract, "extract_from_document_exhaustive", exhaustive)
    events = list(synthesize_corpus(
        "Sur tous les documents, catégorise les sources de risque et les objectifs visés de façon exhaustive.",
        llm=lambda _prompt: "réponse [src: a.md]"))
    assert len(calls) == 2
    expansion = events[-1]["result"]["execution_control"]["expansion"]
    assert expansion["attempted"] is True
    assert expansion["added_items"] == 1
    assert expansion["stopped_reason"] == "single_bounded_expansion"
