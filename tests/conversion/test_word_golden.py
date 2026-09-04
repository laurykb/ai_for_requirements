"""Golden set des matrices Word réelles (ignoré si le référentiel est absent)."""
import json
from pathlib import Path

import pytest

from conversion.document_import import import_document


ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "evals" / "conversion_word_golden.json"
REFERENTIALS = ROOT / "docs" / "referentiel_system"


@pytest.mark.parametrize("filename, expected", json.loads(
    GOLDEN.read_text(encoding="utf-8")
)["documents"].items())
def test_matrice_word_reelle_conforme_au_golden(filename, expected):
    path = REFERENTIALS / filename
    filename = path.name
    if not path.is_file():
        pytest.skip("référentiel réel absent de cette livraison")

    requirements, _warnings = import_document(path.read_bytes(), filename)
    by_id = {requirement["id"]: requirement for requirement in requirements}

    assert len(requirements) == expected["requirements"]
    assert sum(len(requirement["links"]) for requirement in requirements) == expected["links"]
    assert sum(
        occurrence.get("kind") == "traceability_table"
        for requirement in requirements
        for occurrence in requirement["occurrences"]
    ) == expected["table_occurrences"]
    assert sum(bool(requirement["collision_variants"]) for requirement in requirements) == expected["collisions"]
    for ident, prefix in expected["samples"].items():
        assert by_id[ident]["texte"].startswith(prefix)
        assert "endReq" not in by_id[ident]["texte"]
