"""
Tests unitaires de l'affinage du contexte (retrieval/context_refine) - déterministe.

reorder_long_context : meilleurs passages aux extrémités (atténuation « lost in the middle »).
dedup_chunks : retire les passages quasi-redondants en gardant le mieux classé, ordre préservé.
"""
from retrieval.context_refine import reorder_long_context, dedup_chunks


def _docs(*letters):
    return [{"doc": x} for x in letters]


# -- reorder_long_context ------------------------------------------------------
def test_reorder_puts_best_at_extremities():
    # Entrée triée pertinence décroissante A..E -> A (meilleur) en tête, B (2e) en queue.
    out = [c["doc"] for c in reorder_long_context(_docs("A", "B", "C", "D", "E"))]
    assert out == ["A", "C", "E", "D", "B"]
    assert out[0] == "A" and out[-1] == "B"


def test_reorder_small_inputs_unchanged():
    assert reorder_long_context([]) == []
    assert [c["doc"] for c in reorder_long_context(_docs("A"))] == ["A"]
    assert [c["doc"] for c in reorder_long_context(_docs("A", "B"))] == ["A", "B"]


def test_reorder_preserves_all_elements():
    out = reorder_long_context(_docs("A", "B", "C", "D"))
    assert sorted(c["doc"] for c in out) == ["A", "B", "C", "D"]


# -- dedup_chunks --------------------------------------------------------------
def test_dedup_removes_exact_duplicate_keeps_first():
    chunks = [
        {"doc": "le niveau eal3 augmente est retenu pour la cible", "meta": {"id": "1"}},
        {"doc": "le niveau eal3 augmente est retenu pour la cible", "meta": {"id": "2"}},
        {"doc": "un sujet totalement different sans aucun rapport ici", "meta": {"id": "3"}},
    ]
    out = dedup_chunks(chunks)
    ids = [c["meta"]["id"] for c in out]
    assert ids == ["1", "3"]            # doublon (2) retiré, 1er gardé, ordre préservé


def test_dedup_removes_child_included_in_parent():
    # Un chunk enfant entièrement inclus dans une section parente (parent-child) -> redondant.
    parent = {"doc": "introduction la toe est evaluee eal3 augmente selon les criteres communs v3", "meta": {"id": "p"}}
    child = {"doc": "eal3 augmente", "meta": {"id": "c"}}
    out = dedup_chunks([parent, child], threshold=0.85)
    assert [c["meta"]["id"] for c in out] == ["p"]


def test_dedup_keeps_distinct_passages():
    chunks = _docs("les menaces pesant sur la cible de securite",
                   "les hypotheses sur l'environnement operationnel")
    assert len(dedup_chunks(chunks)) == 2


def test_dedup_handles_empty_docs():
    chunks = [{"doc": ""}, {"doc": "contenu reel et distinct"}]
    assert len(dedup_chunks(chunks)) == 2   # doc vide non traité comme doublon
