"""Utilitaires texte pour générer des IDs stables de chunks."""

import hashlib

def normalize_text(t: str) -> str:
    """Normalise un texte en supprimant les espaces superflus."""
    return " ".join((t or "").strip().split())


def make_doc_id(text: str, source: str, chunk_idx: int | None) -> str:
    """
    Fabrique un identifiant stable basé sur contenu, source et index de chunk.
    """
    txt = normalize_text(text)
    parts = []
    if source:
        parts.append(f"source={source}")
    if chunk_idx is not None:
        # Accepte int ou str, formate proprement
        if isinstance(chunk_idx, int):
            parts.append(f"idx={chunk_idx:06d}")
        else:
            parts.append(f"idx={str(chunk_idx)}")
    parts.append(f"text={txt}")

    key = "\n---\n".join(parts)
    # SHA256, tronqué à 16 caractères
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]

