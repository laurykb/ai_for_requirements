"""
Planificateur-exécuteur multi-hop au-dessus de l'outil rag_search.

Le mode agent du chat ne se contente plus d'une boucle ReAct pas-à-pas : pour une
question composée, un PLAN de 2 à 5 sous-questions est d'abord produit (sortie JSON
contrainte, validée, un retry avec les erreurs réinjectées), puis exécuté étape par
étape :

    Plan         : {etapes: [{sous_question, but}]} - visible dans l'UI dès émission
    Exécution    : chaque étape lance rag_search (retrieve-only) sur sa sous-question,
                   affinée avec le contexte accumulé des étapes précédentes
    Adaptation   : une étape hors périmètre déclenche UNE re-planification maximum,
                   qui ne révise que les étapes restantes (5 étapes au total, borné)
    Synthèse     : UNE génération finale ancrée sur tous les passages accumulés
                   (dédoublonnés entre étapes), citations au format habituel

Si la planification échoue (JSON invalide après retry), on RETOMBE silencieusement
sur l'agent ReAct historique (core.agent) : jamais moins bien qu'avant.

Comme l'agent, tout est injectable (LLM, exécuteur d'outil, synthétiseurs, agent de
repli) -> testable 100 % hors-ligne. CLI : python -m core.planner "question multi-hop"
"""
from __future__ import annotations

import json
import time

from utils.logging_config import get_logger
from utils.tracing import start_trace, span
from utils.task_metrics import operation
from env_config import PLANNER_MODEL

from core.agent import (
    ReActAgent, _accumulate_chunks, _extract_json_object, _gather_passage,
    _passages_observation, _synthesize, _synthesize_stream, _unpack_synthesis,
)

logger = get_logger("rag.planner")

# Bornes du plan : 2 à 5 étapes au plan initial, 5 étapes EXÉCUTÉES au total
# (re-planification comprise) - même esprit que AGENT_MAX_ITERATIONS.
MIN_PLAN_STEPS = 2
MAX_TOTAL_STEPS = 5

_NO_RESULT_ANSWER = (
    "Je n'ai pas trouvé d'information sur ce sujet dans vos documents. "
    "Essayez de reformuler la question, ou choisissez d'autres documents à interroger."
)


def _build_planner_llm():
    """LLM de planification (rôle 'planner' - voir core.model_router)."""
    from core.model_router import build_llm
    return build_llm("planner")


def _llm_json_call(llm, prompt: str) -> str:
    """Appel LLM en sortie contrainte JSON (format Ollama), tolérant aux mocks
    qui n'acceptent pas le paramètre `format`."""
    try:
        out = llm.invoke(prompt, format="json")
    except TypeError:
        out = llm.invoke(prompt)
    return out if isinstance(out, str) else str(out)


def _parse_plan_json(raw: str) -> dict | None:
    """Texte LLM -> objet JSON (json strict, sinon premier objet équilibré)."""
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, TypeError):
        return _extract_json_object(raw or "")


def validate_plan(obj, min_steps: int = MIN_PLAN_STEPS,
                  max_steps: int = MAX_TOTAL_STEPS) -> tuple[list[dict] | None, list[str]]:
    """Valide un plan {etapes: [{sous_question, but}]} -> (étapes, erreurs).

    Étapes normalisées (chaînes nettoyées, `but` optionnel). Un plan trop long est
    TRONQUÉ à max_steps (pas une erreur : on garde le début) ; trop court, vide ou
    mal formé -> erreurs explicites, réinjectées au LLM pour le retry.
    """
    errors: list[str] = []
    if not isinstance(obj, dict):
        return None, ["la sortie n'est pas un objet JSON"]
    etapes = obj.get("etapes")
    if not isinstance(etapes, list):
        return None, ["clé 'etapes' manquante ou n'est pas une liste"]
    steps: list[dict] = []
    for i, e in enumerate(etapes, 1):
        if not isinstance(e, dict):
            errors.append(f"étape {i} : n'est pas un objet")
            continue
        sq = str(e.get("sous_question") or "").strip()
        if not sq:
            errors.append(f"étape {i} : 'sous_question' manquante ou vide")
            continue
        steps.append({"sous_question": sq, "but": str(e.get("but") or "").strip()})
    if errors:
        return None, errors
    if len(steps) < min_steps:
        return None, [f"au moins {min_steps} étapes attendues (reçu : {len(steps)})"]
    return steps[:max_steps], []


_PLANNER_INSTRUCTIONS = (
    "Décompose la demande complexe en recherches documentaires autonomes, "
    "complémentaires et orientées vers une synthèse finale exhaustive."
)


def _plan_prompt(question: str, history_block: str = "") -> str:
    from core.prompt_registry import get_prompt
    instructions = get_prompt("planner.plan", _PLANNER_INSTRUCTIONS)
    return (
        instructions + "\n\n" +
        "Tu prépares un plan de recherche documentaire pour répondre à une question "
        "complexe sur des documents techniques (cibles de sécurité ANSSI / Critères "
        "Communs). Découpe la question en 2 à 5 sous-questions AUTONOMES : chacune doit "
        "pouvoir être cherchée telle quelle dans la base documentaire.\n\n"
        "Réponds UNIQUEMENT avec un objet JSON de la forme :\n"
        '{"etapes": [{"sous_question": "...", "but": "..."}]}\n'
        "- sous_question : la recherche à lancer (précise, autosuffisante, sans pronom "
        "renvoyant à une autre étape)\n"
        "- but : ce que l'étape doit apporter à la réponse finale (une ligne)\n"
        "N'ajoute AUCUN texte hors du JSON.\n\n"
        f"{history_block}Question : {question}\n"
    )


def build_plan(question: str, llm, history_block: str = "") -> list[dict] | None:
    """Planification avec validation + UN retry (erreurs réinjectées).

    Retourne les étapes validées, ou None si le LLM n'a pas produit de plan
    exploitable après retry (l'appelant retombe alors sur l'agent ReAct)."""
    prompt = _plan_prompt(question, history_block)
    raw = ""
    for attempt in (1, 2):
        try:
            raw = _llm_json_call(llm, prompt)
        except Exception as e:
            logger.warning("[planner] Appel LLM de planification impossible : %s", e)
            return None
        steps, errors = validate_plan(_parse_plan_json(raw))
        if steps:
            return steps
        if attempt == 1:
            # Retry unique : la sortie fautive et les erreurs sont réinjectées.
            prompt = (
                f"{_plan_prompt(question, history_block)}\n"
                f"Ta précédente réponse était invalide :\n{(raw or '')[:800]}\n"
                f"Erreurs : {'; '.join(errors)}.\n"
                "Corrige et renvoie UNIQUEMENT le JSON demandé."
            )
    logger.info("[planner] Plan invalide après retry -> repli sur l'agent ReAct.")
    return None


def _replan_prompt(question: str, notes: list[str], failed_step: dict, budget: int) -> str:
    context = "\n\n".join(notes) if notes else "(aucun passage trouvé pour l'instant)"
    return (
        "Tu révises un plan de recherche documentaire en cours d'exécution.\n"
        f"Question globale : {question}\n\n"
        "Étapes déjà exécutées et leurs observations :\n"
        f"{context}\n\n"
        f"La recherche « {failed_step.get('sous_question', '')} » n'a rien donné "
        "(hors du périmètre documentaire). Propose de NOUVELLES étapes de recherche "
        f"pour ce qui manque encore à la réponse - au plus {budget}, reformulées "
        "autrement (synonymes, identifiants exacts vus dans les observations).\n"
        "Réponds UNIQUEMENT avec un objet JSON de la forme :\n"
        '{"etapes": [{"sous_question": "...", "but": "..."}]}\n'
    )


class PlannerAgent:
    """Planificateur-exécuteur multi-hop (plan visible, adaptatif, borné) avec
    repli silencieux sur l'agent ReAct. Mêmes injections que ReActAgent."""

    def __init__(self, llm=None, tool_runner=None, max_steps: int = MAX_TOTAL_STEPS,
                 synthesizer=None, stream_synthesizer=None, fallback=None,
                 tool_specs: list[dict] | None = None):
        if tool_runner is None:
            from tools.rag_tool import run_tool
            tool_runner = run_tool
        self._llm = llm
        self.tool_runner = tool_runner
        self.tool_specs = tool_specs  # None = spec rag_search seule (défaut ReAct)
        self.max_steps = max(1, int(max_steps))
        self.synthesizer = synthesizer or _synthesize
        self.stream_synthesizer = stream_synthesizer or _synthesize_stream
        self._fallback = fallback  # agent de repli injectable (tests hors-ligne)

    @property
    def llm(self):
        # Construction paresseuse : on ne touche Ollama que si on planifie vraiment.
        if self._llm is None:
            self._llm = _build_planner_llm()
        return self._llm

    @property
    def fallback(self):
        """Agent ReAct de repli (comportement historique), construit à la demande."""
        if self._fallback is None:
            self._fallback = ReActAgent(
                tool_runner=self.tool_runner,
                tool_specs=self.tool_specs,
                synthesizer=self.synthesizer if self.synthesizer is not _synthesize else None,
                stream_synthesizer=(self.stream_synthesizer
                                    if self.stream_synthesizer is not _synthesize_stream else None),
            )
        return self._fallback

    # -- exécution ---------------------------------------------------------------
    def run(self, question: str) -> dict:
        """Variante bloquante : consomme run_stream et renvoie le résultat structuré
        (CLI, harnais d'éval). La réponse est assemblée depuis les tokens streamés."""
        result: dict = {}
        parts: list[str] = []
        for ev in self.run_stream(question):
            if ev.get("type") == "answer_token":
                parts.append(ev.get("text", ""))
            elif ev.get("type") == "done":
                result = ev.get("result", {})
        if result.get("ok") and not result.get("answer"):
            result["answer"] = "".join(parts).strip()
        return result

    def run_stream(self, question: str, conversation_history: list[dict] | None = None):
        """Générateur d'événements pour l'UI :

            plan | step_start | step_done | replan | answer_token | done
            (+ thought/action/observation si repli sur l'agent ReAct)

        `done` porte le résultat structuré { ok, answer, plan, steps, iterations,
        tool_calls, sources, chunks, stopped_reason, latency_s }."""
        if not question or not str(question).strip():
            yield {"type": "done", "result": {"ok": False, "error": "La question est requise."}}
            return
        question = str(question).strip()

        history_block = ""
        if conversation_history:
            lines = [f"{'Utilisateur' if m.get('role') == 'user' else 'Assistant'}: "
                     f"{str(m.get('content', ''))[:800]}"
                     for m in conversation_history[-6:]]
            history_block = ("Contexte de conversation (échanges précédents) :\n"
                             + "\n".join(lines) + "\n\n")

        t0 = time.perf_counter()
        with start_trace("rag.planner", question=question, model=PLANNER_MODEL) as tr:
            with span("plan"), operation("plan"):
                plan = build_plan(question, self.llm, history_block)
            if plan is None:
                # REPLI silencieux : comportement agent historique, événements inclus.
                tr.set("fallback", True)
                yield from self.fallback.run_stream(question,
                                                    conversation_history=conversation_history)
                return

            tr.set("plan_steps", len(plan))
            yield {"type": "plan", "steps": [dict(s) for s in plan]}

            queue = [dict(s) for s in plan]
            done_steps: list[dict] = []       # étapes exécutées (résultat structuré)
            notes: list[str] = []             # observations résumées -> contexte chaîné
            sources: list[dict] = []          # registre global de citations
            gathered: list[dict] = []         # passages (texte) -> synthèse finale
            gathered_chunks: list[dict] = []  # chunks intégraux -> UI
            seen_chunk_keys: set = set()
            seen_passages: set = set()        # dédoublonnage des passages entre étapes
            tool_calls = 0
            replanned = False
            executed = 0

            while queue and executed < self.max_steps:
                step = queue.pop(0)
                executed += 1
                total = executed + len(queue)
                yield {"type": "step_start", "index": executed, "total": total,
                       "sous_question": step["sous_question"], "but": step.get("but", "")}

                # Chaînage : la sous-question est affinée avec le contexte accumulé.
                query = step["sous_question"]
                if notes:
                    query = self._refine_query(question, step, notes) or query

                tool_calls += 1
                with span("plan_step", index=executed) as sp:
                    try:
                        result = self.tool_runner(
                            "rag_search",
                            {"query": query, "mode": "passages", "max_passages": 6})
                    except Exception as exc:
                        # Retrieval indisponible en cours de plan : on dégrade sans
                        # tuer le flux SSE (la trame `done` finale doit être émise).
                        logger.exception("[planner] étape %d en échec", executed)
                        result = {"ok": False, "error": str(exc)[:200], "passages": [], "chunks": []}
                    if sp is not None:
                        sp.set("ok", bool(result.get("ok")))
                        sp.set("hors_scope", bool(result.get("hors_scope")))

                # Dédoublonnage entre étapes : un passage déjà vu n'est pas re-versé
                # au contexte de synthèse (les étapes proches se recouvrent souvent).
                # Chaque passage retenu est versé avec son chunk INTÉGRAL (aligné
                # index à index par tools.rag_tool) -> synthèse ancrée sur le même
                # contenu que celui montré à l'utilisateur.
                res_chunks = result.get("chunks") or []
                new_passages = []
                for j, p in enumerate(result.get("passages") or []):
                    key = (p.get("source"), p.get("page"), (p.get("text") or "").strip()[:160])
                    if key not in seen_passages:
                        seen_passages.add(key)
                        new_passages.append(p)
                        _gather_passage(gathered, p,
                                        res_chunks[j] if j < len(res_chunks) else None)
                observation = _passages_observation({"passages": new_passages}, sources,
                                                    max_chars=1600)
                _accumulate_chunks(gathered_chunks, seen_chunk_keys, result.get("chunks"))

                hors_scope = (not result.get("ok", False)
                              or bool(result.get("hors_scope"))
                              or not (result.get("passages") or []))
                if not result.get("ok", False):
                    resume = f"recherche impossible ({result.get('error', 'erreur inconnue')})"
                elif hors_scope:
                    resume = "aucun passage pertinent (hors du périmètre documentaire)"
                elif not new_passages:
                    resume = "passages déjà couverts par les étapes précédentes"
                else:
                    srcs = sorted({p.get("source") or "document" for p in new_passages})
                    resume = f"{len(new_passages)} passage(s) retenus - {', '.join(srcs)}"
                notes.append(f"Étape {executed} - {step['sous_question']}\n{observation}")
                done_steps.append({"sous_question": step["sous_question"],
                                   "but": step.get("but", ""), "query": query,
                                   "resume": resume, "hors_scope": hors_scope})
                yield {"type": "step_done", "index": executed, "resume": resume,
                       "hors_scope": hors_scope}

                # Adaptation : UNE re-planification max, sur étape vide/hors périmètre,
                # qui ne révise QUE les étapes restantes (borne globale respectée).
                budget = self.max_steps - executed
                if hors_scope and not replanned and budget > 0:
                    revised = self._replan(question, notes, step, budget)
                    if revised:
                        replanned = True
                        queue = revised
                        tr.set("replanned", True)
                        yield {"type": "replan", "index": executed,
                               "steps": ([{"sous_question": s["sous_question"],
                                           "but": s.get("but", "")} for s in done_steps]
                                         + [dict(s) for s in queue])}

            # Synthèse finale STREAMÉE, ancrée sur tous les passages accumulés.
            answer = ""
            if gathered:
                parts = []
                syn_citations, used_chunks = None, None
                with span("synthesis", passages=len(gathered)):
                    try:
                        token_gen, syn_citations, used_chunks = _unpack_synthesis(
                            self.stream_synthesizer(question, gathered))
                        for tok in token_gen:
                            parts.append(tok)
                            yield {"type": "answer_token", "text": tok}
                    except Exception:
                        # Coupure/timeout LLM pendant la synthèse : note + trame `done` garantie.
                        logger.exception("[planner] synthèse interrompue")
                        yield {"type": "answer_token", "text": "\n\n(synthèse interrompue)"}
                answer = "".join(parts).strip()
                if syn_citations:
                    sources = syn_citations
                if used_chunks is not None:
                    # Contrat marqueur↔passage : les chunks exposés à l'UI sont
                    # EXACTEMENT la liste numérotée [1..n] de la synthèse.
                    gathered_chunks = used_chunks
            else:
                answer = _NO_RESULT_ANSWER
                yield {"type": "answer_token", "text": answer}

            tr.set("tool_calls", tool_calls)
            tr.set("stopped_reason", "planned")

        yield {"type": "done", "result": {
            "ok": True, "answer": answer,
            "plan": [dict(s) for s in plan],
            "steps": done_steps, "iterations": len(done_steps),
            "tool_calls": tool_calls, "replanned": replanned,
            "sources": sources, "chunks": gathered_chunks,
            "stopped_reason": "planned",
            "latency_s": round(time.perf_counter() - t0, 2),
        }}

    # -- helpers internes ----------------------------------------------------------
    def _refine_query(self, question: str, step: dict, notes: list[str]) -> str | None:
        """Affine la sous-question avec les observations accumulées (une ligne).
        None si l'affinage échoue -> la sous-question du plan est utilisée telle quelle."""
        context = "\n\n".join(notes)[-4000:]
        prompt = (
            f"Question globale : {question}\n\n"
            "Observations déjà collectées :\n"
            f"{context}\n\n"
            f"Prochaine recherche prévue : {step.get('sous_question', '')}"
            f"{' (but : ' + step['but'] + ')' if step.get('but') else ''}\n"
            "Réécris cette recherche en UNE requête documentaire autonome, en y intégrant "
            "si utile les termes exacts déjà observés (identifiants, sigles, noms). "
            "Réponds UNIQUEMENT avec la requête, sur une seule ligne, sans commentaire."
        )
        try:
            with operation("refine"):
                out = self.llm.invoke(prompt)
        except Exception as e:
            logger.warning("[planner] Affinage de sous-question impossible : %s", e)
            return None
        line = next((ln.strip().strip('"').strip("'")
                     for ln in str(out or "").splitlines() if ln.strip()), "")
        return line if line and len(line) <= 300 else None

    def _replan(self, question: str, notes: list[str], failed_step: dict,
                budget: int) -> list[dict] | None:
        """Révision des étapes restantes (validation souple : 1 étape suffit)."""
        try:
            with operation("replan"):
                raw = _llm_json_call(self.llm, _replan_prompt(question, notes,
                                                              failed_step, budget))
        except Exception as e:
            logger.warning("[planner] Re-planification impossible : %s", e)
            return None
        steps, _errors = validate_plan(_parse_plan_json(raw), min_steps=1,
                                       max_steps=budget)
        return steps


def run_planner(question: str, **kwargs) -> dict:
    """Raccourci : instancie un planificateur-exécuteur par défaut et exécute."""
    return PlannerAgent(**kwargs).run(question)


if __name__ == "__main__":
    import sys

    q = " ".join(sys.argv[1:]).strip() or input("Question : ").strip()
    agent = PlannerAgent()
    res: dict = {}
    for ev in agent.run_stream(q):
        kind = ev.get("type")
        if kind == "plan":
            print("Plan :")
            for n, s in enumerate(ev["steps"], 1):
                but = f"  ({s['but']})" if s.get("but") else ""
                print(f"  {n}. {s['sous_question']}{but}")
        elif kind == "step_start":
            print(f"[{ev['index']}/{ev['total']}] {ev['sous_question']} ...")
        elif kind == "step_done":
            print(f"    -> {ev['resume']}")
        elif kind == "replan":
            print("    (re-planification des étapes restantes)")
        elif kind in ("thought", "action", "observation"):
            print(f"    {kind}: {ev.get('text') or ev.get('input')}")
        elif kind == "done":
            res = ev.get("result", {})
    print("\n" + "=" * 70)
    print(f"Réponse finale ({res.get('stopped_reason')}, {res.get('tool_calls')} "
          f"appel(s) d'outil, {res.get('latency_s')}s) :\n")
    print(res.get("answer"))
    if res.get("sources"):
        print("\n--- Sources ---")
        for s in res["sources"]:
            page = f", page {s['page']}" if s.get("page") else ""
            print(f"  [{s.get('idx')}] {s.get('source')}{page}")
