from types import SimpleNamespace

from indexing.chunk_quality import classify_chunk, qualify_documents


def test_regular_chunk_is_accepted_with_no_quality_reason():
    result = classify_chunk(
        "Ce rapport décrit les capacités et objectifs observés sur le segment spatial.",
        {"source": "rapport-officiel.md", "chunk_type": "chunk"},
    )

    assert result == {
        "quality_status": "accepted",
        "quality_reasons": [],
        "content_provenance": "raw",
    }


def test_assistant_boilerplate_and_broken_table_are_quarantined():
    assistant = classify_chunk(
        "Je suis désolé, vous n'avez pas fourni le tableau demandé.",
        {"source": "rapport.md", "chunk_type": "chunk"},
    )
    broken_table = classify_chunk(
        "| Colonne | Valeur |\n| --- | --- |",
        {"source": "rapport.md", "chunk_type": "table"},
    )

    assert assistant["quality_status"] == "quarantined"
    assert "assistant_boilerplate" in assistant["quality_reasons"]
    assert broken_table["quality_status"] == "quarantined"
    assert "empty_or_broken_table" in broken_table["quality_reasons"]


def test_generated_source_is_degraded_and_keeps_structured_reasons():
    doc = SimpleNamespace(
        page_content="Une ancienne réponse suffisamment longue pour constituer un chunk indexable.",
        metadata={"source": "ThalesGPT - Question Sources.md", "chunk_type": "chunk"},
    )

    stats = qualify_documents([doc])

    assert doc.metadata["quality_status"] == "degraded"
    assert doc.metadata["quality_reasons"] == ["generated_source"]
    assert doc.metadata["content_provenance"] == "generated"
    assert stats["counts"] == {"degraded": 1}
