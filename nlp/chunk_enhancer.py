# nlp/chunk_enhancer.py
"""
Enrichissement des chunks via LLM (Ollama) :
- Extraction de mots-clés importants (auto_keywords)
- Génération de questions auxquelles le chunk répond (auto_questions)
- Génération de résumés par section (RAPTOR simplifié)

Inspiré de RAGFlow : ces métadonnées enrichies améliorent le retrieval
car keyword↔keyword et question↔question matching sont plus fiables
que content↔query matching brut.
"""

import os
import requests
import json
import hashlib
import re
from pathlib import Path
from typing import Optional
from langchain_core.documents import Document
from env_config import ENHANCE_COMBINED
from core.model_router import model_for, ollama_options
from utils.text_utils import make_doc_id
from utils.logging_config import get_logger
from nlp.ner_extractor import extract_entities, entities_to_str, entities_to_flat_list

logger = get_logger("rag.enhance")


# ─────────────────── Cache LLM (évite de re-générer pour le même chunk) ───────────────────

CACHE_DIR = Path("data/enhancement_cache")
# Modèle par défaut pour l'enrichissement : routé par le rôle « enhance » (table
# centrale core/model_router) — ENHANCEMENT_MODEL si défini, sinon REWRITER_MODEL.
DEFAULT_ENHANCE_MODEL = model_for("enhance")

# Un seul message d'erreur détaillé par processus (évite des centaines de lignes en log)
_ollama_error_logged = False


def _ollama_base_url() -> str:
    return os.environ.get("OLLAMA_HOST", "http://localhost:11434").strip().rstrip("/")


def _cache_key(text: str, task: str, model: str) -> str:
    """Génère une clé de cache unique basée sur le contenu + tâche + modèle."""
    h = hashlib.sha256(f"{model}:{task}:{text}".encode()).hexdigest()[:16]
    return h


def _get_cache(text: str, task: str, model: str) -> Optional[str]:
    """Récupère un résultat depuis le cache s'il existe."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{_cache_key(text, task, model)}.json"
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            return data.get("result")
        except Exception:
            pass
    return None


def _set_cache(text: str, task: str, model: str, result: str):
    """Stocke un résultat dans le cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{_cache_key(text, task, model)}.json"
    try:
        cache_file.write_text(
            json.dumps({"task": task, "model": model, "result": result}, ensure_ascii=False),
            encoding="utf-8"
        )
    except Exception:
        pass


def _clean_generated_lines(raw_text: str, separator: str = "\n") -> list[str]:
    """Nettoie une sortie LLM (puces, numérotation, espaces)."""
    cleaned: list[str] = []
    for raw_part in raw_text.split(separator):
        line = raw_part.strip().strip("-").strip("•").strip("*").strip()
        line = re.sub(r"^\d+[\.\)]\s*", "", line)
        if line:
            cleaned.append(line)
    return cleaned


# ─────────────────── Appel LLM Ollama ───────────────────

def _call_ollama(prompt: str, model: str = None, base_url: str = None) -> str:
    """
    Appel à l'API Ollama /api/chat avec thinking désactivé.
    Compatible avec les modèles qwen3 (qui activent le thinking par défaut).
    Retourne le texte brut de la réponse.
    """
    global _ollama_error_logged
    model = model or DEFAULT_ENHANCE_MODEL
    base = (base_url or _ollama_base_url()).rstrip("/")
    url = f"{base}/api/chat"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,            # Désactive le thinking (qwen3, etc.)
        # Hyperparamètres (température/top_p/num_predict) routés par le rôle « enhance ».
        "options": ollama_options("enhance"),
    }
    try:
        resp = requests.post(url, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        content = data.get("message", {}).get("content", "").strip()
        _ollama_error_logged = False
        return content
    except requests.HTTPError as e:
        if not _ollama_error_logged:
            r = e.response
            api_err = ""
            if r is not None:
                try:
                    api_err = (r.json().get("error") or "").strip()
                except Exception:
                    api_err = (r.text or "")[:300].strip()
            hint = ""
            if r is not None and r.status_code == 404:
                hint = (
                    f" Modèle « {model} » introuvable ou mauvaise URL ? "
                    f"Vérifiez `ollama list` et `ollama pull …`, ou la variable OLLAMA_HOST."
                )
            logger.warning("[enhance] Ollama HTTP %s sur %s:%s — %s",
                           getattr(r, "status_code", "?"), url, hint, api_err or e)
            logger.warning("[enhance] Enrichissement LLM (mots-clés / questions) ignoré pour la suite "
                           "de cette exécution ; les autres étapes (NER, etc.) continuent.")
            _ollama_error_logged = True
        return ""
    except Exception as e:
        if not _ollama_error_logged:
            logger.warning("[enhance] Ollama indisponible (%s) : %s", url, e)
            logger.warning("[enhance] Vérifiez que `ollama serve` tourne et que OLLAMA_HOST "
                           "pointe vers le bon hôte (défaut http://localhost:11434).")
            _ollama_error_logged = True
        return ""


# ─────────────────── Extraction de mots-clés ───────────────────

KEYWORD_PROMPT = """Tu es un extracteur de mots-clés techniques.

Extrait exactement {topn} mots-clés ou expressions-clés du texte ci-dessous.
Les mots-clés doivent être les concepts les plus importants et discriminants du texte.
Privilégie les termes techniques, noms propres, acronymes, identifiants, normes.

Réponds UNIQUEMENT avec les mots-clés séparés par des virgules, sans numérotation, sans explication.

Texte :
{text}

Mots-clés :"""


def extract_keywords(text: str, topn: int = 5, model: str = None) -> list[str]:
    """
    Extrait les mots-clés d'un chunk via le LLM.
    Retourne une liste de mots-clés (strings).
    """
    if not text or len(text.strip()) < 30:
        return []

    # Check cache
    cached = _get_cache(text, f"keywords_{topn}", model or DEFAULT_ENHANCE_MODEL)
    if cached:
        return [k.strip() for k in cached.split(",") if k.strip()]

    prompt = KEYWORD_PROMPT.format(topn=topn, text=text[:3000])
    result = _call_ollama(prompt, model=model)

    if result:
        _set_cache(text, f"keywords_{topn}", model or DEFAULT_ENHANCE_MODEL, result)
        keywords = [k for k in _clean_generated_lines(result.replace("\n", ","), separator=",") if len(k) > 1]
        return keywords[:topn]
    return []


# ─────────────────── Génération de questions ───────────────────

QUESTION_PROMPT = """Tu es un générateur de questions techniques.

Génère exactement {topn} questions auxquelles le texte ci-dessous permet de répondre.
Les questions doivent être précises, variées et couvrir les points clés du texte.
Formule les questions en français, comme si un utilisateur cherchait cette information.

Réponds UNIQUEMENT avec les questions, une par ligne, sans numérotation.

Texte :
{text}

Questions :"""


def generate_questions(text: str, topn: int = 3, model: str = None) -> list[str]:
    """
    Génère des questions auxquelles le chunk répond, via le LLM.
    Retourne une liste de questions (strings).
    """
    if not text or len(text.strip()) < 30:
        return []

    # Check cache
    cached = _get_cache(text, f"questions_{topn}", model or DEFAULT_ENHANCE_MODEL)
    if cached:
        return [q.strip() for q in cached.split("\n") if q.strip()]

    prompt = QUESTION_PROMPT.format(topn=topn, text=text[:3000])
    result = _call_ollama(prompt, model=model)

    if result:
        _set_cache(text, f"questions_{topn}", model or DEFAULT_ENHANCE_MODEL, result)
        cleaned_lines = _clean_generated_lines(result, separator="\n")
        questions = [q for q in cleaned_lines if len(q) > 10 and "?" in q]
        # Si pas assez de "?" trouvées, on accepte aussi les lignes sans "?"
        if len(questions) < topn:
            for q in cleaned_lines:
                if len(q) > 10 and q not in questions:
                    questions.append(q)
        return questions[:topn]
    return []


# ─────────────────── Mots-clés + questions en UN SEUL appel ───────────────────

KEYWORDS_QUESTIONS_PROMPT = """Tu es un assistant d'indexation documentaire.

À partir du texte ci-dessous, produis :
- {n_kw} mots-clés ou expressions-clés : les concepts les plus discriminants
  (termes techniques, noms propres, acronymes, identifiants, normes).
- {n_q} questions précises, en français, auxquelles le texte permet de répondre.

Réponds UNIQUEMENT en JSON valide, sans aucun texte autour :
{{"keywords": ["...", "..."], "questions": ["... ?", "... ?"]}}

Texte :
{text}

JSON :"""


def _parse_kwq_json(raw: str) -> tuple[list[str], list[str]]:
    """Extrait (keywords, questions) d'une réponse LLM JSON, tolérante au texte autour."""
    if not raw:
        return [], []
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(raw[start:end])
            kws = [str(k).strip() for k in data.get("keywords", []) if str(k).strip()]
            qs = [str(q).strip() for q in data.get("questions", []) if str(q).strip()]
            return kws, qs
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    return [], []


def extract_keywords_and_questions(text: str, num_keywords: int = 5, num_questions: int = 3,
                                   model: str = None) -> tuple[list[str], list[str]]:
    """
    Extrait mots-clés ET questions en UN SEUL appel LLM (au lieu de deux).
    Retourne (keywords, questions). En cas de JSON illisible, retombe sur les
    deux appels séparés historiques (robustesse, rarement déclenché).
    """
    if not text or len(text.strip()) < 30:
        return [], []

    cache_model = model or DEFAULT_ENHANCE_MODEL
    task = f"kwq_{num_keywords}_{num_questions}"
    cached = _get_cache(text, task, cache_model)
    if cached:
        kws, qs = _parse_kwq_json(cached)
        if kws or qs:
            return kws[:num_keywords], qs[:num_questions]

    prompt = KEYWORDS_QUESTIONS_PROMPT.format(n_kw=num_keywords, n_q=num_questions, text=text[:3000])
    raw = _call_ollama(prompt, model=model)
    kws, qs = _parse_kwq_json(raw)

    if kws or qs:
        _set_cache(text, task, cache_model,
                   json.dumps({"keywords": kws, "questions": qs}, ensure_ascii=False))
        return kws[:num_keywords], qs[:num_questions]

    # Fallback : anciens appels séparés si le modèle n'a pas produit de JSON exploitable
    return (extract_keywords(text, topn=num_keywords, model=model),
            generate_questions(text, topn=num_questions, model=model))


# ─────────────────── Description de tables/figures (Étape 5) ───────────────────

TABLE_DESCRIPTION_PROMPT = """Tu es un expert en analyse de documents techniques.

Voici un tableau extrait d'un document. Décris en UNE SEULE phrase ce que ce tableau contient,
son objectif et les informations clés qu'il fournit.

Sois factuel et précis. Ne répète pas le contenu du tableau, décris-le.

{heading_context}
Tableau :
{text}

Description (une phrase) :"""


def describe_table(text: str, heading: str = "", model: str = None) -> str:
    """
    Génère une description en une phrase pour un chunk de type table.
    La description est injectée dans le contenu du chunk pour améliorer le retrieval.
    """
    if not text or len(text.strip()) < 20:
        return ""

    task_key = "table_description"
    cached = _get_cache(text, task_key, model or DEFAULT_ENHANCE_MODEL)
    if cached:
        return cached

    heading_ctx = f"Section : {heading}\n" if heading else ""
    prompt = TABLE_DESCRIPTION_PROMPT.format(
        heading_context=heading_ctx,
        text=text[:3000]
    )
    result = _call_ollama(prompt, model=model)

    if result:
        # Nettoyer : garder uniquement la première phrase
        clean = result.strip().split("\n")[0].strip()
        _set_cache(text, task_key, model or DEFAULT_ENHANCE_MODEL, clean)
        return clean
    return ""


# ─────────────────── Enrichissement complet d'un chunk ───────────────────

def enhance_chunk(doc, num_keywords: int = 5, num_questions: int = 3, model: str = None):
    """
    Enrichit un Document LangChain avec keywords et questions.
    Pour les chunks de type table/figure/mixed, ajoute aussi une description.
    Ajoute dans metadata :
      - 'keywords': liste de mots-clés
      - 'questions': liste de questions
      - 'keywords_str': mots-clés concaténés (pour BM25)
      - 'questions_str': questions concaténées (pour BM25)
      - 'table_description': description du tableau (si chunk_type == table/mixed)
    
    Retourne le document modifié (in-place).
    """
    text = doc.page_content
    chunk_type = doc.metadata.get("chunk_type", "text")

    # Étape 5 : générer une description pour les chunks table/mixed
    if chunk_type in ("table", "mixed"):
        heading = doc.metadata.get("heading", "")
        desc = describe_table(text, heading=heading, model=model)
        if desc:
            doc.metadata["table_description"] = desc
            # Injecter la description dans le contenu pour le retrieval
            doc.page_content = f"[Description] {desc}\n{text}"

    # Mots-clés + questions : un seul appel LLM fusionné (si ENHANCE_COMBINED),
    # sinon deux appels dédiés (historique). Bascule pour A/B via le harnais d'éval.
    if ENHANCE_COMBINED and num_keywords > 0 and num_questions > 0:
        keywords, questions = extract_keywords_and_questions(
            text, num_keywords=num_keywords, num_questions=num_questions, model=model
        )
    else:
        keywords = extract_keywords(text, topn=num_keywords, model=model) if num_keywords > 0 else []
        questions = generate_questions(text, topn=num_questions, model=model) if num_questions > 0 else []

    doc.metadata["keywords"] = keywords
    doc.metadata["keywords_str"] = ", ".join(keywords)
    doc.metadata["questions"] = questions
    doc.metadata["questions_str"] = " | ".join(questions)

    # Étape 7 : Extraction d'entités nommées (spaCy + regex)
    ner_dict = extract_entities(doc.page_content)
    doc.metadata["entities"] = ner_dict                         # dict structuré
    doc.metadata["entities_flat"] = entities_to_flat_list(ner_dict)  # liste plate
    doc.metadata["entities_str"] = entities_to_str(ner_dict)    # string pour BM25

    return doc


def enhance_chunks(docs, num_keywords: int = 5, num_questions: int = 3,
                   model: str = None, progress_callback=None, max_workers: int = None):
    """
    Enrichit une liste de Documents LangChain en parallèle.

    Les appels LLM (mots-clés/questions) sont I/O-bound : on les lance sur un pool
    de threads pour saturer le serveur Ollama au lieu de l'interroger un par un.
    Le NER spaCy (rapide) est sérialisé via un verrou interne à ner_extractor.

    Args:
        docs: liste de Document
        num_keywords: nombre de mots-clés à extraire par chunk
        num_questions: nombre de questions à générer par chunk
        model: modèle Ollama à utiliser (défaut: REWRITER_MODEL)
        progress_callback: callable(current, total) pour le suivi de progression
        max_workers: parallélisme (défaut: config ENHANCE_MAX_WORKERS)

    Returns:
        La même liste de docs, enrichie in-place.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    if max_workers is None:
        from env_config import ENHANCE_MAX_WORKERS
        max_workers = ENHANCE_MAX_WORKERS

    total = len(docs)
    if total == 0:
        return docs

    lock = threading.Lock()
    done = 0

    def _work(doc):
        return enhance_chunk(doc, num_keywords=num_keywords, num_questions=num_questions, model=model)

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        futures = [executor.submit(_work, doc) for doc in docs]
        for future in as_completed(futures):
            future.result()  # propage les exceptions éventuelles
            with lock:
                done += 1
                if progress_callback:
                    progress_callback(done, total)
                if done % 10 == 0 or done == total:
                    logger.debug("[enhance] %d/%d chunks enrichis", done, total)
    return docs


# ─────────────────── RAPTOR simplifié : résumés par section ───────────────────

SUMMARY_PROMPT = """Tu es un expert en synthèse de documents techniques.

Rédige un résumé concis et informatif de la section ci-dessous.
Le résumé doit :
- Capturer les points clés, décisions et concepts principaux
- Être autonome (compréhensible sans lire la section complète)
- Mentionner les entités importantes (noms, normes, identifiants, acronymes)
- Faire entre 3 et 8 phrases

Réponds UNIQUEMENT avec le résumé, sans introduction ni commentaire.

{heading_context}
Contenu de la section :
{text}

Résumé :"""


def generate_section_summary(section_text: str, heading: str = "",
                             model: str = None) -> str:
    """
    Génère un résumé LLM pour une section (groupe de chunks).

    Args:
        section_text: texte concaténé de la section
        heading: titre de la section (optionnel, pour contexte)
        model: modèle Ollama à utiliser

    Returns:
        Résumé textuel de la section.
    """
    if not section_text or len(section_text.strip()) < 100:
        return ""

    # Check cache
    cache_model = model or DEFAULT_ENHANCE_MODEL
    cached = _get_cache(section_text[:5000], "summary", cache_model)
    if cached:
        return cached

    heading_context = f"Section : {heading}\n" if heading else ""
    prompt = SUMMARY_PROMPT.format(
        heading_context=heading_context,
        text=section_text[:6000]  # Limiter pour ne pas dépasser le contexte LLM
    )

    result = _call_ollama(prompt, model=model)

    if result:
        # Nettoyer : enlever les préfixes courants
        for prefix in ["Résumé :", "Résumé:", "Summary:", "Voici le résumé :"]:
            if result.lower().startswith(prefix.lower()):
                result = result[len(prefix):].strip()
        _set_cache(section_text[:5000], "summary", cache_model, result)

    return result


def build_raptor_summaries(docs: list[Document], min_chunks: int = 3,
                           max_input_chunks: int = 15,
                           model: str = None,
                           progress_callback=None) -> list[Document]:
    """
    RAPTOR simplifié : génère un chunk-résumé pour chaque section
    suffisamment longue (≥ min_chunks chunks).

    Le résumé est indexé comme un Document supplémentaire avec
    chunk_type="summary", ce qui permet :
    - De répondre aux questions larges/générales avec une vue d'ensemble
    - D'améliorer le retrieval quand la question couvre plusieurs sous-sections

    Args:
        docs: liste de Documents (chunks) issus du chunking
        min_chunks: nombre min de chunks dans une section pour générer un résumé
        max_input_chunks: nombre max de chunks concaténés pour le prompt
        model: modèle Ollama pour la génération
        progress_callback: callable(current, total) pour le suivi

    Returns:
        Liste de Documents résumés (à ajouter aux chunks existants).
    """
    # Regrouper les chunks par section_idx
    sections: dict[int, list[Document]] = {}
    for doc in docs:
        sec_idx = doc.metadata.get("section_idx", -1)
        sections.setdefault(sec_idx, []).append(doc)

    # Filtrer les sections assez longues
    eligible = {k: v for k, v in sections.items() if len(v) >= min_chunks}

    if not eligible:
        logger.debug("[raptor] Aucune section éligible pour un résumé.")
        return []

    logger.info("[raptor] %d sections éligibles (≥%d chunks) sur %d sections totales",
                len(eligible), min_chunks, len(sections))

    summaries: list[Document] = []
    total = len(eligible)

    for progress_idx, (sec_idx, sec_docs) in enumerate(sorted(eligible.items())):
        # Construire le texte concaténé de la section (limité à max_input_chunks)
        sec_docs_limited = sec_docs[:max_input_chunks]
        section_text = "\n\n---\n\n".join(d.page_content for d in sec_docs_limited)

        # Récupérer heading et breadcrumb de la section.
        heading = next((d.metadata.get("heading", "") for d in sec_docs if d.metadata.get("heading")), "")
        breadcrumb = next((d.metadata.get("breadcrumb", "") for d in sec_docs if d.metadata.get("breadcrumb")), "")
        source = sec_docs[0].metadata.get("source", "unknown")
        page_number = sec_docs[0].metadata.get("page_number")

        # Générer le résumé
        summary_text = generate_section_summary(section_text, heading=heading, model=model)

        if summary_text and len(summary_text) > 50:
            # Construire le préfixe du résumé
            prefix_parts = []
            if breadcrumb:
                prefix_parts.append(f"[{breadcrumb}]")
            if heading:
                prefix_parts.append(f"## {heading}")
            prefix_parts.append(f"**[Résumé de section — {len(sec_docs)} chunks]**\n")
            prefix = "\n".join(prefix_parts)

            full_content = f"{prefix}\n{summary_text}"

            doc_id = make_doc_id(full_content, source, f"summary_{sec_idx}")
            meta = {
                "id": doc_id,
                "chunk_idx": 0,
                "section_idx": sec_idx,
                "source": source,
                "chunk_type": "summary",
                "chunking_mode": sec_docs[0].metadata.get("chunking_mode", ""),
                "summary_num_chunks": len(sec_docs),
                "keywords": [],
                "keywords_str": "",
                "questions": [],
                "questions_str": "",
            }
            if heading:
                meta["heading"] = heading
            if breadcrumb:
                meta["breadcrumb"] = breadcrumb
            if page_number is not None:
                meta["page_number"] = page_number

            summaries.append(Document(page_content=full_content, metadata=meta))
            logger.debug("[raptor] sec %s: résumé OK (%d chars) — %s", sec_idx, len(summary_text), heading or "(sans titre)")

        if progress_callback:
            progress_callback(progress_idx + 1, total)

    logger.info("[raptor] %d résumés générés", len(summaries))
    return summaries
