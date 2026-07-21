# nlp/ner_extractor.py
"""
Extraction d'entités nommées (NER).

Combine :
- spaCy NER (fr_core_news_sm) pour PER, ORG, LOC, MISC
- Regex pour les normes techniques, identifiants et acronymes courants
  dans les documents ANSSI / Common Criteria / cybersécurité.

Les entités sont stockées dans les métadonnées de chaque chunk pour :
- Améliorer le filtrage / faceted search
- Enrichir le contenu pour le retrieval (entities_str dans BM25)
"""

import re
import threading

from utils.logging_config import get_logger

logger = get_logger("rag.ner")

# ---------------- Chargement spaCy (lazy, une seule fois) ----------------

_nlp = None
# spaCy n'est pas garanti thread-safe sur un même pipeline : on sérialise
# l'inférence (rapide) pour pouvoir paralléliser l'enrichissement LLM autour.
_spacy_lock = threading.Lock()


def _get_spacy_model():
    """Charge le modèle spaCy français une seule fois (lazy loading)."""
    global _nlp
    if _nlp is None:
        import spacy
        try:
            _nlp = spacy.load("fr_core_news_sm", disable=["parser", "lemmatizer"])
            # Désactiver parser/lemmatizer pour accélérer - on n'a besoin que du NER
            logger.info("[ner] Modèle spaCy fr_core_news_sm chargé")
        except OSError:
            logger.error("[ner] modèle fr_core_news_sm introuvable. "
                         "Installer avec : python -m spacy download fr_core_news_sm")
            _nlp = False  # Marquer comme indisponible
    return _nlp if _nlp else None


# ---------------- Regex pour entités techniques ----------------

# Normes et référentiels (CC, ANSSI, ISO, RFC, NIST, etc.)
_RE_NORMS = re.compile(
    r"\b("
    r"ISO[/ ]?\d[\d\-\.]*"           # ISO 27001, ISO/IEC 15408
    r"|IEC[/ ]?\d[\d\-\.]*"          # IEC 62443
    r"|CC[-\s]?\d[\d\.\-]*"          # CC-2014-91, CC3.1
    r"|ANSSI-[\w\-]+"               # ANSSI-CC-2025, ANSSI-CSPN-xxx (tiret obligatoire)
    r"|EAL\s*[1-7]\+?"              # EAL4+, EAL 5
    r"|RFC\s*\d+"                    # RFC 5280
    r"|NIST[-\s]?SP[-\s]?\d[\d\-]*"  # NIST SP 800-53
    r"|FIPS[-\s]?\d+"               # FIPS 140-2
    r"|CEM\s*v?[\d\.]+"             # CEM v3.1
    r"|PP-[\w\-]+"                  # PP-0084 (tiret obligatoire)
    r"|ST-[\w\-]+"                  # ST-xxx (Security Target, tiret obligatoire)
    r"|SAR[-\s][\w\.]+"             # Security Assurance Requirement
    r"|SFR[-\s][\w\.]+"             # Security Functional Requirement
    r"|RGS\s*v?\d+"                 # RGS v2 (exiger au moins un chiffre)
    r")\b",
    re.IGNORECASE
)

# Acronymes en majuscules (3+ lettres) - typiques cybersec / IT
_RE_ACRONYMS = re.compile(
    r"\b([A-Z][A-Z0-9]{2,}(?:[-/][A-Z0-9]+)*)\b"
)

# Identifiants CC/cybersec avec underscores : FCS_CKM, FDP_ITC, FAU_GEN, etc.
_RE_CC_IDS = re.compile(
    r"\b([A-Z]{2,4}_[A-Z]{2,4}(?:\.[0-9]+)*)\b"
)

# Versions logicielles / identifiants produit (ex: v2.1.3, Mistral AI 7B)
_RE_VERSIONS = re.compile(
    r"\b(v\d+(?:\.\d+)+[a-z]?)\b",
    re.IGNORECASE
)

# ---------------- Listes de stop-acronymes (faux positifs) ----------------

_STOP_ACRONYMS = {
    # Mots courants en majuscules qui ne sont pas des entités
    "THE", "AND", "FOR", "NOT", "BUT", "ARE", "HAS", "WAS", "CAN",
    "DES", "LES", "UNE", "PAR", "QUI", "EST", "SUR", "AUX", "CES",
    "NOM", "OUI", "NON", "MAX", "MIN", "TAB", "FIG", "SEC", "REF",
    # Marqueurs markdown
    "HTTP", "HTTPS", "HTML", "CSS", "PDF", "URL", "API",
}
# ---------------- Extraction principale ----------------

def extract_entities(text: str, max_entities: int = 20) -> dict[str, list[str]]:
    """
    Extrait les entités nommées d'un texte.

    Retourne un dict avec les catégories :
      - 'PER'   : personnes
      - 'ORG'   : organisations
      - 'LOC'   : lieux
      - 'NORM'  : normes et référentiels techniques
      - 'ACRO'  : acronymes significatifs
      - 'MISC'  : divers (spaCy MISC)

    Chaque catégorie contient une liste dédupliquée et triée.
    """
    if not text or len(text.strip()) < 10:
        return {}

    entities: dict[str, set[str]] = {
        "PER": set(),
        "ORG": set(),
        "LOC": set(),
        "NORM": set(),
        "ACRO": set(),
        "MISC": set(),
    }

    # -- 1) spaCy NER --
    nlp = _get_spacy_model()
    if nlp:
        # Limiter la taille pour éviter les ralentissements
        with _spacy_lock:
            doc = nlp(text[:5000])
        for ent in doc.ents:
            label = ent.label_
            entity_text = ent.text.strip()
            # Filtrage qualité : ignorer les entités trop courtes ou trop longues
            if len(entity_text) < 2 or len(entity_text) > 60:
                continue
            # Ignorer les entités multi-lignes mal découpées
            if "\n" in entity_text:
                entity_text = entity_text.split("\n")[0].strip()
                if len(entity_text) < 2:
                    continue
            # Ignorer les entités qui ressemblent à du bruit (pipe-separated, identifiants CC)
            if entity_text.startswith(("T.", "O.", "FDP_", "FIA_", "FCS_", "FPT_", "The ", "used ")):
                continue
            if label == "PER":
                # Ignorer les faux positifs PER courants (mots anglais/français)
                if entity_text.lower() in ("threat", "security", "protection", "access"):
                    continue
                entities["PER"].add(entity_text)
            elif label == "ORG":
                # Ignorer les ORG qui semblent être du bruit (trop de mots, phrases)
                if len(entity_text.split()) > 4:
                    continue
                # Ignorer si contient des mots de liaison anglais typiques de bruit NER
                lower = entity_text.lower()
                if any(w in lower for w in ("were ", "also ", " and ", "the ", "used ", "for ", " or ")):
                    continue
                entities["ORG"].add(entity_text)
            elif label == "LOC":
                entities["LOC"].add(entity_text)
            elif label == "MISC":
                # Ignorer les MISC trop courtes (v2, etc.) ou numériques
                if len(entity_text) < 3 or re.match(r"^v?\d", entity_text):
                    continue
                entities["MISC"].add(entity_text)

    # -- 2) Regex : normes techniques --
    for match in _RE_NORMS.finditer(text):
        norm = match.group(1).strip()
        if len(norm) >= 3:
            # Vérifier que la norme ne contient pas de mots parasites
            if not any(w in norm.lower() for w in ("recommande", "considère", "définit")):
                entities["NORM"].add(norm)

    # -- 3) Regex : acronymes --
    for match in _RE_ACRONYMS.finditer(text):
        acro = match.group(1)
        if acro not in _STOP_ACRONYMS and len(acro) >= 3:
            # Vérifier que ce n'est pas déjà capturé en NORM ou ORG
            if acro not in entities["NORM"] and acro not in entities["ORG"]:
                entities["ACRO"].add(acro)

    # -- 3b) Regex : identifiants CC avec underscores (FCS_CKM, FDP_ITC.1, etc.) --
    for match in _RE_CC_IDS.finditer(text):
        cc_id = match.group(1)
        entities["NORM"].add(cc_id)

    # -- 4) Dédoublonner : si un acronyme est aussi dans MISC ou ORG, le retirer de ACRO --
    for cat in ("ORG", "MISC", "NORM"):
        entities["ACRO"] -= entities[cat]

    # -- 5) Construire le résultat final (trié, limité) --
    result = {}
    total = 0
    for cat in ("PER", "ORG", "LOC", "NORM", "ACRO", "MISC"):
        sorted_ents = sorted(entities[cat])
        if sorted_ents:
            remaining = max_entities - total
            if remaining <= 0:
                break
            result[cat] = sorted_ents[:remaining]
            total += len(result[cat])

    return result


def entities_to_flat_list(entities_dict: dict[str, list[str]]) -> list[str]:
    """
    Aplatit le dict d'entités en une liste unique dédupliquée.
    Utile pour le stockage en metadata et le BM25.
    """
    seen = set()
    flat = []
    for cat_entities in entities_dict.values():
        for e in cat_entities:
            if e not in seen:
                seen.add(e)
                flat.append(e)
    return flat


def entities_to_str(entities_dict: dict[str, list[str]]) -> str:
    """
    Sérialise le dict d'entités en string lisible pour BM25 / affichage.
    Format : "PER: Jean Dupont; ORG: ANSSI, CNIL; NORM: ISO 27001"
    """
    parts = []
    for cat, ents in entities_dict.items():
        if ents:
            parts.append(f"{cat}: {', '.join(ents)}")
    return "; ".join(parts)
