"""
Tests unitaires de l'attribution par affirmation (core.attribution).

100 % hors-ligne : le LLM est injecté (aucun Ollama/Mongo). On vérifie :
validation stricte du JSON (statuts, bornes des passages, cohérence),
retry unique avec erreurs réinjectées, échec/timeout non bloquants (la
réponse n'est JAMAIS modifiée), compteurs, juge de précision d'attribution,
persistance en session, et la trame SSE `attribution` de bout en bout.
"""
import json
import time

import pytest

from core.attribution import (
    attribute_answer, judge_attribution_support, validate_attribution,
)


class ScriptedLLM:
    """LLM factice : renvoie des complétions pré-écrites, une par appel `invoke`."""
    def __init__(self, scripted: list[str]):
        self.scripted = list(scripted)
        self.calls = 0
        self.prompts: list[str] = []

    def invoke(self, prompt, stop=None, format=None, **kwargs):
        self.prompts.append(prompt)
        out = self.scripted[min(self.calls, len(self.scripted) - 1)]
        self.calls += 1
        return out


CHUNKS = [
    {"doc": "La TOE Mistral est certifiée EAL3+ selon les Critères Communs.",
     "meta": {"source": "cible.md", "page_number": 12}},
    {"doc": "Le chiffrement des flux repose sur AES-256 en mode GCM.",
     "meta": {"source": "cible.md", "page_number": 33}},
]

ANSWER = "La TOE est certifiée EAL3+ [1]. Les flux sont chiffrés en AES-256 [2]."


def _payload(*affirmations) -> str:
    return json.dumps({"affirmations": list(affirmations)}, ensure_ascii=False)


# ─── Validation ───────────────────────────────────────────────────────────────

def test_validate_rejette_formes_invalides():
    assert validate_attribution("pas un objet", 2)[0] is None
    assert validate_attribution({"autre": []}, 2)[0] is None
    # texte vide -> erreur ciblée
    aff, errs = validate_attribution(
        {"affirmations": [{"texte": " ", "passages": [1], "statut": "sourcee"}]}, 2)
    assert aff is None and any("texte" in e for e in errs)
    # statut inconnu
    aff, errs = validate_attribution(
        {"affirmations": [{"texte": "x", "passages": [1], "statut": "inconnue"}]}, 2)
    assert aff is None and any("statut" in e for e in errs)
    # passage hors bornes
    aff, errs = validate_attribution(
        {"affirmations": [{"texte": "x", "passages": [7], "statut": "sourcee"}]}, 2)
    assert aff is None and any("passage" in e for e in errs)
    # incohérence : sourcée sans passage
    aff, errs = validate_attribution(
        {"affirmations": [{"texte": "x", "passages": [], "statut": "sourcee"}]}, 2)
    assert aff is None and any("sans aucun passage" in e for e in errs)


def test_validate_normalise_passages_et_statuts():
    aff, errs = validate_attribution({"affirmations": [
        {"texte": "a", "passages": [2, 1, 2], "statut": "sourcee"},
        {"texte": "b", "passages": [1], "statut": "non_sourcee"},  # forcé à []
        {"texte": "c", "statut": "non_sourcee"},                   # passages absents
    ]}, 2)
    assert errs == []
    assert aff[0]["passages"] == [1, 2]      # dédupliqués + triés
    assert aff[1]["passages"] == []          # non_sourcee => pas de passages
    assert aff[2]["passages"] == []


def test_validate_liste_vide_est_valide():
    # « Je ne sais pas » : aucune affirmation factuelle, c'est un résultat sain.
    aff, errs = validate_attribution({"affirmations": []}, 3)
    assert aff == [] and errs == []


# ─── attribute_answer : succès, retry, échecs ────────────────────────────────

def test_attribution_valide_et_compteurs():
    llm = ScriptedLLM([_payload(
        {"texte": "La TOE est certifiée EAL3+", "passages": [1], "statut": "sourcee"},
        {"texte": "Les flux sont chiffrés en AES-256", "passages": [2], "statut": "completee"},
        {"texte": "La TOE est conforme RGPD", "passages": [], "statut": "non_sourcee"},
    )])
    res = attribute_answer("q", ANSWER, CHUNKS, llm=llm)
    assert res["ok"] is True and res["error"] is None
    assert res["n_affirmations"] == 3
    assert res["n_sourcees"] == 1 and res["n_completees"] == 1
    assert res["n_non_sourcees"] == 1
    # Le prompt contient bien les passages numérotés dans l'ordre des chunks.
    assert "[1] cible.md p.12" in llm.prompts[0]
    assert "[2] cible.md p.33" in llm.prompts[0]


def test_retry_unique_avec_erreurs_reinjectees():
    llm = ScriptedLLM([
        _payload({"texte": "x", "passages": [9], "statut": "sourcee"}),  # invalide
        _payload({"texte": "x", "passages": [1], "statut": "sourcee"}),  # corrigé
    ])
    res = attribute_answer("q", ANSWER, CHUNKS, llm=llm)
    assert res["ok"] is True and llm.calls == 2
    # Les erreurs de la 1re sortie sont réinjectées dans le prompt du retry.
    assert "invalide" in llm.prompts[1] and "passage" in llm.prompts[1]


def test_double_echec_non_bloquant_reponse_intacte():
    answer = str(ANSWER)
    chunks = [dict(c) for c in CHUNKS]
    llm = ScriptedLLM(["pas du json", "toujours pas"])
    res = attribute_answer("q", answer, chunks, llm=llm)
    assert res["ok"] is False and "invalide" in res["error"]
    assert res["affirmations"] == [] and res["n_affirmations"] == 0
    assert llm.calls == 2  # un retry, pas plus
    assert answer == ANSWER  # la réponse n'est jamais modifiée


def test_exception_llm_non_bloquante():
    class BoomLLM:
        def invoke(self, *a, **k):
            raise RuntimeError("ollama coupé")
    res = attribute_answer("q", ANSWER, CHUNKS, llm=BoomLLM())
    assert res["ok"] is False and "ollama coupé" in res["error"]


def test_timeout_borne():
    class SlowLLM:
        def invoke(self, *a, **k):
            time.sleep(0.3)
            return _payload({"texte": "x", "passages": [1], "statut": "sourcee"})
    t0 = time.perf_counter()
    res = attribute_answer("q", ANSWER, CHUNKS, llm=SlowLLM(), timeout_s=0.05)
    assert res["ok"] is False and "délai" in res["error"]
    assert time.perf_counter() - t0 < 0.25  # on n'attend pas la fin de l'appel


def test_entrees_vides_sans_objet():
    assert attribute_answer("q", "", CHUNKS)["ok"] is False
    assert attribute_answer("q", ANSWER, [])["ok"] is False


# ─── Juge de précision d'attribution (harnais d'éval) ────────────────────────

def test_judge_attribution_support():
    llm = ScriptedLLM(['{"verdicts": [true, false]}'])
    affirmations = [
        {"texte": "certifiée EAL3+", "passages": [1], "statut": "sourcee"},
        {"texte": "chiffré AES-256", "passages": [2], "statut": "completee"},
        {"texte": "non sourcée", "passages": [], "statut": "non_sourcee"},  # ignorée
    ]
    assert judge_attribution_support(affirmations, CHUNKS, llm) == 0.5
    # Les deux couples (affirmation, passage cité) figurent dans le prompt.
    assert "COUPLE 1" in llm.prompts[0] and "COUPLE 2" in llm.prompts[0]


def test_judge_attribution_sans_couple():
    llm = ScriptedLLM(['{"verdicts": [true]}'])
    assert judge_attribution_support(
        [{"texte": "x", "passages": [], "statut": "non_sourcee"}], CHUNKS, llm) is None
    assert llm.calls == 0  # aucun appel inutile


# ─── Persistance en session (Mongo simulé) ───────────────────────────────────

class _FakeCol:
    """Collection Mongo minimale : find_one + update_one avec clés pointées."""
    def __init__(self, doc):
        self.doc = doc

    def find_one(self, query, projection=None):
        return self.doc if query.get("session_id") == self.doc["session_id"] else None

    def update_one(self, query, update):
        for key, value in update.get("$set", {}).items():
            target = self.doc
            parts = key.split(".")
            for p in parts[:-1]:
                target = target[int(p)] if p.isdigit() else target[p]
            last = parts[-1]
            if last.isdigit():
                target[int(last)] = value
            else:
                target[last] = value


def test_persistance_attribution_dernier_message(monkeypatch):
    import core.chat_sessions as cs
    doc = {"session_id": "s1", "messages": [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "r1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "r2"},
    ]}
    monkeypatch.setattr(cs, "_col", lambda: _FakeCol(doc))
    attribution = {"ok": True, "affirmations": [], "n_affirmations": 0,
                   "n_sourcees": 0, "n_completees": 0, "n_non_sourcees": 0,
                   "error": None}
    cs.set_last_assistant_attribution("s1", attribution)
    assert doc["messages"][3]["attribution"] == attribution   # DERNIER assistant
    assert "attribution" not in doc["messages"][1]


# ─── Trame SSE `attribution` de bout en bout (API simulée) ───────────────────

def _sse_events(body: str) -> list[dict]:
    return [json.loads(line[6:]) for line in body.splitlines()
            if line.startswith("data: ")]


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app
    import api.rag as rag

    monkeypatch.setattr(rag, "_persist_exchange", lambda *a, **k: None)
    return TestClient(app)


def test_trame_attribution_apres_done(client, monkeypatch):
    import core.ask
    import core.attribution

    chunk = {"doc": "extrait", "meta": {"source": "doc.md"}}
    monkeypatch.setattr(
        core.ask, "process_query_stream",
        lambda *a, **k: (iter(["Réponse [1]."]), [chunk],
                         [{"idx": 1, "source": "doc.md"}]))
    fixed = {"ok": True, "affirmations": [
        {"texte": "Réponse", "passages": [1], "statut": "sourcee"}],
        "n_affirmations": 1, "n_sourcees": 1, "n_completees": 0,
        "n_non_sourcees": 0, "error": None}
    monkeypatch.setattr(core.attribution, "attribute_answer",
                        lambda *a, **k: fixed)
    persisted = {}
    import core.chat_sessions
    monkeypatch.setattr(core.chat_sessions, "set_last_assistant_attribution",
                        lambda sid, att: persisted.update({sid: att}))

    r = client.post("/api/ask", json={"question": "q", "mode": "rag",
                                      "session_id": "s-att"})
    events = _sse_events(r.text)
    kinds = [e["type"] for e in events]
    # L'attribution arrive APRÈS sources et done (passe post-hoc).
    assert kinds.index("sources") < kinds.index("done") < kinds.index("attribution")
    att = next(e for e in events if e["type"] == "attribution")
    assert att["ok"] is True and att["n_affirmations"] == 1
    assert persisted["s-att"] == fixed  # persistée avec le message


def test_trame_attribution_echec_non_bloquant(client, monkeypatch):
    import core.ask
    import core.attribution

    chunk = {"doc": "extrait", "meta": {"source": "doc.md"}}
    monkeypatch.setattr(
        core.ask, "process_query_stream",
        lambda *a, **k: (iter(["Réponse."]), [chunk], [{"idx": 1}]))
    monkeypatch.setattr(
        core.attribution, "attribute_answer",
        lambda *a, **k: {"ok": False, "affirmations": [], "n_affirmations": 0,
                         "n_sourcees": 0, "n_completees": 0, "n_non_sourcees": 0,
                         "error": "délai dépassé (0.1 s)"})

    r = client.post("/api/ask", json={"question": "q", "mode": "rag",
                                      "session_id": "s-att2"})
    events = _sse_events(r.text)
    kinds = [e["type"] for e in events]
    assert "done" in kinds  # le flux s'est terminé normalement
    att = next(e for e in events if e["type"] == "attribution")
    assert att["ok"] is False and "délai" in att["error"]
    # Les tokens de la réponse ont bien été servis avant (réponse intacte).
    assert kinds.index("token") < kinds.index("attribution")
