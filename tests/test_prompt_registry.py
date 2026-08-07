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
