from core.answer_contract import (
    build_evidence_dossier,
    classify_answer_contract,
    contract_prompt,
    validate_answer,
)


def test_contract_classification_is_independent_from_execution_mode():
    assert classify_answer_contract("Compare les deux mécanismes")["id"] == "comparison"
    assert classify_answer_contract("Liste toutes les exigences")["id"] == "enumeration"
    assert classify_answer_contract("Comment configurer le service ?")["id"] == "procedure"
    assert classify_answer_contract("Quel est le niveau EAL ?")["id"] == "factual"


def test_numeric_evidence_dossier_and_validation():
    chunks = [
        {"doc": "Le produit est EAL3+.", "meta": {"source": "cible.md", "page_number": 4}},
        {"doc": "La portée est limitée.", "meta": {"source": "cible.md"}},
    ]
    dossier = build_evidence_dossier("Quel est le niveau EAL ?", chunks, "rag")
    result = validate_answer("## Réponse\nLe niveau est EAL3+ [1].\n## Limites\nPortée limitée [2].", dossier)
    assert dossier["contract"]["id"] == "factual"
    assert result["structure_complete"] is True
    assert result["invalid_citations"] == []
    assert result["completion"] == "complete"


def test_validation_reports_missing_sections_and_bad_markers():
    dossier = build_evidence_dossier(
        "Compare A et B", [{"doc": "preuve", "meta": {"source": "d.md"}}], "agent")
    result = validate_answer("## Conclusion\nA diffère de B [9].", dossier)
    assert result["structure_complete"] is False
    assert result["invalid_citations"] == [9]
    assert result["completion"] == "partial"


def test_source_contract_for_corpus_synthesis():
    prompt = contract_prompt("Analyse les menaces", citation_style="source")
    assert "Synthèse exécutive" in prompt
    assert "[src: document]" in prompt
    dossier = build_evidence_dossier(
        "Analyse les menaces", [{"doc": "menace", "meta": {"source": "a.md"}}], "synth")
    answer = (
        "## Synthèse exécutive\nConstat [src: a.md]\n"
        "## Analyse\nDétail [src: a.md]\n"
        "## Points de vigilance\nAucun autre [src: a.md]\n"
        "## Informations manquantes\nAucune [src: a.md]"
    )
    assert validate_answer(answer, dossier, citation_style="source")["completion"] == "complete"
