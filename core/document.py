"""Conteneur minimal d'un passage de document (remplace langchain_core.documents).

Le pipeline ne se sert que de deux champs : le texte (`page_content`) et ses
métadonnées (`metadata`). Une petite dataclass suffit, sans dépendance externe.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Document:
    page_content: str = ""
    metadata: dict = field(default_factory=dict)
