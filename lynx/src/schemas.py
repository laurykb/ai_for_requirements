"""Registre des schémas de réponse des skills LynX (sorties structurées).

Chaque skill LLM (``lynx/skills/*.md``) promet une forme JSON précise dans son
prompt ; ce module la formalise en modèles Pydantic. ``llm.call_skill`` s'en sert
pour :

- contraindre la génération (``response_format: json_schema`` dérivé du modèle,
  avec repli automatique sur ``json_object`` si le backend le rejette) ;
- valider la réponse après parsing (un retry ciblé en cas d'écart, puis
  ``SCHEMA_VALIDATION_ERROR`` si l'écart persiste).

Les modèles sont TOLÉRANTS là où les appelants le sont déjà (listes d'ids
acceptant ``"REQ-1"`` ou ``{"id": "REQ-1"}``, gravités synonymes
BLOQUANT/BLOCKING, champs texte à ``null``) : l'objectif est de fiabiliser la
STRUCTURE, pas de rejeter une réponse exploitable. Les clés inattendues sont
conservées (``extra="allow"``) pour ne rien perdre dans la boîte de verre.

IMPORTANT — ordre des champs : la grammaire de génération suit l'ordre des
propriétés du schéma. Chaque modèle déclare donc ses champs dans l'ORDRE EXACT
du JSON d'exemple de son prompt (le verdict d'abord, la preuve/synthèse à la
fin) : inverser cet ordre ferait rédiger la justification avant le raisonnement.

Deux skills n'ont volontairement PAS de schéma :
- ``synthese_message`` : sortie Markdown streamée (pas de JSON) ;
- ``regles_redaction`` : référentiel injecté dans les prompts, jamais appelé.
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Literal, Optional, Type

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


# --------------------------------------------------------------------------
# Coercitions partagées (tolérance alignée sur celle des appelants)
# --------------------------------------------------------------------------
def _norm_gravite(alias: str, canonique: str):
    """Fabrique un normaliseur de gravité qui remplace ``alias`` par ``canonique``
    (casse et espaces tolérés) ; utilisé pour les synonymes BLOQUANT/BLOCKING
    selon la langue du prompt (analyseurs en anglais, audit en français)."""
    def normaliser(v: Any) -> Any:
        if not isinstance(v, str):
            return v
        up = v.strip().upper()
        return canonique if up == alias else up
    return normaliser


def _ids(v: Any) -> Any:
    """Liste d'identifiants : tolère ``"REQ-1"`` comme ``{"id": "REQ-1"}``."""
    if not isinstance(v, list):
        return v
    out: List[str] = []
    for item in v:
        if isinstance(item, dict):
            item = item.get("id") or item.get("id_soeur") or item.get("id_ancetre")
        if item:
            out.append(str(item))
    return out


def _texte(v: Any) -> Any:
    """Champ texte facultatif : ``null`` vaut chaîne vide."""
    return "" if v is None else v


def _en_dicts_id(v: Any) -> Any:
    """Liste d'objets ``{id, ...}`` : tolère les ids nus (``"REQ-1"``)."""
    if isinstance(v, list):
        return [{"id": i} if isinstance(i, str) else i for i in v]
    return v


TexteVide = Annotated[str, BeforeValidator(_texte)]
ListeIds = Annotated[List[str], BeforeValidator(_ids)]
GraviteEN = Annotated[Literal["INFO", "WARNING", "BLOCKING"],
                       BeforeValidator(_norm_gravite("BLOQUANT", "BLOCKING"))]
GraviteFR = Annotated[Literal["INFO", "WARNING", "BLOQUANT"],
                       BeforeValidator(_norm_gravite("BLOCKING", "BLOQUANT"))]


class _SkillModel(BaseModel):
    """Socle commun : clés inattendues conservées (boîte de verre)."""

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _cles_propres(cls, data: Any) -> Any:
        """Répare les clés parasitées par des espaces (vu en génération
        contrainte : ``"preuve "`` au lieu de ``"preuve"``). Une clé déjà
        présente n'est pas écrasée par une valeur vide."""
        if not isinstance(data, dict):
            return data
        out: Dict[Any, Any] = {}
        for k, v in data.items():
            kk = k.strip() if isinstance(k, str) else k
            if kk in out and v in (None, "", []):
                continue
            out[kk] = v
        return out


class _VerdictNiveau(_SkillModel):
    """Base des schémas « à gravité » : si le verdict booléen rendu est « propre »
    (rien à signaler), la gravité est ramenée à INFO. Cette cohérence croisée
    élimine la source n°1 de faux positifs (une gravité WARNING alors que le
    booléen dit que tout va bien — cf. les prompts « INFO si … »). Chaque
    sous-classe déclare SA condition via `_verdict_propre` ; la base n'ajoute
    aucun champ, donc l'ordre des champs envoyé au LLM reste celui de la
    sous-classe."""

    def _verdict_propre(self) -> bool:
        return False

    @model_validator(mode="after")
    def _gravite_coherente(self):
        if self._verdict_propre():
            self.niveau_gravite = "INFO"
        return self


# --------------------------------------------------------------------------
# coherence_pertinence (T1) / coherence_pertinence_aval (T4) — même contrat
# --------------------------------------------------------------------------
class CoherencePertinence(_VerdictNiveau):
    """Verdict de cohérence/pertinence vs la chaîne amont (T1) ou les filles (T4)."""

    est_coherent: bool
    rupture_avec: ListeIds = Field(default_factory=list)
    niveau_gravite: GraviteEN = "INFO"
    preuve: TexteVide = ""
    synthese: TexteVide = ""

    def _verdict_propre(self) -> bool:
        return self.est_coherent


# --------------------------------------------------------------------------
# couverture_amont (T2)
# --------------------------------------------------------------------------
class CouvertureAmont(_SkillModel):
    """Complétude de la déclinaison : le parent reste-t-il couvert par ses filles ?"""

    concepts_parent: List[str] = Field(default_factory=list)
    concepts_non_couverts: List[str] = Field(default_factory=list)
    est_complet: bool
    preuve: TexteVide = ""
    synthese: TexteVide = ""


# --------------------------------------------------------------------------
# redondance_surspec (T3)
# --------------------------------------------------------------------------
class RedondanceSurspec(_VerdictNiveau):
    """Redondance / sur-spécification de la cible vs ses sœurs."""

    aspects_cible: List[str] = Field(default_factory=list)
    aspects_nouveaux: List[str] = Field(default_factory=list)
    est_redondante: bool
    est_sur_specifiee: bool
    soeurs_en_conflit: ListeIds = Field(default_factory=list)
    niveau_gravite: GraviteEN = "INFO"
    preuve: TexteVide = ""
    synthese: TexteVide = ""

    def _verdict_propre(self) -> bool:
        # Ni redondante ni sur-spécifiée : la cible apporte une couverture
        # nouvelle -> INFO (cf. prompt).
        return not self.est_redondante and not self.est_sur_specifiee


# --------------------------------------------------------------------------
# impact_latent
# --------------------------------------------------------------------------
class ExigenceImpactee(_SkillModel):
    id: str
    raison: TexteVide = ""


class ImpactLatent(_VerdictNiveau):
    """Exigences non reliées mais réellement impactées par la modification."""

    impactees: Annotated[List[ExigenceImpactee], BeforeValidator(_en_dicts_id)] = \
        Field(default_factory=list)
    niveau_gravite: GraviteEN = "INFO"
    preuve: TexteVide = ""
    synthese: TexteVide = ""

    def _verdict_propre(self) -> bool:
        return not self.impactees


# --------------------------------------------------------------------------
# coherence_coreference
# --------------------------------------------------------------------------
class Conflit(_SkillModel):
    id: str
    probleme: TexteVide = ""


class CoherenceCoreference(_VerdictNiveau):
    """Cohérence entre exigences partageant un référent concret."""

    coherent: bool
    conflits: Annotated[List[Conflit], BeforeValidator(_en_dicts_id)] = \
        Field(default_factory=list)
    niveau_gravite: GraviteEN = "INFO"
    preuve: TexteVide = ""
    synthese: TexteVide = ""

    def _verdict_propre(self) -> bool:
        return self.coherent


# --------------------------------------------------------------------------
# audit_exigence (audit global : cinq axes en une passe)
# --------------------------------------------------------------------------
class AuditRedaction(_SkillModel):
    conforme: bool
    probleme: TexteVide = ""


class AuditPertinence(_SkillModel):
    coherent: bool
    probleme: TexteVide = ""


class AuditCouverture(_SkillModel):
    complet: bool
    manques: List[str] = Field(default_factory=list)


class AuditRedondance(_SkillModel):
    redondant: bool
    avec: ListeIds = Field(default_factory=list)


class AuditPertinenceAval(_SkillModel):
    coherent: bool
    avec: ListeIds = Field(default_factory=list)
    probleme: TexteVide = ""


class AuditExigence(_SkillModel):
    """Audit d'une exigence dans son contexte de traçabilité (cinq axes)."""

    redaction: AuditRedaction
    pertinence: AuditPertinence
    couverture: AuditCouverture
    redondance: AuditRedondance
    pertinence_aval: AuditPertinenceAval
    gravite: GraviteFR = "INFO"


# --------------------------------------------------------------------------
# redaction_exigence (assistant de rédaction EN9100)
# --------------------------------------------------------------------------
class Violation(_SkillModel):
    regle: TexteVide = ""
    probleme: TexteVide = ""
    extrait: TexteVide = ""


def _score_entier(v: Any) -> Any:
    return round(v) if isinstance(v, float) else v


class RedactionExigence(_SkillModel):
    """Conformité rédactionnelle + réécriture conforme."""

    conforme: bool
    violations: List[Violation] = Field(default_factory=list)
    score: Annotated[int, BeforeValidator(_score_entier)] = Field(ge=0, le=100)
    reecriture: TexteVide = ""
    synthese: TexteVide = ""


# --------------------------------------------------------------------------
# suggest_correction (détection → proposition de réécriture)
# --------------------------------------------------------------------------
class SuggestCorrection(_SkillModel):
    """Réécriture proposée pour une exigence signalée."""

    texte_propose: TexteVide
    changements: List[str] = Field(default_factory=list)
    justification: TexteVide = ""
    corrige_tout: bool = False


# --------------------------------------------------------------------------
# synthese_impact (verdict + message agrégés, non streamé)
# --------------------------------------------------------------------------
def _majuscules(v: Any) -> Any:
    return v.strip().upper() if isinstance(v, str) else v


class SyntheseImpact(_SkillModel):
    """Verdict global et message de synthèse adressé à l'ingénieur."""

    verdict: Annotated[Literal["VALIDE", "ATTENTION", "BLOQUANT"], BeforeValidator(_majuscules)]
    message: str


# --------------------------------------------------------------------------
# generation_filles (déclinaison descendante L(n) -> L(n+1))
# --------------------------------------------------------------------------
class FilleProposee(_SkillModel):
    texte: TexteVide
    justification: TexteVide = ""
    aspect_couvert: TexteVide = ""


class GenerationFilles(_SkillModel):
    """Exigences filles proposées pour décliner une mère (2 à 7, borné côté code)."""

    filles: List[FilleProposee] = Field(default_factory=list)
    aspects_non_couverts: List[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# defense_exigence / juge_verdict (débat contradictoire sur BLOQUANT)
# --------------------------------------------------------------------------
class DefenseExigence(_SkillModel):
    """Plaidoyer de l'avocat de la défense contre une accusation BLOQUANT.

    Ordre voulu : le raisonnement (plaidoyer, arguments, éléments) AVANT le
    verdict ``refutation_possible`` — l'avocat argumente puis conclut.
    """

    plaidoyer: TexteVide = ""
    arguments: List[str] = Field(default_factory=list)
    elements_contexte: List[str] = Field(default_factory=list)
    refutation_possible: bool


def _norm_verdict_debat(v: Any) -> Any:
    """MAINTENU / RETROGRADE : tolère accents et casse (« Rétrogradé »)."""
    if not isinstance(v, str):
        return v
    up = v.strip().upper().replace("É", "E").replace("È", "E")
    return up


class JugeVerdict(_SkillModel):
    """Arbitrage du juge : l'accusation tient-elle face au plaidoyer ?"""

    verdict: Annotated[Literal["MAINTENU", "RETROGRADE"],
                       BeforeValidator(_norm_verdict_debat)]
    motivation: TexteVide = ""


# --------------------------------------------------------------------------
# Registre skill -> schéma
# --------------------------------------------------------------------------
SKILL_SCHEMAS: Dict[str, Type[BaseModel]] = {
    "audit_exigence": AuditExigence,
    "coherence_coreference": CoherenceCoreference,
    "coherence_pertinence": CoherencePertinence,
    "coherence_pertinence_aval": CoherencePertinence,
    "couverture_amont": CouvertureAmont,
    "defense_exigence": DefenseExigence,
    "generation_filles": GenerationFilles,
    "juge_verdict": JugeVerdict,
    "impact_latent": ImpactLatent,
    "redaction_exigence": RedactionExigence,
    "redondance_surspec": RedondanceSurspec,
    "suggest_correction": SuggestCorrection,
    "synthese_impact": SyntheseImpact,
}


# Skills VALIDÉS mais dont la génération n'est PAS contrainte par grammaire.
# Mesuré sur le golden set : contraindre `redondance_surspec` fait basculer à
# tort le verdict `est_sur_specifiee` (faux positifs T3, la précision est la
# métrique reine). On garde la validation Pydantic + retry, sans json_schema.
GENERATION_LIBRE = frozenset({"redondance_surspec"})


def schema_for(skill_name: Optional[str]) -> Optional[Type[BaseModel]]:
    """Schéma de réponse attendu pour un skill, ou ``None`` (sortie libre)."""
    return SKILL_SCHEMAS.get(skill_name) if skill_name else None


def constrain_generation(skill_name: Optional[str]) -> bool:
    """La génération de ce skill doit-elle être contrainte par grammaire ?"""
    return skill_name not in GENERATION_LIBRE


def _interdire_extras(node: Any) -> None:
    """Force ``additionalProperties: false`` dans tout le schéma JSON émis.

    La GRAMMAIRE de génération interdit ainsi les clés parasites (ex.
    ``"preuve "`` avec espace, qui siphonne la vraie valeur) ; la VALIDATION,
    elle, reste tolérante (``extra="allow"``) pour le repli ``json_object``.
    """
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            node["additionalProperties"] = False
        for v in node.values():
            _interdire_extras(v)
    elif isinstance(node, list):
        for v in node:
            _interdire_extras(v)


def response_format(schema: Type[BaseModel]) -> dict:
    """Bloc ``response_format`` OpenAI-compatible dérivé d'un modèle Pydantic."""
    json_schema = schema.model_json_schema()
    _interdire_extras(json_schema)
    return {"type": "json_schema",
            "json_schema": {"name": schema.__name__, "schema": json_schema}}
