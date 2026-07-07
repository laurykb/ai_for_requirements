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
