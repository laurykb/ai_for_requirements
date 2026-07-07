"""Garde-fous de génération (api/rag.py) : détection des sorties dégénérées
et coupure propre du flux SSE (réponse partielle conservée, trame `error`,
puis `done`). Tout est hors-ligne : le pipeline RAG est simulé."""
import itertools
import json

import pytest

from api.rag import _degenerate, _LOOP_WINDOW, _LOOP_MIN_REPEATS, MAX_ANSWER_CHARS


# ─── Unitaire : _degenerate ───────────────────────────────────────────────────

def test_texte_normal_non_signale():
    assert _degenerate("Une réponse courte et saine.") is None


def test_texte_long_mais_varie_non_signale():
    # Long (mais < MAX) et non périodique : ne doit pas être signalé.
    text = " ".join(f"phrase-{i}" for i in range(2000))[:MAX_ANSWER_CHARS - 1]
    assert _degenerate(text) is None


def test_boucle_de_repetition_detectee():
    motif = "Le système doit journaliser les accès. " * 30  # >> _LOOP_WINDOW
    text = "Préambule sain. " + motif
    assert _degenerate(text) == "boucle de répétition détectée"


def test_repetition_trop_courte_non_signalee():
    # Répétition réelle mais texte < fenêtre × répétitions : pas de verdict.
    text = "ab" * ((_LOOP_WINDOW * _LOOP_MIN_REPEATS - 2) // 2)
    assert _degenerate(text) is None


def test_reponse_trop_longue_detectee():
    text = " ".join(f"mot{i}" for i in range(6000))
    assert len(text) > MAX_ANSWER_CHARS
    assert _degenerate(text) == "réponse anormalement longue"


# ─── Intégration : flux SSE /api/ask coupé proprement ────────────────────────

def _sse_events(body: str) -> list[dict]:
    events = []
    for line in body.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app
    import api.rag as rag
    import core.attribution

    # Pas d'écriture Mongo ni d'appel LLM d'attribution pendant le test.
    monkeypatch.setattr(rag, "_persist_exchange", lambda *a, **k: None)
    monkeypatch.setattr(
        core.attribution, "attribute_answer",
        lambda *a, **k: {"ok": False, "affirmations": [], "n_affirmations": 0,
                         "n_sourcees": 0, "n_completees": 0, "n_non_sourcees": 0,
                         "error": "hors-ligne (test)"})
    return TestClient(app)


def _fake_pipeline(monkeypatch, token_source):
    """process_query_stream simulé : un chunk, des citations, et le flux fourni."""
    import core.ask

    closed = {"value": False}

    def tokens():
        try:
            yield from token_source
        finally:
            closed["value"] = True

    chunk = {"text": "extrait", "source": "doc.md", "chunk_id": "c1"}
    monkeypatch.setattr(
        core.ask, "process_query_stream",
        lambda *a, **k: (tokens(), [chunk], [{"source": "doc.md"}]))
    return closed


def test_flux_boucle_coupe_avec_reponse_partielle(client, monkeypatch):
    # Le "modèle" répète le même token à l'infini : le flux doit être coupé.
    closed = _fake_pipeline(
        monkeypatch, itertools.repeat("Le système doit journaliser. ", 100_000))

    r = client.post("/api/ask", json={"question": "q", "mode": "rag",
                                      "session_id": "s-test"})
    assert r.status_code == 200
    events = _sse_events(r.text)
    kinds = [e["type"] for e in events]

    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1
    assert "boucle de répétition" in errors[0]["message"]
    # Des tokens ont bien été servis avant la coupure (réponse partielle)...
    assert kinds.index("token") < kinds.index("error")
    # ... le flux se termine proprement (sources + done) et Ollama est coupé.
    assert kinds.index("error") < kinds.index("sources") < kinds.index("done")
    assert closed["value"] is True
    # La coupure est rapide : on n'a pas consommé les 100 000 tokens.
    assert kinds.count("token") < 200


def test_flux_trop_long_coupe(client, monkeypatch):
    # Texte varié (pas de boucle) mais interminable : plafond de longueur.
    closed = _fake_pipeline(
        monkeypatch, (f"mot{i} " for i in range(100_000)))

    r = client.post("/api/ask", json={"question": "q", "mode": "rag",
                                      "session_id": "s-test"})
    events = _sse_events(r.text)
    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1
    assert "anormalement longue" in errors[0]["message"]
    assert closed["value"] is True


def test_flux_sain_sans_coupure(client, monkeypatch):
    # Une génération normale ne déclenche AUCUN garde-fou.
    _fake_pipeline(monkeypatch, iter(["Réponse ", "complète ", "et saine."]))

    r = client.post("/api/ask", json={"question": "q", "mode": "rag",
                                      "session_id": "s-test"})
    events = _sse_events(r.text)
    kinds = [e["type"] for e in events]
    assert "error" not in kinds
    assert kinds.count("token") == 3
    assert kinds[-1] in ("done", "eval", "attribution")
