"""Schéma de données du framework d'analyse d'impact.

Modèles Pydantic stricts partagés entre l'UI, le moteur d'arbre et les agents.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ActionType(str, Enum):
    """Action utilisateur sur le corpus."""

    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"


class Severity(str, Enum):
    """Gravité d'un constat d'impact."""

    INFO = "INFO"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


class Scope(str, Enum):
    """Axe d'impact analysé."""

    ALLOCATION = "ALLOCATION"   # roll-up budgétaire (somme enfants vs parent)
    HORIZONTAL = "HORIZONTAL"   # T3 — redondance / sur-spécification entre frères
    AVAL = "AVAL"               # propagation déterministe vers les descendants
    AMONT = "AMONT"             # T1 — pertinence / non-cassure vs ancêtres (N+1, N+2…)
    COUVERTURE = "COUVERTURE"   # T2 — complétude : le parent reste-t-il couvert ?
    STRUCTURE = "STRUCTURE"     # validité structurelle de l'action elle-même


class VerifMethod(str, Enum):
    """Méthode de vérification IADT (EN9100)."""

    INSPECTION = "I"
    ANALYSE = "A"
    DEMONSTRATION = "D"
    TEST = "T"


class LinkType(str, Enum):
    """Type de lien de traçabilité (la matrice est un DAG de liens typés)."""

    DERIVE = "DERIVE"              # se décline de (décomposition principale)
    SATISFIES = "SATISFIES"       # satisfait un besoin amont
    VERIFIES = "VERIFIES"         # vérifie (essai/analyse) une exigence
    REFINES = "REFINES"           # raffine / précise
    ALLOCATES_TO = "ALLOCATES_TO"  # alloue à un composant/sous-système


class Link(BaseModel):
    type: LinkType
    target: str = Field(..., description="ID de l'exigence cible du lien")


class Requirement(BaseModel):
    """Une exigence du corpus, nœud de la matrice de traçabilité.

    ``parent_id`` reste le lien de décomposition principal (rétro-compatibilité) ;
    ``links`` ajoute les liens typés transverses (DAG). Les attributs EN9100 sont
    optionnels : un corpus historique sans ces champs se charge avec des défauts.
    """

    id: str = Field(..., description="Identifiant unique")
    niveau: int = Field(..., ge=0, le=5, description="Profondeur L0..L5")
    type: str = Field(default="Exigence", description="Nature de l'exigence")
    domaine: str = Field(default="Général", description="Spécialité métier")
    texte: str = Field(default="", description="Énoncé de l'exigence")
    parent_id: Optional[str] = Field(None, description="ID de l'exigence mère (lien DERIVE principal)")
    test_status: str = Field(default="PENDING", description="OK | KO | PENDING")
    # Liens typés transverses (en plus du parent_id).
    links: List[Link] = Field(default_factory=list)
    # Attributs EN9100 (optionnels).
    verification: Optional[str] = Field(None, description="Méthode IADT : I, A, D ou T")
    source: Optional[str] = Field(None, description="Origine / document amont")
    rationale: Optional[str] = Field(None, description="Justification du besoin")
    criticite: Optional[str] = Field(None, description="cle | majeure | mineure")
    version: Optional[str] = Field(None, description="Version de l'exigence")

    def short(self) -> Dict[str, Any]:
        return {"id": self.id, "niveau": self.niveau, "texte": self.texte}


class Action(BaseModel):
    """Une action d'édition à analyser avant intégration."""

    action_type: ActionType
    target_id: str
    new_text: str = ""
    # Champs optionnels pour CREATE
    parent_id: Optional[str] = None
    domaine: Optional[str] = None
    niveau: Optional[int] = None
    test_status: Optional[str] = None
    # Dérogation humaine
    force_override: bool = False
    override_rationale: str = ""


class Finding(BaseModel):
    """Un constat émis par un analyseur."""

    analyzer: str = Field(..., description="Nom de l'analyseur émetteur")
    scope: Scope
    severity: Severity
    message: str
    impacted_ids: List[str] = Field(default_factory=list)
    details: Dict[str, Any] = Field(default_factory=dict)


class ImpactReport(BaseModel):
    """Rapport agrégé renvoyé à l'UI après une action."""

    action_type: ActionType
    target_id: str
    global_status: Severity = Severity.INFO
    findings: List[Finding] = Field(default_factory=list)
    impacted_ids: List[str] = Field(default_factory=list)
    narrative: str = ""

    def recompute_status(self) -> "ImpactReport":
        """Statut global = pire gravité parmi les constats."""
        order = {Severity.INFO: 0, Severity.WARNING: 1, Severity.BLOCKING: 2}
        worst = Severity.INFO
        impacted: list[str] = []
        for f in self.findings:
            if order[f.severity] > order[worst]:
                worst = f.severity
            impacted.extend(f.impacted_ids)
        self.global_status = worst
        # union ordonnée, en gardant la cible en tête
        seen: dict[str, None] = {}
        for rid in [self.target_id, *impacted]:
            if rid:
                seen.setdefault(rid, None)
        self.impacted_ids = list(seen.keys())
        return self
