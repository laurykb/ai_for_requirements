# core/attribution.py
"""
Attribution par affirmation : passe POST-HOC de vérification des citations.

Après la fin de la génération (RAG direct ou synthèse agent), UN appel LLM en
sortie contrainte JSON (format Ollama) découpe la réponse en affirmations
factuelles et rattache chacune à son ou ses passages sources :

    { "texte": "extrait exact de la réponse",
      "passages": [n, ...],                       # numéros [1..n] du contexte
      "statut": "sourcee" | "completee" | "non_sourcee" }

    sourcee     la réponse citait déjà un passage correct pour cette affirmation
    completee   marqueur absent/erroné dans la réponse, mais un passage la soutient
    non_sourcee AUCUN passage ne soutient l'affirmation (surlignée côté UI)

La numérotation des passages est CELLE du contexte de génération (contrat
marqueur↔passage, voir core.llm_answer.refine_for_generation) : l'appelant doit
fournir la même liste de chunks que celle envoyée au front (trame `retrieved`).

Robustesse : validation stricte + UN retry (erreurs réinjectées), budget de
temps TOTAL borné (ATTRIBUTION_TIMEOUT_S). Échec ou dépassement -> résultat
{ok: False, error} : la réponse reste telle quelle, avec ses marqueurs inline —
la passe n'est JAMAIS bloquante et ne modifie JAMAIS la réponse.

Tout est injectable (LLM) -> testable 100 % hors-ligne.
"""
from __future__ import annotations

import concurrent.futures as _futures
import json
import time

from env_config import ATTRIBUTION_TIMEOUT_S
from utils.logging_config import get_logger

logger = get_logger("rag.attribution")

# Statuts autorisés pour une affirmation (contrat partagé avec le front).
STATUTS = ("sourcee", "completee", "non_sourcee")

# Bornes du prompt : la passe doit rester UN appel court, même sur les longues
# réponses (au-delà, on tronque — mieux vaut une attribution partielle que pas
# d'attribution du tout).
_MAX_ANSWER_CHARS = 4000
_MAX_PASSAGES = 15
_MAX_PASSAGE_CHARS = 600
_MAX_AFFIRMATION_CHARS = 300

_ATTRIBUTION_PROMPT = """Tu es un VÉRIFICATEUR d'attribution pour un assistant documentaire.
On te donne des PASSAGES numérotés et une RÉPONSE générée à partir d'eux
(dans la réponse, un marqueur [n] cite le passage numéro n).

QUESTION :
{question}

PASSAGES :
{passages}

RÉPONSE À VÉRIFIER :
{answer}

TÂCHE : découpe la RÉPONSE en affirmations factuelles (une idée vérifiable par
affirmation). Pour CHAQUE affirmation :
- "texte" : extrait EXACT et COURT de la réponse (recopié tel quel, sans les marqueurs [n])
- "passages" : les numéros des passages qui la soutiennent RÉELLEMENT (liste vide si aucun)
- "statut" :
  * "sourcee"     : la réponse citait déjà un passage correct pour cette affirmation
  * "completee"   : marqueur absent ou erroné dans la réponse, mais un passage la soutient
  * "non_sourcee" : AUCUN passage ne soutient cette affirmation
Ignore les phrases sans contenu factuel (transitions, politesse, « je ne sais pas »).
Réponds UNIQUEMENT avec un objet JSON de la forme :
{{"affirmations": [{{"texte": "...", "passages": [1], "statut": "sourcee"}}]}}
JSON :"""

# Juge de PRÉCISION d'attribution (harnais d'éval) : un seul appel par réponse,
# qui vérifie un échantillon de couples (affirmation, passage cité).
_SUPPORT_PROMPT = """Tu es un évaluateur : pour chaque COUPLE ci-dessous, dis si le PASSAGE
soutient réellement l'AFFIRMATION (true) ou non (false).

{pairs}

Réponds UNIQUEMENT avec un objet JSON de la forme :
{{"verdicts": [true, false, ...]}}  (un booléen par couple, dans l'ordre)
JSON :"""


def _build_attribution_llm():
    """LLM de la passe d'attribution (rôle 'judge' — tâche de vérification,
    modèle léger, température nulle). Sortie plafonnée : la liste d'affirmations
    d'une réponse tient largement en 1500 tokens."""
    from core.model_router import build_llm
    return build_llm("judge", num_predict=1500)


def _invoke_json(llm, prompt: str) -> str:
    """Appel LLM en sortie contrainte JSON (format Ollama), tolérant aux mocks
    qui n'acceptent pas le paramètre `format`."""
    try:
        out = llm.invoke(prompt, format="json")
    except TypeError:
        out = llm.invoke(prompt)
    return out if isinstance(out, str) else str(out)


def _parse_json_object(raw: str) -> dict | None:
    """Texte LLM -> objet JSON (json strict, sinon premier objet équilibré)."""
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, TypeError):
        from core.agent import _extract_json_object
        return _extract_json_object(raw or "")


def _passages_block(chunks: list[dict]) -> str:
    """Formate les passages numérotés [1..n] — MÊME ordre que le contexte de
    génération (la liste `chunks` est celle envoyée au front)."""
    lines = []
    for i, c in enumerate(chunks[:_MAX_PASSAGES], start=1):
        meta = c.get("meta", {}) or {}
        loc = meta.get("source") or "document"
        page = f" p.{meta['page_number']}" if meta.get("page_number") else ""
        text = (c.get("doc") or "").strip()[:_MAX_PASSAGE_CHARS]
        lines.append(f"[{i}] {loc}{page}\n{text}")
    return "\n\n".join(lines)


def validate_attribution(obj, n_passages: int) -> tuple[list[dict] | None, list[str]]:
    """Valide {affirmations: [{texte, passages, statut}]} -> (affirmations, erreurs).

    Normalisations tolérées (pas des erreurs) : texte tronqué à
    _MAX_AFFIRMATION_CHARS, numéros de passage dédupliqués/triés. Erreurs
    (réinjectées au LLM pour le retry) : forme invalide, texte vide, statut
    inconnu, numéro hors [1..n_passages], statut sourcé sans passage."""
    errors: list[str] = []
    if not isinstance(obj, dict):
        return None, ["la sortie n'est pas un objet JSON"]
    affirmations = obj.get("affirmations")
    if not isinstance(affirmations, list):
        return None, ["clé 'affirmations' manquante ou n'est pas une liste"]
    out: list[dict] = []
    for i, a in enumerate(affirmations, 1):
        if not isinstance(a, dict):
            errors.append(f"affirmation {i} : n'est pas un objet")
            continue
        texte = str(a.get("texte") or "").strip()[:_MAX_AFFIRMATION_CHARS]
        if not texte:
            errors.append(f"affirmation {i} : 'texte' manquant ou vide")
            continue
        statut = str(a.get("statut") or "").strip()
        if statut not in STATUTS:
            errors.append(f"affirmation {i} : statut inconnu '{statut}' "
                          f"(attendu : {', '.join(STATUTS)})")
            continue
        raw_passages = a.get("passages")
        if raw_passages is None:
            raw_passages = []
        if not isinstance(raw_passages, list):
            errors.append(f"affirmation {i} : 'passages' n'est pas une liste")
            continue
        passages: list[int] = []
        bad = False
        for p in raw_passages:
            if not isinstance(p, int) or isinstance(p, bool) or not (1 <= p <= n_passages):
                errors.append(f"affirmation {i} : numéro de passage invalide "
                              f"{p!r} (attendu : entier entre 1 et {n_passages})")
                bad = True
                break
            if p not in passages:
                passages.append(p)
        if bad:
            continue
        passages.sort()
        if statut in ("sourcee", "completee") and not passages:
            errors.append(f"affirmation {i} : statut '{statut}' sans aucun passage")
            continue
        if statut == "non_sourcee":
            passages = []
        out.append({"texte": texte, "passages": passages, "statut": statut})
    if errors:
        return None, errors
    return out, []


def _counters(affirmations: list[dict]) -> dict:
    n_sourcees = sum(1 for a in affirmations if a["statut"] == "sourcee")
    n_completees = sum(1 for a in affirmations if a["statut"] == "completee")
    return {
        "n_affirmations": len(affirmations),
        "n_sourcees": n_sourcees,
        "n_completees": n_completees,
        "n_non_sourcees": len(affirmations) - n_sourcees - n_completees,
    }


def _failure(reason: str) -> dict:
    return {"ok": False, "affirmations": [], "n_affirmations": 0, "n_sourcees": 0,
            "n_completees": 0, "n_non_sourcees": 0, "error": reason}


def attribute_answer(question: str, answer: str, chunks: list[dict],
                     llm=None, timeout_s: float | None = None) -> dict:
    """Passe post-hoc d'attribution : UN appel LLM (JSON contraint) + validation
    + UN retry, le tout sous un budget de temps borné.

    `chunks` : la liste EXACTE de passages numérotés du contexte de génération
    (celle de la trame `retrieved`). Retourne toujours un dict :
    {ok, affirmations, n_affirmations, n_sourcees, n_completees, n_non_sourcees,
    error} — ok=False n'est jamais bloquant pour l'appelant."""
    if not (answer or "").strip():
        return _failure("réponse vide : rien à attribuer")
    if not chunks:
        return _failure("aucun passage : attribution sans objet")
    if timeout_s is None:
        timeout_s = ATTRIBUTION_TIMEOUT_S
    if llm is None:
        try:
            llm = _build_attribution_llm()
        except Exception as e:  # config/modèle indisponible
            return _failure(f"LLM d'attribution indisponible : {e}")

    n_passages = min(len(chunks), _MAX_PASSAGES)
    prompt = _ATTRIBUTION_PROMPT.format(
        question=(question or "")[:400],
        passages=_passages_block(chunks),
        answer=(answer or "").strip()[:_MAX_ANSWER_CHARS],
    )

    deadline = time.monotonic() + max(0.01, float(timeout_s))
    executor = _futures.ThreadPoolExecutor(max_workers=1)
    errors: list[str] = []
    try:
        for attempt in (1, 2):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return _failure(f"délai dépassé ({timeout_s:g} s)")
            future = executor.submit(_invoke_json, llm, prompt)
            try:
                raw = future.result(timeout=remaining)
            except _futures.TimeoutError:
                future.cancel()
                return _failure(f"délai dépassé ({timeout_s:g} s)")
            except Exception as e:
                logger.warning("[attribution] Appel LLM impossible : %s", e)
                return _failure(f"appel LLM impossible : {e}")

            affirmations, errors = validate_attribution(
                _parse_json_object(raw), n_passages)
            if affirmations is not None:
                return {"ok": True, "affirmations": affirmations,
                        **_counters(affirmations), "error": None}
            if attempt == 1:
                # Retry unique : sortie fautive + erreurs réinjectées.
                prompt = (
                    f"{prompt}\n"
                    f"Ta précédente réponse était invalide :\n{(raw or '')[:800]}\n"
                    f"Erreurs : {'; '.join(errors[:6])}.\n"
                    "Corrige et renvoie UNIQUEMENT le JSON demandé."
                )
        logger.info("[attribution] Sortie invalide après retry : %s", "; ".join(errors[:6]))
        return _failure("sortie LLM invalide après retry : " + "; ".join(errors[:4]))
    finally:
        # Ne JAMAIS attendre le thread (un invoke Ollama peut durer) : le flux
        # de l'appelant reprend immédiatement, le thread meurt avec sa requête.
        executor.shutdown(wait=False)


def judge_attribution_support(affirmations: list[dict], chunks: list[dict],
                              llm, max_pairs: int = 6) -> float | None:
    """Précision d'attribution (harnais d'éval) : sur un échantillon de couples
    (affirmation sourcée, premier passage cité), le juge LLM confirme-t-il que
    le passage soutient l'affirmation ? UN appel fusionné (JSON contraint).
    Retourne la fraction de verdicts positifs, ou None (aucun couple/échec)."""
    pairs = []
    for a in affirmations or []:
        if a.get("statut") in ("sourcee", "completee") and a.get("passages"):
            idx = a["passages"][0]
            if 1 <= idx <= len(chunks):
                pairs.append((a["texte"], idx))
        if len(pairs) >= max_pairs:
            break
    if not pairs:
        return None
    blocks = []
    for i, (texte, idx) in enumerate(pairs, 1):
        passage = (chunks[idx - 1].get("doc") or "").strip()[:_MAX_PASSAGE_CHARS]
        blocks.append(f"COUPLE {i}\nAFFIRMATION : {texte}\nPASSAGE : {passage}")
    prompt = _SUPPORT_PROMPT.format(pairs="\n\n".join(blocks))
    try:
        obj = _parse_json_object(_invoke_json(llm, prompt))
    except Exception as e:
        logger.warning("[attribution] Juge de précision indisponible : %s", e)
        return None
    verdicts = (obj or {}).get("verdicts")
    if not isinstance(verdicts, list) or not verdicts:
        return None
    verdicts = verdicts[:len(pairs)]
    return round(sum(1 for v in verdicts if v is True) / len(pairs), 4)
