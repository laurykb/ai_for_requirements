import logging
import time
import requests
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from env_config import EMBED_MODEL, OLLAMA_HOST, EMBED_TIMEOUT_S, OLLAMA_NUM_GPU

logger = logging.getLogger(__name__)

# -- Cache LRU process-level : {(model, hash(text)) -> vector} -----------------
# Évite de rappeler Ollama pour une requête déjà embeddée dans la même session.
# Taille max : 256 entrées (~256 x 1024 floats x 4 bytes ~ 1 MB - négligeable)
_EMBED_CACHE: dict = {}
_EMBED_CACHE_MAX = 256

def _cache_key(model: str, text: str) -> str:
    h = hashlib.md5(text.encode("utf-8", errors="replace")).hexdigest()
    return f"{model}:{h}"


class OllamaEmbedding:
    def __init__(self, model=EMBED_MODEL, base_url=OLLAMA_HOST, max_workers: int = 4,
                 timeout: int = EMBED_TIMEOUT_S):
        self.model = model
        self.base_url = base_url
        self.max_workers = max_workers  # parallélisme pour embed_documents
        self.timeout = timeout  # tolère le swap de modèle à froid (VRAM contrainte)
        self._dim = None  # dimension auto-detectee au premier appel reussi

    def embed_documents(self, texts):
        """Encode plusieurs textes en parallèle (max_workers threads simultanés)."""
        results = [None] * len(texts)
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(self.embed_query, text): i for i, text in enumerate(texts)}
            for future in as_completed(futures):
                idx = futures[future]
                results[idx] = future.result()
        return results

    def embed_query(self, text):
        # Proteger contre les textes vides (Ollama retourne 500)
        if not text or not text.strip():
            return self._zero_vector()

        # -- Cache hit : retour immédiat sans appel réseau ----------------------
        key = _cache_key(self.model, text)
        if key in _EMBED_CACHE:
            return _EMBED_CACHE[key]

        url = f"{self.base_url}/api/embeddings"
        payload = {
            "model": self.model,
            "prompt": text
        }
        # Offload GPU forcé par l'environnement (OLLAMA_NUM_GPU, ex. 0 = CPU
        # quand le GPU est occupé) - aligné sur core.model_router.llm_kwargs.
        if OLLAMA_NUM_GPU is not None:
            payload["options"] = {"num_gpu": OLLAMA_NUM_GPU}
        # Retry : un 500 transitoire (pression VRAM, chargement de modèle) ne doit
        # pas invalider tout un chunk et faire échouer l'ingestion entière.
        last_err = None
        for attempt in range(3):
            try:
                response = requests.post(url, json=payload, timeout=self.timeout)
                response.raise_for_status()
                vec = response.json()["embedding"]
                # Memoriser la dimension au premier succes
                if self._dim is None and vec:
                    self._dim = len(vec)
                # -- Stocker dans le cache (avec LRU basique par FIFO si plein) ----
                if len(_EMBED_CACHE) >= _EMBED_CACHE_MAX:
                    # Retire le premier élément inséré (FIFO)
                    oldest = next(iter(_EMBED_CACHE))
                    del _EMBED_CACHE[oldest]
                _EMBED_CACHE[key] = vec
                return vec
            except requests.exceptions.Timeout as e:
                # Un timeout = le modèle ne se charge pas (VRAM saturée par un autre
                # modèle). Réessayer ne fait que MULTIPLIER l'attente : on échoue vite.
                last_err = e
                break
            except Exception as e:
                last_err = e
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))

        logger.warning(
            "Échec embedding après 3 tentatives (%s chars, timeout=%ss) : %s - "
            "vecteur nul renvoyé (jambe sémantique dégradée). Sur VRAM contrainte, "
            "augmentez EMBED_TIMEOUT_S ou préchargez bge-m3.",
            len(text), self.timeout, last_err,
        )
        return self._zero_vector()

    def _zero_vector(self):
        """Vecteur zero de la bonne dimension (auto-detectee ou 1024 par defaut)."""
        dim = self._dim if self._dim else 1024
        return [0.0] * dim
