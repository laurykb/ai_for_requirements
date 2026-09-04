"""
Magasin vectoriel (ChromaDB, embarqué).

Le reste du pipeline ne dépend que de l'interface VectorStore (add / query / count /
reset) et d'un résultat normalisé (VectorHit), jamais de la forme de réponse propre au
backend.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from utils.logging_config import get_logger
from utils.sources import normalize_sources
from env_config import CHROMA_PATH, COLLECTION_NAME

logger = get_logger("rag.vectorstore")

# Configuration HNSW (cosine) appliquée à la création de la collection - identique à
# l'historique `index_chroma`, conservée pour ne pas changer la qualité du retrieval.
_CHROMA_HNSW = {"hnsw": {"space": "cosine", "ef_construction": 200, "ef_search": 50}}


@dataclass
class VectorHit:
    """Un résultat de recherche vectorielle, indépendant du backend."""
    id: str
    document: str
    metadata: dict
    distance: float | None = None


class VectorStore(ABC):
    """Contrat minimal d'un magasin vectoriel (lecture + écriture)."""

    @abstractmethod
    def add(self, ids, documents, metadatas, embeddings) -> None:
        """Indexe un lot (ids, textes, métadonnées, vecteurs déjà calculés)."""

    @abstractmethod
    def query(self, embedding: list[float], n_results: int,
              source_filter: "str | list[str] | None" = None) -> list[VectorHit]:
        """Recherche les `n_results` plus proches voisins d'un vecteur de requête.

        `source_filter` restreint la recherche à un OU plusieurs documents
        (appartenance de la métadonnée `source`). Accepte un nom, une liste de
        noms, ou None (= tout l'index). Chaque backend traduit ce filtre dans son
        propre langage (`$in` côté Chroma).
        """

    @abstractmethod
    def count(self) -> int:
        """Nombre de vecteurs indexés (diagnostic/UI)."""

    @abstractmethod
    def reset(self) -> None:
        """Vide la collection et la recrée (réingestion propre, anti-doublons)."""

    def delete_source(self, source: str) -> None:
        """Supprime tous les vecteurs d'UN document (métadonnée `source`).
        Optionnel : les backends qui ne le supportent pas gardent ce no-op
        (le nettoyage se fait alors par `reset()` + réingestion)."""

    def replace_source(self, source: str, ids, documents, metadatas, embeddings) -> None:
        """Remplace une source. Le repli historique reste non transactionnel."""
        self.delete_source(source)
        self.add(ids, documents, metadatas, embeddings)

    def delete_version(self, source: str, version: str) -> None:
        """Supprime une version préparée, si le backend sait la filtrer."""

    def count_version(self, source: str, version: str) -> int | None:
        """Nombre d'unités d'une version, ou None si non supporté."""
        return None

    def source_counts(self) -> dict[str, int] | None:
        """Nombre de vecteurs par source, ou None si non supporté."""
        return None


class ChromaVectorStore(VectorStore):
    """Adaptateur ChromaDB persistant (le backend par défaut, embarqué)."""

    backend = "chroma"

    def __init__(self, collection_name: str = COLLECTION_NAME, path: str = None,
                 collection=None):
        self.collection_name = collection_name
        self._path = str(path) if path else str(CHROMA_PATH)
        self._client = None
        self._collection = collection  # injectable (tests) ou caché après 1re ouverture

    # -- connexion paresseuse --------------------------------------------------
    def _get_client(self):
        if self._client is None:
            import chromadb
            self._client = chromadb.PersistentClient(path=self._path)
        return self._client

    def _coll(self, configuration: dict | None = None):
        if self._collection is None:
            self._collection = self._get_client().get_or_create_collection(
                name=self.collection_name,
                configuration=configuration,
                embedding_function=None,  # embeddings fournis, jamais calculés par Chroma
            )
        return self._collection

    # -- écriture --------------------------------------------------------------
    def reset(self) -> None:
        client = self._get_client()
        try:
            client.delete_collection(self.collection_name)
        except Exception:
            pass  # la collection n'existait pas : sans conséquence
        self._collection = None
        self._coll(configuration=_CHROMA_HNSW)  # recrée avec la métrique cosine

    def add(self, ids, documents, metadatas, embeddings) -> None:
        self._coll(configuration=_CHROMA_HNSW).add(
            ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings
        )

    def delete_source(self, source: str) -> None:
        try:
            self._coll().delete(where={"source": source})
        except Exception as e:
            logger.warning("delete_source(%s) : %s", source, e)

    def delete_version(self, source: str, version: str) -> None:
        try:
            self._coll().delete(where={"$and": [
                {"source": {"$eq": source}},
                {"ingest_version": {"$eq": version}},
            ]})
        except Exception as e:
            logger.warning("delete_version(%s, %s) : %s", source, version, e)

    def count_version(self, source: str, version: str) -> int | None:
        try:
            result = self._coll().get(where={"$and": [
                {"source": {"$eq": source}},
                {"ingest_version": {"$eq": version}},
            ]}, include=["metadatas"])
            return len(result.get("ids") or [])
        except Exception as e:
            logger.warning("count_version(%s, %s) : %s", source, version, e)
            return None

    def replace_source(self, source: str, ids, documents, metadatas, embeddings) -> None:
        """Ajoute la nouvelle version avant de retirer l'ancienne.

        Les identifiants d'une version sont uniques. Si l'ajout échoue, les
        vecteurs déjà ajoutés pour cette tentative sont supprimés et l'ancienne
        version reste intacte.
        """
        coll = self._coll(configuration=_CHROMA_HNSW)
        # Chroma valide strictement la liste ``include`` selon les versions.
        # Les identifiants sont toujours renvoyés ; demander les métadonnées
        # garde cet appel compatible avec les versions qui refusent ``[]``.
        old = coll.get(where={"source": source}, include=["metadatas"]).get("ids", [])
        try:
            coll.add(ids=ids, documents=documents, metadatas=metadatas,
                     embeddings=embeddings)
        except Exception:
            if ids:
                try:
                    coll.delete(ids=list(ids))
                except Exception:
                    pass
            raise
        obsolete = [item_id for item_id in old if item_id not in set(ids)]
        if obsolete:
            coll.delete(ids=obsolete)

    # -- lecture ---------------------------------------------------------------
    def query(self, embedding, n_results, source_filter=None) -> list[VectorHit]:
        kwargs = dict(
            query_embeddings=[embedding],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )
        srcs = normalize_sources(source_filter)
        legacy_version_filter = None
        if srcs:
            # Une source versionnée reste invisible tant que son registre actif
            # n'a pas basculé : la préparation ne perturbe jamais le chat courant.
            if len(srcs) == 1:
                from core.source_versions import active_version
                version = active_version(srcs[0])
                kwargs["where"] = ({"$and": [
                    {"source": {"$eq": srcs[0]}},
                    {"ingest_version": {"$eq": version}},
                ]} if version else {"source": {"$in": srcs}})
                if not version:
                    from core.reserved_sources import LYNX_BASELINE_SOURCE
                    if srcs[0] == LYNX_BASELINE_SOURCE:
                        legacy_version_filter = srcs[0]
                        kwargs["n_results"] = n_results * 10
            else:
                kwargs["where"] = {"source": {"$in": srcs}}
        else:
            # Recherche non scopée : les sources réservées (baseline LynX)
            # ne doivent jamais surgir dans le monde RAG.
            from core.reserved_sources import RESERVED_SOURCES
            kwargs["where"] = {"source": {"$nin": list(RESERVED_SOURCES)}}
        res = self._coll().query(**kwargs)
        hits = _hits_from_chroma(res)
        if legacy_version_filter:
            from core.source_versions import metadata_is_active
            hits = [hit for hit in hits
                    if metadata_is_active(hit.metadata, legacy_version_filter)]
        return hits[:n_results]

    def count(self) -> int:
        try:
            return self._coll().count()
        except Exception as e:
            logger.debug("count() indisponible : %s", e)
            return 0

    def source_counts(self) -> dict[str, int] | None:
        try:
            result = self._coll().get(include=["metadatas"])
            counts: dict[str, int] = {}
            for metadata in result.get("metadatas") or []:
                source = str((metadata or {}).get("source") or "")
                if source:
                    counts[source] = counts.get(source, 0) + 1
            return counts
        except Exception as e:
            logger.warning("source_counts() indisponible : %s", e)
            return None


def _hits_from_chroma(res) -> list[VectorHit]:
    """Normalise la réponse Chroma (listes imbriquées `[0]`) en `list[VectorHit]`."""
    if not res or not res.get("ids") or not res["ids"][0]:
        return []
    ids = res["ids"][0]
    docs = res["documents"][0]
    metas = res["metadatas"][0]
    dists = res.get("distances", [[]])
    dists = dists[0] if dists else None
    hits = []
    for i, item_id in enumerate(ids):
        hits.append(VectorHit(
            id=item_id,
            document=docs[i],
            metadata=metas[i],
            distance=float(dists[i]) if dists is not None else None,
        ))
    return hits


def get_vector_store(collection_name: str = COLLECTION_NAME) -> VectorStore:
    """Fabrique le magasin vectoriel (ChromaDB, embarqué)."""
    return ChromaVectorStore(collection_name=collection_name)
