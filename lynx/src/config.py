"""Configuration du framework d'analyse d'impact d'exigences.

Cœur léger, in-process : pas de Redis, pas de Neo4j.
Seul service externe optionnel : Ollama (pour les agents LLM).
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = PROJECT_ROOT / "skills"
DATA_DIR = PROJECT_ROOT / "corpus"
DEFAULT_CORPUS = DATA_DIR / "corpus.json"

# --- LLM (API compatible OpenAI) ------------------------------------------
# Compatible avec toute API OpenAI-compatible via LLM_BASE_URL.
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "mistral-small3.2:latest")
LLM_MODEL = os.environ.get("LLM_MODEL", OLLAMA_MODEL)
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "ollama")  # ignoré par Ollama/vLLM local
LLM_TIMEOUT_SECONDS = int(os.environ.get("LLM_TIMEOUT_SECONDS", "120"))
# Concurrence des appels LLM : à monter selon le serveur (vLLM batch >> Ollama).
LLM_MAX_CONCURRENCY = int(os.environ.get("LLM_MAX_CONCURRENCY", "16"))
# Cache des réponses LLM (reproductibilité : mêmes entrées -> même verdict).
LLM_CACHE = os.environ.get("LLM_CACHE", "1") != "0"
# Vote self-consistency sur les verdicts BLOQUANT (1 = désactivé ; 3 = recommandé).
LLM_VOTE = int(os.environ.get("LLM_VOTE", "1"))
# Débat contradictoire (avocat + juge) sur les BLOQUANT sémantiques : un verdict
# réfuté est rétrogradé WARNING (jamais supprimé). LYNX_DEBATE=0 pour couper.
DEBATE_ENABLED = os.environ.get("LYNX_DEBATE", "1") != "0"
# Estimation du temps (minutes) de relecture/test évité par défaut capté tôt
# (à la conception) plutôt que tard (à la remontée du V). Pour le calcul de ROI.
ROI_MINUTES_PER_CATCH = int(os.environ.get("ROI_MINUTES_PER_CATCH", "45"))

# --- Embeddings (pré-filtre déterministe redondance / similarité) ----------
EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-m3:latest")
EMBED_BASE_URL = os.environ.get("EMBED_BASE_URL", LLM_BASE_URL)  # même endpoint OpenAI
# Seuil de similarité cosinus au-delà duquel deux exigences sont quasi-doublons.
EMBED_DUP_THRESHOLD = float(os.environ.get("EMBED_DUP_THRESHOLD", "0.95"))
# En-dessous de ce seuil, deux exigences sont clairement distinctes : on évite
# alors l'appel LLM de redondance (routeur -> gain de latence).
EMBED_DISTINCT_THRESHOLD = float(os.environ.get("EMBED_DISTINCT_THRESHOLD", "0.62"))
# Zone « proche mais non reliée » : au-delà de ce seuil (et sous le doublon), une
# exigence non voisine est un candidat d'impact latent (à faire trancher au LLM).
EMBED_LATENT_THRESHOLD = float(os.environ.get("EMBED_LATENT_THRESHOLD", "0.70"))
# Nombre max de candidats trans-matrice soumis au LLM (impact latent / co-références).
LATENT_TOPK = int(os.environ.get("LATENT_TOPK", "5"))
EMBED_DISABLED = os.environ.get("EMBED_DISABLE", "") == "1"
# Mettre LLM_DISABLE=1 (ou OLLAMA_DISABLE=1) pour ignorer les agents LLM.
LLM_DISABLED = os.environ.get("LLM_DISABLE", os.environ.get("OLLAMA_DISABLE", "")) == "1"
# Si Ollama est injoignable, les agents LLM renvoient un statut SKIPPED
# et l'analyse déterministe (allocation, propagation) continue de fonctionner.

# --- Niveaux du cycle en V ------------------------------------------------
MAX_NIVEAU = 5  # L0 (besoin) .. L5 (réalisation/test)

# --- Analyse d'allocation -------------------------------------------------
# Tolérance relative quand on compare une somme d'enfants à un budget parent.
ALLOCATION_TOLERANCE = 0.0  # 0 % : tout dépassement est signalé
