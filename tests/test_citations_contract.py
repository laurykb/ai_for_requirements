"""
Tests du CONTRAT marqueur↔passage (citations inline [n]).

La numérotation [1..n] du contexte de génération doit correspondre EXACTEMENT
à la liste de chunks exposée aux appelants (trame `retrieved` du front) — sur
le RAG direct (core.ask) ET sur la synthèse agent (core.agent / core.planner,
passages multi-étapes dédupliqués). 100 % hors-ligne : LLM et retrieval simulés.
"""
import re

import core.ask
import core.llm_answer
from core.llm_answer import (
    build_citation_map,
    build_context,
    fit_chunks_to_context,
    refine_for_generation,
)
from core.planner import PlannerAgent


class FakeLLM:
    """LLM factice : invoke/stream déterministes, aucun réseau."""
    def invoke(self, prompt, stop=None, format=None, **kwargs):
        return "Réponse [1]."

    def stream(self, prompt, stop=None, **kwargs):
        yield "Réponse "
        yield "[1]."


def _chunks_avec_doublons():
    # c1 et son doublon strict (la dédup pré-génération doit n'en garder qu'un).
    c1 = {"doc": "La TOE est certifiée EAL3+ selon les Critères Communs.",
          "meta": {"source": "cible.md", "page_number": 12, "id": "a"}}
    c2 = {"doc": "Le chiffrement des flux repose sur AES-256 en mode GCM.",
          "meta": {"source": "cible.md", "page_number": 33, "id": "b"}}
    c3 = {"doc": "La TOE est certifiée EAL3+ selon les Critères Communs.",
          "meta": {"source": "cible.md", "page_number": 12, "id": "a2"}}
    return [c1, c2, c3]


# ─── Numérotation contexte ↔ citations ↔ chunks affinés ─────────────────────

def test_contexte_et_citations_alignes_sur_la_liste_affinee():
    refined = refine_for_generation(_chunks_avec_doublons())
    context = build_context(refined)
    citations = build_citation_map(refined)
    # Autant de citations que de chunks affinés, indices 1..n dans l'ordre.
    assert [c["idx"] for c in citations] == list(range(1, len(refined) + 1))
    # Le doublon a été retiré AVANT la numérotation.
    assert len(refined) < 3
    # Chaque bloc <<<DOCUMENT i>>> du contexte correspond au chunk i de la liste.
    for i, chunk in enumerate(refined, start=1):
        m = re.search(rf"<<<DOCUMENT {i}>>>\n\[{i}\] Source: (\S+), page (\d+)", context)
        assert m, f"bloc [{i}] absent du contexte"
        assert m.group(1) == chunk["meta"]["source"].rstrip(",")
        assert int(m.group(2)) == chunk["meta"]["page_number"]
        assert citations[i - 1]["source"] == chunk["meta"]["source"]
        assert citations[i - 1]["page"] == chunk["meta"]["page_number"]


def test_budget_contexte_exclut_les_citations_non_transmises():
    chunks = [
        {"doc": "A" * 100, "meta": {"source": "a.md"}},
        {"doc": "B" * 100, "meta": {"source": "b.md"}},
    ]
    fitted = fit_chunks_to_context(chunks, max_chars=180)
    context = build_context(fitted)
    citations = build_citation_map(fitted)

    assert len(fitted) == len(citations) == 1
    assert "a.md" in context
    assert "b.md" not in context
    assert citations[0]["source"] == "a.md"


def test_premier_chunk_trop_long_est_copie_et_marque():
    original = {"doc": "X" * 2_000, "meta": {"source": "long.md"}}
    fitted = fit_chunks_to_context([original], max_chars=300)

    assert len(build_context(fitted)) <= 300
    assert fitted[0]["meta"]["context_truncated"] is True
    assert fitted[0]["doc"] != original["doc"]
    assert "context_truncated" not in original["meta"]


def test_process_query_stream_renvoie_la_liste_numerotee(monkeypatch):
    brut = _chunks_avec_doublons()
    monkeypatch.setattr(core.ask, "_prepare_retrieval",
                        lambda *a, **k: ("q", [dict(c) for c in brut]))
    monkeypatch.setattr(core.llm_answer, "_build_answer_llm", lambda **k: FakeLLM())

    gen, chunks, citations = core.ask.process_query_stream("q", self_rag_enabled=False)
    assert "".join(gen) == "Réponse [1]."
    # Les chunks renvoyés (= trame `retrieved`) sont la liste AFFINÉE...
    assert chunks == refine_for_generation([dict(c) for c in brut])
    # ... et les citations pointent dessus, position à position.
    assert len(citations) == len(chunks)
    for cit, ch in zip(citations, chunks):
        assert cit["source"] == ch["meta"]["source"]
        assert cit["page"] == ch["meta"].get("page_number")


def test_process_query_selection_renvoie_la_liste_numerotee(monkeypatch):
    # Régénération avec sélection : même contrat (sélection affinée renvoyée).
    monkeypatch.setattr(core.llm_answer, "_build_answer_llm", lambda **k: FakeLLM())
    brut = _chunks_avec_doublons()
    rep, chunks, citations = core.ask.process_query("q", selected_chunks=brut)
    assert rep == "Réponse [1]."
    assert chunks == refine_for_generation(_chunks_avec_doublons())
    assert [c["idx"] for c in citations] == list(range(1, len(chunks) + 1))


# ─── Synthèse agent : passages multi-étapes dédupliqués ──────────────────────

def _plan_json(*sqs):
    import json
    return json.dumps({"etapes": [{"sous_question": s, "but": ""} for s in sqs]},
                      ensure_ascii=False)


def _tool_result(*texts, source="doc.md"):
    return {"ok": True, "mode": "passages", "num_chunks": len(texts),
            "hors_scope": not texts,
            "passages": [{"source": source, "section": "S", "page": i + 1,
                          "text": t[:20]}  # texte TRONQUÉ, comme rag_tool
                         for i, t in enumerate(texts)],
            "chunks": [{"doc": t, "ce_score": 0.7,
                        "meta": {"id": f"{source}|{t[:8]}", "source": source,
                                 "page_number": i + 1}}
                       for i, t in enumerate(texts)]}


def test_synthese_agent_chunks_egaux_liste_numerotee():
    """Les chunks du résultat agent = la liste affinée passée à la synthèse
    (passages multi-étapes dédupliqués), pas le registre brut accumulé."""
    plan_llm = FakeLLM()
    plan_llm.invoke = lambda p, stop=None, format=None, **k: _plan_json("sq1", "sq2")

    results = [
        _tool_result("Passage un, texte intégral du chunk.",
                     "Passage deux, texte intégral."),
        # L'étape 2 renvoie un passage DÉJÀ VU (dédupliqué) + un nouveau.
        _tool_result("Passage un, texte intégral du chunk.",
                     "Passage trois, texte intégral."),
    ]
    calls = {"n": 0}

    def runner(name, args):
        out = results[min(calls["n"], 1)]
        calls["n"] += 1
        return out

    seen = {}

    def synth(question, gathered):
        seen["gathered"] = gathered
        used = refine_for_generation(gathered)
        seen["used"] = used
        return iter(["Réponse [1]."]), build_citation_map(used), used

    res = {}
    for ev in PlannerAgent(llm=plan_llm, tool_runner=runner,
                           stream_synthesizer=synth).run_stream("q"):
        if ev.get("type") == "done":
            res = ev["result"]

    assert res["ok"] is True
    # La synthèse a reçu les passages dédupliqués, en version INTÉGRALE
    # (chunks complets alignés par rag_tool, pas le texte tronqué).
    docs = [g["doc"] for g in seen["gathered"]]
    assert docs == ["Passage un, texte intégral du chunk.",
                    "Passage deux, texte intégral.",
                    "Passage trois, texte intégral."]
    # Contrat : les chunks du résultat = la liste numérotée de la synthèse.
    assert res["chunks"] == seen["used"]
    assert [c["idx"] for c in res["sources"]] == list(range(1, len(res["chunks"]) + 1))


def test_synthetiseur_historique_2_tuple_compatible():
    """Un synthétiseur injecté « historique » (2-tuple) reste accepté :
    les chunks du résultat retombent sur le registre accumulé."""
    plan_llm = FakeLLM()
    plan_llm.invoke = lambda p, stop=None, format=None, **k: _plan_json("sq1", "sq2")
    result = _tool_result("Un seul passage.")

    def synth(question, gathered):
        return iter(["Réponse."]), [{"idx": 1, "source": "doc.md", "page": 1}]

    res = {}
    for ev in PlannerAgent(llm=plan_llm, tool_runner=lambda n, a: result,
                           stream_synthesizer=synth).run_stream("q"):
        if ev.get("type") == "done":
            res = ev["result"]
    assert res["ok"] is True
    assert res["chunks"] and res["chunks"][0]["doc"] == "Un seul passage."
