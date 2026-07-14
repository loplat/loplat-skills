"""
Extractor that pulls ApiOperation nodes from docs/api/openapi.json.

Extracted items: paths[path][method].operationId
Extracts exactly 44 operations.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tools.traceability.config import get_config
from tools.traceability.extractors import register
from tools.traceability.model import TraceIndex, TraceNode

# Set of HTTP methods to process
_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "options", "head"}


@register("openapi")
def extract(repo_root: Path, index: TraceIndex) -> None:
    """
    Extract ApiOperation nodes from the OpenAPI JSON and add them to the index.

    Each node:
    - id: operationId
    - attrs: path, method, tags

    Args:
        repo_root: repository root path
        index: traceability index
    """
    openapi_rel_path = get_config(repo_root).path("openapi")
    openapi_path = repo_root / openapi_rel_path
    if not openapi_path.exists():
        print(
            f"[openapi] warning: {openapi_rel_path} not found — skipping",
            file=sys.stderr,
        )
        return

    try:
        data = json.loads(openapi_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(
            f"[openapi] warning: failed to parse {openapi_rel_path}: {exc}",
            file=sys.stderr,
        )
        return

    paths = data.get("paths", {})
    # Ensure determinism: sort by path name
    for path in sorted(paths.keys()):
        methods = paths[path]
        # Ensure determinism: sort by method name
        for method in sorted(methods.keys()):
            if method.lower() not in _HTTP_METHODS:
                continue
            op_info = methods[method]
            if not isinstance(op_info, dict):
                continue

            operation_id = op_info.get("operationId")
            if not operation_id:
                print(
                    f"[openapi] warning: {path} {method} has no operationId — skipping",
                    file=sys.stderr,
                )
                continue

            tags = op_info.get("tags", [])

            node = TraceNode(
                id=operation_id,
                type="ApiOperation",
                source_file=openapi_rel_path,
                source_loc=None,
                title=op_info.get("summary") or None,
                attrs={
                    "path": path,
                    "method": method.upper(),
                    "tags": sorted(tags) if tags else [],
                },
            )
            index.add_node(node)
