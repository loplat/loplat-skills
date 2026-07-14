"""
Extractor that pulls TestCase nodes and validates edges from backend/tests/**/*.py.

- TestCase node: id = {repo-relative path}::{function name}
- validates edge: created when a @pytest.mark.uc("UC-...") marker is present
  → TestCase → UseCase (the edge is still created even when the target
  UseCase node doesn't exist — dangling edges are allowed)
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

from tools.traceability.config import get_config
from tools.traceability.extractors import register
from tools.traceability.model import TraceEdge, TraceIndex, TraceNode

# @pytest.mark.uc("...") pattern — used as a regex fallback when AST parsing fails
_UC_MARKER_RE = re.compile(r'@pytest\.mark\.uc\(\s*["\']([^"\']+)["\']\s*\)')


def _extract_uc_markers_from_decorators(
    decorators: list[ast.expr],
) -> list[str]:
    """
    Extract pytest.mark.uc("UC-...") values from a function's decorator list.

    Args:
        decorators: list of AST decorator nodes

    Returns:
        a list of UC ID strings
    """
    uc_ids: list[str] = []
    for dec in decorators:
        # @pytest.mark.uc("UC-X-Y-NN") pattern
        if not isinstance(dec, ast.Call):
            continue
        func = dec.func
        # pytest.mark.uc(...) form
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr != "uc":
            continue
        # Check whether func.value is pytest.mark
        if isinstance(func.value, ast.Attribute):
            if func.value.attr != "mark":
                continue
        elif not isinstance(func.value, ast.Name):
            continue

        # Extract the UC ID from the argument
        for arg in dec.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                uc_ids.append(arg.value)

    return uc_ids


def _process_file(
    repo_root: Path,
    file_path: Path,
    index: TraceIndex,
) -> None:
    """
    Parse a single test file and extract its TestCase nodes and exercises edges.

    Falls back to a regex-based approach if AST parsing fails.

    Args:
        repo_root: repository root path
        file_path: absolute path of the file to parse
        index: traceability index
    """
    rel_path = str(file_path.relative_to(repo_root))
    try:
        source = file_path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(
            f"[pytest_tests] warning: failed to read {rel_path}: {exc}",
            file=sys.stderr,
        )
        return

    # Attempt AST-based parsing
    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError as exc:
        print(
            f"[pytest_tests] warning: AST parse failed for {rel_path} ({exc}) — falling back to regex",
            file=sys.stderr,
        )
        _process_file_regex(repo_root, file_path, rel_path, source, index)
        return

    # Walk top-level functions and functions inside classes
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Only process functions whose name starts with test_
        func_name = node.name
        if not func_name.startswith("test_"):
            continue

        # TestCase node ID: {relative path}::{function name}
        tc_id = f"{rel_path}::{func_name}"
        lineno = node.lineno

        tc_node = TraceNode(
            id=tc_id,
            type="TestCase",
            source_file=rel_path,
            source_loc=f"L{lineno}",
            title=func_name,
            attrs={},
        )
        index.add_node(tc_node)

        # validates edge: extracted from the @pytest.mark.uc("UC-...") decorator
        uc_ids = _extract_uc_markers_from_decorators(node.decorator_list)
        for uc_id in uc_ids:
            edge = TraceEdge(
                type="validates",
                source=tc_id,
                target=uc_id,
                origin="auto",
                evidence=f"{rel_path}:L{lineno}",
            )
            index.add_edge(edge)


def _process_file_regex(
    repo_root: Path,
    file_path: Path,
    rel_path: str,
    source: str,
    index: TraceIndex,
) -> None:
    """
    Regex fallback that extracts TestCase nodes and uc markers when AST parsing fails.

    Args:
        repo_root: repository root path (unused)
        file_path: absolute path of the file to parse (unused)
        rel_path: path relative to the repo root
        source: file source text
        index: traceability index
    """
    lines = source.splitlines()

    # Accumulate recent uc markers — those appearing before a def
    pending_uc: list[str] = []

    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()

        # Collect @pytest.mark.uc(...) decorators
        m = _UC_MARKER_RE.search(stripped)
        if m:
            pending_uc.append(m.group(1))
            continue

        # def test_... function definition
        func_m = re.match(r"^(?:async\s+)?def\s+(test_\w+)\s*\(", stripped)
        if func_m:
            func_name = func_m.group(1)
            tc_id = f"{rel_path}::{func_name}"

            tc_node = TraceNode(
                id=tc_id,
                type="TestCase",
                source_file=rel_path,
                source_loc=f"L{lineno}",
                title=func_name,
                attrs={},
            )
            index.add_node(tc_node)

            for uc_id in pending_uc:
                edge = TraceEdge(
                    type="validates",
                    source=tc_id,
                    target=uc_id,
                    origin="auto",
                    evidence=f"{rel_path}:L{lineno}",
                )
                index.add_edge(edge)

            pending_uc = []
        elif not stripped.startswith("@") and stripped:
            # Reset pending when a line is neither a decorator nor a def
            # (blank lines/comments are ignored)
            if not stripped.startswith("#"):
                pending_uc = []


@register("pytest_tests")
def extract(repo_root: Path, index: TraceIndex) -> None:
    """
    Walk backend/tests/**/*.py files and extract TestCase nodes and
    validates edges.

    Args:
        repo_root: repository root path
        index: traceability index
    """
    pytest_dir = get_config(repo_root).path("pytest_dir")
    test_dir = repo_root / pytest_dir
    if not test_dir.exists():
        print(
            f"[pytest_tests] warning: {pytest_dir} directory not found — skipping",
            file=sys.stderr,
        )
        return

    # Ensure determinism: sort by filename
    py_files = sorted(test_dir.rglob("*.py"))

    for file_path in py_files:
        try:
            _process_file(repo_root, file_path, index)
        except Exception as exc:  # noqa: BLE001
            rel = str(file_path.relative_to(repo_root))
            print(
                f"[pytest_tests] warning: failed to process {rel}: {exc}",
                file=sys.stderr,
            )
