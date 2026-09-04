"""Règles pures du workflow de corpus LynX.

Ce module ne connaît ni FastAPI ni l'état global du serveur. Il transforme des
fichiers en exigences, compare deux baselines et calcule leurs indicateurs de
qualité. Le routeur HTTP peut ainsi rester une couche de transport mince.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from conversion.document_import import import_document
from conversion.matrix_import import import_matrix

_LYNX_DIR = str(Path(__file__).resolve().parent.parent / "lynx")
if _LYNX_DIR not in sys.path:
    sys.path.insert(0, _LYNX_DIR)

from src import corpus_io


def prepare_activation(draft_requirements: list[dict], included_ids: list[str] | None = None) -> list[dict]:
    """Prépare une baseline candidate sans muter le brouillon source."""
    allowed = set(included_ids) if included_ids is not None else None
    selected = [dict(req) for req in draft_requirements if allowed is None or req["id"] in allowed]
    if not selected:
        raise ValueError("Aucune exigence sélectionnée.")
    valid_ids = {req["id"] for req in selected}
    for requirement in selected:
        if requirement.get("parent_id") not in valid_ids:
            requirement["parent_id"] = None
        requirement["links"] = [dict(link) for link in requirement.get("links", []) if link.get("target") in valid_ids]
    return selected


def corpus_diff(before: list[dict], after: list[dict]) -> dict:
    """Retourne les changements et leur voisinage structurel à un bond."""
    old = {req["id"]: req for req in before}
    new = {req["id"]: req for req in after}
    added = sorted(new.keys() - old.keys())
    removed = sorted(old.keys() - new.keys())
    modified = sorted(
        ident for ident in new.keys() & old.keys()
        if _signature(new[ident]) != _signature(old[ident])
    )
    changed = set(added + removed + modified)
    impacted = set(changed)

    for corpus in (before, after):
        for req in corpus:
            targets = {req.get("parent_id")} | {
                link.get("target")
                for link in req.get("links", [])
                if isinstance(link, dict)
            }
            targets.discard(None)
            if req["id"] in changed or targets & changed:
                impacted.add(req["id"])
                impacted.update(target for target in targets if target)

    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "changed": sorted(changed),
        "impacted": sorted(impacted),
    }


def _signature(requirement: dict) -> str:
    """Signature stable des champs fonctionnels d'une exigence."""
    return json.dumps(requirement, ensure_ascii=False, sort_keys=True, default=str)


def corpus_health(corpus: list[dict]) -> dict:
    """Mesure les défauts structurels sans appel LLM."""
    ids = {req["id"] for req in corpus}
    broken, roots, orphans, empty_text = [], [], [], []
    min_level = min((int(req.get("niveau", 0)) for req in corpus), default=0)
    parents: dict[str, list[str]] = {ident: [] for ident in ids}
    by_level: dict[str, int] = {}
    by_domain: dict[str, int] = {}

    for req in corpus:
        ident = req["id"]
        targets = ([req.get("parent_id")] if req.get("parent_id") else []) + [
            link.get("target")
            for link in req.get("links", [])
            if isinstance(link, dict)
        ]
        valid_targets = []
        for target in filter(None, targets):
            if target not in ids:
                broken.append({"source": ident, "target": target})
            else:
                valid_targets.append(target)
        parents[ident] = valid_targets
        if not valid_targets:
            roots.append(ident)
            if not req.get("root_declared") and int(req.get("niveau", 0)) > min_level:
                orphans.append(ident)
        if not str(req.get("texte", "")).strip():
            empty_text.append(ident)
        level = f"L{req.get('niveau', 0)}"
        domain = req.get("domaine") or "Général"
        by_level[level] = by_level.get(level, 0) + 1
        by_domain[domain] = by_domain.get(domain, 0) + 1

    cycles = _find_cycles(parents)
    penalty = min(
        100,
        len(broken) * 3
        + len(cycles) * 5
        + len(empty_text) * 4,
    )
    return {
        "score": max(0, 100 - penalty),
        "n": len(corpus),
        "broken_links": broken[:100],
        "broken_links_count": len(broken),
        "cycles": [list(cycle) for cycle in sorted(cycles)[:50]],
        "cycles_count": len(cycles),
        "roots": roots[:100],
        "roots_count": len(roots),
        "orphans": orphans[:100],
        "orphans_count": len(orphans),
        "empty_text": empty_text[:100],
        "empty_text_count": len(empty_text),
        "by_level": by_level,
        "by_domain": by_domain,
    }


def corpus_issues(corpus: list[dict], impacted_ids: list[str] | None = None) -> dict:
    """Construit une file de travail déterministe, triée par priorité."""
    ids = {req["id"] for req in corpus}
    min_level = min((int(req.get("niveau", 0)) for req in corpus), default=0)
    impacted = set(impacted_ids or [])
    priority_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    issues: list[dict] = []

    for req in corpus:
        ident = req["id"]
        req_issues = []
        if not str(req.get("texte", "")).strip():
            req_issues.append({"code": "EMPTY_TEXT", "priority": "critical",
                               "label": "Énoncé vide",
                               "action": "Rédiger ou restaurer le contenu de l’exigence."})
        targets = ([req.get("parent_id")] if req.get("parent_id") else []) + [
            link.get("target") for link in req.get("links", []) if isinstance(link, dict)
        ]
        broken = [target for target in filter(None, targets) if target not in ids]
        if broken:
            req_issues.append({"code": "BROKEN_LINK", "priority": "high",
                               "label": f"{len(broken)} lien(s) cassé(s)",
                               "action": "Remapper ou retirer les références absentes."})
        if not any(target in ids for target in filter(None, targets)) and not req.get("root_declared") and int(req.get("niveau", 0)) > min((int(item.get("niveau", 0)) for item in corpus), default=0):
            req_issues.append({"code": "NO_RELATION", "priority": "medium",
                               "label": "Exigence isolée",
                               "action": "Vérifier son rattachement à la décomposition."})
        if ident in impacted:
            req_issues.append({"code": "RECENT_IMPACT", "priority": "low",
                               "label": "Impactée récemment",
                               "action": "Revoir cette exigence après le dernier changement."})
        if not req_issues:
            continue
        req_issues.sort(key=lambda item: priority_rank[item["priority"]])
        issues.append({"req_id": ident, "priority": req_issues[0]["priority"],
                       "issues": req_issues, "source": req.get("source"),
                       "niveau": req.get("niveau", 0)})

    issues.sort(key=lambda item: (priority_rank[item["priority"]], -len(item["issues"]), item["req_id"]))
    by_priority = {priority: 0 for priority in priority_rank}
    by_code: dict[str, int] = {}
    for item in issues:
        by_priority[item["priority"]] += 1
        for issue in item["issues"]:
            by_code[issue["code"]] = by_code.get(issue["code"], 0) + 1
    return {"total": len(issues), "by_priority": by_priority, "by_code": by_code, "items": issues}


def _find_cycles(graph: dict[str, list[str]]) -> set[tuple[str, ...]]:
    """DFS sur toutes les relations ; normalise les rotations d'un même cycle."""
    cycles: set[tuple[str, ...]] = set()
    color = {ident: 0 for ident in graph}

    def visit(node: str, path: list[str], positions: dict[str, int]) -> None:
        color[node] = 1
        positions[node] = len(path)
        path.append(node)
        for target in graph.get(node, []):
            if color.get(target, 0) == 0:
                visit(target, path, positions)
            elif color.get(target) == 1 and target in positions:
                cycle = path[positions[target]:]
                rotations = [tuple(cycle[i:] + cycle[:i]) for i in range(len(cycle))]
                if rotations:
                    cycles.add(min(rotations))
        path.pop()
        positions.pop(node, None)
        color[node] = 2

    for ident in graph:
        if color[ident] == 0:
            visit(ident, [], {})
    return cycles


def corpus_facets(corpus: list[dict]) -> dict:
    """Facettes légères utilisées par le catalogue serveur."""
    def counts(field: str, fallback: str) -> dict[str, int]:
        result: dict[str, int] = {}
        for req in corpus:
            value = str(req.get(field) or fallback)
            result[value] = result.get(value, 0) + 1
        return dict(sorted(result.items()))
    ids = {req["id"] for req in corpus}
    min_level = min((int(req.get("niveau", 0)) for req in corpus), default=0)
    roots = 0
    attention = 0
    for req in corpus:
        targets = ([req.get("parent_id")] if req.get("parent_id") else []) + [
            link.get("target") for link in req.get("links", []) if isinstance(link, dict)
        ]
        has_relation = any(target in ids for target in filter(None, targets))
        broken = any(target not in ids for target in filter(None, targets))
        if not has_relation:
            roots += 1
        orphan = not has_relation and not req.get("root_declared") and int(req.get("niveau", 0)) > min_level
        if not str(req.get("texte", "")).strip() or broken or orphan:
            attention += 1
    return {"total": len(corpus), "levels": counts("niveau", "0"),
            "sources": counts("source", "Sans source"),
            "domains": counts("domaine", "Général"),
            "views": {"attention": attention, "roots": roots}}


def query_requirements(corpus: list[dict], query: str = "", level: int | None = None,
                       source: str | None = None, domain: str | None = None,
                       status: str | None = None, impacted_ids: list[str] | None = None,
                       page: int = 1, page_size: int = 100) -> dict:
    """Recherche paginée et stable du catalogue, sans charger tout le corpus côté UI."""
    normalized = query.strip().casefold()
    ids = {req["id"] for req in corpus}
    min_level = min((int(req.get("niveau", 0)) for req in corpus), default=0)
    impacted = set(impacted_ids or [])
    rows = []
    for req in corpus:
        targets = ([req.get("parent_id")] if req.get("parent_id") else []) + [
            link.get("target") for link in req.get("links", []) if isinstance(link, dict)
        ]
        has_relations = any(target in ids for target in filter(None, targets))
        broken = any(target not in ids for target in filter(None, targets))
        orphan = not has_relations and not req.get("root_declared") and int(req.get("niveau", 0)) > min_level
        needs_attention = not str(req.get("texte", "")).strip() or broken or orphan
        matches_status = (
            not status or status == "all"
            or (status == "attention" and needs_attention)
            or (status == "roots" and not has_relations)
            or (status == "impacted" and req.get("id") in impacted)
            or (status == "empty" and not str(req.get("texte", "")).strip())
        )
        if normalized and normalized not in str(req.get("id", "")).casefold() \
                and normalized not in str(req.get("texte", "")).casefold():
            continue
        if level is not None and req.get("niveau") != level:
            continue
        if source and (req.get("source") or "Sans source") != source:
            continue
        if domain and (req.get("domaine") or "Général") != domain:
            continue
        if not matches_status:
            continue
        rows.append(req)
    rows.sort(key=lambda req: (req.get("niveau", 0), req.get("id", "")))
    page = max(1, page)
    page_size = min(500, max(1, page_size))
    start = (page - 1) * page_size
    return {"total": len(rows), "page": page, "page_size": page_size,
            "items": rows[start:start + page_size]}


def requirement_relations(corpus: list[dict], req_id: str) -> dict | None:
    """Voisinage focalisé et chemin principal d’une exigence."""
    by_id = {req["id"]: req for req in corpus}
    selected = by_id.get(req_id)
    if not selected:
        return None
    upstream_ids = set(filter(None, [selected.get("parent_id")]))
    upstream_ids.update(link.get("target") for link in selected.get("links", [])
                        if isinstance(link, dict) and link.get("target"))
    downstream = [req for req in corpus if req.get("parent_id") == req_id
                  or any(isinstance(link, dict) and link.get("target") == req_id
                         for link in req.get("links", []))]
    path, seen = [], {req_id}
    cursor = selected.get("parent_id")
    while cursor and cursor not in seen and cursor in by_id:
        seen.add(cursor)
        path.insert(0, by_id[cursor])
        cursor = by_id[cursor].get("parent_id")
    return {"requirement": selected,
            "upstream": [by_id[ident] for ident in upstream_ids if ident in by_id],
            "downstream": downstream, "path": path}


def _text_signature(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def merge_requirements(payloads: list[list[dict]]) -> tuple[list[dict], list[str]]:
    """Fusionne sans perte les exigences homonymes issues de plusieurs sources."""
    merged: dict[str, dict] = {}
    warnings: list[str] = []
    for payload in payloads:
        for incoming in payload:
            if not isinstance(incoming, dict) or not incoming.get("id"):
                continue
            ident = str(incoming["id"])
            source = incoming.get("source") or "source inconnue"
            occurrences = list(incoming.get("occurrences") or [{"source": source}])
            current = merged.get(ident)
            if current is None:
                current = dict(incoming)
                current["occurrences"] = occurrences
                current["collision_variants"] = list(incoming.get("collision_variants") or [])
                merged[ident] = current
                continue
            current["occurrences"] = list(current.get("occurrences") or []) + occurrences
            existing_text = str(current.get("texte") or "")
            incoming_text = str(incoming.get("texte") or "")
            if _text_signature(existing_text) != _text_signature(incoming_text):
                current_source = current.get("source") or "source inconnue"
                if current_source == source:
                    warnings.append(
                        f"{ident}: occurrences différentes dans {source}; "
                        "la formulation canonique la plus complète est retenue"
                    )
                    if len(incoming_text.strip()) > len(existing_text.strip()):
                        current["texte"] = incoming_text
                else:
                    variants = list(current.get("collision_variants") or [])
                    candidates = [{"source": current_source, "texte": existing_text},
                                  {"source": source, "texte": incoming_text},
                                  *list(incoming.get("collision_variants") or [])]
                    known = {(_text_signature(item.get("texte")), str(item.get("source")))
                             for item in variants}
                    for candidate in candidates:
                        key = (_text_signature(candidate.get("texte")), str(candidate.get("source")))
                        if key[0] and key not in known:
                            known.add(key)
                            variants.append(candidate)
                    current["collision_variants"] = variants
                    warnings.append(f"{ident}: collision, divergence entre {current_source} et {source}; "
                                    "les deux formulations sont conservées pour arbitrage")
                    if len(incoming_text.strip()) > len(existing_text.strip()):
                        current["texte"] = incoming_text
                        current["source"] = source
            links = {(link.get("type"), link.get("target"))
                     for link in current.get("links", []) if isinstance(link, dict)}
            for link in incoming.get("links", []):
                if isinstance(link, dict) and (link.get("type"), link.get("target")) not in links:
                    current.setdefault("links", []).append(link)
            current["niveau"] = min(int(current.get("niveau", 0)), int(incoming.get("niveau", 0)))
    return list(merged.values()), warnings


def parse_corpus_payloads(files: list[tuple[str, bytes]]) -> tuple[list[dict], list[str]]:
    """Normalise et fusionne matrices et documents porteurs dexigences."""
    payloads, warnings = [], []
    for filename, data in files:
        suffix = Path(filename).suffix.lower()
        try:
            if suffix in {".xls", ".xlsx"}:
                rows, matrix_warnings = import_matrix(data, filename)
                payloads.append(rows)
                warnings.extend(matrix_warnings)
            elif suffix == ".json":
                payloads.append(corpus_io._unwrap(json.loads(data)))
            elif suffix in {".doc", ".docx", ".odt", ".txt"}:
                rows, document_warnings = import_document(data, filename)
                payloads.append(rows)
                warnings.extend(document_warnings)
            else:
                raise ValueError("formats acceptés : .json, .xls, .xlsx, .doc, .docx, .odt ou .txt")
        except Exception as exc:
            raise ValueError(f"Source invalide ({filename}) : {exc}") from exc

    raw_merged, collision_warnings = merge_requirements(payloads)
    warnings.extend(collision_warnings)
    payloads = [raw_merged]
    merged, seen = [], set()
    for raw in payloads:
        valid, errors = corpus_io.validate_corpus(raw)
        warnings.extend(errors)
        for req in valid:
            if req["id"] in seen:
                warnings.append(f"{req['id']}: doublon inter-fichiers ignoré")
                continue
            seen.add(req["id"])
            merged.append(req)
    if not merged:
        raise ValueError("Aucune exigence valide dans les fichiers fournis.")
    return merged, warnings
