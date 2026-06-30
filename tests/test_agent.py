"""
Tests unitaires de l'agent ReAct.

100 % hors-ligne : le LLM et l'exécuteur d'outils sont injectés (aucun Ollama/Mongo).
On vérifie la boucle Pensée/Action/Observation, le parsing tolérant, l'agrégation des
sources, le garde-fou max_iterations et la résistance aux formats invalides.
"""
from core.agent import ReActAgent, _parse_action_input, _extract_json_object


class ScriptedLLM:
    """LLM factice : renvoie des complétions pré-écrites, une par appel `invoke`."""
    def __init__(self, scripted: list[str]):
        self.scripted = list(scripted)
        self.calls = 0
        self.prompts: list[str] = []

    def invoke(self, prompt, stop=None, **kwargs):
        self.prompts.append(prompt)
        out = self.scripted[min(self.calls, len(self.scripted) - 1)]
        self.calls += 1
        return out


def _ok_tool(answer="L'EAL est EAL2+ [1].", sources=None, hors_scope=False):
    src = sources if sources is not None else [{"idx": 1, "source": "cible.md", "page": 12}]
    def _runner(name, args):
        return {"ok": True, "answer": answer, "sources": src,
                "num_chunks": len(src), "hors_scope": hors_scope, "latency_s": 0.0}
    return _runner


# -- parsing -----------------------------------------------------------------
def test_parse_action_input_json():
    assert _parse_action_input('{"query": "EAL de la TOE"}') == {"query": "EAL de la TOE"}


def test_parse_action_input_plaintext_fallback():
    # Petit modèle qui oublie le JSON : la ligne brute devient la query.
    assert _parse_action_input('"EAL de la TOE"')["query"] == "EAL de la TOE"


def test_extract_json_object_handles_nesting_and_garbage():
    assert _extract_json_object('blabla {"query": "x", "k": {"a": 1}} fin')["query"] == "x"
    assert _extract_json_object("pas de json ici") is None


# -- boucle nominale -----------------------------------------------------------
def test_single_tool_call_then_final_answer():
    llm = ScriptedLLM([
        'Pensée: je dois chercher.\nAction: rag_search\nAction Input: {"query": "EAL de la TOE"}',
        "Pensée: j'ai la réponse.\nRéponse finale: La TOE est évaluée EAL2+ [1].",
    ])
    agent = ReActAgent(llm=llm, tool_runner=_ok_tool(), max_iterations=4)
    res = agent.run("Quel est le niveau EAL ?")

    assert res["ok"] is True
    assert res["stopped_reason"] == "final_answer"
    assert res["tool_calls"] == 1
    assert "EAL2+" in res["answer"]
    assert res["sources"] == [{"idx": 1, "source": "cible.md", "page": 12}]
    # Le second prompt doit contenir l'Observation réinjectée (mémoire de la boucle).
    assert "Observation:" in llm.prompts[1]


def test_final_answer_without_tool_call():
    llm = ScriptedLLM(["Réponse finale: Bonjour, je suis un agent documentaire."])
    res = ReActAgent(llm=llm, tool_runner=_ok_tool()).run("Bonjour")
    assert res["tool_calls"] == 0
    assert res["stopped_reason"] == "final_answer"


def test_sources_are_deduplicated_across_calls():
    s1 = [{"idx": 1, "source": "a.md", "page": 1}]
    s2 = [{"idx": 1, "source": "a.md", "page": 1}, {"idx": 2, "source": "b.md", "page": 3}]
    runner_outputs = [
        {"ok": True, "answer": "r1 [1]", "sources": s1, "num_chunks": 1, "hors_scope": False},
        {"ok": True, "answer": "r2 [2]", "sources": s2, "num_chunks": 2, "hors_scope": False},
    ]
    calls = {"n": 0}
    def runner(name, args):
        out = runner_outputs[min(calls["n"], 1)]
        calls["n"] += 1
        return out

    llm = ScriptedLLM([
        'Action: rag_search\nAction Input: {"query": "q1"}',
        'Action: rag_search\nAction Input: {"query": "q2"}',
        "Réponse finale: synthèse [1][2].",
    ])
    res = ReActAgent(llm=llm, tool_runner=runner, max_iterations=5).run("question composée")
    assert res["tool_calls"] == 2
    # 3 sources renvoyées au total mais (a.md,1) dédupliquée -> 2 uniques.
    assert len(res["sources"]) == 2


# -- robustesse ----------------------------------------------------------------
def test_max_iterations_guardrail_falls_back_to_best_tool_answer():
    # LLM qui n'aboutit jamais (boucle infinie sans 'Réponse finale').
    llm = ScriptedLLM(['Action: rag_search\nAction Input: {"query": "x"}'])
    res = ReActAgent(llm=llm, tool_runner=_ok_tool(answer="meilleure trouvaille [1]"),
                     max_iterations=3).run("question")
    assert res["stopped_reason"] == "max_iterations"
    # 3 tours mais requête identique -> la dédup n'exécute l'outil qu'une fois.
    assert res["tool_calls"] == 1
    assert res["answer"] == "meilleure trouvaille [1]"  # repli sur la meilleure obs


def test_repeated_identical_call_breaks_early():
    # Le modèle relance EXACTEMENT la même recherche = il tourne en rond. Le code
    # ne ré-exécute pas l'outil ET SORT de la boucle (anti-loop) au lieu de gâcher
    # des itérations de raisonnement jusqu'à max_iterations.
    runs = {"n": 0}
    def counting_runner(name, args):
        runs["n"] += 1
        return {"ok": True, "answer": "réponse [1]",
                "sources": [{"idx": 1, "source": "a.md", "page": 1}],
                "num_chunks": 1, "hors_scope": False}
    llm = ScriptedLLM([
        'Action: rag_search\nAction Input: {"query": "même question"}',
        'Action: rag_search\nAction Input: {"query": "même question"}',  # doublon -> break
        "Réponse finale: jamais atteinte.",
    ])
    res = ReActAgent(llm=llm, tool_runner=counting_runner, max_iterations=5,
                     retrieve_only=False).run("q")
    assert runs["n"] == 1            # outil exécuté UNE seule fois
    assert res["tool_calls"] == 1
    assert any(s.get("cached") for s in res["steps"])
    assert len(llm.prompts) == 2     # sorti après la répétition, pas de 3e appel raisonnement
    assert "réponse [1]" in res["answer"]  # repli sur la réponse de l'outil


def test_invalid_format_then_recovers():
    llm = ScriptedLLM([
        "Je réfléchis tout haut sans suivre le format.",     # ni Action ni Réponse finale
        "Réponse finale: finalement voici la réponse.",
    ])
    res = ReActAgent(llm=llm, tool_runner=_ok_tool(), max_iterations=4).run("question")
    assert res["stopped_reason"] == "final_answer"
    assert "voici la réponse" in res["answer"]
    # Le coup de pouce 'format invalide' a bien été réinjecté avant le 2e appel.
    assert "format invalide" in llm.prompts[1]


def test_tool_error_is_observed_not_crashed():
    def failing_runner(name, args):
        return {"ok": False, "error": "Outil inconnu : 'foo'."}
    llm = ScriptedLLM([
        'Action: foo\nAction Input: {"query": "x"}',
        "Réponse finale: l'outil a échoué, je n'ai pas l'information.",
    ])
    res = ReActAgent(llm=llm, tool_runner=failing_runner, max_iterations=3).run("q")
    assert res["ok"] is True
    assert res["tool_calls"] == 1
    assert "échoué" in res["answer"]


def test_empty_question_is_rejected():
    res = ReActAgent(llm=ScriptedLLM(["x"]), tool_runner=_ok_tool()).run("  ")
    assert res["ok"] is False


def test_retrieve_only_gathers_passages_then_synthesizes():
    # Mode retrieve-only : l'outil renvoie des PASSAGES (pas de génération) ;
    # l'agent les cumule, les numérote GLOBALEMENT [1..], puis une SYNTHÈSE finale
    # unique produit la réponse ancrée. Synthétiseur injecté -> 100 % hors-ligne.
    calls = {"n": 0}
    def passages_runner(name, args):
        assert args.get("mode") == "passages"          # l'agent force le mode passages
        assert args.get("max_passages") == 6
        calls["n"] += 1
        if calls["n"] == 1:
            return {"ok": True, "mode": "passages", "num_chunks": 2, "hors_scope": False,
                    "passages": [{"source": "a.md", "section": "S1", "page": 1, "text": "alpha"},
                                 {"source": "a.md", "section": "S2", "page": 2, "text": "beta"}]}
        return {"ok": True, "mode": "passages", "num_chunks": 1, "hors_scope": False,
                "passages": [{"source": "b.md", "section": None, "page": 3, "text": "gamma"}]}
    seen = {}
    def fake_synth(question, gathered):
        seen["texts"] = [g["doc"] for g in gathered]
        return ("Réponse synthétisée [1][2][3].",
                [{"idx": i + 1, "source": g["meta"]["source"], "page": g["meta"]["page_number"]}
                 for i, g in enumerate(gathered)])
    llm = ScriptedLLM([
        'Action: rag_search\nAction Input: {"query": "q1"}',
        'Action: rag_search\nAction Input: {"query": "q2"}',
        "Réponse finale: peu importe, on synthétise.",
    ])
    res = ReActAgent(llm=llm, tool_runner=passages_runner, synthesizer=fake_synth,
                     max_iterations=5).run("question composée")

    assert res["tool_calls"] == 2
    # Tous les passages cumulés (dans l'ordre) sont passés à la synthèse.
    assert seen["texts"] == ["alpha", "beta", "gamma"]
    assert "synthétisée" in res["answer"]
    # Les sources finales = citations de la synthèse, numérotées 1..3.
    assert [s["idx"] for s in res["sources"]] == [1, 2, 3]
    assert res["sources"][2]["source"] == "b.md"
    # L'observation passages (avec [1], [2]) a bien été réinjectée avant la 2e étape.
    assert "PASSAGES" in llm.prompts[1] and "[1]" in llm.prompts[1]


def test_retrieve_only_exposes_integral_chunks_for_ui():
    # En mode retrieve-only, l'outil renvoie AUSSI des chunks INTÉGRAUX (doc complet +
    # métadonnées enrichies). L'agent les cumule et les déduplique dans result["chunks"]
    # pour le panneau « Passages récupérés » de l'UI, SANS jamais les réinjecter au LLM
    # (qui ne voit que les `passages` tronqués).
    c_alpha = {"doc": "A" * 1500, "ce_score": 0.7,
               "meta": {"id": "x1", "source": "a.md", "keywords_str": "k1", "questions_str": "q1 ?"}}
    c_beta = {"doc": "B" * 1500, "ce_score": 0.6, "meta": {"id": "x2", "source": "a.md"}}
    calls = {"n": 0}
    def passages_runner(name, args):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"ok": True, "mode": "passages", "num_chunks": 2, "hors_scope": False,
                    "passages": [{"source": "a.md", "section": "S1", "page": 1, "text": "alpha"},
                                 {"source": "a.md", "section": "S2", "page": 2, "text": "beta"}],
                    "chunks": [c_alpha, c_beta]}
        # 2e recherche : renvoie de nouveau c_alpha (même id) -> doit être dédupliqué.
        return {"ok": True, "mode": "passages", "num_chunks": 1, "hors_scope": False,
                "passages": [{"source": "a.md", "section": "S1", "page": 1, "text": "alpha"}],
                "chunks": [c_alpha]}
    llm = ScriptedLLM([
        'Action: rag_search\nAction Input: {"query": "q1"}',
        'Action: rag_search\nAction Input: {"query": "q2"}',
        "Réponse finale: synthèse [1].",
    ])
    res = ReActAgent(llm=llm, tool_runner=passages_runner,
                     synthesizer=lambda q, g: ("r [1]", []), max_iterations=5).run("q composée")

    chunks = res["chunks"]
    assert [c["meta"]["id"] for c in chunks] == ["x1", "x2"]   # dédup par id (x1 vu 2x)
    assert chunks[0]["doc"] == "A" * 1500                       # contenu INTÉGRAL préservé
    assert chunks[0]["meta"]["keywords_str"] == "k1"            # métadonnées enrichies conservées


def test_answer_mode_result_has_empty_chunks():
    # En mode réponse (pas retrieve-only), aucun passage intégral cumulé : clé présente, vide.
    res = ReActAgent(llm=ScriptedLLM(["Réponse finale: ok."]), tool_runner=_ok_tool()).run("q")
    assert res["chunks"] == []


def test_run_stream_yields_events_and_streams_answer():
    # run_stream émet thought/action/observation au fil de l'eau + answer_token,
    # et finit par 'done' avec le résultat structuré. Synthétiseur streaming injecté.
    def passages_runner(name, args):
        return {"ok": True, "mode": "passages", "num_chunks": 1, "hors_scope": False,
                "passages": [{"source": "a.md", "section": "S", "page": 1, "text": "alpha"}]}
    def fake_stream_synth(question, gathered):
        assert gathered and gathered[0]["doc"] == "alpha"
        return iter(["Ré", "ponse ", "ancrée"]), [{"idx": 1, "source": "a.md", "page": 1}]
    llm = ScriptedLLM([
        'Pensée: je cherche.\nAction: rag_search\nAction Input: {"query": "q"}',
        "Réponse finale: stop.",
    ])
    agent = ReActAgent(llm=llm, tool_runner=passages_runner,
                       stream_synthesizer=fake_stream_synth, max_iterations=4)
    events = list(agent.run_stream("question"))
    types = [e["type"] for e in events]

    assert "thought" in types and "action" in types and "observation" in types
    assert "answer_token" in types
    assert types[-1] == "done"
    tokens = "".join(e["text"] for e in events if e["type"] == "answer_token")
    assert tokens == "Réponse ancrée"
    done = events[-1]["result"]
    assert done["ok"] is True and done["answer"] == "Réponse ancrée"
    assert done["sources"] == [{"idx": 1, "source": "a.md", "page": 1}]
    assert "chunks" in done   # le résultat streamé expose aussi les passages intégraux (UI)


def test_run_stream_empty_question():
    events = list(ReActAgent(llm=ScriptedLLM(["x"]), tool_runner=_ok_tool()).run_stream("  "))
    assert events[-1]["type"] == "done" and events[-1]["result"]["ok"] is False


def test_retrieve_only_can_be_disabled():
    # retrieve_only=False -> l'agent n'injecte pas le mode passages (relais d'une réponse).
    def answer_runner(name, args):
        assert "mode" not in args
        return {"ok": True, "answer": "réponse complète [1]",
                "sources": [{"idx": 1, "source": "a.md", "page": 1}], "num_chunks": 1, "hors_scope": False}
    llm = ScriptedLLM([
        'Action: rag_search\nAction Input: {"query": "q"}',
        "Réponse finale: ok [1].",
    ])
    res = ReActAgent(llm=llm, tool_runner=answer_runner, max_iterations=4, retrieve_only=False).run("q")
    assert res["tool_calls"] == 1
    assert res["sources"] == [{"idx": 1, "source": "a.md", "page": 1}]
