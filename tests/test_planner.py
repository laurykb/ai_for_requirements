"""
Tests unitaires du planificateur-exécuteur multi-hop (core.planner).

100 % hors-ligne : LLM, exécuteur d'outil, synthétiseurs et agent de repli sont
injectés (aucun Ollama/Mongo). On vérifie : plan produit et suivi dans l'ordre,
chaînage du contexte entre étapes, re-planification unique sur étape vide,
borne globale de 5 étapes, repli silencieux sur plan invalide, dédoublonnage
des passages, et l'ordre des trames SSE côté API.
"""
import json

from core.planner import PlannerAgent, build_plan, validate_plan


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


def _plan_json(*sous_questions: str) -> str:
    return json.dumps({"etapes": [{"sous_question": sq, "but": f"but de {sq}"}
                                  for sq in sous_questions]}, ensure_ascii=False)


def _passages_result(*texts: str, source="doc.md"):
    return {"ok": True, "mode": "passages", "num_chunks": len(texts),
            "hors_scope": not texts,
            "passages": [{"source": source, "section": "S", "page": i + 1, "text": t}
                         for i, t in enumerate(texts)],
            "chunks": [{"doc": t, "ce_score": 0.7,
                        "meta": {"id": f"{source}|{t[:8]}", "source": source}}
                       for t in texts]}


def _recording_runner(results: list[dict]):
    """Exécuteur d'outil scripté : renvoie `results` dans l'ordre, mémorise les requêtes."""
    seen = {"queries": [], "n": 0}
    def _runner(name, args):
        assert name == "rag_search"
        assert args.get("mode") == "passages"
        seen["queries"].append(args.get("query"))
        out = results[min(seen["n"], len(results) - 1)]
        seen["n"] += 1
        return out
    return _runner, seen


def _synth(tokens=("Réponse ", "ancrée."), citations=None):
    """Synthétiseur streaming injecté ; capture les passages reçus."""
    seen = {}
    def _s(question, gathered):
        seen["gathered"] = gathered
        return iter(tokens), (citations or [{"idx": 1, "source": "doc.md", "page": 1}])
    return _s, seen


# -- validation & retry du plan -------------------------------------------------
def test_validate_plan_rejects_bad_shapes():
    assert validate_plan("pas un objet")[0] is None
    assert validate_plan({"autre": []})[0] is None
    # une seule étape : trop court (le RAG direct suffit) -> erreur explicite
    steps, errors = validate_plan({"etapes": [{"sous_question": "q1"}]})
    assert steps is None and "2" in errors[0]
    # étape sans sous_question -> erreur ciblée
    steps, errors = validate_plan({"etapes": [{"sous_question": "q1"}, {"but": "x"}]})
    assert steps is None and any("étape 2" in e for e in errors)


def test_validate_plan_truncates_to_max():
    obj = {"etapes": [{"sous_question": f"q{i}"} for i in range(8)]}
    steps, errors = validate_plan(obj)
    assert errors == [] and len(steps) == 5   # tronqué, pas rejeté


def test_build_plan_retries_once_with_errors_reinjected():
    llm = ScriptedLLM(["ceci n'est pas du JSON", _plan_json("q1", "q2")])
    steps = build_plan("question", llm)
    assert [s["sous_question"] for s in steps] == ["q1", "q2"]
    # Le retry contient la sortie fautive et le mot 'invalide'.
    assert "invalide" in llm.prompts[1] and "pas du JSON" in llm.prompts[1]


def test_build_plan_gives_up_after_retry():
    llm = ScriptedLLM(["rien", "toujours rien"])
    assert build_plan("question", llm) is None
    assert llm.calls == 2   # exactement un retry


# -- exécution nominale : ordre, chaînage, synthèse ------------------------------
def test_plan_followed_in_order_with_context_chaining():
    # Appels LLM attendus : 1) plan  2) affinage de la sous-question 2 (chaînage).
    llm = ScriptedLLM([_plan_json("q1", "q2"), "q2 raffinée avec alpha"])
    runner, seen = _recording_runner([_passages_result("alpha"),
                                      _passages_result("beta")])
    synth, syn_seen = _synth()
    agent = PlannerAgent(llm=llm, tool_runner=runner, stream_synthesizer=synth)
    events = list(agent.run_stream("question composée"))
    types = [e["type"] for e in events]

    # Ordre : plan, puis étape 1 (start/done), étape 2, tokens, done.
    assert types[0] == "plan"
    assert types[1:5] == ["step_start", "step_done", "step_start", "step_done"]
    assert "answer_token" in types and types[-1] == "done"
    # Étape 1 exécutée telle quelle ; étape 2 AFFINÉE avec le contexte accumulé.
    assert seen["queries"] == ["q1", "q2 raffinée avec alpha"]
    # Le prompt d'affinage contient l'observation de l'étape 1 (chaînage).
    assert "alpha" in llm.prompts[1] and "q2" in llm.prompts[1]
    # Synthèse ancrée sur TOUS les passages accumulés, dans l'ordre.
    assert [g["doc"] for g in syn_seen["gathered"]] == ["alpha", "beta"]
    done = events[-1]["result"]
    assert done["ok"] is True and done["answer"] == "Réponse ancrée."
    assert done["stopped_reason"] == "planned" and done["tool_calls"] == 2
    assert [s["sous_question"] for s in done["plan"]] == ["q1", "q2"]


def test_step_events_carry_index_and_resume():
    llm = ScriptedLLM([_plan_json("q1", "q2"), "q2 bis"])
    runner, _ = _recording_runner([_passages_result("alpha"), _passages_result()])
    synth, _ = _synth()
    events = list(PlannerAgent(llm=llm, tool_runner=runner,
                               stream_synthesizer=synth).run_stream("q"))
    starts = [e for e in events if e["type"] == "step_start"]
    dones = [e for e in events if e["type"] == "step_done"]
    assert [s["index"] for s in starts] == [1, 2]
    assert starts[0]["total"] == 2 and starts[0]["sous_question"] == "q1"
    assert "1 passage(s)" in dones[0]["resume"] and dones[0]["hors_scope"] is False
    assert dones[1]["hors_scope"] is True   # étape 2 vide (la re-planification a échoué
    # car le LLM d'affinage/replan renvoie du texte libre, pas un plan JSON)


# -- adaptation : re-planification unique, bornes --------------------------------
def test_replan_once_on_hors_scope_then_forbidden():
    # Plan de 3 étapes ; étape 1 vide -> re-planification (2 étapes révisées) ;
    # étape r2 vide À NOUVEAU -> plus aucune re-planification (une seule par requête).
    llm = ScriptedLLM([
        _plan_json("q1", "q2", "q3"),          # plan initial
        _plan_json("r1", "r2"),                # re-planification des étapes restantes
        "r1 raffinée",                          # affinage étape r1
        "r2 raffinée",                          # affinage étape r2
    ])
    runner, seen = _recording_runner([
        _passages_result(),                     # q1 : vide -> replan
        _passages_result("gamma"),              # r1 : ok
        _passages_result(),                     # r2 : vide -> PAS de 2e replan
    ])
    synth, _ = _synth()
    events = list(PlannerAgent(llm=llm, tool_runner=runner,
                               stream_synthesizer=synth).run_stream("q"))
    replans = [e for e in events if e["type"] == "replan"]
    assert len(replans) == 1
    assert replans[0]["index"] == 1
    # La liste re-reçue = étapes exécutées + étapes révisées (q2/q3 abandonnées).
    assert [s["sous_question"] for s in replans[0]["steps"]] == ["q1", "r1", "r2"]
    assert len(seen["queries"]) == 3            # q1, r1, r2 - rien de plus
    done = [e for e in events if e["type"] == "done"][-1]["result"]
    assert done["replanned"] is True and done["tool_calls"] == 3


def test_total_steps_bounded_to_five_including_replan():
    # Plan initial 4 étapes ; étape 1 vide -> le replan propose 5 étapes mais le
    # budget restant est de 4 -> 5 étapes exécutées AU TOTAL, pas une de plus.
    llm = ScriptedLLM([
        _plan_json("q1", "q2", "q3", "q4"),
        _plan_json("r1", "r2", "r3", "r4", "r5"),   # trop long : tronqué au budget
        "affinée",                                   # affinages suivants (réutilisé)
    ])
    runner, seen = _recording_runner([_passages_result()] +
                                     [_passages_result("x")] * 10)
    synth, _ = _synth()
    events = list(PlannerAgent(llm=llm, tool_runner=runner,
                               stream_synthesizer=synth).run_stream("q"))
    starts = [e for e in events if e["type"] == "step_start"]
    assert len(starts) == 5                       # borne globale respectée
    assert len(seen["queries"]) == 5
    done = [e for e in events if e["type"] == "done"][-1]["result"]
    assert done["tool_calls"] == 5


# -- repli silencieux -------------------------------------------------------------
class FakeFallback:
    """Agent de repli factice : rejoue le contrat run_stream de ReActAgent."""
    def __init__(self):
        self.called_with = None

    def run_stream(self, question, conversation_history=None):
        self.called_with = (question, conversation_history)
        yield {"type": "thought", "text": "je cherche à l'ancienne"}
        yield {"type": "answer_token", "text": "réponse ReAct"}
        yield {"type": "done", "result": {"ok": True, "answer": "réponse ReAct",
                                          "sources": [], "chunks": [],
                                          "stopped_reason": "final_answer"}}


def test_fallback_when_plan_invalid_after_retry():
    llm = ScriptedLLM(["pas un plan", "toujours pas"])
    fb = FakeFallback()
    events = list(PlannerAgent(llm=llm, tool_runner=lambda n, a: {"ok": False},
                               fallback=fb).run_stream("q", conversation_history=[]))
    types = [e["type"] for e in events]
    assert "plan" not in types                     # repli SILENCIEUX : pas de plan
    assert types[0] == "thought" and types[-1] == "done"
    assert events[-1]["result"]["answer"] == "réponse ReAct"
    assert fb.called_with == ("q", [])             # question et historique transmis


def test_empty_question_is_rejected():
    events = list(PlannerAgent(llm=ScriptedLLM(["x"]),
                               tool_runner=lambda n, a: {}).run_stream("  "))
    assert events[-1]["type"] == "done" and events[-1]["result"]["ok"] is False


# -- dédoublonnage ---------------------------------------------------------------
def test_passages_deduplicated_between_steps():
    llm = ScriptedLLM([_plan_json("q1", "q2"), "q2 raffinée"])
    same = _passages_result("alpha")               # même passage aux deux étapes
    runner, _ = _recording_runner([same, same])
    synth, syn_seen = _synth()
    events = list(PlannerAgent(llm=llm, tool_runner=runner,
                               stream_synthesizer=synth).run_stream("q"))
    # Un seul passage versé à la synthèse, un seul chunk intégral pour l'UI.
    assert [g["doc"] for g in syn_seen["gathered"]] == ["alpha"]
    done = [e for e in events if e["type"] == "done"][-1]["result"]
    assert len(done["chunks"]) == 1
    dones = [e for e in events if e["type"] == "step_done"]
    assert "déjà couverts" in dones[1]["resume"]


# -- run() bloquant (CLI / harnais d'éval) ----------------------------------------
def test_run_assembles_stream_result():
    llm = ScriptedLLM([_plan_json("q1", "q2"), "q2 raffinée"])
    runner, _ = _recording_runner([_passages_result("alpha"),
                                   _passages_result("beta")])
    synth, _ = _synth(tokens=("Ré", "ponse."))
    res = PlannerAgent(llm=llm, tool_runner=runner, stream_synthesizer=synth).run("q")
    assert res["ok"] is True and res["answer"] == "Réponse."
    assert res["tool_calls"] == 2 and res["stopped_reason"] == "planned"


# -- traduction SSE (api.rag._agent_events) ----------------------------------------
def test_agent_events_translates_plan_frames_in_order(monkeypatch):
    """L'API traduit les événements du planificateur en trames SSE dans l'ordre :
    plan -> step_start -> step_done -> replan -> token -> _done, et construit la
    trace markdown persistée (plan + étapes + re-planification)."""
    import core.planner as planner_mod
    from api.rag import _agent_events

    class FakePlanner:
        def __init__(self, **kwargs):
            pass
        def run_stream(self, question, conversation_history=None):
            yield {"type": "plan", "steps": [{"sous_question": "q1", "but": "b1"},
                                             {"sous_question": "q2", "but": "b2"}]}
            yield {"type": "step_start", "index": 1, "total": 2,
                   "sous_question": "q1", "but": "b1"}
            yield {"type": "step_done", "index": 1,
                   "resume": "aucun passage pertinent", "hors_scope": True}
            yield {"type": "replan", "index": 1,
                   "steps": [{"sous_question": "q1", "but": "b1"},
                             {"sous_question": "r1", "but": ""}]}
            yield {"type": "step_start", "index": 2, "total": 2,
                   "sous_question": "r1", "but": ""}
            yield {"type": "step_done", "index": 2, "resume": "2 passage(s) retenus",
                   "hors_scope": False}
            yield {"type": "answer_token", "text": "ok"}
            yield {"type": "done", "result": {"ok": True, "answer": "ok",
                                              "sources": [], "chunks": []}}

    monkeypatch.setattr(planner_mod, "PlannerAgent", FakePlanner)
    frames = list(_agent_events("question", None, []))
    kinds = [f[0]["type"] for f in frames]
    assert kinds == ["plan", "step_start", "step_done", "replan",
                     "step_start", "step_done", "token", "_done"]
    plan_frame = frames[0][0]
    assert plan_frame["steps"][0]["sous_question"] == "q1"
    step_frame = frames[1][0]
    assert step_frame == {"type": "step_start", "index": 1, "total": 2, "text": "q1"}
    replan_frame = frames[3][0]
    assert [s["sous_question"] for s in replan_frame["steps"]] == ["q1", "r1"]
    # Trace markdown persistée : plan, étapes, re-planification.
    trace = frames[-1][1]
    assert any("**Plan**" in t for t in trace)
    assert any("Étape 1/2" in t for t in trace)
    assert any("Re-planification" in t for t in trace)
