from core.prompt_registry import template_fields, validate_template


def test_template_fields_extracts_required_variables():
    assert template_fields("Question {question} / contexte {context}") == {
        "question", "context",
    }


def test_validate_template_rejects_missing_variable():
    errors = validate_template("Question {question}", "{question} {context}")
    assert errors == ["Variable obligatoire manquante : {context}"]


def test_validate_template_accepts_extra_text_and_variables():
    assert validate_template(
        "Instruction {question} {context} {optional}",
        "{question} {context}",
    ) == []


def test_validate_template_rejects_empty_and_bad_braces():
    assert validate_template(" ", "{question}")
    assert validate_template("{question", "{question}")


def test_catalogue_expose_les_agents_rag_sans_entrees_sra():
    from api.prompts import _catalog
    catalog = _catalog()
    expected = {
        "agent.behavior", "planner.plan", "generate.system", "extract.map",
        "synthesize.reduce", "synthesize.merge", "synthesize.repair",
        "judge.coverage", "security.boundary",
    }
    assert expected <= catalog.keys()
    assert not any(key.startswith("sra.") for key in catalog)
    assert catalog["security.boundary"]["editable"] is False


def test_baseline_system_prompt_scoped_to_reserved_source():
    from api.prompts import _catalog, baseline_system_default
    from api.rag import _baseline_system_prompt
    from core.reserved_sources import LYNX_BASELINE_SOURCE
    assert "baseline.system" in _catalog()
    assert "BASELINE D'EXIGENCES" in baseline_system_default()
    assert _baseline_system_prompt(None) is None
    assert _baseline_system_prompt("rapport.md") is None
    resolved = _baseline_system_prompt(LYNX_BASELINE_SOURCE)
    assert resolved and "identifiant d'exigence" in resolved
