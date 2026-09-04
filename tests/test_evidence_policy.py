from core.evidence_policy import (
    evidence_authority,
    rank_chat_candidates,
    select_primary_evidence_chunks,
)


def _item(name, score, quality="accepted", provenance="raw"):
    return {
        "doc": name,
        "ce_score": score,
        "meta": {
            "source": f"{name}.md",
            "quality_status": quality,
            "content_provenance": provenance,
        },
    }


def test_chat_prefers_authoritative_raw_evidence_over_slightly_more_relevant_generated():
    generated = _item("chatbot", 0.95, "degraded", "generated")
    official = _item("official", 0.70, "accepted", "raw")

    ranked = rank_chat_candidates([generated, official])

    assert [row["doc"] for row in ranked] == ["official", "chatbot"]
    assert ranked[0]["meta"]["evidence_authority"] == 1.0
    assert ranked[1]["meta"]["evidence_authority"] == 0.40


def test_chat_defensively_excludes_quarantined_chunks():
    ranked = rank_chat_candidates([
        _item("bad", 1.0, "quarantined", "raw"),
        _item("good", 0.5),
    ])

    assert [row["doc"] for row in ranked] == ["good"]


def test_legacy_metadata_defaults_to_primary_authority():
    assert evidence_authority({}) == 1.0


def test_uses_only_primary_raw_chunks_when_available():
    chunks = [
        {"content": "primaire", "quality_status": "accepted", "content_provenance": "raw"},
        {"content": "dégradé", "quality_status": "degraded", "content_provenance": "raw"},
        {"content": "résumé", "quality_status": "accepted", "content_provenance": "derived"},
        {"content": "chatbot", "quality_status": "degraded", "content_provenance": "generated"},
    ]

    selected, policy = select_primary_evidence_chunks(chunks)

    assert [chunk["content"] for chunk in selected] == ["primaire"]
    assert policy == "primary"


def test_degraded_raw_fallback_is_explicit_and_never_uses_generated():
    chunks = [
        {"content": "dégradé", "quality_status": "degraded", "content_provenance": "raw"},
        {"content": "chatbot", "quality_status": "degraded", "content_provenance": "generated"},
    ]

    selected, policy = select_primary_evidence_chunks(chunks)

    assert [chunk["content"] for chunk in selected] == ["dégradé"]
    assert policy == "degraded_fallback"
