"""
Abstraction du magasin vectoriel.

Le reste du pipeline ne dépend que de l'interface VectorStore (add / query / count /
reset) et d'un résultat normalisé (VectorHit), jamais de la forme de réponse propre à
un backend. Changer de backend = un nouvel adaptateur + VECTOR_STORE_BACKEND dans .env,
sans toucher au pipeline. Deux adaptateurs fournis : Chroma (embarqué, défaut) et Qdrant.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from utils.logging_config import get_logger
from env_config import CHROMA_PATH, COLLECTION_NAME, VECTOR_STORE_BACKEND, QDRANT_LOCATION

logger = get_logger("rag.vectorstore")

# Configuration HNSW (cosine) appliquée à la création de la collection — identique à
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
              source_filter: str | None = None) -> list[VectorHit]:
        """Recherche les `n_results` plus proches voisins d'un vecteur de requête.

        `source_filter` restreint à un document (égalité sur la métadonnée `source`).
        Chaque backend traduit ce filtre dans son propre langage.
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

    # ── connexion paresseuse ──────────────────────────────────────────────────
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

    # ── écriture ──────────────────────────────────────────────────────────────
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

    # ── lecture ───────────────────────────────────────────────────────────────
    def query(self, embedding, n_results, source_filter=None) -> list[VectorHit]:
        kwargs = dict(
            query_embeddings=[embedding],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )
        if source_filter:
            kwargs["where"] = {"source": {"$eq": source_filter}}
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


def _qdrant_point_id(s: str) -> str:
    """Qdrant exige des IDs entiers ou UUID ; on dérive un UUID déterministe de l'id
    d'origine (string) et on conserve l'id réel dans le payload (`_id`)."""
    import uuid
    return str(uuid.uuid5(uuid.NAMESPACE_URL, str(s)))


class QdrantVectorStore(VectorStore):
    """Adaptateur Qdrant — 2e backend, prouve le « swap par config ».

    `location` : ':memory:' (éphémère), un chemin local (mono-process) ou une URL
    'http://host:6333' (serveur client-serveur, scalable, multi-process en prod).
    La MÊME interface que Chroma : aucun changement de pipeline pour basculer.
    """
    backend = "qdrant"

    def __init__(self, collection_name: str = COLLECTION_NAME, location: str = None, client=None):
        self.collection_name = collection_name
        self._location = location or QDRANT_LOCATION
        self._client = client  # injectable (tests)

    def _get_client(self):
        if self._client is None:
            from qdrant_client import QdrantClient
            loc = self._location
            if loc == ":memory:":
                self._client = QdrantClient(location=":memory:")
            elif loc.startswith(("http://", "https://")):
                self._client = QdrantClient(url=loc)
            else:
                self._client = QdrantClient(path=loc)
        return self._client

    def _ensure_collection(self, dim: int):
        from qdrant_client.models import Distance, VectorParams
        client = self._get_client()
        names = [c.name for c in client.get_collections().collections]
        if self.collection_name not in names:
            client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )

    def add(self, ids, documents, metadatas, embeddings) -> None:
        from qdrant_client.models import PointStruct
        if not embeddings:
            return
        self._ensure_collection(len(embeddings[0]))
        points = []
        for id_, doc, meta, vec in zip(ids, documents, metadatas, embeddings):
            payload = dict(meta or {})
            payload["document"] = doc
            payload["_id"] = id_  # id d'origine (Chroma-style) conservé
            points.append(PointStruct(id=_qdrant_point_id(id_), vector=list(vec), payload=payload))
        self._get_client().upsert(collection_name=self.collection_name, points=points)

    def query(self, embedding, n_results, source_filter=None) -> list[VectorHit]:
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        flt = None
        if source_filter:
            flt = Filter(must=[FieldCondition(key="source", match=MatchValue(value=source_filter))])
        result = self._get_client().query_points(
            collection_name=self.collection_name, query=list(embedding),
            limit=n_results, query_filter=flt, with_payload=True,
        )
        hits = []
        for p in result.points:
            payload = dict(p.payload or {})
            doc = payload.pop("document", "")
            orig_id = payload.pop("_id", str(p.id))
            # Qdrant renvoie un SCORE de similarité cosine (↑ = proche) ; on le convertit
            # en DISTANCE (↓ = proche) pour rester homogène avec Chroma.
            dist = (1.0 - p.score) if p.score is not None else None
            hits.append(VectorHit(id=orig_id, document=doc, metadata=payload, distance=dist))
        return hits

    def count(self) -> int:
        try:
            return self._get_client().count(self.collection_name).count
        except Exception as e:
            logger.debug("count() Qdrant indisponible : %s", e)
            return 0

    def reset(self) -> None:
        client = self._get_client()
        try:
            client.delete_collection(self.collection_name)
        except Exception:
            pass


# Registre des backends disponibles (bascule via VECTOR_STORE_BACKEND). pgvector = futur.
_BACKENDS = {"chroma": ChromaVectorStore, "qdrant": QdrantVectorStore}


def get_vector_store(collection_name: str = COLLECTION_NAME) -> VectorStore:
    """Fabrique le magasin vectoriel selon `VECTOR_STORE_BACKEND` (.env, défaut: chroma)."""
    backend = (VECTOR_STORE_BACKEND or "chroma").lower()
    cls = _BACKENDS.get(backend)
    if cls is None:
        raise ValueError(
            f"Backend vectoriel inconnu : '{backend}'. Disponibles : {sorted(_BACKENDS)}. "
            "(Qdrant/pgvector : adaptateurs à ajouter dans retrieval/vector_store.py.)"
        )
    return cls(collection_name=collection_name)
