"""Couche Q&A : RAG direct + agent ReAct, streaming SSE.

Contient le cœur agentique de l'application : routage auto RAG/Agent,
boîte de verre (pensées/actions/observations), vérification LLM-as-judge
et régénération sur une sélection de passages. Les imports lourds (core.*)
restent paresseux, à l'intérieur des fonctions : ils conditionnent le temps
de démarrage de l'API.
"""
from __future__ import annotations

import json
import re

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.common import _sse, _trim_chunk

# ─── Garde-fous de génération (défense contre les sorties dégénérées) ─────────
# Un LLM local peut partir en boucle (répétition infinie) : on surveille le
# flux et on COUPE proprement plutôt que de laisser l'utilisateur subir.
MAX_ANSWER_CHARS = 24_000       # ~6k tokens : au-delà, réponse anormale
_LOOP_WINDOW = 240              # motif recherché : fin du texte répétée
_LOOP_MIN_REPEATS = 3           # ... au moins 3 fois d'affilée


def _degenerate(text: str) -> str | None:
    """Détecte une génération dégénérée. Retourne la raison, ou None.

    Appelée à chaque token. La boucle est détectée par la plus petite
    période de la fin du texte (préfixe-fonction de KMP sur les
    `_LOOP_WINDOW × _LOOP_MIN_REPEATS` derniers caractères) : si un motif
    de <= `_LOOP_WINDOW` caractères y est répété >= `_LOOP_MIN_REPEATS`
    fois, le modèle boucle — quelle que soit la longueur du motif
    (un simple `endswith(tail * 3)` ratait tout motif dont la période ne
    divise pas la fenêtre).
    """
    if len(text) > MAX_ANSWER_CHARS:
        return "réponse anormalement longue"
    n = _LOOP_WINDOW * _LOOP_MIN_REPEATS
    if len(text) < n:
        return None
    window = text[-n:]
    fail = [0] * n  # préfixe-fonction : plus long bord de window[:i+1]
    k = 0
    for i in range(1, n):
        while k and window[i] != window[k]:
            k = fail[k - 1]
        if window[i] == window[k]:
            k += 1
        fail[i] = k
    period = n - fail[-1]
    if period <= _LOOP_WINDOW:
        return "boucle de répétition détectée"
    return None

router = APIRouter()


def _task_progress(payload: dict) -> dict | None:
    event_type = payload.get("type")
    if event_type == "route":
        return {"phase": "routing", "label": "Mode " + str(payload.get("mode", "")).upper(), "status": "completed"}
    if event_type == "strategy":
        return {"phase": "strategy", "label": "Stratégie de traitement déterminée", "status": "completed"}
    if event_type == "stage":
        stage = str(payload.get("stage", "processing"))
        labels = {"retrieve": "Recherche documentaire", "generate": "Génération de la réponse", "prefilter": "Sélection du corpus", "map_complete": "Analyse documentaire terminée", "reduce": "Synthèse du corpus", "coverage": "Contrôle de couverture", "repair": "Réparation de la couverture", "expand": "Expansion ciblée des preuves", "expand_complete": "Expansion ciblée terminée"}
        return {"phase": stage, "label": labels.get(stage, stage), "status": "running", "detail": {key: value for key, value in payload.items() if key not in {"type", "stage"}}}
    if event_type == "map":
        document = str(payload.get("document") or "document")
        items = int(payload.get("n_items") or 0)
        detail = f"{items} élément(s) retenu(s)"
        if payload.get("chunks_scanned") is not None:
            detail += f" · {payload.get('chunks_scanned')} chunks examinés"
        return {"phase": "corpus_map", "label": document, "status": "completed" if items else "partial", "current": payload.get("index"), "total": payload.get("total"), "detail": detail}
    if event_type == "retrieved":
        return {"phase": "retrieve", "label": str(len(payload.get("chunks") or [])) + " passage(s) retenu(s)", "status": "completed"}
    if event_type == "plan":
        return {"phase": "plan", "label": "Plan de " + str(len(payload.get("steps") or [])) + " étape(s)", "status": "completed", "current": 0, "total": len(payload.get("steps") or [])}
    if event_type == "step_start":
        return {"phase": "agent_step", "label": str(payload.get("text") or "Étape agent"), "status": "running", "current": payload.get("index"), "total": payload.get("total")}
    if event_type == "step_done":
        return {"phase": "agent_step", "label": str(payload.get("text") or "Étape agent terminée"), "status": "partial" if payload.get("hors_scope") else "completed", "current": payload.get("index")}
    if event_type == "replan":
        return {"phase": "replan", "label": "Plan révisé", "status": "completed"}
    if event_type == "done":
        found = payload.get("found", True)
        return {"phase": "complete", "label": "Exécution terminée" if found else "Exécution terminée sans résultat", "status": "completed" if found else "partial"}
    if event_type == "error":
        return {"phase": "error", "label": str(payload.get("message") or "Échec du traitement"), "status": "failed"}
    return None


def _unload_ollama_model(model: str) -> None:
    """Compatibilité API ; le cycle de vie est centralisé dans le routeur."""
    from core.model_router import unload_model
    unload_model(model)


def _deep_synthesis_events(question, model: str, **kwargs):
    """Garantit le déchargement du modèle profond, succès, erreur ou annulation."""
    try:
        yield from _synthesize_corpus(question, **kwargs)
    finally:
        _unload_ollama_model(model)


def _synthesize_corpus(question, **kwargs):
    """Indirection paresseuse et patchable (tests) vers le pipeline de synthèse
    corpus (Chantier 1b) : import différé pour ne pas alourdir le démarrage."""
    from core.synthesize_corpus import synthesize_corpus
    return synthesize_corpus(question, **kwargs)


class AskBody(BaseModel):
    """Requête du chat : question + périmètre (un document, ou null = tous)
    + historique + options par requête (None = défaut .env) + mode de
    traitement (auto : le routeur décide ; rag/agent : forcé) + session
    persistée (None = en créer une)."""
    question: str
    source: str | None = None
    history: list[dict] = []
    parent_child: bool | None = None
    self_rag: bool | None = None
    system_prompt: str | None = None
    mode: str = "auto"                # auto | rag | agent | synth
    session_id: str | None = None


def _persist_exchange(session_id: str | None, source: str | None, question: str,
                      answer: str, citations: list, reasoning: str | None,
                      chunks: list, evidence_dossier: dict | None = None,
                      answer_validation: dict | None = None,
                      analysis_artifact: dict | None = None) -> None:
    """Enregistre l'échange dans la session Mongo (comme le Streamlit)."""
    if not session_id:
        return
    try:
        from core.chat_sessions import add_message, update_session_source
        update_session_source(session_id, source)
        add_message(session_id, "user", question)
        add_message(session_id, "assistant", answer, citations=citations or [],
                    reasoning=reasoning, chunks=[_trim_chunk(c) for c in (chunks or [])],
                    evidence_dossier=evidence_dossier, answer_validation=answer_validation,
                    analysis_artifact=analysis_artifact)
    except Exception:
        pass  # la persistance est un bonus : ne jamais casser la réponse


def _run_attribution(question: str, answer_txt: str, chunks: list,
                     session_id: str | None) -> dict:
    """Audit déterministe des marqueurs de preuve, sans appel LLM automatique.

    Il mesure la couverture citationnelle observable : chaque phrase factuelle
    portant au moins un marqueur [n] valide est sourcée. La vérification
    sémantique plus coûteuse reste disponible explicitement via /api/verify.
    """
    del question
    max_passage = len(chunks or [])
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", answer_txt)
                 if part.strip() and not part.lstrip().startswith(("#", "-", "*"))]
    affirmations = []
    for sentence in sentences:
        passages = sorted({int(n) for n in re.findall(r"\[(\d+)\]", sentence)
                           if 1 <= int(n) <= max_passage})
        affirmations.append({"texte": sentence, "passages": passages,
                             "statut": "sourcee" if passages else "non_sourcee"})
    n_sourcees = sum(item["statut"] == "sourcee" for item in affirmations)
    attribution = {"ok": True, "affirmations": affirmations,
                   "n_affirmations": len(affirmations), "n_sourcees": n_sourcees,
                   "n_completees": 0,
                   "n_non_sourcees": len(affirmations) - n_sourcees, "error": None}
    if session_id:
        try:
            from core.chat_sessions import set_last_assistant_attribution
            set_last_assistant_attribution(session_id, attribution)
        except Exception:
            pass
    return attribution


def _run_quality(question: str, answer_txt: str, chunks: list,
                 session_id: str | None) -> dict:
    """Évaluation post-hoc sans référence : 3 axes observables, persistés."""
    from core.evaluation import verify_answer
    quality = verify_answer(question, answer_txt, chunks or []) or {}
    if session_id:
        try:
            from core.chat_sessions import set_last_assistant_eval
            set_last_assistant_eval(session_id, quality)
        except Exception:
            pass
    return quality


def _baseline_system_prompt(source: str | None) -> str | None:
    """Prompt système dédié quand le périmètre est la baseline LynX
    (registre `baseline.system`, éditable dans Prompts métier) ; None sinon
    (le défaut `generate.system` s'applique)."""
    from core.reserved_sources import LYNX_BASELINE_SOURCE
    if source != LYNX_BASELINE_SOURCE:
        return None
    from core.prompt_registry import get_prompt
    from api.prompts import baseline_system_default
    return get_prompt("baseline.system", baseline_system_default())


def scope_arguments(source: str | None, arguments: dict) -> dict:
    """Injecte le périmètre documentaire dans les arguments d'un outil agent.

    Ajoute `document=source` UNIQUEMENT si `source` est non vide ET qu'aucun
    `document` n'est déjà spécifié. Périmètre « Tous » (source falsy = None/"")
    -> arguments inchangés : l'agent balaie tout le corpus et n'est jamais
    « collé » au dernier document sélectionné."""
    if source and isinstance(arguments, dict) and not arguments.get("document"):
        return {**arguments, "document": source}
    return arguments


def _agent_events(question: str, source: str | None, history: list[dict]):
    """Mode Agent : traduit les événements du planificateur-exécuteur multi-hop en
    trames SSE (plan/étapes en direct = boîte de verre, puis réponse streamée).
    Si la planification échoue, l'agent retombe sur le ReAct historique dont les
    pensées/actions/observations sont relayées à l'identique.

    Périmètre baseline LynX : l'agent reçoit EN PLUS l'outil `baseline_tree`
    (interrogation déterministe de l'arbre de traçabilité — dérivations,
    chaînes, orphelines, filtres) : les questions structurelles obtiennent des
    réponses exactes au lieu de dépendre du retrieval."""
    from core.planner import PlannerAgent
    from core.reserved_sources import LYNX_BASELINE_SOURCE
    from tools.rag_tool import run_tool as _rt, tool_spec as _rag_spec

    if source == LYNX_BASELINE_SOURCE:
        # Baseline : agent ReAct OUTILLÉ en direct (rag_search + baseline_tree).
        # Le planificateur multi-hop décompose en sous-recherches documentaires
        # et n'appellerait jamais l'outil structurel — c'est le choix d'outil
        # par étape (cœur du ReAct) qui fait la valeur ici.
        from core.agent import ReActAgent
        from tools import baseline_tree_tool

        def _baseline_runner(name, arguments):
            if name == baseline_tree_tool.TOOL_NAME:
                return baseline_tree_tool.run_tool(arguments)
            return _rt(name, scope_arguments(source, arguments))

        # Budget d'étapes dédié : les appels arbre sont déterministes et quasi
        # gratuits (aucun LLM), contrairement aux recherches documentaires pour
        # lesquelles AGENT_MAX_ITERATIONS=4 est calibré.
        import os as _os
        engine = ReActAgent(tool_runner=_baseline_runner,
                            tool_specs=[_rag_spec(), baseline_tree_tool.tool_spec()],
                            max_iterations=int(_os.environ.get("LYNX_AGENT_MAX_ITERATIONS", "8")))
    else:
        def _scoped_runner(name, arguments):
            # L'agent respecte le périmètre documentaire choisi dans le chat.
            return _rt(name, scope_arguments(source, arguments))

        engine = PlannerAgent(tool_runner=_scoped_runner)

    trace: list[str] = []
    result: dict = {}
    for ev in engine.run_stream(question, conversation_history=history):
        kind = ev.get("type")
        if kind == "plan":
            steps = ev.get("steps") or []
            lines = "\n".join(f"{i}. {s.get('sous_question', '')}"
                              for i, s in enumerate(steps, 1))
            trace.append(f"**Plan** ({len(steps)} étapes)\n{lines}")
            yield {"type": "plan", "steps": steps}, trace, result
        elif kind == "step_start":
            trace.append(f"→ **Étape {ev['index']}/{ev['total']}** — {ev['sous_question']}")
            yield {"type": "step_start", "index": ev["index"], "total": ev["total"],
                   "text": ev["sous_question"]}, trace, result
        elif kind == "step_done":
            trace.append(f"_{ev['resume']}_")
            yield {"type": "step_done", "index": ev["index"], "text": ev["resume"],
                   "hors_scope": bool(ev.get("hors_scope"))}, trace, result
        elif kind == "replan":
            trace.append("**Re-planification** — les étapes restantes ont été révisées.")
            yield {"type": "replan", "index": ev["index"],
                   "steps": ev.get("steps") or []}, trace, result
        elif kind == "thought":
            trace.append(f"**Pensée** — {ev['text']}")
            yield {"type": "thought", "text": ev["text"]}, trace, result
        elif kind == "action":
            inp = ev.get("input") or {}
            if ev.get("tool") == "baseline_tree":
                label = str(inp.get("operation", ""))
                if inp.get("req_id"):
                    label += f" {inp['req_id']}"
                trace.append(f"→ **Arbre** `{label}`")
            else:
                label = inp.get("query", "")
                trace.append(f"→ **Recherche** `{label}`")
            yield {"type": "action", "text": label}, trace, result
        elif kind == "observation":
            trace.append(f"_{ev['text']}_")
            yield {"type": "observation", "text": ev["text"]}, trace, result
        elif kind == "answer_token":
            yield {"type": "token", "text": ev["text"]}, trace, result
        elif kind == "done":
            result.update(ev.get("result", {}))
            yield {"type": "_done", "text": ""}, trace, result


@router.post("/api/ask")
def ask(body: AskBody) -> StreamingResponse:
    """Q&A en SSE — boîte de verre : routage (`route`), étapes du pipeline
    (`stage`, `retrieved`), plan de l'agent en direct (`plan`/`step_start`/
    `step_done`/`replan`), pensées du repli ReAct (`thought`/`action`/
    `observation`), `token`, `sources`, `done`, puis attribution par
    affirmation (`attribution`, post-hoc) et métriques déterministes de tâche ; les erreurs une trame
    `error` (le front ne pend jamais). Persiste l'échange dans la session
    (`session` renvoie l'id créé), attribution comprise."""
    def _gen():
        try:
            # Session persistée : créée au premier message si besoin.
            session_id = body.session_id
            if session_id is None:
                try:
                    from core.chat_sessions import create_session
                    session_id = create_session(source_filter=body.source)
                    yield _sse({"type": "session", "id": session_id})
                except Exception:
                    session_id = None  # Mongo down : conversation éphémère

            from core.router import select_query_strategy
            strategy = select_query_strategy(
                body.question, requested_mode=body.mode,
                parent_child=body.parent_child, self_rag=body.self_rag,
            )
            effective = strategy["mode"]
            reason = strategy["rationale"][0]
            yield _sse({"type": "route", "mode": effective, "reason": reason})
            yield _sse({"type": "strategy", **strategy})

            if effective == "synth":
                answer_txt = ""
                documents: list = []
                chunks: list = []
                synth_kwargs = {}
                if body.source:
                    # La synthèse corpus respecte le périmètre documentaire du
                    # chat (un document épinglé, ou la baseline LynX) au lieu
                    # de balayer tout l'index.
                    scoped = body.source
                    synth_kwargs["prefilter"] = lambda _aspect: [scoped]
                if body.mode == "deep":
                    from core.model_router import build_llm
                    from env_config import DEEP_RESEARCH_MODEL
                    synth_kwargs["llm"] = build_llm(
                        "synthesize", model=DEEP_RESEARCH_MODEL,
                        think=True, num_predict=4096,
                    ).invoke
                events = (_deep_synthesis_events(body.question, DEEP_RESEARCH_MODEL, **synth_kwargs)
                          if body.mode == "deep" else
                          _synthesize_corpus(body.question, **synth_kwargs))
                for ev in events:
                    t = ev.get("type")
                    if t == "stage":
                        yield _sse(ev)
                    elif t == "map":
                        yield _sse(ev)
                    elif t == "token":
                        answer_txt += ev["text"]
                        yield _sse({"type": "token", "text": ev["text"]})
                    elif t == "done":
                        documents = ev["result"].get("documents", [])
                        chunks = ev["result"].get("chunks", [])
                        citations = [{"source": d} for d in documents]
                        document_coverage = ev["result"].get("document_coverage")
                        axis_coverage = ev["result"].get("coverage")
                        dossier = ev["result"].get("evidence_dossier")
                        validation = ev["result"].get("answer_validation")
                        artifact = ev["result"].get("analysis_artifact")
                        if artifact:
                            yield _sse({"type": "analysis_artifact", "artifact": artifact})
                        if dossier:
                            yield _sse({"type": "answer_contract", "dossier": dossier})
                        if validation:
                            yield _sse({"type": "answer_validation", "validation": validation})
                        if document_coverage:
                            yield _sse({"type": "coverage", "documents": document_coverage,
                                        "axes": axis_coverage})
                        if chunks:
                            yield _sse({"type": "retrieved",
                                        "chunks": [_trim_chunk(c) for c in chunks]})
                        yield _sse({"type": "sources", "citations": citations})
                        _persist_exchange(session_id, body.source, body.question,
                                          answer_txt, citations, None, chunks, dossier, validation, artifact)
                        yield _sse({"type": "done", "found": bool(answer_txt)})
                return

            if effective == "agent":
                reasoning_parts: list[str] = []
                result: dict = {}
                answer_txt = ""
                for frame, trace, res in _agent_events(body.question, body.source,
                                                       body.history or []):
                    reasoning_parts = trace
                    result = res
                    if frame["type"] == "_done":
                        break
                    if frame["type"] == "token":
                        answer_txt += frame["text"]
                    yield _sse(frame)
                if not result.get("ok", False):
                    yield _sse({"type": "error",
                                "message": result.get("error", "Échec de l'agent.")})
                    return
                answer_txt = result.get("answer", answer_txt)
                citations = result.get("sources", [])
                chunks = result.get("chunks", [])
                from core.answer_contract import build_evidence_dossier, validate_answer
                dossier = build_evidence_dossier(body.question, chunks, "agent", plan=result.get("plan"))
                validation = validate_answer(answer_txt, dossier, enforce_structure=False)
                yield _sse({"type": "retrieved",
                            "chunks": [_trim_chunk(c) for c in chunks]})
                yield _sse({"type": "answer_contract", "dossier": dossier})
                yield _sse({"type": "answer_validation", "validation": validation})
                yield _sse({"type": "sources", "citations": citations})
                _persist_exchange(session_id, body.source, body.question, answer_txt,
                                  citations, "\n\n".join(reasoning_parts), chunks, dossier, validation)
                yield _sse({"type": "done", "found": True})
                # Attribution par affirmation sur la SYNTHÈSE de l'agent (les
                # chunks sont la liste numérotée de la synthèse — même contrat
                # que le RAG direct). Après `done` : la réponse est déjà là.
                try:
                    if answer_txt and chunks:
                        yield _sse({"type": "attribution",
                                    **_run_attribution(body.question, answer_txt,
                                                       chunks, session_id)})
                except Exception:
                    pass  # l'attribution est un bonus : ne jamais bloquer
                return

            # ─ RAG direct ─
            from core.ask import process_query_stream
            yield _sse({"type": "stage", "stage": "retrieve"})
            token_gen, chunks, citations = process_query_stream(
                body.question,
                source_filter=body.source,
                conversation_history=body.history or [],
                parent_child_on=strategy["retrieval"]["parent_child"],
                self_rag_enabled=strategy["retrieval"]["self_rag"],
                system_prompt=body.system_prompt or _baseline_system_prompt(body.source),
            )
            yield _sse({"type": "retrieved",
                        "chunks": [_trim_chunk(c) for c in (chunks or [])]})
            from core.answer_contract import build_evidence_dossier, validate_answer
            dossier = build_evidence_dossier(body.question, chunks or [], "rag")
            yield _sse({"type": "answer_contract", "dossier": dossier})

            if token_gen is None:
                yield _sse({"type": "done", "found": False})
                return

            yield _sse({"type": "stage", "stage": "generate"})
            answer_txt = ""
            aborted_reason = None
            for token in token_gen:
                answer_txt += token
                yield _sse({"type": "token", "text": token})
                aborted_reason = _degenerate(answer_txt)
                if aborted_reason:
                    token_gen.close()  # coupe la génération jusqu'à Ollama
                    yield _sse({"type": "error",
                                "message": f"Génération interrompue automatiquement : "
                                           f"{aborted_reason}. Réponse partielle conservée."})
                    break
            validation = validate_answer(answer_txt, dossier, enforce_structure=False)
            yield _sse({"type": "answer_validation", "validation": validation})
            yield _sse({"type": "sources", "citations": citations or []})
            # Persistance AVANT `done` : le front rafraîchit la liste des
            # conversations sur `done` (le titre auto doit déjà être posé).
            _persist_exchange(session_id, body.source, body.question, answer_txt,
                              citations or [], None, chunks or [], dossier, validation)
            yield _sse({"type": "done", "found": bool(chunks)})

            # Attribution par affirmation (post-hoc, APRÈS `done` : la réponse
            # est déjà affichée, la passe n'ajoute que de la transparence).
            attribution = None
            try:
                if answer_txt and chunks:
                    attribution = _run_attribution(body.question, answer_txt,
                                                   chunks, session_id)
                    yield _sse({"type": "attribution", **attribution})
            except Exception:
                pass  # l'attribution est un bonus : ne jamais bloquer

        except Exception as e:  # Ollama/Mongo coupé, timeout… -> trame lisible
            yield _sse({"type": "error",
                        "message": f"{type(e).__name__}: {str(e)[:200]}"})

    def gen():
        from utils import task_metrics
        corpus_capable = body.mode in {"auto", "synth", "deep"}
        budget = ({"max_llm_calls": 500, "max_wall_s": 3600,
                   "max_total_tokens": 2000000} if corpus_capable else
                  {"max_llm_calls": 30, "max_wall_s": 600,
                   "max_total_tokens": 250000})
        task = task_metrics.begin("chat", budget=budget, question=body.question[:300], requested_mode=body.mode, source=body.source)
        task_id = task["task_id"]
        status = "completed"
        outcome = {"completion": "technical_success"}
        steps = tool_calls = replans = 0
        yield _sse({"type": "task", "task_id": task_id, "status": "running"})
        inner = _gen()
        try:
            while True:
                token = task_metrics.bind(task_id)
                try:
                    frame = next(inner)
                except StopIteration:
                    break
                finally:
                    task_metrics.unbind(token)
                try:
                    payload = json.loads(frame.removeprefix("data: ").strip())
                    event_type = payload.get("type")
                    progress = _task_progress(payload)
                    if progress:
                        yield _sse({"type": "task_progress", **progress})
                    if event_type == "route":
                        outcome["route"] = payload.get("mode")
                    elif event_type == "plan":
                        steps = len(payload.get("steps") or [])
                    elif event_type in {"action", "step_start"}:
                        tool_calls += 1
                    elif event_type == "replan":
                        replans += 1
                    elif event_type == "done":
                        outcome["found"] = payload.get("found", True)
                        if payload.get("found") is False:
                            status = "partial"; outcome["completion"] = "technical_partial"
                    elif event_type == "error":
                        error = payload.get("message", "")
                        status = "budget_exceeded" if "TaskBudgetExceeded" in error else "failed"
                        outcome = {"completion": status, "error": error}
                except Exception:
                    pass
                yield frame
        except GeneratorExit:
            status = "stopped"; outcome = {"completion": "stopped"}
            raise
        finally:
            task_metrics.set_trajectory(task_id, tool_calls=tool_calls, steps=steps, replans=replans)
            metrics = task_metrics.finish(task_id, status, **outcome)
            if status != "stopped":
                yield _sse({"type": "task_metrics", "metrics": metrics})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store"})


class RegenerateBody(BaseModel):
    """Régénération avec une SÉLECTION de passages (cochés par l'utilisateur)."""
    question: str
    chunks: list[dict]
    system_prompt: str | None = None
    session_id: str | None = None


@router.post("/api/regenerate")
def regenerate(body: RegenerateBody) -> dict:
    from core.ask import process_query
    # `used` = la sélection APRÈS affinage pré-génération : c'est la liste
    # numérotée [1..n] du contexte (contrat marqueur↔passage) — c'est ELLE
    # qu'on persiste et qu'on renvoie au front, pas la sélection brute.
    rep, used, citations = process_query(body.question, selected_chunks=body.chunks,
                                         system_prompt=body.system_prompt or None)
    if body.session_id:
        try:
            from core.chat_sessions import replace_last_assistant_message
            replace_last_assistant_message(body.session_id, rep or "",
                                           citations or [], chunks=used or body.chunks)
        except Exception:
            pass
    evidence = used or body.chunks or []
    return {"answer": rep or "", "citations": citations or [],
            "chunks": [_trim_chunk(c) for c in evidence]}


class VerifyBody(BaseModel):
    question: str
    answer: str
    chunks: list[dict]
    task_id: str | None = None


@router.post("/api/verify")
def verify(body: VerifyBody) -> dict:
    """Vérification LLM-as-judge de la dernière réponse (à la demande) :
    fidélité aux sources, pertinence réponse/contexte + points à vérifier."""
    from core.evaluation import verify_answer
    result = verify_answer(body.question, body.answer, body.chunks) or {}
    if body.task_id:
        from utils.task_metrics import set_quality
        set_quality(body.task_id, result)
    return result
