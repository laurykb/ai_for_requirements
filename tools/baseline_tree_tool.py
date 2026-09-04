"""L'arbre d'exigences exposé comme OUTIL déterministe de l'agent.

Le retrieval voit les liens de traçabilité comme du TEXTE (« Dérivée de : … »)
— une question structurelle (« quelles L2 dérivent de SYS-001 et lesquelles
n'ont pas de vérification ? ») dépendait de la chance du retrieval. Cet outil
répond depuis la STRUCTURE : réponses exactes, zéro LLM, zéro hallucination.

Exposé à l'agent (ReAct/planner) uniquement quand le périmètre du chat est la
baseline LynX. Pur : opère sur la liste d'exigences injectée (testable), la
production branche le corpus de travail vivant (api.lynx_api._get_corpus).
"""
from __future__ import annotations

TOOL_NAME = "baseline_tree"

TOOL_DESCRIPTION = (
    "Interroge la STRUCTURE exacte de la baseline d'exigences (arbre de "
    "traçabilité) : dérivations parent/enfants, chaînes de dérivation, liens "
    "typés, exigences orphelines, filtres par niveau/domaine/vérification, "
    "statistiques. À utiliser pour toute question de traçabilité ou de "
    "complétude structurelle — le résultat est exact (aucune recherche "
    "sémantique). Pour le CONTENU des exigences, utiliser rag_search."
)

TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": ["exigence", "enfants", "chaine", "liens", "orphelines",
                     "filtrer", "stats"],
            "description": "exigence: fiche d'une exigence ; enfants: dérivées directes ; "
                           "chaine: ascendance jusqu'à la racine ; liens: liens typés "
                           "entrants/sortants ; orphelines: niveau>0 sans parent ; "
                           "filtrer: par domaine/niveau/vérification/statut ; stats: "
                           "répartition par domaine et niveau, couverture de vérification.",
        },
        "req_id": {"type": "string",
                   "description": "Identifiant d'exigence (requis pour exigence/enfants/chaine/liens)."},
        "domaine": {"type": "string", "description": "Filtre : domaine exact."},
        "niveau": {"type": "integer", "description": "Filtre : niveau Ln."},
        "sans_verification": {"type": "boolean",
                              "description": "Filtre : exigences SANS méthode de vérification (IADT)."},
        "test_status": {"type": "string", "description": "Filtre : OK | KO | PENDING."},
    },
    "required": ["operation"],
    "additionalProperties": False,
}


def tool_spec() -> dict:
    return {"name": TOOL_NAME, "description": TOOL_DESCRIPTION,
            "parameters": TOOL_PARAMETERS,
            "example": {"operation": "filtrer", "domaine": "Cybersécurité",
                        "sans_verification": True}}


def _brief(req: dict) -> dict:
    """Fiche compacte : tout ce qu'une réponse d'agent doit pouvoir citer."""
    texte = str(req.get("texte") or "")
    return {"id": req.get("id"), "niveau": req.get("niveau"),
            "domaine": req.get("domaine"), "parent_id": req.get("parent_id"),
            "verification": req.get("verification"),
            "test_status": req.get("test_status"),
            "texte": texte[:180] + ("…" if len(texte) > 180 else "")}


def _by_id(corpus: list[dict], req_id: str) -> dict | None:
    return next((r for r in corpus if str(r.get("id")) == req_id), None)


def run(corpus: list[dict], operation: str, req_id: str | None = None,
        domaine: str | None = None, niveau: int | None = None,
        sans_verification: bool | None = None,
        test_status: str | None = None) -> dict:
    """Exécute une opération structurelle. Retourne {ok, operation, result…}."""
    if operation in ("exigence", "enfants", "chaine", "liens"):
        if not req_id:
            return {"ok": False, "error": f"'{operation}' requiert req_id."}
        req = _by_id(corpus, req_id)
        if req is None:
            return {"ok": False,
                    "error": f"Exigence inconnue : {req_id}. Identifiants valides : "
                             f"{', '.join(sorted(str(r.get('id')) for r in corpus)[:15])}…"}

    if operation == "exigence":
        full = dict(_brief(req))
        full["texte"] = str(req.get("texte") or "")
        full["links"] = req.get("links") or []
        full["rationale"] = req.get("rationale")
        full["source"] = req.get("source")
        return {"ok": True, "operation": operation, "exigence": full}

    if operation == "enfants":
        enfants = [_brief(r) for r in corpus if r.get("parent_id") == req_id]
        return {"ok": True, "operation": operation, "parent": req_id,
                "n": len(enfants), "enfants": enfants}

    if operation == "chaine":
        chaine, cursor, seen = [], req, set()
        while cursor is not None and str(cursor.get("id")) not in seen:
            seen.add(str(cursor.get("id")))
            chaine.append(_brief(cursor))
            pid = cursor.get("parent_id")
            cursor = _by_id(corpus, str(pid)) if pid else None
        return {"ok": True, "operation": operation,
                "chaine": chaine, "racine_atteinte": chaine[-1]["parent_id"] is None}

    if operation == "liens":
        sortants = [{"type": l.get("type"), "vers": l.get("target_id")}
                    for l in (req.get("links") or []) if isinstance(l, dict)]
        entrants = [{"type": l.get("type"), "depuis": r.get("id")}
                    for r in corpus for l in (r.get("links") or [])
                    if isinstance(l, dict) and l.get("target_id") == req_id]
        return {"ok": True, "operation": operation, "exigence": req_id,
                "sortants": sortants, "entrants": entrants}

    if operation == "orphelines":
        rows = [_brief(r) for r in corpus
                if int(r.get("niveau") or 0) > 0 and not r.get("parent_id")]
        return {"ok": True, "operation": operation, "n": len(rows), "orphelines": rows}

    if operation == "filtrer":
        rows = corpus
        if domaine is not None:
            rows = [r for r in rows if str(r.get("domaine") or "Général") == domaine]
        if niveau is not None:
            rows = [r for r in rows if int(r.get("niveau") or 0) == int(niveau)]
        if sans_verification:
            rows = [r for r in rows if not r.get("verification")]
        if test_status is not None:
            rows = [r for r in rows if str(r.get("test_status") or "PENDING") == test_status]
        return {"ok": True, "operation": operation, "n": len(rows),
                "exigences": [_brief(r) for r in rows[:60]],
                "tronque": len(rows) > 60}

    if operation == "stats":
        domaines: dict[str, int] = {}
        niveaux: dict[str, int] = {}
        sans_verif = 0
        for r in corpus:
            domaines[str(r.get("domaine") or "Général")] = \
                domaines.get(str(r.get("domaine") or "Général"), 0) + 1
            niveaux[f"L{int(r.get('niveau') or 0)}"] = \
                niveaux.get(f"L{int(r.get('niveau') or 0)}", 0) + 1
            if not r.get("verification"):
                sans_verif += 1
        return {"ok": True, "operation": operation, "n_exigences": len(corpus),
                "par_domaine": dict(sorted(domaines.items())),
                "par_niveau": dict(sorted(niveaux.items())),
                "sans_verification": sans_verif}

    return {"ok": False, "error": f"Opération inconnue : '{operation}'."}


def run_tool(arguments: dict, corpus: list[dict] | None = None) -> dict:
    """Point d'entrée outil : valide les arguments, branche le corpus vivant."""
    if not isinstance(arguments, dict):
        return {"ok": False, "error": "Le champ 'arguments' doit être un objet JSON."}
    if "operation" not in arguments:
        return {"ok": False, "error": "Paramètre requis manquant : 'operation'."}
    allowed = set(TOOL_PARAMETERS["properties"])
    kwargs = {k: v for k, v in arguments.items() if k in allowed}
    if corpus is None:
        from api.lynx_api import _get_corpus
        corpus = _get_corpus()
    if not corpus:
        return {"ok": False, "error": "Baseline vide : aucune exigence chargée."}
    try:
        return run(corpus, **kwargs)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
