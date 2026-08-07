from types import SimpleNamespace
import indexing.embedding as E


def _doc(cid, content, breadcrumb):
    return SimpleNamespace(page_content=content, metadata={
        "id": cid, "questions": [], "questions_str": "", "source": "d.pdf",
        "section_idx": 1, "chunk_idx": 0, "chunk_type": "chunk",
        "breadcrumb": breadcrumb})


def test_header_prefixes_embed_text_only(monkeypatch):
    monkeypatch.setattr(E, "HYPE_ENABLED", False)
    monkeypatch.setattr(E, "CONTEXT_HEADERS_ENABLED", True)
    units = E.build_embedding_units([_doc("c1", "AES-256 en 6.2.1", "Crypto > 6.2.1")])
    assert units[0]["embed_text"].startswith("[Crypto > 6.2.1]")
    assert units[0]["document"] == "AES-256 en 6.2.1"    # generation text untouched


def test_header_not_duplicated_when_already_in_content(monkeypatch):
    monkeypatch.setattr(E, "HYPE_ENABLED", False)
    monkeypatch.setattr(E, "CONTEXT_HEADERS_ENABLED", True)
    units = E.build_embedding_units([_doc("c1", "[Crypto > 6.2.1]\nAES-256", "Crypto > 6.2.1")])
    assert units[0]["embed_text"].count("[Crypto > 6.2.1]") == 1
