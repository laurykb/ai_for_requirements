# retrieval/graph_retrieve.py
"""
Étape 8 — GraphRAG : retrieval via traversée du graphe d'entités.

Quand une question arrive :
1) Extraire les entités de la question (NER)
2) Trouver ces entités dans le graphe
3) Traverser les voisins (1-2 hops) pour découvrir des entités liées
4) Récupérer les chunk_ids associés aux nœuds et arêtes traversés
5) Retourner ces chunks comme candidats supplémentaires pour la fusion RRF

Cela permet de retrouver des chunks qui ne matchent ni en sémantique ni en BM25
mais qui sont connectés par des relations d'entités.
"""

import re
import networkx as nx
from nlp.ner_extractor import extract_entities
from env_config import MONGO_URI, MONGO_DB
from utils.logging_config import get_logger

logger = get_logger("rag.graph")

_mongo_client = None


def _get_chunks_collection():
    """Retourne la collection Mongo des chunks (singleton)."""
    global _mongo_client
    if _mongo_client is None:
        from pymongo import MongoClient
        _mongo_client = MongoClient(MONGO_URI)
    return _mongo_client[MONGO_DB]["chunks"]


def _extract_query_entity_names(query_entities: dict) -> list[str]:
    """Aplati les entités NER de la query en noms bruts."""
    raw_names = []
    for ents in query_entities.values():
        if isinstance(ents, list):
            raw_names.extend(ents)
    return raw_names


def _fallback_keywords(query: str) -> list[str]:
    """Extrait quelques mots-clés robustes quand le NER échoue."""
    stopwords = {
        "quoi", "quels", "quelles", "quel", "quelle", "dont", "comment",
        "pourquoi", "combien", "que", "qui", "quand", "où", "est", "sont",
        "les", "des", "une", "pour", "dans", "avec", "par", "sur", "du",
        "de", "le", "la", "au", "aux", "ce", "se", "sa", "son", "ses",
        "the", "and", "for", "what", "which", "how", "where", "when",
        "document", "partie", "section", "chapitre", "page", "objet",
        "parle", "dit", "décrit", "contient", "traite",
    }
    return [
        w
        for w in re.sub(r"[^\w\s]", " ", query.lower()).split()
        if w not in stopwords and len(w) >= 4
    ]


def graph_retrieve(G: nx.DiGraph, query: str,
                   max_hops: int = 2,
                   max_chunks: int = 10) -> tuple[list[str], dict]:
    """
    Retrouve des chunk_ids pertinents via traversée du graphe.

    Args:
        G: graphe d'entités (NetworkX DiGraph)
        query: question utilisateur
        max_hops: profondeur de traversée (1 = voisins directs, 2 = voisins de voisins)
        max_chunks: nombre max de chunk_ids à retourner

    Returns:
        (chunk_ids, chunk_scores) — ids triés par pertinence + dict id->score
    """
    if G is None or G.number_of_nodes() == 0:
        return [], {}

    # 1) Extraire les entités de la question
    query_entities = extract_entities(query)
    if not query_entities:
        return [], {}

    raw_names = _extract_query_entity_names(query_entities)

    if not raw_names:
        return [], {}

    # 2) Trouver les entités de la question qui existent dans le graphe
    matched_nodes = []
    for entity in raw_names:
        if G.has_node(entity):
            matched_nodes.append(entity)
        else:
            # Matching partiel : chercher les nœuds qui contiennent l'entité
            for node in G.nodes():
                if entity.lower() in node.lower() or node.lower() in entity.lower():
                    matched_nodes.append(node)

    matched_nodes = list(set(matched_nodes))  # Dédoublonner

    if not matched_nodes:
        # Fallback mots-clés pour les requêtes où le NER ne trouve rien.
        query_words = _fallback_keywords(query)
        if query_words:
            for word in query_words[:5]:  # limiter pour ne pas trop diluer
                for node in G.nodes():
                    if word in node.lower():
                        matched_nodes.append(node)
            matched_nodes = list(set(matched_nodes))
            if matched_nodes:
                logger.debug("[graph] Fallback mots-clés '%s' → %d nœuds", query_words[:5], len(matched_nodes))

    if not matched_nodes:
        return [], {}

    logger.debug("[graph] Entités matchées dans le graphe : %s", matched_nodes[:10])

    # 3) Traverser le graphe pour collecter les chunk_ids
    #    Score = pondéré par la distance (hop 0 = entité directe, hop 1 = voisin, etc.)
    chunk_scores: dict[str, float] = {}

    for start_node in matched_nodes:
        # Hop 0 : chunks de l'entité elle-même
        node_data = G.nodes.get(start_node, {})
        for cid in node_data.get("chunk_ids", set()):
            chunk_scores[cid] = chunk_scores.get(cid, 0) + 3.0  # poids fort

        # Hop 1+ : parcours BFS limité
        visited = {start_node}
        frontier = [start_node]

        for hop in range(1, max_hops + 1):
            next_frontier = []
            hop_weight = 1.0 / hop  # Poids décroissant avec la distance

            for node in frontier:
                # Voisins sortants et entrants
                neighbors = set(G.successors(node)) | set(G.predecessors(node))
                for neighbor in neighbors:
                    if neighbor in visited:
                        continue
                    visited.add(neighbor)
                    next_frontier.append(neighbor)

                    # Chunks du voisin
                    n_data = G.nodes.get(neighbor, {})
                    for cid in n_data.get("chunk_ids", set()):
                        chunk_scores[cid] = chunk_scores.get(cid, 0) + hop_weight

                    # Chunks de l'arête (si relation typée)
                    if G.has_edge(node, neighbor):
                        edge_data = G[node][neighbor]
                        rel = edge_data.get("relation", "co_occurrence")
                        edge_weight = edge_data.get("weight", 1)
                        # Les relations typées valent plus que les co-occurrences
                        rel_bonus = 1.5 if rel != "co_occurrence" else 0.5
                        for cid in edge_data.get("chunk_ids", set()):
                            chunk_scores[cid] = chunk_scores.get(cid, 0) + hop_weight * rel_bonus * min(edge_weight, 3)

            frontier = next_frontier

    # 4) Trier par score décroissant et limiter
    sorted_chunks = sorted(chunk_scores.items(), key=lambda x: x[1], reverse=True)
    result = [cid for cid, score in sorted_chunks[:max_chunks]]

    logger.debug("[graph] %d chunks trouvés via le graphe (scores: %s)",
                 len(result), [f"{s:.1f}" for _, s in sorted_chunks[:5]])

    return result, chunk_scores  # retourne aussi les scores pour le lookup


def graph_retrieve_to_lookup(G: nx.DiGraph, query: str,
                             chunks_collection,
                             max_hops: int = 2,
                             max_chunks: int = 10) -> tuple[list[str], dict]:
    """
    Version compatible avec le pipeline RRF :
    retourne (ids, lookup) au même format que semantic et BM25.

    Args:
        G: graphe d'entités
        query: question utilisateur
        chunks_collection: collection MongoDB "chunks" pour récupérer le contenu
        max_hops: profondeur de traversée
        max_chunks: nombre max de chunks

    Returns:
        (ids, lookup) compatible avec fuse_with_rrf
    """
    chunk_ids, chunk_scores = graph_retrieve(G, query, max_hops=max_hops, max_chunks=max_chunks)

    if not chunk_ids:
        return [], {}

    col = _get_chunks_collection()

    ids = []
    lookup = {}

    # Récupérer tous les chunks en une seule requête MongoDB (anti N+1)
    docs_found = {
        doc["_id"]: doc
        for doc in col.find({"_id": {"$in": chunk_ids}})
    }

    # Reconstruire dans l'ordre du score graphe (chunk_ids est déjà trié par score)
    for cid in chunk_ids:
        doc = docs_found.get(cid)
        if doc:
            ids.append(cid)
            # Récupérer le score graphe correspondant depuis sorted_chunks
            g_score = chunk_scores.get(cid, 1.0)
            content = doc.get("content", "")
            lookup[cid] = {
                "id":    cid,
                "doc":   content,   # "doc" = clé attendue par le pipeline RRF + cross-encoder
                "text":  content,   # alias de compatibilité
                "meta": {
                    "source":            doc.get("source", ""),
                    "page_number":       doc.get("page_number"),
                    "heading":           doc.get("heading", ""),
                    "breadcrumb":        doc.get("breadcrumb", ""),
                    "chunk_type":        doc.get("chunk_type", "chunk"),
                    "section_idx":       doc.get("section_idx"),
                    "chunk_idx":         doc.get("chunk_idx"),
                    "keywords_str":      doc.get("keywords_str", ""),
                    "questions_str":     doc.get("questions_str", ""),
                    "entities_str":      doc.get("entities_str", ""),
                    "table_description": doc.get("table_description", ""),
                },
                "graph_score": g_score,  # score réel issu de la traversée du graphe
                "sim_est":     min(g_score / 10.0, 1.0),  # normalisé pour RRF
            }

    logger.debug("[graph] %d/%d chunks récupérés depuis MongoDB", len(ids), len(chunk_ids))
    return ids, lookup
