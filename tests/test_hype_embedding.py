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
