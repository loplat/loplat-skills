"""
Extractor that pulls CodeSymbol nodes from backend/app/api/v1/*.py,
backend/app/services/*.py, and backend/app/domain/**/*.py.

- CodeSymbol node: a top-level class / def / async def
  id = {repo-relative path}:{symbol name}
- Route decorator (@router.<method>(...)) parsing:
  records path + method into CodeSymbol attrs
- routed_to edge: ApiOperation → CodeSymbol
  (only when path+method matches openapi, best-effort)
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

from tools.traceability.config import get_config
from tools.traceability.extractors import register
from tools.traceability.model import TraceEdge, TraceIndex, TraceNode

# HTTP methods (in router.METHOD form)
_ROUTE_METHODS = {"get", "post", "put", "delete", "patch", "options", "head"}


def _normalize_path(path: str) -> str:
    """
    Strip path parameter placeholder names to normalize into a comparable form.

    Replace every {…} with {} to absorb name differences such as
    {device_id} vs {deviceId}.

    Args:
        path: raw HTTP path string

    Returns:
        the normalized path with placeholder names stripped
    """
    return re.sub(r"\{[^}]+\}", "{}", path.rstrip("/"))


def _build_openapi_lookup(repo_root: Path) -> dict[tuple[str, str], str]:
    """
    Build a (normalized_path, method_upper) → operationId mapping from openapi.json.

    Both sides' paths are normalized with the same rule as _normalize_path
    to absorb name differences such as {deviceId} vs {device_id}.

    Args:
        repo_root: repository root path

    Returns:
        a dict mapping (normalized_path, method) → operationId
    """
    openapi_path = repo_root / get_config(repo_root).path("openapi")
    if not openapi_path.exists():
        return {}

    try:
        data = json.loads(openapi_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}

    lookup: dict[tuple[str, str], str] = {}
    http_methods = {"get", "post", "put", "delete", "patch", "options", "head"}

    for path, methods in data.get("paths", {}).items():
        for method, op_info in methods.items():
            if method.lower() not in http_methods:
                continue
            if not isinstance(op_info, dict):
                continue
            op_id = op_info.get("operationId")
            if op_id:
                # Apply the same normalization to the OpenAPI side (guarantees both sides match)
                lookup[(_normalize_path(path), method.upper())] = op_id

    return lookup


def _collect_target_files(repo_root: Path) -> list[Path]:
    """
    Collect and sort the list of target files.

    Args:
        repo_root: repository root path

    Returns:
        a sorted list of file paths
    """
    seen: set[Path] = set()
    files: list[Path] = []

    for pattern in get_config(repo_root).path_list("code_globs"):
        for p in sorted(repo_root.glob(pattern)):
            if p.name == "__init__.py":
                continue
            if p not in seen:
                seen.add(p)
                files.append(p)

    return sorted(files)


def _extract_route_info(
    decorator_list: list[ast.expr],
) -> list[tuple[str, str]]:
    """
    Extract route information from a function's decorators.
    @router.METHOD(path_str, ...) or @router.METHOD("path", ...)

    Multi-line decorators are also handled robustly since AST is used.

    Args:
        decorator_list: list of AST decorator nodes

    Returns:
        a list of [(path_str, method_upper), ...]
    """
    routes: list[tuple[str, str]] = []

    for dec in decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        func = dec.func
        if not isinstance(func, ast.Attribute):
            continue
        method = func.attr.lower()
        if method not in _ROUTE_METHODS:
            continue

        # The first positional argument is the path string
        if not dec.args:
            continue
        path_arg = dec.args[0]
        if isinstance(path_arg, ast.Constant) and isinstance(path_arg.value, str):
            routes.append((path_arg.value, method.upper()))

    return routes


def _get_router_prefix(tree: ast.Module) -> str:
    """
    Extract the prefix from router = APIRouter(prefix="...") in the module.
    Returns an empty string if the prefix cannot be determined.

    Args:
        tree: AST module node

    Returns:
        the prefix string (empty if absent)
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        # router = APIRouter(...)
        if not isinstance(node.value, ast.Call):
            continue
        call = node.value
        # Check for APIRouter
        call_name = ""
        if isinstance(call.func, ast.Name):
            call_name = call.func.id
        elif isinstance(call.func, ast.Attribute):
            call_name = call.func.attr
        if call_name != "APIRouter":
            continue

        # Find the prefix keyword argument
        for kw in call.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                return str(kw.value.value)

    return ""


def _process_file(
    repo_root: Path,
    file_path: Path,
    index: TraceIndex,
    openapi_lookup: dict[tuple[str, str], str],
) -> None:
    """
    Parse a single file with AST and extract CodeSymbol nodes and routed_to edges.

    Args:
        repo_root: repository root path
        file_path: absolute path of the file to parse
        index: traceability index
        openapi_lookup: (path, method) → operationId mapping
    """
    rel_path = str(file_path.relative_to(repo_root))

    try:
        source = file_path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(
            f"[code_symbol] warning: failed to read {rel_path}: {exc}",
            file=sys.stderr,
        )
        return

    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError as exc:
        print(
            f"[code_symbol] warning: AST parse failed for {rel_path}: {exc} — skipping",
            file=sys.stderr,
        )
        return

    # Extract the router prefix (used for api/v1 files)
    router_prefix = _get_router_prefix(tree)

    # Process only top-level definitions (class/def/async def)
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        symbol_name = node.name
        symbol_id = f"{rel_path}:{symbol_name}"

        # Extract route info (applies only to functions/methods)
        route_info: list[tuple[str, str]] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            route_info = _extract_route_info(node.decorator_list)

        # Build route_path, route_method attrs
        attrs: dict = {}
        if route_info:
            # Use the first decorator if there are several
            r_path, r_method = route_info[0]
            attrs["route_path"] = r_path
            attrs["route_method"] = r_method

        sym_node = TraceNode(
            id=symbol_id,
            type="CodeSymbol",
            source_file=rel_path,
            source_loc=f"L{node.lineno}",
            title=symbol_name,
            attrs=attrs,
        )
        index.add_node(sym_node)

        # Create a routed_to edge (when path+method matches openapi)
        for r_path, r_method in route_info:
            # Combine with the prefix, then normalize
            full_path = _normalize_path(router_prefix + r_path) or "/"
            op_id = openapi_lookup.get((full_path, r_method))
            if op_id:
                edge = TraceEdge(
                    type="routed_to",
                    source=op_id,
                    target=symbol_id,
                    origin="auto",
                    evidence=f"{rel_path}:L{node.lineno}",
                )
                index.add_edge(edge)


@register("code_symbol")
def extract(repo_root: Path, index: TraceIndex) -> None:
    """
    Extract CodeSymbol nodes and routed_to edges from the backend code.

    Every top-level class/function is extracted, including ConsentsService
    and AuthService.

    Args:
        repo_root: repository root path
        index: traceability index
    """
    files = _collect_target_files(repo_root)
    if not files:
        print(
            "[code_symbol] warning: no target files found — skipping",
            file=sys.stderr,
        )
        return

    # OpenAPI path → operationId mapping (used to create routed_to edges)
    openapi_lookup = _build_openapi_lookup(repo_root)

    for file_path in files:
        try:
            _process_file(repo_root, file_path, index, openapi_lookup)
        except Exception as exc:  # noqa: BLE001
            rel = str(file_path.relative_to(repo_root))
            print(
                f"[code_symbol] warning: failed to process {rel}: {exc}",
                file=sys.stderr,
            )
