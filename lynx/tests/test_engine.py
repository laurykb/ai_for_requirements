"""Tests du moteur d'analyse d'impact (déterministe, sans Ollama)."""

from src.extract import primary_quantity
from src.models import Action, ActionType, Scope, Severity
from src.orchestrator import run_impact_analysis
from src.tree import RequirementTree

# Fixture stable (indépendante du fichier corpus/corpus.json) :
# budget L1=150, L2 = 40+30+35=105 (OK), L3-MECH=15 avec enfants L4=10+6=16 (dépassement).
_FIXTURE = [
    {"id": "REQ-L0-SYS-001", "niveau": 0, "type": "Besoin", "domaine": "Systeme_Global",
     "texte": "Le véhicule doit être aérotransportable.", "parent_id": None, "test_status": "PENDING"},
    {"id": "REQ-L1-MAS-001", "niveau": 1, "type": "Spec", "domaine": "Systeme_Global",
     "texte": "Le poids total ne doit pas excéder 150 kg.", "parent_id": "REQ-L0-SYS-001", "test_status": "PENDING"},
    {"id": "REQ-L2-IT-001", "niveau": 2, "type": "Concept", "domaine": "IT",
     "texte": "Allocation IT fixée à un maximum de 40 kg. Baie IP68.", "parent_id": "REQ-L1-MAS-001", "test_status": "PENDING"},
    {"id": "REQ-L2-CAP-001", "niveau": 2, "type": "Concept", "domaine": "Capteur",
     "texte": "Allocation capteurs fixée à un maximum de 30 kg.", "parent_id": "REQ-L1-MAS-001", "test_status": "PENDING"},
    {"id": "REQ-L2-ANT-001", "niveau": 2, "type": "Concept", "domaine": "Antenne",
     "texte": "Allocation antennaire fixée à un maximum de 35 kg.", "parent_id": "REQ-L1-MAS-001", "test_status": "PENDING"},
    {"id": "REQ-L3-ANT-MECH-001", "niveau": 3, "type": "Detail", "domaine": "Antenne_Mecanique",
     "texte": "Le boîtier mécanique ne doit pas dépasser 15 kg.", "parent_id": "REQ-L2-ANT-001", "test_status": "PENDING"},
    {"id": "REQ-L3-ANT-ELEC-001", "niveau": 3, "type": "Detail", "domaine": "Antenne_Electronique",
     "texte": "La partie électronique ne doit pas dépasser 20 kg.", "parent_id": "REQ-L2-ANT-001", "test_status": "PENDING"},
    {"id": "REQ-L4-ANT-CHAS-001", "niveau": 4, "type": "Real", "domaine": "Antenne_Mecanique",
     "texte": "Châssis aluminium : masse mesurée à 10 kg.", "parent_id": "REQ-L3-ANT-MECH-001", "test_status": "OK"},
    {"id": "REQ-L4-ANT-RAD-001", "niveau": 4, "type": "Real", "domaine": "Antenne_Mecanique",
     "texte": "Dissipateur thermique : masse mesurée à 6 kg.", "parent_id": "REQ-L3-ANT-MECH-001", "test_status": "OK"},
]


def _corpus():
    return [dict(r) for r in _FIXTURE]


# --- extraction -----------------------------------------------------------
def test_extract_budget_max():
    q = primary_quantity("Le poids total ne doit pas excéder 150 kg.")
    assert q is not None and q.value == 150.0 and q.unit == "kg" and q.kind == "max"


def test_extract_measure():
    q = primary_quantity("Usinage du châssis : masse mesurée à 10 kg.")
    assert q.value == 10.0 and q.unit == "kg" and q.kind == "measure"


def test_extract_ignores_ip_code():
    # "IP68" ne doit pas être pris pour une masse ; on préfère 40 kg
    q = primary_quantity("Allocation fixée à 40 kg. La baie doit respecter IP68.", prefer_unit="kg")
    assert q.value == 40.0 and q.unit == "kg"


def test_extract_thousands_separator():
    # Séparateur de milliers par espace (format FR) ne doit pas donner 0
    q = primary_quantity("Le coût ne doit pas excéder 150 000 euros.")
    assert q.value == 150000.0 and q.unit == "EUR"
    q2 = primary_quantity("Puissance maximale 1 000 000 W.")
    assert q2.value == 1000000.0 and q2.unit == "W"


def test_extract_ms_not_metre():
    q = primary_quantity("Le délai de réponse ne doit pas dépasser 200 ms.")
    assert q.unit == "ms" and q.value == 200.0


def test_extract_single_letter_unit_not_in_word():
    # "10 mol" ne doit pas être capté comme "10 m"
    from src.extract import extract_quantities
    assert extract_quantities("Concentration de 10 mol par litre.") == []


def test_thousands_only_collapses_before_unit():
    from src.extract import extract_quantities
    # « 150 000 kg » -> 150000 ; « version 2 014 » (sans unité) ne doit PAS fusionner
    q = primary_quantity("Budget max 150 000 kg pour la version 2 014.")
    assert q.value == 150000.0 and q.unit == "kg"


def test_thousands_nbsp_and_decimal():
    # Séparateur de milliers insécable (FR, Word/LibreOffice) + décimale
    assert primary_quantity("masse mesurée à 1 500 kg").value == 1500.0      # NBSP
    assert primary_quantity("masse mesurée à 1 500 kg").value == 1500.0      # narrow-NBSP
    assert primary_quantity("masse mesurée à 1 500,5 kg").value == 1500.5         # milliers + décimale
    assert primary_quantity("coût max 12 000,00 euros").value == 12000.0
    # ne doit PAS recoller un nombre sans unité (ex. une version)
    assert primary_quantity("version 2 014 du document") is None


def test_range_excluded_from_rollup():
    from src.extract import extract_quantities, allocation_rollup
    qs = extract_quantities("La masse doit être comprise entre 5 et 10 kg.")
    assert qs and qs[0].kind == "range"
    # une plage n'est pas sommée dans le roll-up
    roll = allocation_rollup("Plafond max 20 kg.",
                             [("C1", "entre 5 et 10 kg"), ("C2", "masse mesurée à 8 kg")])
    assert [c[0] for c in roll["contributions"]] == ["C2"]


def test_unit_conversion_in_allocation():
    # parent en kg, enfants en g/kg : conversion correcte (1200 g + 0.5 kg = 1.7 kg < 2 kg)
    from src.extract import allocation_rollup
    roll = allocation_rollup("La masse ne doit pas dépasser 2 kg.",
                             [("A", "masse mesurée à 1200 g"), ("B", "masse mesurée à 0,5 kg")])
    assert round(roll["total_base"], 3) == 1.7
    # et un dépassement franc en unités mixtes : 1500 g + 1 kg = 2.5 kg > 2 kg
    roll2 = allocation_rollup("Plafond 2 kg.", [("A", "1500 g mesurés"), ("B", "1 kg mesuré")])
    assert round(roll2["total_base"], 3) == 2.5


def test_allocation_tolerance_and_epsilon():
    # somme exactement au plafond ne doit pas déclencher de dépassement (epsilon flottant)
    from src.models import Action, ActionType, Scope, Severity
    corpus = [
        {"id": "P", "niveau": 0, "type": "x", "domaine": "d",
         "texte": "Budget max 0.3 kg.", "parent_id": None, "test_status": "PENDING"},
        {"id": "C1", "niveau": 1, "type": "x", "domaine": "d",
         "texte": "masse mesurée à 0.1 kg", "parent_id": "P", "test_status": "PENDING"},
        {"id": "C2", "niveau": 1, "type": "x", "domaine": "d",
         "texte": "masse mesurée à 0.2 kg", "parent_id": "P", "test_status": "PENDING"},
    ]
    rep = run_impact_analysis(corpus, Action(action_type=ActionType.UPDATE, target_id="C1",
                                             new_text="masse mesurée à 0.1 kg"), semantic=False)
    blocking = [f for f in rep.findings if f.scope == Scope.ALLOCATION and f.severity == Severity.BLOCKING]
    assert not blocking  # 0.1 + 0.2 == 0.3, pas de faux dépassement


def test_primary_quantity_prefers_max_budget():
    # Le budget parent doit être le plafond, pas une autre quantité écrite avant
    t = "Disponible 24 h/24, la masse totale ne doit pas excéder 150 kg."
    q = primary_quantity(t, prefer_kind="max")
    assert q.value == 150.0 and q.unit == "kg"


# --- arbre ----------------------------------------------------------------
def test_tree_navigation():
    t = RequirementTree(_corpus())
    assert "REQ-L3-ANT-MECH-001" in t
    children = [c.id for c in t.children("REQ-L3-ANT-MECH-001")]
    assert set(children) == {"REQ-L4-ANT-CHAS-001", "REQ-L4-ANT-RAD-001"}
    anc = [a.id for a in t.ancestors("REQ-L4-ANT-CHAS-001")]
    assert anc == ["REQ-L3-ANT-MECH-001", "REQ-L2-ANT-001", "REQ-L1-MAS-001", "REQ-L0-SYS-001"]
    desc = {d.id for d in t.descendants("REQ-L2-ANT-001")}
    assert "REQ-L4-ANT-RAD-001" in desc


# --- allocation : le dépassement L4 (16) > L3 (15) doit être BLOCKING -----
def test_allocation_overflow_detected():
    # On "modifie" un L4 sans changer sa valeur -> l'analyse recontrôle les ancêtres
    action = Action(action_type=ActionType.UPDATE, target_id="REQ-L4-ANT-CHAS-001",
                    new_text="Usinage du châssis en aluminium : masse mesurée à 10 kg.")
    report = run_impact_analysis(_corpus(), action)
    alloc = [f for f in report.findings if f.scope == Scope.ALLOCATION]
    blocking = [f for f in alloc if f.severity == Severity.BLOCKING]
    assert blocking, "le dépassement 16kg > 15kg doit être détecté"
    assert "REQ-L3-ANT-MECH-001" in blocking[0].impacted_ids
    assert report.global_status == Severity.BLOCKING


def test_allocation_respected_at_l1():
    # L1=150, enfants L2 = 40+30+35 = 105 -> marge, pas de blocage à ce niveau
    action = Action(action_type=ActionType.UPDATE, target_id="REQ-L2-IT-001",
                    new_text="Allocation fixée à un maximum de 40 kg.")
    report = run_impact_analysis(_corpus(), action)
    l1 = [f for f in report.findings
          if f.scope == Scope.ALLOCATION and f.details.get("parent_id") == "REQ-L1-MAS-001"]
    assert l1 and l1[0].severity == Severity.INFO


def test_update_overflow_via_new_text():
    # Porter le châssis à 30 kg crée un dépassement franc (30+6 > 15)
    action = Action(action_type=ActionType.UPDATE, target_id="REQ-L4-ANT-CHAS-001",
                    new_text="Usinage du châssis : masse mesurée à 30 kg.")
    report = run_impact_analysis(_corpus(), action)
    assert report.global_status == Severity.BLOCKING


# --- propagation aval -----------------------------------------------------
def test_delete_creates_orphans():
    action = Action(action_type=ActionType.DELETE, target_id="REQ-L3-ANT-MECH-001")
    report = run_impact_analysis(_corpus(), action)
    aval = [f for f in report.findings if f.scope == Scope.AVAL]
    assert aval and aval[0].severity == Severity.BLOCKING
    assert "REQ-L4-ANT-CHAS-001" in aval[0].impacted_ids


def test_update_flags_downstream_retest():
    action = Action(action_type=ActionType.UPDATE, target_id="REQ-L2-ANT-001",
                    new_text="Allocation antennaire fixée à un maximum de 35 kg.")
    report = run_impact_analysis(_corpus(), action)
    aval = [f for f in report.findings if f.scope == Scope.AVAL]
    assert aval
    impacted = aval[0].impacted_ids
    assert "REQ-L3-ANT-MECH-001" in impacted and "REQ-L4-ANT-CHAS-001" in impacted


# --- override -------------------------------------------------------------
def test_create_id_collision_blocks():
    action = Action(action_type=ActionType.CREATE, target_id="REQ-L2-ANT-001",
                    new_text="doublon", parent_id="REQ-L1-MAS-001")
    report = run_impact_analysis(_corpus(), action)
    struct = [f for f in report.findings if f.scope == Scope.STRUCTURE]
    assert struct and report.global_status == Severity.BLOCKING


def test_create_beyond_max_level_blocks():
    # Enfant sous une L4 (max=5 -> niveau 5 OK) : on force niveau 6 pour tester la borne
    action = Action(action_type=ActionType.CREATE, target_id="REQ-NEW-X",
                    new_text="trop profond", parent_id="REQ-L4-ANT-CHAS-001", niveau=6)
    report = run_impact_analysis(_corpus(), action)
    assert any(f.scope == Scope.STRUCTURE for f in report.findings)
    assert report.global_status == Severity.BLOCKING


def test_duplicate_id_in_corpus_raises():
    import pytest
    dup = _corpus() + [dict(_corpus()[0])]  # ré-injecte un id existant
    with pytest.raises(ValueError):
        RequirementTree(dup)


def test_audit_structural_detects_allocation_and_missing_link():
    from src.audit import audit_matrix
    corpus = _corpus() + [
        {"id": "REQ-ORPHAN", "niveau": 2, "type": "X", "domaine": "Y",
         "texte": "Exigence rattachée à un parent inexistant.", "parent_id": "REQ-DOES-NOT-EXIST",
         "test_status": "PENDING"},
        # enfant qui fait déborder REQ-L3-ANT-MECH-001 (15 kg) : 10+6+12 = 28
        {"id": "REQ-L4-EXTRA", "niveau": 4, "type": "Real", "domaine": "Antenne_Mecanique",
         "texte": "Module additionnel : masse mesurée à 12 kg.", "parent_id": "REQ-L3-ANT-MECH-001",
         "test_status": "PENDING"},
    ]
    rep = audit_matrix(corpus, deep=False)
    axes = {f.axis for f in rep.findings}
    assert "LIEN" in axes        # parent inexistant
    assert "ALLOCATION" in axes  # dépassement de budget
    assert rep.score < 100
    assert "REQ-ORPHAN" in rep.flagged_ids


def test_typed_links_and_en9100_attributes():
    from src.models import Requirement, Link, LinkType
    r = Requirement(id="X", niveau=1, texte="t", verification="T", source="DOC-1",
                    links=[Link(type=LinkType.VERIFIES, target="Y")])
    assert r.verification == "T" and r.links[0].type == LinkType.VERIFIES
    # un corpus historique sans ces champs reste valide
    assert Requirement(id="Z", niveau=0, texte="t").links == []


def test_audit_typed_link_integrity():
    from src.audit import audit_matrix
    corpus = _corpus()
    corpus[1]["links"] = [{"type": "VERIFIES", "target": "REQ-GHOST"}]
    rep = audit_matrix(corpus, deep=False)
    axes = {f.axis for f in rep.findings}
    assert "LIEN" in axes        # lien typé vers une cible inexistante
    assert any("REQ-GHOST" in f.message for f in rep.findings)


def test_dag_links_add_parents_children_ancestors():
    corpus = _corpus()
    for r in corpus:
        if r["id"] == "REQ-L4-ANT-CHAS-001":
            r["links"] = [{"type": "DERIVE", "target": "REQ-L3-ANT-ELEC-001"}]
    t = RequirementTree(corpus)
    parents = {p.id for p in t.parents("REQ-L4-ANT-CHAS-001")}
    assert parents == {"REQ-L3-ANT-MECH-001", "REQ-L3-ANT-ELEC-001"}
    assert "REQ-L4-ANT-CHAS-001" in {c.id for c in t.children("REQ-L3-ANT-ELEC-001")}
    anc = {a.id for a in t.ancestors("REQ-L4-ANT-CHAS-001")}
    assert "REQ-L3-ANT-ELEC-001" in anc and "REQ-L2-ANT-001" in anc  # les deux branches amont
    # frères = partagent un parent (les enfants de ELEC + ceux de MECH)
    sibs = {s.id for s in t.siblings("REQ-L4-ANT-CHAS-001")}
    assert "REQ-L4-ANT-RAD-001" in sibs


def test_delete_orphans_via_typed_link():
    corpus = _corpus()
    for r in corpus:
        if r["id"] == "REQ-L4-ANT-RAD-001":
            r["links"] = [{"type": "DERIVE", "target": "REQ-L3-ANT-ELEC-001"}]
    rep = run_impact_analysis(corpus, Action(action_type=ActionType.DELETE,
                                             target_id="REQ-L3-ANT-ELEC-001"), semantic=False)
    aval = [f for f in rep.findings if f.scope == Scope.AVAL]
    assert aval and "REQ-L4-ANT-RAD-001" in aval[0].impacted_ids  # orphelin par lien typé


def test_load_many_merges_documents(tmp_path):
    import json
    from src.corpus_io import load_many
    from src.tree import RequirementTree
    # Document A : le besoin L0 ; Document B : une fille L1 qui référence le L0 de A.
    doc_a = tmp_path / "besoin.json"
    doc_b = tmp_path / "fonctionnel.json"
    doc_a.write_text(json.dumps({"exigences": [
        {"id": "R0", "niveau": 0, "type": "Besoin", "domaine": "Sys", "texte": "Besoin global.", "parent_id": None}]}))
    doc_b.write_text(json.dumps({"exigences": [
        {"id": "R1", "niveau": 1, "type": "Spec", "domaine": "Sys", "texte": "Décline le besoin.", "parent_id": "R0"}]}))
    merged, errs = load_many([str(doc_a), str(doc_b)])
    assert len(merged) == 2 and not errs
    t = RequirementTree(merged)
    # le lien inter-documents est résolu : R1 a bien R0 comme parent
    assert [p.id for p in t.parents("R1")] == ["R0"]


def test_load_many_dedups_across_documents(tmp_path):
    import json
    from src.corpus_io import load_many
    d1 = tmp_path / "a.json"
    d2 = tmp_path / "b.json"
    item = {"id": "DUP", "niveau": 0, "type": "x", "domaine": "d", "texte": "t", "parent_id": None}
    d1.write_text(json.dumps({"exigences": [item]}))
    d2.write_text(json.dumps({"exigences": [item]}))
    merged, errs = load_many([str(d1), str(d2)])
    assert len(merged) == 1  # doublon inter-documents écarté
    assert any("dupliqué" in e for e in errs)


# --- remap : liens typés entre exigences existantes -----------------------
def test_link_action_adds_typed_edge():
    from src.models import LinkType
    from src.orchestrator import build_candidate_tree
    tree = RequirementTree(_corpus())
    # rattache RAD (fille) à une seconde mère ELEC via un lien DERIVE
    action = Action(action_type=ActionType.LINK, target_id="REQ-L4-ANT-RAD-001",
                    link_target="REQ-L3-ANT-ELEC-001", link_type=LinkType.DERIVE)
    cand = build_candidate_tree(tree, action)
    parents = {p.id for p in cand.parents("REQ-L4-ANT-RAD-001")}
    assert parents == {"REQ-L3-ANT-MECH-001", "REQ-L3-ANT-ELEC-001"}
    # l'analyse n'échoue pas structurellement
    rep = run_impact_analysis(_corpus(), action, semantic=False)
    assert not any(f.scope == Scope.STRUCTURE and f.severity == Severity.BLOCKING
                   for f in rep.findings)


def test_link_cycle_blocked():
    from src.models import LinkType
    # rattacher un ancêtre (L2) sous l'un de ses descendants (L4) fermerait une boucle
    action = Action(action_type=ActionType.LINK, target_id="REQ-L2-ANT-001",
                    link_target="REQ-L4-ANT-CHAS-001", link_type=LinkType.DERIVE)
    rep = run_impact_analysis(_corpus(), action, semantic=False)
    assert rep.global_status == Severity.BLOCKING
    assert any(f.scope == Scope.STRUCTURE for f in rep.findings)


def test_link_self_blocked():
    from src.models import LinkType
    action = Action(action_type=ActionType.LINK, target_id="REQ-L2-ANT-001",
                    link_target="REQ-L2-ANT-001", link_type=LinkType.DERIVE)
    rep = run_impact_analysis(_corpus(), action, semantic=False)
    assert rep.global_status == Severity.BLOCKING


def test_link_duplicate_blocked():
    from src.models import LinkType
    corpus = _corpus()
    for r in corpus:
        if r["id"] == "REQ-L4-ANT-RAD-001":
            r["links"] = [{"type": "DERIVE", "target": "REQ-L3-ANT-ELEC-001"}]
    action = Action(action_type=ActionType.LINK, target_id="REQ-L4-ANT-RAD-001",
                    link_target="REQ-L3-ANT-ELEC-001", link_type=LinkType.DERIVE)
    rep = run_impact_analysis(corpus, action, semantic=False)
    assert rep.global_status == Severity.BLOCKING


def test_unlink_removes_typed_edge():
    from src.models import LinkType
    from src.orchestrator import build_candidate_tree
    corpus = _corpus()
    for r in corpus:
        if r["id"] == "REQ-L4-ANT-RAD-001":
            r["links"] = [{"type": "DERIVE", "target": "REQ-L3-ANT-ELEC-001"}]
    tree = RequirementTree(corpus)
    assert "REQ-L3-ANT-ELEC-001" in {p.id for p in tree.parents("REQ-L4-ANT-RAD-001")}
    action = Action(action_type=ActionType.UNLINK, target_id="REQ-L4-ANT-RAD-001",
                    link_target="REQ-L3-ANT-ELEC-001", link_type=LinkType.DERIVE)
    cand = build_candidate_tree(tree, action)
    assert "REQ-L3-ANT-ELEC-001" not in {p.id for p in cand.parents("REQ-L4-ANT-RAD-001")}


def test_unlink_missing_blocked():
    action = Action(action_type=ActionType.UNLINK, target_id="REQ-L4-ANT-RAD-001",
                    link_target="REQ-L3-ANT-ELEC-001")
    rep = run_impact_analysis(_corpus(), action, semantic=False)
    assert rep.global_status == Severity.BLOCKING


def test_trace_humanize_pertinence():
    from src import trace
    records = [{
        "label": "coherence_pertinence",
        "input": {"exigence_cible": {"id": "REQ-X", "texte": "Le châssis pèse 10 kg."},
                  "chaine_amont": [{"id": "REQ-P", "texte": "Budget 15 kg."}]},
        "output": {"est_coherent": False, "synthese": "Incohérent.", "preuve": "10 > 8",
                   "rupture_avec": ["REQ-P"]},
        "latency_ms": 120, "ok": True}]
    msgs = trace.humanize(records)
    assert len(msgs) == 1
    assert "Pertinence amont" in msgs[0]["agent"]
    assert "REQ-X" in msgs[0]["input"]
    assert "non" in msgs[0]["output"] and "REQ-P" in msgs[0]["output"]


def test_trace_humanize_audit_flags_only_problems():
    from src import trace
    recs = [
        {"label": "audit_exigence",
         "input": {"exigence": {"id": "REQ-A", "texte": "t"}, "parent": {"id": "P"},
                   "soeurs": [{"id": "S1"}], "filles": []},
         "output": {"redaction": {"conforme": True}, "pertinence": {"coherent": False,
                    "probleme": "contredit le parent"}, "couverture": {"complet": True},
                    "redondance": {"redondant": False}}, "ok": True},
        {"label": "audit_exigence",
         "input": {"exigence": {"id": "REQ-B", "texte": "t"}, "parent": None,
                   "soeurs": [], "filles": []},
         "output": {"redaction": {"conforme": True}, "pertinence": {"coherent": True},
                    "couverture": {"complet": True}, "redondance": {"redondant": False}}, "ok": True},
    ]
    hs = trace.humanize_audit(recs)
    assert [a["req_id"] for a in hs] == ["REQ-A", "REQ-B"]
    assert hs[0]["flagged"] is True and "Pertinence" in hs[0]["output"]
    assert hs[1]["flagged"] is False and "Conforme" in hs[1]["output"]
    # Cartes d'audit : échange complet (agent/role) pour la boîte de verre.
    assert hs[0]["agent"] == "Audit REQ-A" and hs[0]["role"] == "IA"


def test_trace_humanize_debate_agents():
    """Avocat + juge : nommés et formatés (plus de JSON brut)."""
    from src import trace
    records = [
        {"label": "defense_exigence",
         "input": {"exigence": {"id": "REQ-X", "texte": "Cible 850 MW."},
                   "accusation": "Contredit la plage 900-1000 MW amont."},
         "output": {"plaidoyer": "Deux modes distincts.",
                    "elements_contexte": ["mode dégradé"],
                    "refutation_possible": True},
         "ok": True},
        {"label": "juge_verdict",
         "input": {"accusation": "Contredit 900-1000 MW.", "plaidoyer": "Deux modes."},
         "output": {"verdict": "MAINTENU", "motivation": "850 viole la plage citée."},
         "ok": True},
    ]
    msgs = trace.humanize(records)
    assert msgs[0]["agent"] == "Avocat de la défense"
    assert "REQ-X" in msgs[0]["input"] and "Réfutation possible" in msgs[0]["output"]
    assert msgs[1]["agent"] == "Juge du débat"
    assert "MAINTENU" in msgs[1]["output"]
    # Aucun dump JSON brut (pas d'accolade de dict Python).
    assert "{" not in msgs[0]["output"] and "{" not in msgs[1]["output"]


def test_trace_humanize_audit_surfaces_debate():
    """Le débat déclenché pendant l'audit remonte dans la boîte de verre."""
    from src import trace
    recs = [
        {"label": "audit_exigence",
         "input": {"exigence": {"id": "REQ-A", "texte": "t"}, "parent": None,
                   "soeurs": [], "filles": []},
         "output": {"redaction": {"conforme": True}, "pertinence": {"coherent": False,
                    "probleme": "x"}, "couverture": {"complet": True},
                    "redondance": {"redondant": False}}, "ok": True},
        {"label": "defense_exigence",
         "input": {"exigence": {"id": "REQ-A"}, "accusation": "incohérent"},
         "output": {"plaidoyer": "p", "refutation_possible": False}, "ok": True},
        {"label": "juge_verdict",
         "input": {"accusation": "incohérent", "plaidoyer": "p"},
         "output": {"verdict": "RETROGRADE", "motivation": "m"}, "ok": True},
    ]
    hs = trace.humanize_audit(recs)
    agents = [a["agent"] for a in hs]
    assert agents == ["Audit REQ-A", "Avocat de la défense", "Juge du débat"]
    assert "RÉTROGRADÉ" in hs[2]["output"]


def test_trace_generation_timeline_filters_to_children():
    """La timeline de génération garde la proposition + les records des filles,
    et écarte l'audit des exigences préexistantes (bruit)."""
    from src import trace
    records = [
        {"label": "generation_filles",
         "input": {"exigence_mere": {"id": "REQ-L1-A-001", "texte": "m"},
                   "niveau_filles": 2, "filles_existantes": []},
         "output": {"filles": [{"texte": "fille 1", "aspect_couvert": "poids"}],
                    "aspects_non_couverts": []}, "ok": True},
        # Audit d'une exigence préexistante : bruit -> écarté.
        {"label": "audit_exigence",
         "input": {"exigence": {"id": "REQ-OLD-999"}, "parent": None,
                   "soeurs": [], "filles": []},
         "output": {"redaction": {"conforme": True}}, "ok": True},
        # Audit d'une fille générée : conservé.
        {"label": "audit_exigence",
         "input": {"exigence": {"id": "REQ-L2-A-001"}, "parent": None,
                   "soeurs": [], "filles": []},
         "output": {"redaction": {"conforme": True}}, "ok": True},
    ]
    tl = trace.build_generation_timeline(records, child_ids=["REQ-L2-A-001"])
    agents = [m["agent"] for m in tl]
    assert "Agent Génération (déclinaison)" in agents[0]
    assert "Agent Audit" in agents  # la fille
    # L'exigence préexistante n'a produit aucune carte.
    assert not any("REQ-OLD-999" in (m.get("input") or "") for m in tl)
    assert "fille 1" in tl[0]["output"]


# --- suggestion de correction --------------------------------------------
def test_suggest_correction_builds_context_and_returns(monkeypatch):
    from src import correction
    captured = {}

    def fake_call_agent(prompt, payload, label=None):
        captured.update(prompt=prompt, payload=payload, label=label)
        return {"texte_propose": "Le châssis doit présenter une masse ≤ 10 kg.",
                "changements": ["Ajout du verbe « doit »", "Critère quantifié"],
                "justification": "Énoncé rendu vérifiable.", "corrige_tout": True}

    monkeypatch.setattr(correction.llm, "call_agent", fake_call_agent)
    # REQ-L3-ANT-MECH-001 a des ancêtres ET des filles (CHAS, RAD)
    out = correction.suggest_correction(_corpus(), "REQ-L3-ANT-MECH-001",
                                        problems=["[REDACTION] termes imprécis"])
    assert out["texte"].startswith("Le châssis") and out["corrige_tout"] is True
    assert out["changements"]
    p = captured["payload"]
    assert p["exigence"]["id"] == "REQ-L3-ANT-MECH-001"
    assert any(a["id"] == "REQ-L2-ANT-001" for a in p["ancetres"])
    assert {c["id"] for c in p["filles"]} == {"REQ-L4-ANT-CHAS-001", "REQ-L4-ANT-RAD-001"}
    assert p["problemes_detectes"] == ["[REDACTION] termes imprécis"]
    assert captured["label"] == "suggest_correction"
    assert "R-DOIT" in captured["prompt"]  # règles EN9100 injectées dans le prompt


def test_suggest_correction_missing_req():
    from src import correction
    out = correction.suggest_correction(_corpus(), "REQ-DOES-NOT-EXIST")
    assert out.get("error")


def test_suggest_correction_llm_error(monkeypatch):
    from src import correction
    monkeypatch.setattr(correction.llm, "call_agent",
                        lambda *a, **k: {"error": "LLM_INVOCATION_ERROR"})
    out = correction.suggest_correction(_corpus(), "REQ-L4-ANT-CHAS-001")
    assert out.get("error") == "LLM_INVOCATION_ERROR"


def test_override_downgrades_blocking():
    action = Action(action_type=ActionType.DELETE, target_id="REQ-L3-ANT-MECH-001",
                    force_override=True, override_rationale="Refonte du sous-système.")
    report = run_impact_analysis(_corpus(), action)
    assert report.global_status != Severity.BLOCKING
    assert any(f.details.get("overridden") for f in report.findings)


# --- T4 : pertinence aval (cohérence de la cible vs ses filles) -----------
def _ctx_for(corpus, action):
    from src.analyzers import Ctx
    from src.orchestrator import build_candidate_tree
    current = RequirementTree(corpus)
    return Ctx(current=current, candidate=build_candidate_tree(current, action), action=action)


_AVAL_CORPUS = [
    {"id": "P", "niveau": 0, "type": "x", "domaine": "d",
     "texte": "Le drone doit voler au moins 2 heures.", "parent_id": None, "test_status": "PENDING"},
    {"id": "C", "niveau": 1, "type": "x", "domaine": "d",
     "texte": "L'autonomie de vol est limitée à 30 minutes.", "parent_id": "P", "test_status": "PENDING"},
]


def test_pertinence_aval_skips_when_no_children():
    # Cible feuille (C n'a pas de fille) -> aucun constat, aucun appel LLM.
    from src.analyzers import analyze_pertinence_aval
    ctx = _ctx_for(_AVAL_CORPUS, Action(action_type=ActionType.UPDATE, target_id="C",
                                        new_text="L'autonomie de vol est d'au moins 2 heures."))
    assert analyze_pertinence_aval(ctx) == []


def test_pertinence_aval_skips_on_delete():
    from src.analyzers import analyze_pertinence_aval
    ctx = _ctx_for(_AVAL_CORPUS, Action(action_type=ActionType.DELETE, target_id="C"))
    assert analyze_pertinence_aval(ctx) == []


def test_pertinence_aval_flags_incoherent_child(monkeypatch):
    from src import analyzers
    from src.analyzers import analyze_pertinence_aval
    monkeypatch.setattr(analyzers, "LLM_VOTE", 1)  # neutralise le re-vote
    monkeypatch.setattr(analyzers.llm, "call_skill", lambda skill, payload: {
        "est_coherent": False, "rupture_avec": ["C"], "niveau_gravite": "BLOCKING",
        "preuve": "limitée à 30 minutes", "synthese": "La fille contredit la cible."})
    # Cible P (a une fille C) modifiée -> l'agent aval juge la déclinaison.
    ctx = _ctx_for(_AVAL_CORPUS, Action(action_type=ActionType.UPDATE, target_id="P",
                                        new_text="Le drone doit voler au moins 2 heures."))
    findings = analyze_pertinence_aval(ctx)
    assert len(findings) == 1
    assert findings[0].scope == Scope.PERTINENCE_AVAL
    assert findings[0].severity == Severity.BLOCKING
    assert "C" in findings[0].impacted_ids


# --- Impact latent & co-références (analyseurs trans-matrice) --------------
def test_impact_latent_skips_on_delete():
    from src.analyzers import analyze_impact_latent
    ctx = _ctx_for(_AVAL_CORPUS, Action(action_type=ActionType.DELETE, target_id="C"))
    assert analyze_impact_latent(ctx) == []


def test_impact_latent_skips_without_embeddings(monkeypatch):
    from src import analyzers
    from src.analyzers import analyze_impact_latent
    monkeypatch.setattr(analyzers.embeddings, "embeddings_available", lambda: False)
    ctx = _ctx_for(_AVAL_CORPUS, Action(action_type=ActionType.UPDATE, target_id="P",
                                        new_text="Le drone doit voler au moins 2 heures."))
    assert analyze_impact_latent(ctx) == []


def test_referents_extracts_codes_and_units():
    from src.analyzers import _referents
    toks, units = _referents("Le bus CAN délivre 28 V via une liaison RS422.")
    assert "CAN" in toks and "RS422" in toks
    assert "V" in units


def test_coreference_skips_when_no_shared_referent():
    from src.analyzers import analyze_coreference
    corpus = [
        {"id": "X", "niveau": 0, "texte": "Le drone doit être léger.", "parent_id": None},
        {"id": "Y", "niveau": 0, "texte": "La caméra doit filmer en couleur.", "parent_id": None},
    ]
    ctx = _ctx_for(corpus, Action(action_type=ActionType.UPDATE, target_id="X",
                                  new_text="Le drone doit être léger."))
    assert analyze_coreference(ctx) == []


def test_coreference_flags_conflict(monkeypatch):
    from src import analyzers
    from src.analyzers import analyze_coreference
    corpus = [
        {"id": "CR-A", "niveau": 0, "texte": "La radio est alimentée en 28 V.", "parent_id": None},
        {"id": "CR-B", "niveau": 0, "texte": "Le bus délivre une tension de 24 V.", "parent_id": None},
    ]
    monkeypatch.setattr(analyzers.llm, "call_skill", lambda skill, payload: {
        "coherent": False, "conflits": [{"id": "CR-B", "probleme": "28 V vs 24 V"}],
        "niveau_gravite": "BLOCKING", "preuve": "28 V / 24 V", "synthese": "Tension incohérente."})
    ctx = _ctx_for(corpus, Action(action_type=ActionType.UPDATE, target_id="CR-A",
                                  new_text="La radio est alimentée en 28 V."))
    findings = analyze_coreference(ctx)
    assert len(findings) == 1
    assert findings[0].scope == Scope.COHERENCE_REF
    assert findings[0].severity == Severity.BLOCKING
    assert "CR-B" in findings[0].impacted_ids


def test_link_forbids_level_jump():
    import pytest
    from src.models import LinkType
    from src.orchestrator import build_candidate_tree, ActionError
    corpus = [
        {"id": "L0", "niveau": 0, "texte": "Besoin.", "parent_id": None},
        {"id": "L1", "niveau": 1, "texte": "Système.", "parent_id": "L0"},
        {"id": "L2", "niveau": 2, "texte": "Sous-système.", "parent_id": "L1"},
        {"id": "L3", "niveau": 3, "texte": "Composant.", "parent_id": "L2"},
    ]
    tree = RequirementTree(corpus)
    # Saut de niveau L3 (L3) -> L1 (L1) : diff de 2 niveaux -> rejeté.
    with pytest.raises(ActionError):
        build_candidate_tree(tree, Action(action_type=ActionType.LINK, target_id="L3",
                                          link_target="L1", link_type=LinkType.DERIVE))
    # Niveaux adjacents L2 -> L1 : accepté.
    out = build_candidate_tree(tree, Action(action_type=ActionType.LINK, target_id="L2",
                                            link_target="L1", link_type=LinkType.DERIVE))
    assert any(lk.target == "L1" for lk in out.get("L2").links)
