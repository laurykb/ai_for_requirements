#!/usr/bin/env python3
"""Contrôle statique d'un paquet Docker produit avant transfert hors ligne."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REQUIRED = (
    ".env.compose.example", "compose.yaml", "serve.py", "deploy/offline.sh",
    "docs/INSTALLATION_HORS_LIGNE.md", "images.tar",
    "models/ollama-models.tsv", "models/bge-reranker-v2-m3/model.safetensors",
    "models/ollama-store/blobs",
    "models/ollama-store/manifests", "data/mongo.archive", "SHA256SUMS",
)
FORBIDDEN_PARTS = {
    ".git", ".venv", "node_modules", "tests", "evals", ".playwright-cli",
    ".claude", ".worktrees", "superpowers", "__pycache__", ".pytest_cache",
}
FORBIDDEN_NAMES = {
    ".env", ".env.local", ".offline-installed", "SESSION_HANDOFF_UX_RPP.md", "nohup.out",
}
MACHINE_PATHS = ("/home/marsattacks", "/mnt/swap-files")
TRACE_PATTERNS = (
    re.compile(r"BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
)


def audit(root: Path) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    files = [path for path in root.rglob("*") if path.is_file()]
    for item in REQUIRED:
        if not (root / item).exists():
            errors.append(f"élément runtime manquant : {item}")
    manifest = root / "models" / "ollama-models.tsv"
    if manifest.is_file() and not manifest.read_text(encoding="utf-8").strip():
        errors.append("manifeste Ollama vide")
    for path in files:
        if path.name == "OFFLINE_AUDIT.json":
            continue
        rel = path.relative_to(root)
        standalone_modules = rel.parts[:4] == ("web", ".next", "standalone", "node_modules")
        forbidden_parts = any(part in FORBIDDEN_PARTS for part in rel.parts)
        if standalone_modules:
            forbidden_parts = False
        if path.name in FORBIDDEN_NAMES or forbidden_parts:
            errors.append(f"artefact interdit dans le produit : {rel}")
            continue
        if standalone_modules:
            continue  # dépendances minimales produites et tracées par Next.js
        if path.is_symlink() and path.resolve().is_absolute() and root not in path.resolve().parents:
            errors.append(f"lien symbolique externe non portable : {rel}")
        if path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern in TRACE_PATTERNS:
            if pattern.search(text):
                errors.append(f"trace ou secret potentiel ({pattern.pattern}) : {rel}")
                break
        if any(machine_path in text for machine_path in MACHINE_PATHS):
            errors.append(f"chemin propre à la machine source : {rel}")
        cloud_endpoints = ("api." + "openai.com", "api." + "anthropic.com")
        if any(endpoint in text for endpoint in cloud_endpoints):
            errors.append(f"endpoint cloud détecté : {rel}")
    total = sum(path.stat().st_size for path in files)
    model_manifest = root / "models" / "ollama-models.tsv"
    minimal_models = all(
        (root / "models" / item).is_file()
        for item in (
            "bge-m3.gguf", "generation.gguf", "Modelfile.bge-m3",
            "Modelfile.generation", "generation.name",
        )
    )
    if not model_manifest.is_file() and not minimal_models:
        errors.append("modèles Ollama absents ou incomplets")
    return {"ok": not errors, "root": str(root), "files": len(files),
            "bytes": total, "errors": sorted(set(errors)),
            "warnings": sorted(set(warnings))}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = audit(args.root.resolve())
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.report:
        args.report.write_text(rendered + "\n", encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
