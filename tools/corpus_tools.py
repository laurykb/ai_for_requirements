"""
Outils agent pour le corpus-large (synthèse corpus) : lister les documents indexés,
extraire un aspect d'UN document. Contrat tool calling identique à `tools/rag_tool.py`
(nom + description en langage naturel + exécuteur), mais dans un registre dédié :
ces outils ne prennent pas de `query`, alors que `tools.rag_tool.run_tool` l'exige
en paramètre obligatoire (contrat de `rag_search`, à ne pas toucher). Plutôt que
d'assouplir cette validation - au risque de fragiliser rag_search - on garde les
deux registres séparés et on expose un dispatcher `run_corpus_tool` propre, avec
sa propre validation par outil.
"""
from __future__ import annotations

LIST_SOURCES_NAME = "list_sources"
EXTRACT_NAME = "extract_from_document"

LIST_SOURCES_DESCRIPTION = (
    "Liste les documents indexés du corpus (noms de sources). À utiliser en amont "
    "d'un balayage/agrégation sur l'ensemble du corpus, ou pour connaître le périmètre "
    "documentaire disponible."
)

EXTRACT_DESCRIPTION = (
    "Extrait d'UN document tous les éléments correspondant à un aspect donné (ex : "
    "« attaques », « exigences de sécurité »). Étape MAP réutilisable pour synthétiser "
    "un aspect sur plusieurs documents (à appeler une fois par document, puis agréger "
    "les résultats)."
)

LIST_SOURCES_PARAMETERS = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}

EXTRACT_PARAMETERS = {
    "type": "object",
    "properties": {
        "document": {
            "type": "string",
            "description": "Nom exact du document à traiter.",
        },
        "aspect": {
            "type": "string",
            "description": "L'aspect à extraire, en langage naturel.",
        },
    },
    "required": ["document", "aspect"],
    "additionalProperties": False,
}


def list_sources_tool_spec() -> dict:
    """Spécification d'outil générique pour list_sources."""
    return {"name": LIST_SOURCES_NAME, "description": LIST_SOURCES_DESCRIPTION,
            "parameters": LIST_SOURCES_PARAMETERS}


def extract_tool_spec() -> dict:
    """Spécification d'outil générique pour extract_from_document."""
    return {"name": EXTRACT_NAME, "description": EXTRACT_DESCRIPTION,
            "parameters": EXTRACT_PARAMETERS}


def list_sources() -> dict:
    """Liste les documents indexés du corpus (pour un balayage/agrégation)."""
    try:
        from core.corpus import list_indexed_sources
        return {"ok": True, "sources": list_indexed_sources()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def extract_from_document_tool(document: str, aspect: str) -> dict:
    """Extrait d'UN document tous les éléments d'un aspect (MAP réutilisable)."""
    if not (document or "").strip() or not (aspect or "").strip():
        return {"ok": False, "error": "document et aspect requis."}
    try:
        from core.corpus_extract import extract_from_document
        res = extract_from_document(document, aspect)
        return {"ok": True, "document": res["document"], "items": res["items"]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# Registre dédié (séparé de tools.rag_tool._TOOLS) : ces outils n'ont pas de `query`.
CORPUS_TOOLS = {
    LIST_SOURCES_NAME: list_sources,
    EXTRACT_NAME: extract_from_document_tool,
}

_REQUIRED_ARGS = {
    LIST_SOURCES_NAME: (),
    EXTRACT_NAME: ("document", "aspect"),
}


def run_corpus_tool(name: str, arguments: dict) -> dict:
    """
    Dispatcher du registre corpus : valide le nom et les arguments requis par outil,
    puis exécute. Le code (et non le modèle) reste l'unique exécutant. Renvoie une
    erreur informative en cas de problème.
    """
    fn = CORPUS_TOOLS.get(name)
    if fn is None:
        return {"ok": False, "error": f"Outil inconnu : '{name}'. Outils disponibles : {list(CORPUS_TOOLS)}."}
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return {"ok": False, "error": "Le champ 'arguments' doit être un objet JSON."}

    missing = [a for a in _REQUIRED_ARGS.get(name, ()) if a not in arguments]
    if missing:
        return {"ok": False, "error": f"Paramètre(s) requis manquant(s) : {missing}."}

    allowed = set(_REQUIRED_ARGS.get(name, ()))
    kwargs = {k: v for k, v in arguments.items() if k in allowed}
    return fn(**kwargs)
