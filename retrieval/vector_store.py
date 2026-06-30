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
        propre langage (`$in` côté Chroma, `MatchAny` côté Qdrant).
        """

    @abstractmethod
    def count(self) -> int:
        """Nombre de vecteurs indexés (diagnostic/UI)."""

    @abstractmethod
    def reset(self) -> None:
        """Vide la collection et la recrée (réingestion propre, anti-doublons)."""


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

    # -- lecture ---------------------------------------------------------------
    def query(self, embedding, n_results, source_filter=None) -> list[VectorHit]:
        kwargs = dict(
            query_embeddings=[embedding],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )
        srcs = normalize_sources(source_filter)
        if srcs:
            # `$in` couvre 1 ou N documents de façon uniforme.
            kwargs["where"] = {"source": {"$in": srcs}}
        res = self._coll().query(**kwargs)
        return _hits_from_chroma(res)

    def count(self) -> int:
        try:
            return self._coll().count()
        except Exception as e:
            logger.debug("count() indisponible : %s", e)
            return 0


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
