from indexing.embedding import _sanitize_chroma_metadata


def test_sanitize_chroma_metadata_serializes_empty_quality_reasons():
    metadata = _sanitize_chroma_metadata(
        {"quality_status": "accepted", "quality_reasons": []}
    )

    assert metadata["quality_reasons"] == ""


def test_sanitize_chroma_metadata_serializes_all_collection_values():
    metadata = _sanitize_chroma_metadata(
        {
            "quality_reasons": ["very_short", "table_of_contents"],
            "questions": ["Question 1 ?", "Question 2 ?"],
            "tags": ("a", "b"),
        }
    )

    assert metadata["quality_reasons"] == "very_short, table_of_contents"
    assert metadata["questions"] == "Question 1 ? | Question 2 ?"
    assert metadata["tags"] == "a, b"
