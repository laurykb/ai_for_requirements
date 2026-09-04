#!/usr/bin/env python3
"""Validation reproductible à exécuter avant toute livraison/export."""
from __future__ import annotations
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MINIMUM_NODE_VERSION = (20, 9, 0)


def run(*command: str, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True, env=env)


def node_version(node: Path) -> tuple[int, int, int] | None:
    try:
        output = subprocess.check_output(
            [str(node), "--version"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", output)
    return tuple(map(int, match.groups())) if match else None


def frontend_env() -> dict[str, str]:
    candidates: list[Path] = []
    if current_node := shutil.which("node"):
        candidates.append(Path(current_node).resolve())

    nvm_dir = Path(os.environ.get("NVM_DIR", Path.home() / ".nvm"))
    candidates.extend(
        sorted(
            nvm_dir.glob("versions/node/*/bin/node"),
            key=lambda path: node_version(path) or (0, 0, 0),
            reverse=True,
        )
    )

    for node in dict.fromkeys(candidates):
        version = node_version(node)
        npm = node.parent / "npm"
        if version and version >= MINIMUM_NODE_VERSION and npm.is_file():
            env = os.environ.copy()
            env["PATH"] = f"{node.parent}{os.pathsep}{env.get('PATH', '')}"
            print(
                f"Node.js retenu : {'.'.join(map(str, version))} ({node})",
                flush=True,
            )
            return env

    required = ".".join(map(str, MINIMUM_NODE_VERSION))
    found = node_version(Path(shutil.which("node") or "node"))
    detail = f" (version active : {'.'.join(map(str, found))})" if found else ""
    raise RuntimeError(
        f"Node.js >= {required} est requis pour valider le frontend{detail}. "
        "Installez Node 22 (par exemple avec `nvm install 22`) puis relancez l'export."
    )


def validate_python_syntax(*paths: Path) -> None:
    for path in paths:
        print(f"+ validation syntaxique {path}", flush=True)
        source = path.read_text(encoding="utf-8")
        compile(source, str(path), "exec")


def main() -> int:
    # Le cache pytest n'est pas utile à une recette reproductible et peut avoir
    # été créé par root lors d'un ancien packaging. Ne jamais en dépendre.
    run(str(ROOT / ".venv" / "bin" / "pytest"), "-q", "-p", "no:cacheprovider")
    node_env = frontend_env()
    run("npm", "run", "lint", cwd=ROOT / "web", env=node_env)
    run("npm", "run", "build", cwd=ROOT / "web", env=node_env)
    standalone = ROOT / "web" / ".next" / "standalone" / "server.js"
    if not standalone.is_file():
        raise RuntimeError("Build Next.js standalone absent : export hors ligne impossible.")
    run("bash", "-n", str(ROOT / "deploy" / "offline.sh"))
    run("docker", "compose", "config", "--quiet")
    validate_python_syntax(
        ROOT / "scripts" / "audit_export.py",
        ROOT / "scripts" / "repair_vector_index.py",
        ROOT / "core" / "index_consistency.py",
        ROOT / "indexing" / "embedding.py",
        ROOT / "serve.py",
    )
    run(str(ROOT / ".venv" / "bin" / "python"), str(ROOT / "scripts" / "rag_quality_gate.py"))
    print("Export validé : contrats, tests, frontend et qualité RAG conformes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
