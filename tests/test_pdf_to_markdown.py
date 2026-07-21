"""
Tests unitaires de la conversion document -> Markdown (preprocessing/pdf_to_markdown).

100 % hors-ligne : on teste les fonctions PURES et la logique d'émission Markdown via
de faux DocItem (aucun appel à Docling, dont les imports sont paresseux). La conversion
réelle (Docling) est vérifiée à part, sur de vrais documents.
"""
from preprocessing.pdf_to_markdown import (
    _emit_items, _looks_scanned, _heading_depth_from_text, doc_to_md, pdf_to_md,
)


# -- faux DocItem (mimant l'API Docling utilisée : type name, .text, .prov, .label) --
class _Prov:
    def __init__(self, page_no):
        self.page_no = page_no


class _Label:
    def __init__(self, value):
        self.value = value


def _item(cls_name, text, page, label_value):
    """Construit un faux item dont type(item).__name__ == cls_name (lu par _item_text)."""
    cls = type(cls_name, (), {})
    it = cls()
    it.text = text
    it.prov = [_Prov(page)]
    it.label = _Label(label_value)
    return it


class _FakeDoc:
    def __init__(self, items):
        self._items = items

    def iterate_items(self):
        return [(it, 0) for it in self._items]


# -- _looks_scanned ------------------------------------------------------------
def test_looks_scanned_detects_empty_and_figure_only():
    assert _looks_scanned("") is True
    assert _looks_scanned("<!-- page:1 -->\n[Figure: schéma]") is True   # aucun vrai texte
    assert _looks_scanned("Un contenu textuel bien réel. " * 10) is False


# -- alias rétro-compatible ----------------------------------------------------
def test_pdf_to_md_is_alias_of_doc_to_md():
    assert pdf_to_md is doc_to_md


# -- _heading_depth_from_text (numérotation -> profondeur) ----------------------
def test_heading_depth_from_numbering():
    assert _heading_depth_from_text("1. Introduction") == 2     # # réservé au titre doc
    assert _heading_depth_from_text("2.2.1 TOE") == 4
    assert _heading_depth_from_text("AVERTISSEMENT") == 2        # non numéroté -> ##


# -- _emit_items : skip mobilier de page + marqueurs de page + headings --------
def test_emit_items_skips_headers_footers_and_marks_pages():
    doc = _FakeDoc([
        _item("TextItem", "Document confidentiel", 1, "page_header"),   # à retirer
        _item("SectionHeaderItem", "1. Introduction", 1, "section_header"),
        _item("TextItem", "Corps de l'introduction.", 1, "text"),
        _item("TextItem", "Page 1 / 10", 1, "page_footer"),             # à retirer
        _item("TextItem", "Contenu de la page deux.", 2, "text"),
    ])
    md_lines: list[str] = []
    state: dict = {"page": None}
    pages_seen: set[int] = set()

    _emit_items(doc, md_lines, state, pages_seen)
    md = "\n".join(md_lines)

    # En-tête / pied de page retirés à la source.
    assert "Document confidentiel" not in md
    assert "Page 1 / 10" not in md
    # Heading numéroté reconstruit, contenu conservé.
    assert "## 1. Introduction" in md
    assert "Corps de l'introduction." in md
    assert "Contenu de la page deux." in md
    # Marqueurs de page insérés à chaque changement, numéros suivis.
    assert "<!-- page:1 -->" in md and "<!-- page:2 -->" in md
    assert pages_seen == {1, 2}
    assert state["page"] == 2
