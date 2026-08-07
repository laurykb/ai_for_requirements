# tests/test_corpus_extract.py
from core.corpus_extract import extract_from_document, extract_from_document_exhaustive


def test_extract_parses_items_from_llm(monkeypatch):
    fake_material = ("résumé", 3, "résumés de section RAPTOR")
    fake_llm = lambda prompt: "- Déni de service\n- Injection SQL\n\n- Rejeu\n"
    out = extract_from_document("doc.md", "attaques",
                                llm=fake_llm, gather=lambda src: fake_material)
    assert out["document"] == "doc.md"
    assert out["items"] == ["Déni de service", "Injection SQL", "Rejeu"]


def test_extract_empty_material_returns_no_items():
    out = extract_from_document("doc.md", "attaques",
                                llm=lambda p: "IGNORED",
                                gather=lambda src: ("", 0, "échantillon de chunks"))
    assert out["items"] == []
    assert out["document"] == "doc.md"

def test_exhaustive_extraction_scans_every_chunk_and_deduplicates_overlap():
    chunks = [{"content": "A" * 30}, {"content": "B" * 30}, {"content": "C" * 30}]
    prompts = []

    def llm(prompt):
        prompts.append(prompt)
        return "- acteur étatique\n- objectif de perturbation"

    out = extract_from_document_exhaustive(
        "doc.md", "axes", llm=llm, gather=lambda _doc: chunks,
        batch_chars=45, batch_overlap=5)
    assert out["chunks_scanned"] == 3
    assert out["batch_count"] == len(prompts)
    assert out["batch_count"] > 1
    assert out["items"] == ["acteur étatique", "objectif de perturbation"]
