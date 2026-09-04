"""rebuild_vectors._Doc doit préserver `questions` comme LISTE : HyPE
(build_embedding_units) lit meta["questions"] comme une liste pour émettre un
vecteur par question. L'ancien _Doc aplatissait toute liste en str, ce qui
cassait HyPE via le chemin rapide rebuild_vectors."""
import importlib.util
from pathlib import Path


def _load_rv():
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "rebuild_vectors", root / "scripts" / "rebuild_vectors.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_doc_preserves_questions_as_list_for_hype():
    rv = _load_rv()
    record = {
        "_id": "c1", "content": "contenu",
        "questions": ["Q1 ?", "Q2 ?"], "questions_str": "Q1 ? | Q2 ?",
        "source": "d.md", "section_idx": 1, "chunk_idx": 0, "chunk_type": "text",
    }
    doc = rv._Doc(record)
    assert doc.metadata["questions"] == ["Q1 ?", "Q2 ?"]  # liste préservée pour HyPE
    assert doc.page_content == "contenu"
    assert doc.metadata["id"] == "c1"


def test_doc_still_flattens_unrelated_lists():
    """Les listes non critiques restent aplaties (Chroma-compat gérée en aval
    pour keywords/questions/entities, mais une liste arbitraire reste string)."""
    rv = _load_rv()
    record = {"_id": "c2", "content": "x", "misc_list": ["a", "b"],
              "section_idx": 0, "chunk_idx": 0, "source": "d.md"}
    doc = rv._Doc(record)
    assert doc.metadata["misc_list"] == "a, b"
