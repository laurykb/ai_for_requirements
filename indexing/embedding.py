"""Génération d'embeddings et indexation dans le magasin vectoriel."""

import os

from nlp.ollama_embedding import OllamaEmbedding
from env_config import COLLECTION_NAME, HYPE_ENABLED, HYPE_MAX_QUESTIONS, CONTEXT_HEADERS_ENABLED
from retrieval.vector_store import get_vector_store
from utils.logging_config import get_logger

logger = get_logger("rag.embedding")

# Au-delà de ce ratio d'embeddings invalides, on considère un problème systémique
# (Ollama indisponible, modèle cassé) et on abandonne plutôt que de tronquer le doc.
_INVALID_EMBED_ABORT_RATIO = 0.5
_INVALID_EMBED_RETRIES = 2
_MAX_EMBED_TEXT_CHARS = max(1000, int(os.environ.get("EMBED_MAX_TEXT_CHARS", "8000")))
_ADAPTIVE_RETRY_MIN_CHARS = 1000


def _sanitize_chroma_metadata(meta):
    """Retourne des métadonnées scalaires compatibles avec Chroma.

    Mongo conserve les listes structurées sur les chunks. L'index vectoriel n'en
    a besoin que pour l'affichage/retrieval et certaines versions de Chroma
    refusent notamment les listes vides (cas normal pour quality_reasons sur un
    chunk accepté).
    """
    sanitized = dict(meta)
    separators = {
        "questions": " | ",
        "quality_reasons": ", ",
    }
    for key, value in list(sanitized.items()):
        if value is None:
            sanitized.pop(key, None)
        elif isinstance(value, dict):
            if key == "entities":
                from nlp.ner_extractor import entities_to_str
                sanitized[key] = entities_to_str(value)
            else:
                sanitized[key] = str(value)
        elif isinstance(value, (list, tuple, set)):
            separator = separators.get(key, ", ")
            sanitized[key] = separator.join(str(item) for item in value)
    return sanitized


def _prefix_header(text, breadcrumb):
    """Préfixe [breadcrumb] au texte à embarquer si absent (idempotent)."""
    if not (CONTEXT_HEADERS_ENABLED and breadcrumb):
        return text
    tag = f"[{breadcrumb}]"
    return text if tag in text else f"{tag}\n{text}"


def _fit_embedding_text(text):
    """Borne l'entrée modèle sans tronquer le document restitué au retrieval."""
    if len(text) <= _MAX_EMBED_TEXT_CHARS:
        return text, False
    separator = "\n[… contenu intermédiaire omis pour l'embedding …]\n"
    budget = _MAX_EMBED_TEXT_CHARS - len(separator)
    head = int(budget * 0.75)
    return text[:head] + separator + text[-(budget - head):], True


def _adaptive_retry_text(text: str, attempt: int) -> str:
    """Réduit une entrée refusée par le modèle sans modifier le document source."""
    if len(text) <= _ADAPTIVE_RETRY_MIN_CHARS:
        return text
    ratio = max(0.5, 0.875 - (0.125 * attempt))
    target = max(_ADAPTIVE_RETRY_MIN_CHARS, int(len(text) * ratio))
    separator = "\n[… entrée réduite après refus du modèle d'embedding …]\n"
    budget = max(1, target - len(separator))
    head = int(budget * 0.75)
    return text[:head] + separator + text[-(budget - head):]


def build_embedding_units(docs, hype_enabled=None, hype_max_questions=None):
    """Unités à indexer : 1 vecteur contenu par chunk (+ vecteurs HyPE par question
    pointant vers le parent si HyPE actif). Pur : aucune dépendance Ollama/Mongo.

    `hype_enabled`/`hype_max_questions` : None = réglages globaux (.env) ;
    surcharge locale possible (ex. baseline LynX, HyPE élastique par corpus)."""
    if hype_enabled is None:
        hype_enabled = HYPE_ENABLED
    if hype_max_questions is None:
        hype_max_questions = HYPE_MAX_QUESTIONS
    units = []
    for d in docs:
        content = d.page_content.strip()
        if not content:
            continue
        meta = dict(d.metadata)
        cid = meta["id"]
        breadcrumb = meta.get("breadcrumb", "")
        embed_text, truncated = _fit_embedding_text(_prefix_header(content, breadcrumb))
        if truncated:
            meta["embedding_truncated"] = True
            meta["embedding_original_chars"] = len(content)
        units.append({
            "id": cid,
            "document": content,
            "embed_text": embed_text,
            "metadata": meta,
        })
        if hype_enabled:
            questions = [q for q in (meta.get("questions") or []) if q and q.strip()]
            for i, q in enumerate(questions[:hype_max_questions]):
                hmeta = dict(meta)
                hmeta["chunk_type"] = "hype_question"
                hmeta["parent_id"] = cid
                hmeta["id"] = cid  # résout vers le parent en aval
                embed_text, truncated = _fit_embedding_text(
                    _prefix_header(q.strip(), breadcrumb)
                )
                if truncated:
                    hmeta["embedding_truncated"] = True
                    hmeta["embedding_original_chars"] = len(q.strip())
                units.append({
                    "id": f"{cid}::hype::{i}",
                    "document": content,
                    "embed_text": embed_text,
                    "metadata": hmeta,
                })
    return units


def build_embeddings(docs, hype_enabled=None, hype_max_questions=None):
    """
    Génère les embeddings (vecteurs) pour une liste de Documents à l'aide du modèle OllamaEmbedding.

    Stratégie d'embedding (voir build_embedding_units pour le détail) :
    - 1 vecteur "contenu" par chunk : embarque page_content (préfixé du
      breadcrumb en embed_text si CONTEXT_HEADERS_ENABLED), jamais les questions.
    - Si HYPE_ENABLED : +1 vecteur par question générée (HyPE), qui embarque la
      question mais pointe (via metadata["id"]) vers le chunk parent — un hit
      sur la question retourne directement le contenu du parent.

    Il peut donc y avoir plusieurs unités (texte + vecteur + métadonnées + id)
    par chunk source ; les listes retournées sont alignées par unité, pas par chunk.

    Args:
        docs (list): Liste d'objets Document (doivent avoir .page_content et .metadata)

    Returns:
        tuple: (texts, vecs, metadatas, ids)
            - texts (list[str]): Texte "document" restitué (contenu du chunk, jamais les questions)
            - vecs (list[list[float]]): Embeddings vectoriels
            - metadatas (list[dict]): Métadonnées associées à chaque unité
            - ids (list[str]): Identifiants uniques de chaque unité (Chroma)
    """
    model = OllamaEmbedding(role="embed_ingest")

    # Filtrer les documents vides (pas de texte = pas d'embedding utile)
    docs = [d for d in docs if d.page_content.strip()]

    # Une unité par vecteur à produire : 1 par chunk (contenu) + éventuellement
    # 1 par question HyPE (pointant vers le parent). Voir build_embedding_units.
    units = build_embedding_units(docs, hype_enabled=hype_enabled,
                                  hype_max_questions=hype_max_questions)
    texts = [u["document"] for u in units]
    texts_to_embed = [u["embed_text"] for u in units]
    raw_metadatas = [u["metadata"] for u in units]
    ids = [u["id"] for u in units]

    vecs = model.embed_documents(texts_to_embed)

    # Détecter les embeddings invalides (vides ou tout-zéro = échec côté modèle).
    def _invalid(vec):
        return not vec or all(v == 0.0 for v in vec)

    invalid_idx = [i for i, vec in enumerate(vecs) if _invalid(vec)]
    # Une requête isolée peut échouer pendant un chargement de modèle ou sous
    # pression VRAM alors que le reste du lot réussit. Retenter uniquement ces
    # unités évite de publier un index LynX partiel et de recalculer les milliers
    # de vecteurs déjà valides.
    for attempt in range(_INVALID_EMBED_RETRIES):
        if not invalid_idx:
            break
        logger.warning(
            "Nouvelle tentative d'embedding pour %d unité(s) invalide(s) (%d/%d)",
            len(invalid_idx), attempt + 1, _INVALID_EMBED_RETRIES,
        )
        for i in invalid_idx:
            retry_text = _adaptive_retry_text(texts_to_embed[i], attempt)
            vecs[i] = model.embed_query(retry_text)
            if not _invalid(vecs[i]) and retry_text != texts_to_embed[i]:
                raw_metadatas[i]["embedding_adaptive_retry"] = attempt + 1
                raw_metadatas[i]["embedding_retry_chars"] = len(retry_text)
        invalid_idx = [i for i in invalid_idx if _invalid(vecs[i])]

    if invalid_idx:
        ratio = len(invalid_idx) / max(1, len(vecs))
        sample_ids = [raw_metadatas[i].get("id", "<unknown>") for i in invalid_idx[:10]]
        if ratio > _INVALID_EMBED_ABORT_RATIO:
            # Trop d'échecs -> problème systémique, on abandonne sans rien indexer.
            raise RuntimeError(
                f"Embeddings invalides pour {len(invalid_idx)}/{len(vecs)} documents "
                f"({int(ratio * 100)}%) - problème systémique probable (Ollama/modèle). "
                f"Exemples : {sample_ids}. Vérifiez le service Ollama et relancez l'ingestion."
            )
        # Sinon : on retire les quelques chunks en échec et on indexe le reste.
        logger.warning(
            "%d/%d embeddings invalides - chunks ignorés (ex: %s)",
            len(invalid_idx), len(vecs), sample_ids,
        )
        keep = [i for i in range(len(vecs)) if i not in set(invalid_idx)]
        texts = [texts[i] for i in keep]
        raw_metadatas = [raw_metadatas[i] for i in keep]
        ids = [ids[i] for i in keep]
        vecs = [vecs[i] for i in keep]

    # Chroma n'accepte pas les objets complexes dans metadata.
    metadatas = [_sanitize_chroma_metadata(meta) for meta in raw_metadatas]

    return texts, vecs, metadatas, ids


def index_chroma(ids, texts, metadatas, embeddings, collection_name=COLLECTION_NAME,
                 clean_collection=True, replace_source=None):
    """
    Indexe (ids, textes, métadonnées, embeddings) dans le magasin vectoriel abstrait.

    Anti-doublons :
    - clean_collection=True : vide TOUTE la collection avant d'ajouter (rebuild
      complet — scripts/outils seulement, jamais l'ingestion d'UN document).
    - replace_source=<nom> : ne retire que les vecteurs de CE document avant
      d'ajouter — les autres documents restent interrogeables (multi-document).

    Le nom historique `index_chroma` est conservé (appelé par l'ingestion).

    Args:
        ids (list[str]): Identifiants uniques des chunks
        texts (list[str]): Textes des chunks
        metadatas (list[dict]): Métadonnées associées à chaque chunk
        embeddings (list[list[float]]): Embeddings vectoriels
        collection_name (str): Nom de la collection (défaut: COLLECTION_NAME)
        clean_collection (bool): Vider la collection avant l'indexation
        replace_source (str|None): purge ciblée des vecteurs d'une source

    Returns:
        VectorStore: le magasin vectoriel contenant les données indexées
    """
    store = get_vector_store(collection_name)
    if clean_collection:
        store.reset()  # supprime + recrée (métrique cosine), anti-doublons
    if not ids:
        # Ne jamais appeler add([]) : Chroma lève « Expected Embeddings to be
        # non-empty list ». Rien à indexer -> on s'arrête là, sans casser.
        logger.warning("index_chroma : aucun embedding à indexer (ids vides).")
        return store
    if replace_source:
        store.replace_source(replace_source, ids, texts, metadatas, embeddings)
    else:
        store.add(ids=ids, documents=texts, metadatas=metadatas, embeddings=embeddings)
    return store
