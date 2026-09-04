"""Registre de preuves typé entre extraction corpus et rédaction finale."""
from __future__ import annotations

import hashlib
import re

FIELDS = {
    "axe": "axis", "axis": "axis", "categorie": "category",
    "catégorie": "category", "element": "element", "élément": "element",
    "caracterisation": "characterization", "caractérisation": "characterization",
    "objectif": "target_objective", "objectif_lie": "target_objective",
    "objectif lié": "target_objective", "preuve": "evidence_quote",
}


def _clean(value: str) -> str:
    return value.strip().strip('"“”«» ')


def parse_evidence_item(item: str, source: str, source_text: str = "") -> dict:
    values = {name: "" for name in set(FIELDS.values())}
    for part in re.split(r"\s*\|\s*", str(item or "")):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        canonical = FIELDS.get(key.strip().casefold().replace("_", " "))
        if canonical:
            values[canonical] = _clean(value)
    if not values["element"]:
        values["element"] = str(item or "").strip()
    quote = values["evidence_quote"]
    verified = bool(quote and quote.casefold() in (source_text or "").casefold())
    status = "validated" if verified else "unverified" if quote else "ambiguous"
    identity = "|".join((values["axis"], values["category"], values["element"],
                         values["target_objective"], source)).casefold()
    return {"id": hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12],
            **values, "source": source, "evidence_verified": verified,
            "status": status, "raw": str(item or "")}


def build_registry(extractions: list[dict]) -> dict:
    candidates = []
    for extraction in extractions or []:
        source = extraction.get("document") or "inconnu"
        supplied = extraction.get("candidates")
        candidates.extend(supplied if isinstance(supplied, list) else
                          [parse_evidence_item(item, source)
                           for item in extraction.get("items") or []])
    groups: dict[tuple, dict] = {}
    for candidate in candidates:
        key = tuple(str(candidate.get(field) or "").casefold().strip()
                    for field in ("axis", "category", "element", "target_objective"))
        row = groups.get(key)
        if row is None:
            row = {field: candidate.get(field, "") for field in
                   ("axis", "category", "element", "characterization", "target_objective")}
            row.update({"sources": [], "evidence": [], "candidate_ids": [],
                        "status": "validated" if candidate.get("evidence_verified") else "to_review"})
            groups[key] = row
        if candidate.get("source") not in row["sources"]:
            row["sources"].append(candidate.get("source"))
        if candidate.get("evidence_quote"):
            row["evidence"].append({"quote": candidate["evidence_quote"],
                                    "source": candidate.get("source"),
                                    "verified": bool(candidate.get("evidence_verified"))})
        row["candidate_ids"].append(candidate.get("id"))
        if candidate.get("evidence_verified"):
            row["status"] = "validated"
    rows = list(groups.values())
    return {"rows": rows, "candidates": len(candidates), "consolidated": len(rows),
            "validated": sum(row["status"] == "validated" for row in rows),
            "to_review": sum(row["status"] != "validated" for row in rows)}


def registry_material(registry: dict) -> str:
    lines = []
    for row in registry.get("rows") or []:
        sources = ", ".join(f"src: {source}" for source in row["sources"])
        lines.append(
            f"AXE={row['axis']} | CATÉGORIE={row['category']} | ÉLÉMENT={row['element']} | "
            f"CARACTÉRISATION={row['characterization']} | OBJECTIF={row['target_objective']} "
            f"[{sources}]"
        )
    return "\n".join(lines)
