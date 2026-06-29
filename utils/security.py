"""
Garde-fous anti-injection de prompt.

Le risque principal d'un RAG est l'injection indirecte : un chunk ingéré peut
contenir « ignore tes instructions... » et détourner la génération. Défense en
profondeur :
  1) séparation stricte contenu/instructions dans le prompt (cf. llm_answer) ;
  2) détection heuristique de motifs d'injection (ce module) -> log + annotation.

Ce module ne bloque pas (pour éviter les faux positifs sur des questions légitimes) :
il détecte et signale. La neutralisation passe par le cadrage du prompt et l'annotation
des passages suspects.
"""
import re

from utils.logging_config import get_logger

logger = get_logger("rag.security")

# Motifs d'injection courants (FR + EN), insensibles à la casse.
# Approche par proximité (.{0,25}) : tolère les variantes ("ignore all previous instructions").
_PATTERNS = [
    r"ignor[ez]\w*\b.{0,25}\b(instructions|consignes|r[èe]gles|prompt|previous|above|pr[ée]c[èe]dent)",
    r"disregard\b.{0,25}\b(instructions|prompt|rules)",
    r"oubli[ez]\w*\b.{0,25}\b(instructions|consignes|tout|everything)",
    r"forget\b.{0,25}\b(instructions|everything|previous|above)",
    r"tu\s+es\s+(maintenant|d[ée]sormais)\b",
    r"you\s+are\s+now\b",
    r"(reveal|r[ée]v[èe]l[ez]\w*|montre|affiche|divulgue)\b.{0,25}\b(prompt|system|syst[èe]me|instructions)",
    r"system\s+prompt",
    r"prompt\s+syst[èe]me",
    r"(new|nouvelles?)\s+(instructions|consignes)",
    r"\bact\s+as\b|agis\s+comme\b|comporte[\s-]toi\s+comme\b",
    r"\bjailbreak\b|\bDAN\s+mode\b",
    r"prompt\s+injection",
]
_COMPILED = [re.compile(p, re.IGNORECASE) for p in _PATTERNS]


def scan(text: str) -> list[str]:
    """Retourne les fragments d'injection détectés dans un texte (vide si rien)."""
    if not text:
        return []
    hits = []
    for rx in _COMPILED:
        m = rx.search(text)
        if m:
            hits.append(m.group(0).strip()[:60])
    return hits


def scan_query(query: str) -> list[str]:
    """Scanne la requête utilisateur (injection directe). Log si détection."""
    hits = scan(query)
    if hits:
        logger.warning("Injection potentielle dans la requête utilisateur : %s", hits)
    return hits


def scan_chunks(chunks: list[dict]) -> list[int]:
    """Retourne les indices des chunks suspects (injection indirecte). Log si détection."""
    suspicious = [i for i, c in enumerate(chunks) if scan(c.get("doc", "") or "")]
    if suspicious:
        logger.warning("Injection potentielle dans %d chunk(s) récupéré(s) (indices %s)",
                       len(suspicious), suspicious)
    return suspicious
