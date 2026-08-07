# core/evaluation.py
"""
Pipeline d'évaluation automatique du RAG - inspiré de RAGAS, 100% local (Ollama).

Deux niveaux de métriques :
--------------------------------------------------------------------------------
NIVEAU 1 - Heuristiques rapides (sans LLM, instantanées) :
  - exact_match        : la référence est-elle contenue dans la réponse ?
  - f1_token           : overlap token SQuAD entre réponse générée et référence
  - context_recall_lexical     : les chunks couvrent-ils les tokens de la référence ?
  - context_precision_lexical  : les topK chunks sont-ils pertinents (F1 > seuil) ?

NIVEAU 2 - LLM-as-a-judge local (Ollama, comme RAGAS) :
  - faithfulness_llm       : la réponse ne contient-elle que ce qui est dans le contexte ?
  - answer_relevance_llm   : la réponse répond-elle à la question ?
  - context_relevance_llm  : les chunks récupérés sont-ils pertinents à la question ?

Stratégie d'évaluation :
  On évalue les réponses BRUTES du LLM (pas besoin de référence pour les métriques LLM).
  Les métriques heuristiques nécessitent une réponse de référence.
  En pratique : pour un corpus Q/R, on a la référence -> on peut tout calculer.
  Pour un monitoring sans référence, on utilise uniquement les métriques LLM.
"""
from __future__ import annotations

import re
import time
import types
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from utils.mongo import get_client
from utils.logging_config import get_logger
from env_config import RAGAS_JUDGE_CONCURRENCY

logger = get_logger("rag.eval")


# -----------------------------------------------------------------------------
#  Helper de parallélisation (appels juge LLM indépendants)
# -----------------------------------------------------------------------------

def _parallel_map(fn, items, workers):
    """Applique fn à chaque item en parallèle (ordre préservé). workers<=1 -> séquentiel.
    Sûr pour des appels LLM (OllamaClient.invoke est un POST sans état partagé)."""
    items = list(items)
    if workers <= 1 or len(items) <= 1:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fn, items))


# -----------------------------------------------------------------------------
#  Helpers tokenisation
# -----------------------------------------------------------------------------

def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().strip())

def _tokens(text: str) -> set:
    return set(re.findall(r"\w+", _normalize(text), flags=re.UNICODE))


# -----------------------------------------------------------------------------
#  NIVEAU 1 - Métriques heuristiques (sans LLM)
# -----------------------------------------------------------------------------

def exact_match(generated: str, reference: str) -> float:
    return float(_normalize(reference) in _normalize(generated))


def f1_token(generated: str, reference: str) -> float:
    gen_tok = _tokens(generated)
    ref_tok = _tokens(reference)
    if not ref_tok or not gen_tok:
        return 0.0
    common = gen_tok & ref_tok
    precision = len(common) / len(gen_tok)
    recall    = len(common) / len(ref_tok)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def context_recall_lexical(chunks: list, reference: str) -> float:
    if not reference or not chunks:
        return 0.0
    ref_tok = _tokens(reference)
    covered = set()
    for c in chunks:
        covered |= _tokens(c.get("doc", ""))
    if not ref_tok:
        return 0.0
    return len(ref_tok & covered) / len(ref_tok)


def context_precision_lexical(chunks: list, reference: str, topk: int = 5) -> float:
    if not reference or not chunks:
        return 0.0
    relevant = sum(
        1 for c in chunks[:topk]
        if f1_token(c.get("doc", ""), reference) > 0.1
    )
    return relevant / min(topk, len(chunks))


def structured_axis_coverage(generated: str, expected_axes: list) -> Optional[float]:
    """Part des axes structurés dont les faits obligatoires apparaissent.

    Chaque axe est {name, keywords, min_hits?}. Cette métrique déterministe mesure
    la couverture du contrat métier, indépendamment du style de rédaction.
    """
    if not expected_axes:
        return None
    blob = _normalize(generated)
    covered = 0
    for axis in expected_axes:
        keywords = [str(k) for k in (axis.get("keywords") or []) if str(k).strip()]
        minimum = int(axis.get("min_hits", len(keywords) or 1))
        hits = sum(1 for keyword in keywords if _normalize(keyword) in blob)
        if hits >= minimum:
            covered += 1
    return covered / len(expected_axes)


def keyword_hit_rate(chunks: list, expected_keywords: list, topk: int = 10) -> Optional[float]:
    """
    Métrique de retrieval PURE (indépendante de la génération) :
    fraction des mots-clés attendus présents dans les top-k chunks récupérés.
    Mesure « le bon passage a-t-il été retrouvé ? » sans dépendre du LLM.
    Retourne None si aucun mot-clé attendu n'est fourni.
    """
    if not expected_keywords:
        return None
    if not chunks:
        return 0.0
    blob = _normalize(" \n ".join(c.get("doc", "") for c in chunks[:topk]))
    hits = sum(1 for kw in expected_keywords if _normalize(kw) in blob)
    return hits / len(expected_keywords)


# -----------------------------------------------------------------------------
#  NIVEAU 2 - LLM-as-a-judge local (Ollama, style RAGAS)
# -----------------------------------------------------------------------------

def _get_judge_llm(model: str = None):
    """LLM-as-judge (rôle 'judge' - voir core.model_router). `model` force un modèle dédié."""
    from core.model_router import build_llm
    return build_llm("judge", model=model)


def _ask_judge(llm, prompt: str, max_retries: int = 2) -> float:
    for attempt in range(max_retries + 1):
        try:
            try:
                raw = llm.invoke(prompt, format="json").strip()
            except TypeError:
                # Compatibilité avec les juges injectés des tests.
                raw = llm.invoke(prompt).strip()
            matches = re.findall(r"\b(0(?:\.\d+)?|1(?:\.0+)?)\b", raw)
            if matches:
                return min(1.0, max(0.0, float(matches[0])))
            m10 = re.search(r"(\d+(?:\.\d+)?)\s*/\s*10", raw)
            if m10:
                return min(1.0, float(m10.group(1)) / 10.0)
            else:
                # Sortie non parsable (ex. « Oui » sans nombre) : on retente, et au
                # dernier essai on trace le texte brut — un juge dégénéré fausserait
                # silencieusement la boussole RAGAS (biais vers 0). Diagnosticable.
                if attempt == max_retries:
                    logger.warning("[judge] Réponse non parsable (score=0.0) : %r",
                                   raw[:200])
        except Exception as e:
            if attempt == max_retries:
                logger.warning("[judge] Échec après %d tentatives : %s", max_retries, e)
    return 0.0


def _call_llm(llm, prompt: str) -> str:
    """Appelle un juge injecté : callable(prompt)->str OU objet avec .invoke."""
    if llm is None:
        llm = _get_judge_llm()
    fn = llm if callable(llm) else llm.invoke
    return fn(prompt) or ""


_CLAIMS_PROMPT = """[TÂCHE] Décompose le TEXTE en affirmations ATOMIQUES (une idée vérifiable par ligne).
- Une affirmation par ligne, préfixée « - ». Rien d'autre.
- Fidèle au texte, sans invention, sans reformulation excessive.

[TEXTE]
{text}

[AFFIRMATIONS]"""


def decompose_claims(text: str, llm=None) -> list[str]:
    """Extrait des affirmations atomiques d'un texte (LLM injectable)."""
    if not (text or "").strip():
        return []
    raw = _call_llm(llm, _CLAIMS_PROMPT.format(text=text[:2000]))
    items = []
    for line in raw.splitlines():
        s = line.strip().lstrip("-*•").strip()
        if s:
            items.append(s)
    return items


# -----------------------------------------------------------------------------
#  NIVEAU 2b - Métriques RAGAS par affirmation (fidélité / rappel de contexte)
# -----------------------------------------------------------------------------
#
# Contrairement à faithfulness_llm/context_recall_lexical (un seul score global), ces
# variantes décomposent le texte en affirmations atomiques (decompose_claims)
# puis jugent chacune individuellement (_judge_supported) - style RAGAS.

def _judge_supported(claim: str, context: str, llm) -> bool:
    """Le CONTEXTE soutient-il l'AFFIRMATION ? (juge LLM, >= 0.5 = soutenue)."""
    prompt = f"""[TÂCHE] Le CONTEXTE soutient-il l'AFFIRMATION ? Réponds uniquement en JSON strict : {{"score": 1.0}} si oui (directement justifiable), sinon {{"score": 0.0}}.

[CONTEXTE]
{context[:2000]}

[AFFIRMATION]
{claim}

Score :"""
    judge = llm
    if callable(judge) and not hasattr(judge, "invoke"):
        # _ask_judge attend un objet .invoke -> on enveloppe un llm callable injecté.
        judge = types.SimpleNamespace(invoke=judge)
    return _ask_judge(judge, prompt) >= 0.5


def _context_text(chunks: list, topk: int = 5, per: int = 500) -> str:
    """Concatène les textes des topk premiers chunks (tronqués à `per` caractères chacun)."""
    return "\n\n".join((c.get("doc", "") or "")[:per] for c in (chunks or [])[:topk])


def faithfulness_ragas(generated: str, chunks: list, llm=None) -> float:
    """Fidélité par affirmations : affirmations de la RÉPONSE soutenues par le contexte / total."""
    claims = decompose_claims(generated, llm=llm)
    if not claims or not chunks:
        return 0.0
    if llm is None:
        llm = _get_judge_llm()
    ctx = _context_text(chunks)
    supported = sum(_parallel_map(lambda c: _judge_supported(c, ctx, llm), claims, RAGAS_JUDGE_CONCURRENCY))
    return supported / len(claims)


def _generate_questions(generated: str, n: int, llm) -> list[str]:
    """Génère `n` questions auxquelles la réponse `generated` répondrait (LLM injectable)."""
    prompt = f"""[TÂCHE] À partir de la RÉPONSE, génère {n} question(s) auxquelles elle répondrait. Une par ligne, préfixée « - ». Rien d'autre.

[RÉPONSE]
{generated[:1500]}

[QUESTIONS]"""
    raw = _call_llm(llm, prompt)
    qs = [l.strip().lstrip("-*•").strip() for l in raw.splitlines() if l.strip()]
    return qs[:n]


def _cosine(a, b) -> float:
    import numpy as np
    va, vb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float(va @ vb / (na * nb))


def answer_relevancy_ragas(generated, question, llm=None, embed=None, n=3) -> float:
    """Cosinus moyen entre la question d'origine et des questions générées depuis la réponse."""
    if not (generated or "").strip():
        return 0.0
    if embed is None:
        from nlp.ollama_embedding import OllamaEmbedding
        embed = OllamaEmbedding().embed_query
    gen_qs = _generate_questions(generated, n, llm)
    if not gen_qs:
        return 0.0
    q_vec = embed(question)
    sims = [_cosine(q_vec, embed(gq)) for gq in gen_qs]
    return sum(sims) / len(sims)


def context_recall_ragas(reference: str, chunks: list, llm=None) -> float:
    """Rappel de contexte par affirmations : affirmations de la RÉFÉRENCE attribuables au contexte récupéré / total."""
    claims = decompose_claims(reference, llm=llm)
    if not claims or not chunks:
        return 0.0
    if llm is None:
        llm = _get_judge_llm()
    ctx = _context_text(chunks)
    attrib = sum(_parallel_map(lambda c: _judge_supported(c, ctx, llm), claims, RAGAS_JUDGE_CONCURRENCY))
    return attrib / len(claims)


def _judge_chunk_relevant(question: str, reference: str, doc: str, llm) -> bool:
    """Le PASSAGE est-il pertinent pour répondre à la QUESTION (au vu de la RÉFÉRENCE) ?"""
    prompt = f"""[TÂCHE] Ce PASSAGE est-il pertinent pour répondre à la QUESTION (au vu de la RÉPONSE attendue) ? Réponds uniquement en JSON strict : {{"score": 1.0}} si pertinent, sinon {{"score": 0.0}}.

[QUESTION] {question}
[RÉPONSE ATTENDUE] {reference[:500]}
[PASSAGE] {doc[:800]}

Score :"""
    judge = llm
    if callable(judge) and not hasattr(judge, "invoke"):
        # _ask_judge attend un objet .invoke -> on enveloppe un llm callable injecté.
        judge = types.SimpleNamespace(invoke=judge)
    return _ask_judge(judge, prompt) >= 0.5


def context_precision_ragas(question: str, reference: str, chunks: list, topk: int = 5, llm=None) -> float:
    """Mean average precision@k : pertinence jugée par chunk, pondérée par le rang."""
    kept = (chunks or [])[:topk]
    if not kept:
        return 0.0
    if llm is None:
        llm = _get_judge_llm()
    # Jugement de pertinence PAR CHUNK en parallèle (indépendants), ordre préservé ;
    # l'AP@k reste calculée séquentiellement ensuite (dépend du rang -> non parallélisable).
    rels = _parallel_map(
        lambda c: _judge_chunk_relevant(question, reference, c.get("doc", "") or "", llm),
        kept, RAGAS_JUDGE_CONCURRENCY,
    )
    hits = 0
    precisions = []
    for i, is_rel in enumerate(rels, 1):
        if is_rel:
            hits += 1
            precisions.append(hits / i)
    if not precisions:
        return 0.0
    return sum(precisions) / len(precisions)


def faithfulness_llm(generated: str, chunks: list, llm=None) -> float:
    """Fidélité : la réponse est-elle entièrement fondée sur le contexte ? (0.0 = hallucination, 1.0 = fidèle)"""
    if not generated or not chunks:
        return 0.0
    context = "\n\n".join(c.get("doc", "")[:500] for c in chunks[:5])
    prompt = f"""Tu es un évaluateur expert en RAG (Génération Augmentée par Récupération).

CONTEXTE (passages récupérés) :
{context}

RÉPONSE GÉNÉRÉE :
{generated[:1000]}

TÂCHE : Évalue la **fidélité** de la réponse.
La réponse est-elle entièrement fondée sur le contexte fourni, sans information inventée ?

Règles de notation :
- 1.0 : Toutes les affirmations de la réponse sont directement justifiables par le contexte.
- 0.5 : Certaines affirmations sont dans le contexte, d'autres sont extrapolées ou ambiguës.
- 0.0 : La réponse contient des informations absentes ou contredisant le contexte (hallucination).

Réponds UNIQUEMENT avec un nombre décimal entre 0.0 et 1.0. Exemple : 0.8
Score de fidélité :"""
    if llm is None:
        llm = _get_judge_llm()
    return _ask_judge(llm, prompt)


def answer_relevance_llm(generated: str, question: str, llm=None) -> float:
    """Pertinence de la réponse : répond-elle bien à la question ? (0.0 = hors sujet, 1.0 = direct et complet)"""
    if not generated or not question:
        return 0.0
    prompt = f"""Tu es un évaluateur expert en RAG.

QUESTION : {question}

RÉPONSE : {generated[:1000]}

TÂCHE : Évalue la **pertinence de la réponse** par rapport à la question posée.

Règles de notation :
- 1.0 : La réponse répond directement et complètement à la question.
- 0.5 : La réponse répond partiellement ou contient des informations non demandées.
- 0.0 : La réponse ne répond pas à la question, est hors sujet, ou dit uniquement "je ne sais pas".

Réponds UNIQUEMENT avec un nombre décimal entre 0.0 et 1.0. Exemple : 0.7
Score de pertinence de la réponse :"""
    if llm is None:
        llm = _get_judge_llm()
    return _ask_judge(llm, prompt)


def context_relevance_llm(chunks: list, question: str, llm=None) -> float:
    """Pertinence du contexte : les passages récupérés sont-ils utiles pour répondre à la question ?"""
    if not chunks or not question:
        return 0.0
    snippets = "\n---\n".join(c.get("doc", "")[:300] for c in chunks[:5])
    prompt = f"""Tu es un évaluateur expert en RAG.

QUESTION : {question}

PASSAGES RÉCUPÉRÉS :
{snippets}

TÂCHE : Évalue la **pertinence des passages récupérés** par rapport à la question.
Ces passages contiennent-ils les informations nécessaires pour répondre à la question ?

Règles de notation :
- 1.0 : Tous les passages récupérés sont directement utiles pour répondre à la question.
- 0.5 : Certains passages sont pertinents, d'autres sont du bruit ou hors sujet.
- 0.0 : Aucun passage n'est utile pour répondre à la question.

Réponds UNIQUEMENT avec un nombre décimal entre 0.0 et 1.0. Exemple : 0.6
Score de pertinence du contexte :"""
    if llm is None:
        llm = _get_judge_llm()
    return _ask_judge(llm, prompt)


# -----------------------------------------------------------------------------
#  Vérificateur fusionné - multi-axes + preuves EN UN SEUL appel LLM
# -----------------------------------------------------------------------------
#
# Les 3 métriques LLM ci-dessus font 3 appels séparés (lent sur petit GPU). Le
# vérificateur les fusionne en UN appel (~3x moins de latence) et, à la manière d'un
# agent évaluateur, CITE les extraits problématiques (preuves) au lieu d'un score nu.
# Utilisé en temps réel (UI à la demande, Self-RAG) ; le harnais batch garde les 3
# appels séparés (rigueur du benchmark) via evaluate_single.

import json

_VERIFY_PROMPT = """Tu es un VÉRIFICATEUR qualité de réponses RAG. Tu n'effectues PAS la tâche :
tu évalues la RÉPONSE par rapport AUX SEULS passages fournis.

QUESTION :
{question}

PASSAGES AUTORISÉS (contexte) :
{context}

RÉPONSE À VÉRIFIER :
{answer}

Note 3 axes, chacun entre 0.0 et 1.0 :
- faithfulness : la réponse est-elle entièrement fondée sur les passages, sans rien inventer ?
- answer_relevance : répond-elle directement et complètement à la question ?
- context_relevance : les passages contiennent-ils de quoi répondre ?
Pour CHAQUE axe < 1.0, ajoute dans "issues" un extrait COURT (<= 20 mots) de la réponse qui pose
problème, ou la nature du manque. N'invente pas d'extrait.

Réponds UNIQUEMENT en JSON valide, sans texte autour :
{{"faithfulness": 0.0, "answer_relevance": 0.0, "context_relevance": 0.0, "issues": ["..."]}}
JSON :"""

_VERIFY_AXES = ("faithfulness", "answer_relevance", "context_relevance")


def _clamp01(v) -> float:
    try:
        return min(1.0, max(0.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def _parse_verify_json(raw: str) -> dict:
    """Extrait {faithfulness, answer_relevance, context_relevance, issues} d'une sortie LLM,
    tolérante au texte autour du JSON. Scores bornés à [0,1] ; issues = liste de chaînes."""
    out = {ax: 0.0 for ax in _VERIFY_AXES}
    out["issues"] = []
    if not raw:
        return out
    try:
        start, end = raw.find("{"), raw.rfind("}") + 1
        if start < 0 or end <= start:
            return out
        data = json.loads(raw[start:end])
        for ax in _VERIFY_AXES:
            out[ax] = _clamp01(data.get(ax))
        issues = data.get("issues", [])
        if isinstance(issues, str):
            issues = [issues]
        out["issues"] = [str(i).strip() for i in issues if str(i).strip()][:5]
    except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
        pass
    return out


def verify_answer(question: str, generated: str, chunks: list, llm=None) -> dict:
    """Vérificateur fusionné : UN appel LLM-as-judge -> les 3 axes + des « issues » qui citent
    les extraits problématiques. ~3x moins de latence que les 3 appels séparés, et plus
    actionnable. Retourne {faithfulness, answer_relevance, context_relevance, issues:[...]}.
    Le LLM (rôle 'judge') est injectable pour les tests / la réutilisation batch."""
    base = {ax: 0.0 for ax in _VERIFY_AXES}
    base["issues"] = []
    if not generated or not chunks:
        return base
    context = "\n\n".join(c.get("doc", "")[:500] for c in chunks[:5])
    prompt = _VERIFY_PROMPT.format(question=question[:400], context=context, answer=generated[:1200])
    if llm is None:
        llm = _get_judge_llm()
    try:
        raw = llm.invoke(prompt).strip()
    except Exception as e:
        logger.warning("[verify] Échec du vérificateur : %s", e)
        return base
    return _parse_verify_json(raw)


# -----------------------------------------------------------------------------
#  Correspondance clé -> label français (pour l'affichage UI)
# -----------------------------------------------------------------------------

METRIC_LABELS_FR = {
    "faithfulness":       "Fidélité (réponse <-> contexte)",
    "answer_relevance":   "Pertinence de la réponse",
    "context_relevance":  "Pertinence du contexte récupéré",
    "exact_match":        "Correspondance exacte",
    "f1_token":           "Score F1 (tokens)",
    "context_recall":     "Rappel du contexte",
    "context_precision":  "Précision du contexte",
    "latency_s":          "Latence (secondes)",
    "num_chunks_retrieved": "Passages récupérés",
}

METRIC_DESCRIPTIONS_FR = {
    "faithfulness":      "La réponse ne contient que des informations présentes dans le contexte (0 = hallucination, 1 = fidèle).",
    "answer_relevance":  "La réponse répond directement à la question posée (0 = hors sujet, 1 = complet).",
    "context_relevance": "Les passages récupérés sont utiles pour répondre à la question (0 = bruit, 1 = pertinent).",
    "exact_match":       "La réponse de référence est contenue mot pour mot dans la réponse générée.",
    "f1_token":          "Overlap de tokens entre réponse générée et référence (style SQuAD).",
    "context_recall":    "Les tokens de la référence sont-ils couverts par le contexte récupéré ?",
    "context_precision": "Quelle fraction des passages récupérés est pertinente à la référence ?",
}

def evaluate_single(
    question: str,
    generated_answer: str,
    retrieved_chunks: list,
    reference_answer: str = "",
    use_llm_judge: bool = True,
    llm_judge=None,
    topk_precision: int = 5,
) -> dict:
    """
    Calcule toutes les métriques pour une paire (question, réponse générée).
    - reference_answer optionnel : active les métriques heuristiques si fourni.
    - use_llm_judge : active les métriques LLM-as-a-judge (faithfulness, relevance...).
    """
    has_ref = bool(reference_answer and reference_answer.strip())

    metrics = {
        "question": question,
        "reference": reference_answer,
        "generated": generated_answer,
        "num_chunks_retrieved": len(retrieved_chunks),
    }

    # Métriques heuristiques (nécessitent la référence)
    if has_ref:
        metrics["exact_match"]       = exact_match(generated_answer, reference_answer)
        metrics["f1_token"]          = f1_token(generated_answer, reference_answer)
        metrics["context_recall"]    = context_recall_lexical(retrieved_chunks, reference_answer)
        metrics["context_precision"] = context_precision_lexical(retrieved_chunks, reference_answer, topk=topk_precision)
    else:
        metrics["exact_match"]       = None
        metrics["f1_token"]          = None
        metrics["context_recall"]    = None
        metrics["context_precision"] = None

    # Métriques LLM-as-a-judge (ne nécessitent PAS de référence)
    if use_llm_judge:
        judge = llm_judge or _get_judge_llm()
        metrics["faithfulness"]      = faithfulness_llm(generated_answer, retrieved_chunks, llm=judge)
        metrics["answer_relevance"]  = answer_relevance_llm(generated_answer, question, llm=judge)
        metrics["context_relevance"] = context_relevance_llm(retrieved_chunks, question, llm=judge)
    else:
        metrics["faithfulness"]      = None
        metrics["answer_relevance"]  = None
        metrics["context_relevance"] = None

    return metrics


# -----------------------------------------------------------------------------
#  Pipeline d'évaluation batch
# -----------------------------------------------------------------------------

def run_evaluation_batch(
    qa_pairs: list,
    source_filter: str = None,
    system_prompt: str = None,
    use_llm_judge: bool = True,
    progress_callback=None,
) -> list:
    """
    Lance l'évaluation sur une liste de paires Q/R.
    qa_pairs : liste de dicts {"question": ..., "answer": ...}
    progress_callback(i, n, question) : appelé à chaque étape.
    """
    from core.ask import process_query

    results = []
    n = len(qa_pairs)
    # Instancier le juge UNE seule fois pour tout le batch
    judge = _get_judge_llm() if use_llm_judge else None

    for i, pair in enumerate(qa_pairs):
        question  = (pair.get("question") or "").strip()
        reference = (pair.get("answer") or pair.get("reference") or "").strip()

        if not question:
            continue

        if progress_callback:
            progress_callback(i, n, question)

        t0 = time.time()
        try:
            generated, chunks, _ = process_query(
                question,
                system_prompt=system_prompt,
                source_filter=source_filter,
            )
            latency = time.time() - t0
            metrics = evaluate_single(
                question=question,
                generated_answer=generated or "",
                retrieved_chunks=chunks or [],
                reference_answer=reference,
                use_llm_judge=use_llm_judge,
                llm_judge=judge,
            )
            metrics["latency_s"] = round(latency, 2)
            metrics["status"]    = "ok"
        except Exception as e:
            metrics = {
                "question": question,
                "reference": reference,
                "generated": "",
                "exact_match": None,
                "f1_token": None,
                "context_recall": None,
                "context_precision": None,
                "faithfulness": None,
                "answer_relevance": None,
                "context_relevance": None,
                "num_chunks_retrieved": 0,
                "latency_s": round(time.time() - t0, 2),
                "status": f"error: {e}",
            }

        results.append(metrics)

    return results


def aggregate_metrics(results: list) -> dict:
    """Calcule les moyennes de toutes les métriques numériques (ignore les None)."""
    keys = [
        "keyword_hit_rate", "exact_match", "f1_token",
        "context_recall", "context_precision",
        "faithfulness", "answer_relevance", "answer_relevancy", "context_relevance",
        "latency_s", "num_chunks_retrieved",
    ]
    ok = [r for r in results if r.get("status") == "ok"]
    if not ok:
        return {k: None for k in keys}
    agg = {}
    for k in keys:
        vals = [r[k] for r in ok if r.get(k) is not None]
        agg[k] = round(sum(vals) / len(vals), 4) if vals else None
    return agg


# -----------------------------------------------------------------------------
#  MongoDB persistence
# -----------------------------------------------------------------------------

def save_eval_run_to_mongo(results: list, run_name: str,
                           db_name: str = "ragdb",
                           collection_name: str = "eval_runs",
                           dataset: str | None = None, mode: str | None = None,
                           breakdown: dict | None = None) -> str:
    client = get_client()
    col = client[db_name][collection_name]
    doc = {
        "run_name": run_name,
        "dataset": dataset,
        "mode": mode,
        "breakdown": breakdown or {},
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "aggregate": aggregate_metrics(results),
        "details": results,
        "num_questions": len(results),
    }
    inserted = col.insert_one(doc)
    return str(inserted.inserted_id)


def load_eval_runs_from_mongo(db_name: str = "ragdb",
                               collection_name: str = "eval_runs") -> list:
    client = get_client()
    col = client[db_name][collection_name]
    runs = []
    for r in col.find({}, {"details": 0}):
        r["_id"] = str(r["_id"])
        runs.append(r)
    return sorted(runs, key=lambda x: x.get("timestamp", ""), reverse=True)


def load_eval_run_details(run_id: str, db_name: str = "ragdb",
                           collection_name: str = "eval_runs") -> Optional[dict]:
    from bson import ObjectId
    from bson.errors import InvalidId
    try:
        oid = ObjectId(run_id)
    except (InvalidId, TypeError):
        logger.warning("run_id invalide : %r", run_id)
        return None
    client = get_client()
    col = client[db_name][collection_name]
    r = col.find_one({"_id": oid})
    if r:
        r["_id"] = str(r["_id"])
    return r
