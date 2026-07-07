"""Tests du harnais d'éval réutilisable (`run_golden_eval`), hors-ligne.

Cas minimaux injectés via `load_cases` mocké ; mode rapide (déterministe)
uniquement — aucune dépendance LLM/embeddings.
"""

import json

import pytest

from eval import run_eval

# Deux cas sur un mini-corpus : un dépassement d'allocation (ALLOCATION
# attendu) et une mise à jour propre (aucun axe attendu).
_CORPUS = [
    {"id": "P", "niveau": 0, "type": "x", "domaine": "d",
     "texte": "La masse totale ne doit pas excéder 10 kg.", "parent_id": None, "test_status": "PENDING"},
    {"id": "C1", "niveau": 1, "type": "x", "domaine": "d",
     "texte": "masse mesurée à 8 kg", "parent_id": "P", "test_status": "PENDING"},
]

_CASES = [
    {"name": "alloc_overflow", "expected": ["ALLOCATION"], "corpus": _CORPUS,
     "action": {"action_type": "UPDATE", "target_id": "C1",
                "new_text": "La masse mesurée est de 15 kg."}},
    {"name": "clean_update", "expected": [], "corpus": _CORPUS,
     "action": {"action_type": "UPDATE", "target_id": "C1",
                "new_text": "masse mesurée à 8 kg"}},
]


@pytest.fixture(autouse=True)
def _sandbox(monkeypatch, tmp_path):
    # last_eval.json écrit dans un dossier jetable, cas injectés.
    monkeypatch.setattr(run_eval, "_DIR", tmp_path)
    monkeypatch.setattr(run_eval, "load_cases", lambda: (list(_CASES), 1))
    yield tmp_path


def test_scores_et_structure(_sandbox):
    out = run_eval.run_golden_eval(semantic=False)
    assert out["cases"] == 2 and out["dropped"] == 1 and out["semantic"] is False
    # ALLOCATION : 1 TP, pas de FP/FN attendus sur ce mini-jeu.
    ax = out["per_axis"]["ALLOCATION"]
    assert ax["tp"] == 1 and ax["fn"] == 0
    assert out["micro"]["tp"] >= 1
    assert set(out) >= {"precision", "recall", "f1", "per_axis", "micro"}


def test_ecrit_last_eval_json(_sandbox):
    out = run_eval.run_golden_eval(semantic=False)
    data = json.loads((_sandbox / "last_eval.json").read_text(encoding="utf-8"))
    assert data["cases"] == 2
    assert data["precision"] == out["precision"]
    # Format historique conservé : per_axis ne porte que precision/recall.
    assert set(data["per_axis"]["ALLOCATION"]) == {"precision", "recall"}


def test_on_progress_et_on_log(_sandbox):
    ticks, lignes = [], []
    run_eval.run_golden_eval(semantic=False,
                             on_progress=lambda d, t: ticks.append((d, t)),
                             on_log=lignes.append)
    assert ticks == [(1, 2), (2, 2)]
    assert lignes and "2 cas valides (1 écartés)" in lignes[0]


def test_annulation_remonte(_sandbox):
    # L'appelant (API SSE) lève depuis on_progress pour annuler : la levée
    # doit remonter telle quelle, sans écrire de résultat.
    class Stop(Exception):
        pass

    def cancel(done, total):
        raise Stop()

    with pytest.raises(Stop):
        run_eval.run_golden_eval(semantic=False, on_progress=cancel)
    assert not (_sandbox / "last_eval.json").exists()
