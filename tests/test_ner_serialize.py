"""Tests unitaires de la sérialisation d'entités NER (sans spaCy)."""
from nlp.ner_extractor import entities_to_flat_list, entities_to_str


def test_flat_list_dedup_preserves_order():
    d = {"ORG": ["ANSSI", "CNIL"], "NORM": ["ANSSI", "ISO 27001"]}
    assert entities_to_flat_list(d) == ["ANSSI", "CNIL", "ISO 27001"]


def test_entities_to_str_skips_empty_categories():
    s = entities_to_str({"ORG": ["ANSSI"], "NORM": []})
    assert "ORG: ANSSI" in s
    assert "NORM" not in s
