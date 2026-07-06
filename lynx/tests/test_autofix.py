"""Tests de la correction en lot (LLM mocké, ré-audit déterministe deep=False)."""

import threading

import pytest

from src import autofix
from src.audit import audit_matrix

# Fixture : le plafond de P (10 kg) est dépassé par ses filles (8 + 5 = 13 kg)
# -> constat ALLOCATION BLOQUANT sur P, corrigeable en réécrivant son texte.
_FIXTURE = [
    {"id": "P", "niveau": 0, "type": "x", "domaine": "d",
     "texte": "La masse totale ne doit pas excéder 10 kg.", "parent_id": None, "test_status": "PENDING"},
    {"id": "C1", "niveau": 1, "type": "x", "domaine": "d",
     "texte": "masse mesurée à 8 kg", "parent_id": "P", "test_status": "PENDING"},
    {"id": "C2", "niveau": 1, "type": "x", "domaine": "d",
     "texte": "masse mesurée à 5 kg", "parent_id": "P", "test_status": "PENDING"},
]


def _corpus():
    return [dict(r) for r in _FIXTURE]


@pytest.fixture(autouse=True)
def _no_embeddings(monkeypatch):
    # Ré-audit 100 % déterministe : pas d'appel embeddings pendant les tests.
    monkeypatch.setattr("src.audit.embeddings.embeddings_available", lambda: False)


def _findings(corpus):
    return [vars(f) for f in audit_matrix(corpus, deep=False).findings]


def _fake_suggest(texte_par_id):
    """suggest_correction mocké : renvoie le texte prévu pour l'exigence."""
    def fake(corpus, req_id, problems=None):
        val = texte_par_id[req_id]
        if callable(val):
            val = val()
        if isinstance(val, dict):
            return val
        return {"texte": val, "justification": f"Réécriture de {req_id}.",
                "changements": ["budget ajusté"], "corrige_tout": True}
    return fake


def test_convergence_en_une_passe(monkeypatch):
    # Porter le budget à 15 kg absorbe les 13 kg des filles : plus aucun constat.
    monkeypatch.setattr(autofix, "suggest_correction",
                        _fake_suggest({"P": "La masse totale ne doit pas excéder 15 kg."}))
    corpus = _corpus()
    events = []
    out = autofix.run_batch_fix(corpus, _findings(corpus), deep=False,
                                on_progress=events.append)
    assert out["passes"] == 1
    assert out["compteurs"] == {"total": 1, "corrigees": 1, "ameliorees": 0,
                                "recalcitrantes": 0, "echecs": 0, "inchangees": 0}
    (et,) = out["recap"]
    assert et["req_id"] == "P" and et["statut"] == "corrigee" and et["passes"] == 1
    assert et["texte_avant"] == "La masse totale ne doit pas excéder 10 kg." and et["texte_apres"] == "La masse totale ne doit pas excéder 15 kg."
    assert et["findings_avant"] and et["findings_apres"] == []
    assert et["justification"]
    # La progression annonce bien la passe et l'exigence en cours.
    corr = [e for e in events if e["phase"] == "correction"]
    assert corr == [{"phase": "correction", "passe": 1, "done": 1, "total": 1, "req_id": "P"}]


def test_recalcitrante_apres_trois_passes(monkeypatch):
    # L'agent propose un texte différent à chaque passe… toujours insuffisant.
    propositions = iter(["La masse totale ne doit pas excéder 11 kg.", "La masse totale ne doit pas excéder 12 kg.", "La masse totale ne doit pas excéder 12,5 kg."])
    monkeypatch.setattr(autofix, "suggest_correction",
                        _fake_suggest({"P": lambda: next(propositions)}))
    corpus = _corpus()
    out = autofix.run_batch_fix(corpus, _findings(corpus), max_passes=3, deep=False)
    (et,) = out["recap"]
    assert et["statut"] == "recalcitrante" and et["passes"] == 3
    assert out["passes"] == 3 and out["compteurs"]["recalcitrantes"] == 1
    assert any(f["severity"] == "BLOQUANT" for f in et["findings_apres"])


def test_erreur_de_suggestion_ninterrompt_pas_le_lot(monkeypatch):
    # Deux sous-arbres en dépassement : P1 échoue (erreur LLM), P2 est corrigé.
    corpus = [
        {"id": "P1", "niveau": 0, "type": "x", "domaine": "d",
         "texte": "La masse totale ne doit pas excéder 10 kg.", "parent_id": None, "test_status": "PENDING"},
        {"id": "A", "niveau": 1, "type": "x", "domaine": "d",
         "texte": "masse mesurée à 13 kg", "parent_id": "P1", "test_status": "PENDING"},
        {"id": "P2", "niveau": 0, "type": "x", "domaine": "d",
         "texte": "La masse ne doit pas excéder 2 kg.", "parent_id": None, "test_status": "PENDING"},
        {"id": "B", "niveau": 1, "type": "x", "domaine": "d",
         "texte": "masse mesurée à 3 kg", "parent_id": "P2", "test_status": "PENDING"},
    ]
    appels = []

    def fake(c, req_id, problems=None):
        appels.append(req_id)
        if req_id == "P1":
            return {"error": "LLM_INVOCATION_ERROR"}
        return {"texte": "La masse ne doit pas excéder 4 kg.", "justification": "ok"}

    monkeypatch.setattr(autofix, "suggest_correction", fake)
    out = autofix.run_batch_fix(corpus, _findings(corpus), deep=False)
    par_id = {e["req_id"]: e for e in out["recap"]}
    assert par_id["P1"]["statut"] == "echec_suggestion"
    assert par_id["P1"]["erreur"] == "LLM_INVOCATION_ERROR"
    assert par_id["P2"]["statut"] == "corrigee"
    assert out["compteurs"]["echecs"] == 1 and out["compteurs"]["corrigees"] == 1
    assert appels.count("P1") == 1  # l'échec n'est pas retenté


def test_corpus_dentree_jamais_mute(monkeypatch):
    monkeypatch.setattr(autofix, "suggest_correction",
                        _fake_suggest({"P": "La masse totale ne doit pas excéder 15 kg."}))
    corpus = _corpus()
    avant = [dict(r) for r in corpus]
    autofix.run_batch_fix(corpus, _findings(corpus), deep=False)
    assert corpus == avant  # aucune mutation du corpus réel


def test_texte_identique_donne_inchangee(monkeypatch):
    appels = []

    def fake(c, req_id, problems=None):
        appels.append(req_id)
        return {"texte": "La masse totale ne doit pas excéder 10 kg.", "justification": "rien à changer"}

    monkeypatch.setattr(autofix, "suggest_correction", fake)
    corpus = _corpus()
    out = autofix.run_batch_fix(corpus, _findings(corpus), deep=False)
    (et,) = out["recap"]
    assert et["statut"] == "inchangee"
    assert et["texte_apres"] == et["texte_avant"]
    assert out["compteurs"]["inchangees"] == 1
    assert appels == ["P"]  # pas de nouvelle tentative sur un texte identique


def test_warning_dans_le_perimetre_et_info_ignore(monkeypatch):
    # Un constat WARNING est traité ; un INFO ne déclenche rien.
    monkeypatch.setattr(autofix, "suggest_correction",
                        _fake_suggest({"C1": "La masse de C1 est mesurée à 8 kg."}))
    corpus = _corpus()
    findings = [
        {"req_id": "C1", "axis": "REDACTION", "severity": "WARNING",
         "message": "Rédaction : forme non normative."},
        {"req_id": "C2", "axis": "NON_AUDITE", "severity": "INFO",
         "message": "Non audité."},
    ]
    out = autofix.run_batch_fix(corpus, findings, deep=False)
    assert [e["req_id"] for e in out["recap"]] == ["C1"]
    assert out["recap"][0]["statut"] == "corrigee"  # le ré-audit structurel ne signale rien


def test_annulation_stoppe_la_boucle(monkeypatch):
    cancelled = threading.Event()
    cancelled.set()
    monkeypatch.setattr(autofix, "suggest_correction",
                        _fake_suggest({"P": "La masse totale ne doit pas excéder 15 kg."}))
    corpus = _corpus()
    with pytest.raises(autofix.BatchCancelled):
        autofix.run_batch_fix(corpus, _findings(corpus), deep=False, cancelled=cancelled)
