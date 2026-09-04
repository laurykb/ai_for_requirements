from io import BytesIO

from openpyxl import Workbook

from conversion.matrix_import import import_matrix, rows_to_requirements


def test_extrait_paires_et_blocs_multi_exigences():
    rows = [
        ("STB", "Texte STB", "ID SSS", "Texte SSS"),
        ("[SYS-REQ-001]", "Le système démarre.",
         "[SUB-REQ-010]\n[SUB-REQ-011]",
         "[SUB-REQ-010]\nLe service démarre.\n[SUB-REQ-011]\nLe service journalise."),
    ]
    reqs, warnings = rows_to_requirements(rows, "djem.xlsx")
    by_id = {r["id"]: r for r in reqs}
    assert warnings == []
    assert set(by_id) == {"SYS-REQ-001", "SUB-REQ-010", "SUB-REQ-011"}
    assert by_id["SUB-REQ-011"]["texte"] == "Le service journalise."
    assert by_id["SUB-REQ-010"]["links"] == [{"type": "SATISFIES", "target": "SYS-REQ-001"}]


def test_deduplication_conserve_le_texte_le_plus_complet():
    reqs, _ = rows_to_requirements([
        ("ABC-REQ-001", "Court."),
        ("ABC-REQ-001", "Texte sensiblement plus complet."),
    ])
    assert len(reqs) == 1
    assert reqs[0]["texte"] == "Texte sensiblement plus complet."


def test_import_xlsx_toutes_les_feuilles():
    wb = Workbook()
    wb.active.append(["ID", "Texte"])
    wb.active.append(["AAA-REQ-001", "Première exigence."])
    second = wb.create_sheet("Autre")
    second.append(["BBB-REQ-002", "Seconde exigence."])
    stream = BytesIO()
    wb.save(stream)
    reqs, warnings = import_matrix(stream.getvalue(), "matrice.xlsx")
    assert warnings == []
    assert {r["id"] for r in reqs} == {"AAA-REQ-001", "BBB-REQ-002"}


def test_matrice_vide_signalee():
    reqs, warnings = rows_to_requirements([("Titre", "Sans identifiant")])
    assert reqs == []
    assert warnings


def test_relations_reciproques_ne_creent_pas_de_cycle():
    rows = [
        ("AAA-REQ-001", "A", "BBB-REQ-002", "B"),
        ("BBB-REQ-002", "B", "AAA-REQ-001", "A"),
    ]
    reqs, warnings = rows_to_requirements(rows)
    links = {(req["id"], link["target"])
             for req in reqs for link in req["links"]}
    assert not ({("AAA-REQ-001", "BBB-REQ-002"),
                 ("BBB-REQ-002", "AAA-REQ-001")} <= links)
    assert any("acyclique" in warning for warning in warnings)
