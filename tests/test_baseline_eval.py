"""Harnais d'éval du chat baseline : scoring pur (sans index ni LLM)."""
from __future__ import annotations

from evals.run_baseline_eval import aggregate, load_golden, load_reference, score_case


def test_score_retrieval_case_recall_and_rank():
    case = {"question": "q", "expected_req_ids": ["A", "B"], "expect_abstain": False}
    row = score_case(case, ["X", "B", "A"], abstained=False)
    assert row["ok"] and row["recall"] == 1.0 and row["first_rank"] == 2


def test_score_retrieval_case_false_abstention_fails():
    case = {"question": "q", "expected_req_ids": ["A"], "expect_abstain": False}
    row = score_case(case, [], abstained=True)
    assert not row["ok"] and row["abstained"]


def test_score_abstention_case():
    case = {"question": "q", "expected_req_ids": [], "expect_abstain": True}
    assert score_case(case, [], abstained=True)["ok"]
    assert not score_case(case, ["A"], abstained=False)["ok"]


def test_aggregate_mixes_kinds():
    rows = [
        score_case({"question": "a", "expected_req_ids": ["A"], "expect_abstain": False},
                   ["A"], False),
        score_case({"question": "b", "expected_req_ids": ["B"], "expect_abstain": False},
                   [], True),
        score_case({"question": "c", "expected_req_ids": [], "expect_abstain": True},
                   [], True),
    ]
    s = aggregate(rows)
    assert s["retrieval_ok"] == 1 and s["retrieval_total"] == 2
    assert s["false_abstentions"] == 1
    assert s["abstention_ok"] == 1 and s["abstention_total"] == 1


def test_golden_set_consistent_with_reference():
    ref_ids = {r["id"] for r in load_reference()}
    for case in load_golden():
        for rid in case.get("expected_req_ids") or []:
            assert rid in ref_ids, f"{rid} absent de la baseline de référence"
        if case.get("expect_abstain"):
            assert not case.get("expected_req_ids")


def test_looks_like_refusal_markers():
    from evals.run_baseline_eval import looks_like_refusal
    assert looks_like_refusal("Je ne sais pas sur la base du contexte fourni.")
    assert looks_like_refusal("La baseline ne couvre pas ce point.")
    assert not looks_like_refusal("CYB-001 impose le chiffrement AES-256 [1].")


def test_extract_req_ids_known_vs_invented():
    from evals.run_baseline_eval import extract_req_ids
    known = {"CYB-001", "ALM-002"}
    cited, invented = extract_req_ids(
        "CYB-001 impose le chiffrement [1] ; voir aussi CYB-999 et ALM-002. CYB-001 encore.",
        known)
    assert cited == ["CYB-001", "ALM-002"]     # dédupliqués, ordre d'apparition
    assert invented == ["CYB-999"]              # forme REQ mais absent de la baseline


def test_score_generation_case_grounding():
    from evals.run_baseline_eval import score_generation_case
    case = {"question": "q", "expected_req_ids": ["CYB-001"]}
    known = {"CYB-001", "CYB-002"}
    ok = score_generation_case(case, "CYB-001 exige AES-256 [1].", known,
                               retrieved_ids=["CYB-001"], quality={"faithfulness": 0.9})
    assert ok["ok"] and ok["id_grounded"] and ok["id_recall"] == 1.0
    bad = score_generation_case(case, "CYB-002 répond à la question.", known,
                                retrieved_ids=["CYB-001"], quality=None)
    assert not bad["ok"]                        # l'attendu n'est pas nommé
    assert bad["off_context"] == ["CYB-002"]    # cité mais pas dans les passages
