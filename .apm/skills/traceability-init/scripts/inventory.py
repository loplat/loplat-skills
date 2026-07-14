#!/usr/bin/env python3
"""traceability-init step 1: inventory scan of a project's doc/code assets.

Usage: python3 inventory.py [repo_root]
Output: JSON (stdout). No external dependencies (stdlib only).
Assumes no directory layout — doc assets are detected by name via a
bounded tree walk (depth 4).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SKIP_DIRS = {".git", ".bare", "node_modules", ".venv", "venv", "__pycache__",
             "build", "dist", ".gradle", "Pods", "DerivedData", "graphify-out",
             ".next", "target", "vendor"}
MAX_DEPTH = 4


def repo_root(arg: str | None) -> Path:
    if arg:
        return Path(arg).resolve()
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True)
        return Path(out.stdout.strip())
    except Exception:
        return Path.cwd()


def walk(root: Path):
    """Depth- and noise-bounded tree walk (yields both files and directories)."""
    base_depth = len(root.parts)
    for p in root.rglob("*"):
        if len(p.parts) - base_depth > MAX_DEPTH:
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        yield p


def scan(root: Path) -> tuple[list[str], dict[str, list[str]]]:
    """Collect file patterns and directory names in a single walk."""
    prd_hits: list[str] = []
    openapi_hits: list[str] = []
    dir_hits: dict[str, list[str]] = {"adr": [], "specs": [], "ontology": []}
    seen_dirs: set[str] = set()
    dir_names = {"adr": "adr", "decisions": "adr", "specs": "specs",
                 "spec": "specs", "ontology": "ontology"}
    for p in walk(root):
        rel = str(p.relative_to(root))
        name = p.name.lower()
        if p.is_dir():
            key = dir_names.get(name)
            # Drop case-only duplicates (macOS case-insensitive filesystems)
            if key and rel.lower() not in seen_dirs:
                seen_dirs.add(rel.lower())
                dir_hits[key].append(rel)
        elif p.suffix.lower() in (".md", ".json", ".yml", ".yaml"):
            if ("prd" in name or "requirement" in name) and len(prd_hits) < 20:
                prd_hits.append(rel)
            if ("openapi" in name or "swagger" in name) and len(openapi_hits) < 20:
                openapi_hits.append(rel)
    return prd_hits, {"prd": prd_hits, "openapi": openapi_hits, **dir_hits}


def main() -> None:
    root = repo_root(sys.argv[1] if len(sys.argv) > 1 else None)
    inv: dict = {"repo_root": str(root)}

    def exists(rel: str) -> bool:
        return (root / rel).exists()

    _, hits = scan(root)
    inv["docs"] = {
        "adr_dirs": hits["adr"],
        "prd_like": hits["prd"],
        "openapi": hits["openapi"],
        "specs_dirs": hits["specs"],
        "ontology_dirs": hits["ontology"],
        "design_dir": exists("design"),
    }
    inv["toolkit"] = {
        "vendored": exists("tools/traceability"),
        "config": any(exists(f"{d}/trace-config.yml") for d in hits["ontology"])
                  or exists("docs/ontology/trace-config.yml"),
    }
    inv["platforms"] = {
        "python": exists("pyproject.toml") or exists("requirements.txt")
                  or exists("backend/pyproject.toml"),
        "node": exists("package.json"),
        "go": exists("go.mod"),
        "android": exists("build.gradle") or exists("build.gradle.kts")
                   or exists("android"),
        "ios": bool(list(root.glob("*.xcodeproj")) + list(root.glob("*.xcworkspace"))
                    + list(root.glob("ios/*.xcodeproj"))),
        "flutter": exists("pubspec.yaml"),
    }
    inv["tests"] = {
        "pytest": exists("tests") or exists("backend/tests")
                  or exists("pytest.ini") or exists("conftest.py"),
        "jest_or_vitest": exists("package.json") and any(
            (root / f).exists() for f in
            ("jest.config.js", "jest.config.ts", "vitest.config.ts",
             "vitest.config.js", "vitest.config.mts")),
        "instructions": [f for f in ("AGENTS.md", "CLAUDE.md") if exists(f)],
    }

    d = inv["docs"]
    if d["ontology_dirs"] and inv["toolkit"]["vendored"]:
        profile = "already-initialized"
    elif not (d["adr_dirs"] or d["prd_like"]):
        profile = "not-ready"
    elif d["openapi"] and (inv["tests"]["pytest"] or inv["tests"]["jest_or_vitest"]):
        profile = "full-stack" if (inv["platforms"]["android"] or inv["platforms"]["ios"]
                                   or d["design_dir"]) else "backend-api"
    else:
        profile = "docs-only"
    inv["suggested_profile"] = profile

    json.dump(inv, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    main()
