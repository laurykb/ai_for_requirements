from src import corpus_gen


def test_architecture_arbre_valide_et_profond():
    elems = corpus_gen.build_architecture()
    ids = {e["id"] for e in elems}
    racines = [e for e in elems if e["parent"] is None]
    assert len(racines) == 1 and racines[0]["id"] == "AE-SYS"
    by_id = {e["id"]: e for e in elems}
    for e in elems:
        if e["parent"] is not None:
            assert e["parent"] in ids
            assert e["niveau"] == by_id[e["parent"]]["niveau"] + 1
    assert len(ids) == len(elems)                    # ids uniques
    assert max(e["niveau"] for e in elems) == 7      # profondeur atteinte
    assert 250 <= len(elems) <= 500                  # branche descendante ~350


def test_catalogues_coherents():
    # 8 niveaux de DÉCLINAISON (aucun « Test » : la vérification est une branche à part)
    assert [n["niveau"] for n in corpus_gen.NIVEAUX] == list(range(8))
    assert all("test" not in n["label"].lower() for n in corpus_gen.NIVEAUX)
    assert all("id" in m and "label" in m for m in corpus_gen.MODES)
    assert len(corpus_gen.SOUS_SYSTEMES) >= 5


def test_derive_requirements_structure_coherente():
    elems = corpus_gen.build_architecture()
    reqs = corpus_gen.derive_requirements(elems)
    assert len(reqs) == len(elems)
    ids = {r["id"] for r in reqs}
    by_id = {r["id"]: r for r in reqs}
    for r in reqs:
        if r["parent_id"] is not None:
            assert r["parent_id"] in ids
            assert r["niveau"] == by_id[r["parent_id"]]["niveau"] + 1
        assert r["alloue_a"] and isinstance(r["alloue_a"], list)
        assert set(r["contexte_operationnel"]) <= set(corpus_gen.MODE_IDS)
        assert r["base_derivation"]["phase"]
    assert all(r["id"].startswith("REQ-L") for r in reqs)
    assert sum(1 for r in reqs if r["parent_id"] is None) == 1
