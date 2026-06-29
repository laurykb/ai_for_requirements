"""Extraction de quantités numériques dans le texte d'une exigence.

Sert à l'analyse d'allocation déterministe : repérer un budget (« ne doit pas
excéder 150 kg »), une mesure (« masse mesurée à 10 kg »), une borne basse
(« au moins 5 kg ») ou une plage (« entre 5 et 10 kg »), avec leur unité, et
faire le roll-up somme-des-enfants vs budget-parent — avec conversion d'unités.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Tuple

# Unité reconnue -> forme canonique.
_UNITS = {
    "kg": "kg", "g": "g", "t": "t", "tonne": "t", "tonnes": "t",
    "mm": "mm", "cm": "cm", "m": "m", "km": "km",
    "w": "W", "kw": "kW", "mw": "MW",
    "v": "V", "kv": "kV", "a": "A", "ma": "mA",
    "%": "%", "euro": "EUR", "euros": "EUR", "eur": "EUR",
    "s": "s", "ms": "ms", "h": "h",
    "hz": "Hz", "khz": "kHz", "mhz": "MHz", "ghz": "GHz",
    "db": "dB", "bar": "bar", "pa": "Pa",
    # données / mémoire (octets FR et bytes EN)
    "ko": "ko", "mo": "Mo", "go": "Go", "to": "To",
    "kb": "kB", "mb": "MB", "gb": "GB", "tb": "TB",
}

# Famille physique + facteur vers l'unité de base de la famille (pour conversion).
_FAMILY = {
    "g": ("masse", 0.001), "kg": ("masse", 1.0), "t": ("masse", 1000.0),
    "mm": ("longueur", 0.001), "cm": ("longueur", 0.01), "m": ("longueur", 1.0), "km": ("longueur", 1000.0),
    "ms": ("temps", 0.001), "s": ("temps", 1.0), "h": ("temps", 3600.0),
    "W": ("puissance", 1.0), "kW": ("puissance", 1000.0), "MW": ("puissance", 1e6),
    "V": ("tension", 1.0), "kV": ("tension", 1000.0),
    "A": ("courant", 1.0), "mA": ("courant", 0.001),
    "Hz": ("freq", 1.0), "kHz": ("freq", 1e3), "MHz": ("freq", 1e6), "GHz": ("freq", 1e9),
    "%": ("ratio", 1.0), "EUR": ("monnaie", 1.0), "dB": ("dB", 1.0),
    "bar": ("pression", 1.0), "Pa": ("pression", 1.0),
    "ko": ("donnee", 1e3), "Mo": ("donnee", 1e6), "Go": ("donnee", 1e9), "To": ("donnee", 1e12),
    "kB": ("donnee", 1e3), "MB": ("donnee", 1e6), "GB": ("donnee", 1e9), "TB": ("donnee", 1e12),
}


def same_family(u1: Optional[str], u2: Optional[str]) -> bool:
    return bool(u1 and u2 and u1 in _FAMILY and u2 in _FAMILY and _FAMILY[u1][0] == _FAMILY[u2][0])


def to_base(value: float, unit: str) -> Optional[float]:
    return value * _FAMILY[unit][1] if unit in _FAMILY else None


def from_base(value_base: float, unit: str) -> Optional[float]:
    return value_base / _FAMILY[unit][1] if unit in _FAMILY else None


# Mots-clés (sans accent) du sens de la contrainte.
# Radicaux (sans accent) appariés par SOUS-CHAINE pour couvrir les flexions FR :
# « exced » -> excéder / n'excédant / excède ; « inferieur » -> inférieur(e) ;
# « limit » -> limite / limiter. Les exigences réelles disent « n'excédant pas X »,
# « inférieure ou égale à X », « doit limiter … à X » — qu'un mot exact raterait.
_MAX_KW = ["exced", "depass", "inferieur", "maxim", "au plus", "plafon",
           "limit", "fixee a", "fixe a", "borne sup", "sous le seuil", "sous la barre"]
_MIN_KW = ["au moins", "minim", "superieur", "au minimum", "pas moins", "borne inf"]
_MEASURE_KW = ["mesur", "constate", "releve", "pese", "vaut", "estimee a"]
_RANGE_KW = ["entre", "plage", "fourchette", "±", "+/-", "+ ou -"]
_MAX_SYM = ["<=", "≤"]
_MIN_SYM = [">=", "≥"]
_MEASURE_SYM = ["=="]


@dataclass
class Quantity:
    value: float
    unit: Optional[str]
    kind: str          # "max" | "min" | "measure" | "range"
    raw: str

    def __repr__(self) -> str:  # pragma: no cover
        return f"Quantity({self.value}{self.unit or ''}, {self.kind})"


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


# Motif d'unité réutilisable (tokens longs d'abord, puis classe d'une lettre).
_UNIT_PAT = (r"%|kg|km|cm|mm|ms|mw|kw|kv|ma|ko|mo|go|to|kb|mb|gb|tb|"
             r"ghz|mhz|khz|hz|db|bar|pa|tonnes?|euros?|eur|[gtmwvash]")
_NUM_RE = re.compile(rf"(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>{_UNIT_PAT})(?![a-z])", re.IGNORECASE)
# Recolle les séparateurs de milliers UNIQUEMENT si suivis (décimale optionnelle
# comprise) d'une unité reconnue — évite de fusionner « version 2 014 ».
_THOUSANDS_RE = re.compile(
    rf"\d{{1,3}}(?: \d{{3}})+(?=(?:[.,]\d+)?\s*(?:{_UNIT_PAT})(?![a-z]))", re.IGNORECASE)


def _normalize(text: str) -> str:
    # Normalise les espaces insécables (séparateur de milliers FR de Word/LibreOffice).
    norm = text.replace(" ", " ").replace(" ", " ").replace(" ", " ")
    return _THOUSANDS_RE.sub(lambda m: m.group(0).replace(" ", ""), norm)


_CLAUSE_SEP = ".;:,"


def _clause(text: str, pos: int) -> str:
    """Proposition contenant la position ``pos`` (bornée par . ; : ,)."""
    start = max((text.rfind(c, 0, pos) for c in _CLAUSE_SEP), default=-1)
    ends = [e for e in (text.find(c, pos) for c in _CLAUSE_SEP) if e != -1]
    end = min(ends) if ends else len(text)
    return text[start + 1:end]


def _has_kw(ctx: str, words: list[str], symbols: list[str], original: str) -> bool:
    # Appariement par sous-chaîne (radical) : robuste aux flexions françaises.
    if any(w in ctx for w in words):
        return True
    return any(s in original for s in symbols)


def _classify_kind(clause: str) -> str:
    ctx = _strip_accents(clause.lower())
    if any(k in clause.lower() or k in ctx for k in _RANGE_KW):
        return "range"
    if _has_kw(ctx, _MEASURE_KW, _MEASURE_SYM, clause):
        return "measure"
    if _has_kw(ctx, _MIN_KW, _MIN_SYM, clause):
        return "min"
    if _has_kw(ctx, _MAX_KW, _MAX_SYM, clause):
        return "max"
    # Défaut : un nombre nu n'est pas un plafond — c'est une valeur/mesure.
    return "measure"


def extract_quantities(text: str) -> List[Quantity]:
    if not text:
        return []
    found: List[Quantity] = []
    norm = _normalize(text)
    for match in _NUM_RE.finditer(norm):
        raw_unit = (match.group("unit") or "").lower()
        unit = _UNITS.get(raw_unit)
        if unit is None:
            continue
        value = float(match.group("num").replace(" ", "").replace(",", "."))
        kind = _classify_kind(_clause(norm, match.start()))
        found.append(Quantity(value=value, unit=unit, kind=kind, raw=match.group(0).strip()))
    return found


def primary_quantity(text: str, prefer_unit: Optional[str] = None,
                     prefer_kind: Optional[str] = None) -> Optional[Quantity]:
    qs = extract_quantities(text)
    if not qs:
        return None
    if prefer_kind:
        same_kind = [q for q in qs if q.kind == prefer_kind]
        if same_kind:
            qs = same_kind
    if prefer_unit:
        same = [q for q in qs if q.unit == prefer_unit]
        if same:
            for kind in ("measure", "max", "min"):
                for q in same:
                    if q.kind == kind:
                        return q
            return same[0]
    return qs[0]


def allocation_rollup(parent_text: str, children: List[Tuple[str, str]]) -> Optional[dict]:
    """Roll-up budgétaire avec conversion d'unités.

    ``children`` = liste de (id, texte). Renvoie None si le parent n'exprime pas
    de plafond exploitable, sinon un dict :
      budget (Quantity), budget_base, total_base, contributions [(id, val_unité_budget)],
      non_comparables [id] (enfants avec une quantité d'une autre famille).
    Les sommes sont en unité de base ; les valeurs affichables sont dans
    l'unité du budget.
    """
    budget = primary_quantity(parent_text, prefer_kind="max")
    if not budget or budget.kind != "max" or budget.unit not in _FAMILY:
        return None
    budget_base = to_base(budget.value, budget.unit)
    total_base = 0.0
    contributions: List[Tuple[str, float]] = []
    non_comparables: List[str] = []
    for cid, ctext in children:
        qs = extract_quantities(ctext)
        same = [q for q in qs if same_family(q.unit, budget.unit) and q.kind in {"measure", "max"}]
        if same:
            q = sorted(same, key=lambda x: 0 if x.kind == "measure" else 1)[0]
            vb = to_base(q.value, q.unit)
            total_base += vb
            contributions.append((cid, round(from_base(vb, budget.unit), 4)))
        elif any(q.unit and not same_family(q.unit, budget.unit) for q in qs):
            non_comparables.append(cid)
    return {
        "budget": budget, "budget_base": budget_base, "total_base": total_base,
        "contributions": contributions, "non_comparables": non_comparables,
    }
