"""Outil arbre déterministe de l'agent (chat baseline) : réponses exactes."""
from __future__ import annotations

from tools.baseline_tree_tool import TOOL_NAME, run, run_tool, tool_spec

CORPUS = [
    {"id": "SYS-001", "niveau": 0, "domaine": "Système", "texte": "Racine.",
     "parent_id": None, "verification": "D", "test_status": "PENDING", "links": []},
    {"id": "CYB-001", "niveau": 1, "domaine": "Cyber", "texte": "Chiffrement.",
     "parent_id": "SYS-001", "verification": "T", "test_status": "OK",
     "links": [{"type": "REFERENCE", "target_id": "SYS-001"}]},
    {"id": "CYB-002", "niveau": 2, "domaine": "Cyber", "texte": "Clés 24h.",
     "parent_id": "CYB-001", "verification": None, "test_status": "PENDING", "links": []},
    {"id": "ORF-001", "niveau": 2, "domaine": "Cyber", "texte": "Orpheline.",
     "parent_id": None, "verification": None, "test_status": "KO", "links": []},
]


def test_spec_shape():
    spec = tool_spec()
    assert spec["name"] == TOOL_NAME == "baseline_tree"
    assert "operation" in spec["parameters"]["properties"]


def test_enfants_et_chaine():
    r = run(CORPUS, "enfants", req_id="SYS-001")
    assert r["ok"] and [e["id"] for e in r["enfants"]] == ["CYB-001"]
    c = run(CORPUS, "chaine", req_id="CYB-002")
    assert [e["id"] for e in c["chaine"]] == ["CYB-002", "CYB-001", "SYS-001"]
    assert c["racine_atteinte"]


def test_liens_entrants_sortants():
    r = run(CORPUS, "liens", req_id="SYS-001")
    assert r["sortants"] == []
    assert r["entrants"] == [{"type": "REFERENCE", "depuis": "CYB-001"}]


def test_orphelines_et_filtres():
    assert [o["id"] for o in run(CORPUS, "orphelines")["orphelines"]] == ["ORF-001"]
    f = run(CORPUS, "filtrer", domaine="Cyber", sans_verification=True)
    assert {e["id"] for e in f["exigences"]} == {"CYB-002", "ORF-001"}
    assert run(CORPUS, "filtrer", niveau=1)["n"] == 1


def test_stats():
    s = run(CORPUS, "stats")
    assert s["n_exigences"] == 4
    assert s["par_domaine"]["Cyber"] == 3
    assert s["sans_verification"] == 2


def test_run_tool_validates_and_reports_unknown_id():
    assert not run_tool({"operation": "enfants"}, corpus=CORPUS)["ok"]      # req_id manquant
    r = run_tool({"operation": "chaine", "req_id": "NOPE-1"}, corpus=CORPUS)
    assert not r["ok"] and "Exigence inconnue" in r["error"]
    assert not run_tool({}, corpus=CORPUS)["ok"]                            # operation manquante


def test_cycle_de_parents_ne_boucle_pas():
    cyc = [{"id": "A", "niveau": 1, "parent_id": "B", "texte": "", "links": []},
           {"id": "B", "niveau": 1, "parent_id": "A", "texte": "", "links": []}]
    c = run(cyc, "chaine", req_id="A")
    assert c["ok"] and len(c["chaine"]) == 2  # visite chaque nœud une fois
