"""Construction/chargement du vocabulaire métier du corpus."""

import re
import json
from pathlib import Path
from collections import Counter

from env_config import VOCAB_JSON_PATH

# Expression régulière pour extraire les mots et acronymes (mots, tirets, apostrophes)
TOKEN_RE = re.compile(r"\b[\w\-’']+\b", flags=re.UNICODE)  # garde l'apostrophe typographique


def build_vocab(docs, top_k_terms=3000):
    """
    Construit un vocabulaire de mots fréquents et d'acronymes à partir d'une liste de documents.
    - Parcourt chaque document et extrait tous les mots.
    - Ajoute les acronymes (suite de lettres majuscules, 2 à 10 caractères) dans un ensemble dédié.
    - Compte la fréquence de chaque mot (en minuscules) et garde les top_k_terms plus fréquents.
    Args:
        docs (list): Liste d'objets Document (doivent avoir .page_content)
        top_k_terms (int): Nombre maximum de mots fréquents à retenir
    Returns:
        tuple: (vocabulaire, acronymes) sous forme d'ensembles
    """
    words = []
    acronyms = set()
    for doc in docs:
        text = getattr(doc, "page_content", "") or ""
        for token in TOKEN_RE.findall(text):
            if token.isupper() and token.isalpha() and 2 <= len(token) <= 10:
                acronyms.add(token)
            words.append(token.lower())
    vocab = {word for word, _ in Counter(words).most_common(top_k_terms)}
    return vocab, acronyms


def save_vocab(vocabulaire, acronymes, chemin=None, path=None):
    """
    Sauvegarde le vocabulaire et les acronymes dans un fichier JSON.
    Args:
        vocabulaire (set): Ensemble des mots fréquents
        acronymes (set): Ensemble des acronymes
        chemin (str | None): Chemin du fichier (défaut: config.VOCAB_JSON_PATH)
        path (str | None): Alias de chemin
    """
    if chemin is None and path is not None:
        chemin = path
    if chemin is None:
        chemin = str(VOCAB_JSON_PATH)
    out = Path(chemin)
    out.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "vocab_terms": sorted(vocabulaire),
        "acronyms": sorted(acronymes)
    }
    return out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_vocab(chemin=None):
    """
    Charge le vocabulaire et les acronymes depuis un fichier JSON.
    Args:
        chemin (str | None): Fichier à charger (défaut: config.VOCAB_JSON_PATH)
    Returns:
        tuple: (vocabulaire, acronymes) sous forme d'ensembles ; (set(), set()) si fichier absent
    """
    p = Path(chemin) if chemin is not None else VOCAB_JSON_PATH
    if not p.is_file():
        return set(), set()
    data = json.loads(p.read_text(encoding="utf-8"))
    return set(data.get("vocab_terms", [])), set(data.get("acronyms", []))
