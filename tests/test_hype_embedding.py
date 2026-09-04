from types import SimpleNamespace
import indexing.embedding as E


def _doc(cid, content, questions, breadcrumb=""):
    meta = {"id": cid, "questions": list(questions),
            "questions_str": " | ".join(questions), "source": "d.pdf",
            "section_idx": 1, "chunk_idx": 0, "chunk_type": "chunk",
            "breadcrumb": breadcrumb}
    return SimpleNamespace(page_content=content, metadata=meta)


def test_base_unit_embeds_content_not_questions(monkeypatch):
    monkeypatch.setattr(E, "HYPE_ENABLED", False)
    monkeypatch.setattr(E, "CONTEXT_HEADERS_ENABLED", False)
    units = E.build_embedding_units([_doc("c1", "le contenu du chunk", ["Q1 ?", "Q2 ?"])])
    assert len(units) == 1
    assert units[0]["id"] == "c1"
    assert units[0]["embed_text"] == "le contenu du chunk"   # NOT the questions blob
    assert units[0]["document"] == "le contenu du chunk"


def test_hype_adds_parent_pointing_question_units(monkeypatch):
    monkeypatch.setattr(E, "HYPE_ENABLED", True)
    monkeypatch.setattr(E, "HYPE_MAX_QUESTIONS", 3)
    monkeypatch.setattr(E, "CONTEXT_HEADERS_ENABLED", False)
    units = E.build_embedding_units([_doc("c1", "contenu", ["Q1 ?", "Q2 ?"])])
    base = [u for u in units if u["metadata"].get("chunk_type") != "hype_question"]
    hype = [u for u in units if u["metadata"].get("chunk_type") == "hype_question"]
    assert len(base) == 1 and len(hype) == 2
    assert all(u["metadata"]["parent_id"] == "c1" for u in hype)
    assert all(u["metadata"]["id"] == "c1" for u in hype)          # resolves to parent
    assert {u["embed_text"] for u in hype} == {"Q1 ?", "Q2 ?"}     # embed the question
    assert all(u["document"] == "contenu" for u in hype)           # return parent text
    assert all(u["id"].startswith("c1::hype::") for u in hype)     # unique chroma ids


def test_hype_respects_max_questions(monkeypatch):
    monkeypatch.setattr(E, "HYPE_ENABLED", True)
    monkeypatch.setattr(E, "HYPE_MAX_QUESTIONS", 1)
    monkeypatch.setattr(E, "CONTEXT_HEADERS_ENABLED", False)
    units = E.build_embedding_units([_doc("c1", "contenu", ["Q1 ?", "Q2 ?", "Q3 ?"])])
    assert sum(1 for u in units if u["metadata"].get("chunk_type") == "hype_question") == 1


def test_oversized_content_is_fitted_only_for_embedding(monkeypatch):
    monkeypatch.setattr(E, "CONTEXT_HEADERS_ENABLED", False)
    monkeypatch.setattr(E, "_MAX_EMBED_TEXT_CHARS", 1000)
    content = "début " + ("x" * 1500) + " fin"

    unit = E.build_embedding_units([_doc("long", content, [])])[0]

    assert len(unit["embed_text"]) == 1000
    assert unit["embed_text"].startswith("début ")
    assert unit["embed_text"].endswith(" fin")
    assert unit["document"] == content
    assert unit["metadata"]["embedding_truncated"] is True
    assert unit["metadata"]["embedding_original_chars"] == len(content)


def test_build_embeddings_retries_only_invalid_vectors(monkeypatch):
    monkeypatch.setattr(E, "CONTEXT_HEADERS_ENABLED", False)
    calls = {"bad": 0}

    class FakeEmbedding:
        def __init__(self, **kwargs):
            pass

        def embed_documents(self, texts):
            return [[1.0, 0.0], [0.0, 0.0]]

        def embed_query(self, text):
            calls["bad"] += 1
            return [0.5, 0.5]

    monkeypatch.setattr(E, "OllamaEmbedding", FakeEmbedding)
    docs = [_doc("ok", "contenu valide", []), _doc("bad", "contenu à retenter", [])]

    texts, vecs, metadatas, ids = E.build_embeddings(docs, hype_enabled=False)

    assert ids == ["ok", "bad"]
    assert texts == ["contenu valide", "contenu à retenter"]
    assert vecs == [[1.0, 0.0], [0.5, 0.5]]
    assert [meta["id"] for meta in metadatas] == ids
    assert calls["bad"] == 1


def test_build_embeddings_reduces_long_input_after_model_refusal(monkeypatch):
    monkeypatch.setattr(E, "CONTEXT_HEADERS_ENABLED", False)
    long_content = "début " + ("x" * 7990) + " fin"
    seen = []

    class FakeEmbedding:
        def __init__(self, **kwargs):
            pass

        def embed_documents(self, texts):
            return [[0.0, 0.0]]

        def embed_query(self, text):
            seen.append(text)
            return [1.0, 0.0] if len(text) <= 7000 else [0.0, 0.0]

    monkeypatch.setattr(E, "OllamaEmbedding", FakeEmbedding)
    texts, vecs, metadatas, ids = E.build_embeddings(
        [_doc("long", long_content, [])], hype_enabled=False
    )

    assert ids == ["long"]
    assert texts == [long_content]
    assert vecs == [[1.0, 0.0]]
    assert len(seen) == 1
    assert len(seen[0]) == 7000
    assert seen[0].startswith("début ")
    assert seen[0].endswith(" fin")
    assert metadatas[0]["embedding_adaptive_retry"] == 1
    assert metadatas[0]["embedding_retry_chars"] == 7000


def test_chroma_metadata_drops_null_values():
    metadata = E._sanitize_chroma_metadata({
        "source": "doc.md", "page_number": None, "quality_reasons": []
    })

    assert metadata == {"source": "doc.md", "quality_reasons": ""}
