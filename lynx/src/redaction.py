"""Assistant de rédaction d'exigences (conformité aux règles du guide).

Hors pipeline d'impact : c'est un outil que l'ingénieur déclenche sur le texte
d'une exigence pour vérifier sa formulation et obtenir une réécriture conforme.
Les règles sont chargées depuis ``skills/regles_redaction.md`` (distillées du
guide EN9100) et injectées dans le prompt de l'agent.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict

from . import llm
from .config import SKILLS_DIR


@lru_cache(maxsize=1)
def _rules_text() -> str:
    path = SKILLS_DIR / "regles_redaction.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _prompt() -> str:
    template = (SKILLS_DIR / "redaction_exigence.md").read_text(encoding="utf-8")
    return template.replace("<<REGLES>>", _rules_text())


def check_redaction(texte: str) -> Dict[str, Any]:
    """Vérifie la rédaction d'une exigence ; renvoie conformité + réécriture."""
    if not texte or not texte.strip():
        return {"conforme": False, "violations": [], "score": 0,
                "reecriture": "", "synthese": "Texte vide."}
    # Le label branche le schéma de réponse du registre (schemas.py) et
    # identifie l'agent dans la boîte de verre.
    return llm.call_agent(_prompt(), texte.strip(), label="redaction_exigence")
