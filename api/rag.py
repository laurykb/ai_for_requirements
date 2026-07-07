"""Couche Q&A : RAG direct + agent ReAct, streaming SSE.

Contient le cœur agentique de l'application : routage auto RAG/Agent,
boîte de verre (pensées/actions/observations), vérification LLM-as-judge
et régénération sur une sélection de passages. Les imports lourds (core.*)
restent paresseux, à l'intérieur des fonctions : ils conditionnent le temps
de démarrage de l'API.
"""
from __future__ import annotations

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
    mode: str = "auto"                # auto | rag | agent
    session_id: str | None = None


def _persist_exchange(session_id: str | None, source: str | None, question: str,
                      answer: str, citations: list, reasoning: str | None,
                      chunks: list) -> None:
    """Enregistre l'échange dans la session Mongo (comme le Streamlit)."""
    if not session_id:
        return
    try:
        from core.chat_sessions import add_message, update_session_source
        update_session_source(session_id, source)
        add_message(session_id, "user", question)
        add_message(session_id, "assistant", answer, citations=citations or [],
                    reasoning=reasoning, chunks=[_trim_chunk(c) for c in (chunks or [])])
    except Exception:
        pass  # la persistance est un bonus : ne jamais casser la réponse


def _run_attribution(question: str, answer_txt: str, chunks: list,
                     session_id: str | None) -> dict:
    """Passe post-hoc d'attribution (APRÈS `done`, réponse déjà affichée) :
    calcule l'attribution par affirmation sur les passages numérotés, la
    persiste avec le message (rechargement de conversation) et retourne le
    résultat. Échec/timeout -> {ok: False, error} : jamais bloquant."""
    from core.attribution import attribute_answer
    attribution = attribute_answer(question, answer_txt, chunks or [])
    if session_id and attribution.get("ok"):
        try:
            from core.chat_sessions import set_last_assistant_attribution
            set_last_assistant_attribution(session_id, attribution)
        except Exception:
            pass  # la persistance est un bonus
    return attribution


def _agent_events(question: str, source: str | None, history: list[dict]):
    """Mode Agent : traduit les événements du planificateur-exécuteur multi-hop en
    trames SSE (plan/étapes en direct = boîte de verre, puis réponse streamée).
    Si la planification échoue, l'agent retombe sur le ReAct historique dont les
    pensées/actions/observations sont relayées à l'identique."""
    from core.planner import PlannerAgent
    from tools.rag_tool import run_tool as _rt

    def _scoped_runner(name, arguments):
        # L'agent respecte le périmètre documentaire choisi dans le chat.
        if source and isinstance(arguments, dict) and not arguments.get("document"):
            arguments = {**arguments, "document": source}
        return _rt(name, arguments)

    trace: list[str] = []
    result: dict = {}
    for ev in PlannerAgent(tool_runner=_scoped_runner).run_stream(
            question, conversation_history=history):
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
            q = ev["input"].get("query", "")
            trace.append(f"→ **Recherche** `{q}`")
            yield {"type": "action", "text": q}, trace, result
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
    affirmation (`attribution`, post-hoc) et vérification automatique
    (`eval`, enrichie des compteurs d'attribution) ; les erreurs une trame
    `error` (le front ne pend jamais). Persiste l'échange dans la session
    (`session` renvoie l'id créé), attribution comprise."""
    def gen():
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

            # Routage : Auto -> le routeur (sans LLM) décide ; sinon forcé.
            if body.mode in ("rag", "agent"):
                effective, reason = body.mode, f"mode {body.mode.upper()} forcé"
            else:
                from core.router import route_query
                d = route_query(body.question)
                effective, reason = d["mode"], d["reason"]
            yield _sse({"type": "route", "mode": effective, "reason": reason})

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
                yield _sse({"type": "retrieved",
                            "chunks": [_trim_chunk(c) for c in chunks]})
                yield _sse({"type": "sources", "citations": citations})
                _persist_exchange(session_id, body.source, body.question, answer_txt,
                                  citations, "\n\n".join(reasoning_parts), chunks)
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
                parent_child_on=body.parent_child,
                self_rag_enabled=body.self_rag,
                system_prompt=body.system_prompt or None,
            )
            yield _sse({"type": "retrieved",
                        "chunks": [_trim_chunk(c) for c in (chunks or [])]})

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
            yield _sse({"type": "sources", "citations": citations or []})
            # Persistance AVANT `done` : le front rafraîchit la liste des
            # conversations sur `done` (le titre auto doit déjà être posé).
            _persist_exchange(session_id, body.source, body.question, answer_txt,
                              citations or [], None, chunks or [])
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

            # Vérification ciblée (Auto + question à enjeu). Pas de double
            # éval si le Self-RAG a DÉJÀ vérifié — flag EFFECTIF (.env par
            # défaut quand le front envoie null).
            try:
                from core.router import should_verify
                from env_config import SELF_RAG_ENABLED
                self_rag_on = (body.self_rag if body.self_rag is not None
                               else SELF_RAG_ENABLED)
                if (body.mode == "auto" and citations and not self_rag_on
                        and should_verify(body.question)["verify"]):
                    from core.evaluation import verify_answer
                    frame = {"type": "eval",
                             **(verify_answer(body.question, answer_txt,
                                              chunks or []) or {})}
                    if attribution and attribution.get("ok"):
                        # Compteurs d'attribution dans le bloc de vérification.
                        for k in ("n_affirmations", "n_sourcees", "n_non_sourcees"):
                            frame[k] = attribution.get(k)
                    yield _sse(frame)
            except Exception:
                pass  # la vérif est un bonus : ne jamais bloquer la réponse
        except Exception as e:  # Ollama/Mongo coupé, timeout… -> trame lisible
            yield _sse({"type": "error",
                        "message": f"{type(e).__name__}: {str(e)[:200]}"})

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
    return {"answer": rep or "", "citations": citations or [],
            "chunks": [_trim_chunk(c) for c in (used or body.chunks or [])]}


class VerifyBody(BaseModel):
    question: str
    answer: str
    chunks: list[dict]


@router.post("/api/verify")
def verify(body: VerifyBody) -> dict:
    """Vérification LLM-as-judge de la dernière réponse (à la demande) :
    fidélité aux sources, pertinence réponse/contexte + points à vérifier."""
    from core.evaluation import verify_answer
    return verify_answer(body.question, body.answer, body.chunks) or {}
