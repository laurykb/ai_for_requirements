"""Génération d'embeddings et indexation dans le magasin vectoriel."""

from nlp.ollama_embedding import OllamaEmbedding
from env_config import COLLECTION_NAME
from retrieval.vector_store import get_vector_store
from utils.logging_config import get_logger

logger = get_logger("rag.embedding")

# Au-delà de ce ratio d'embeddings invalides, on considère un problème systémique
# (Ollama indisponible, modèle cassé) et on abandonne plutôt que de tronquer le doc.
_INVALID_EMBED_ABORT_RATIO = 0.5


def build_embeddings(docs):
    """
    Génère les embeddings (vecteurs) pour une liste de Documents à l'aide du modèle OllamaEmbedding.
    
    Stratégie d'embedding (inspirée RAGFlow) :
    - Si le chunk a des questions générées (auto_questions), l'embedding est calculé
      sur les questions plutôt que le contenu brut, car question<->question matching
      est un signal de pertinence plus fort que contenu<->question.
    - Sinon, fallback sur le contenu brut.
    
    Retourne les textes, les vecteurs, les métadonnées et les identifiants associés à chaque chunk.

    Args:
        docs (list): Liste d'objets Document (doivent avoir .page_content et .metadata)

    Returns:
        tuple: (texts, vecs, metadatas, ids)
            - texts (list[str]): Textes des chunks
            - vecs (list[list[float]]): Embeddings vectoriels
            - metadatas (list[dict]): Métadonnées associées à chaque chunk
            - ids (list[str]): Identifiants uniques de chaque chunk
    """
    model = OllamaEmbedding()

    # Filtrer les documents vides (pas de texte = pas d'embedding utile)
    docs = [d for d in docs if d.page_content.strip()]

    texts = [d.page_content.strip() for d in docs]

    # Priorise les questions générées : meilleur signal pour le matching question<->question.
    texts_to_embed = []
    for d in docs:
        questions_str = d.metadata.get("questions_str", "")
        if questions_str and len(questions_str) > 20:
            # Embedding sur les questions générées (meilleur pour le retrieval)
            texts_to_embed.append(questions_str)
        else:
            texts_to_embed.append(d.page_content.strip())
    
    vecs = model.embed_documents(texts_to_embed)

    # Détecter les embeddings invalides (vides ou tout-zéro = échec côté modèle).
    invalid_idx = [i for i, vec in enumerate(vecs) if not vec or all(v == 0.0 for v in vec)]
    if invalid_idx:
        ratio = len(invalid_idx) / max(1, len(vecs))
        sample_ids = [docs[i].metadata.get("id", "<unknown>") for i in invalid_idx[:10]]
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
        docs = [docs[i] for i in keep]
        texts = [texts[i] for i in keep]
        vecs = [vecs[i] for i in keep]

    # Chroma n'accepte pas les objets complexes dans metadata.
    metadatas = []
    for d in docs:
        meta = dict(d.metadata)
        if isinstance(meta.get("keywords"), list):
            meta["keywords"] = ", ".join(meta["keywords"])
        if isinstance(meta.get("questions"), list):
            meta["questions"] = " | ".join(meta["questions"])
        if isinstance(meta.get("entities"), dict):
            from nlp.ner_extractor import entities_to_str
            meta["entities"] = entities_to_str(meta["entities"])
        if isinstance(meta.get("entities_flat"), list):
            meta["entities_flat"] = ", ".join(meta["entities_flat"])
        metadatas.append(meta)
    
    ids = [d.metadata["id"] for d in docs]
    return texts, vecs, metadatas, ids


def index_chroma(ids, texts, metadatas, embeddings, collection_name=COLLECTION_NAME, clean_collection=True):
    """
    Indexe (ids, textes, métadonnées, embeddings) dans le magasin vectoriel abstrait.
    Si clean_collection=True, vide la collection avant d'ajouter (évite les doublons).

    Le nom historique `index_chroma` est conservé (appelé par l'ingestion) ; il écrit
    dans le magasin vectoriel ChromaDB via l'interface VectorStore.

    Args:
        ids (list[str]): Identifiants uniques des chunks
        texts (list[str]): Textes des chunks
        metadatas (list[dict]): Métadonnées associées à chaque chunk
        embeddings (list[list[float]]): Embeddings vectoriels
        collection_name (str): Nom de la collection (défaut: COLLECTION_NAME)
        clean_collection (bool): Vider la collection avant l'indexation (défaut: True)

    Returns:
        VectorStore: le magasin vectoriel contenant les données indexées
    """
    store = get_vector_store(collection_name)
    if clean_collection:
        store.reset()  # supprime + recrée (métrique cosine), anti-doublons
    store.add(ids=ids, documents=texts, metadatas=metadatas, embeddings=embeddings)
    return store




