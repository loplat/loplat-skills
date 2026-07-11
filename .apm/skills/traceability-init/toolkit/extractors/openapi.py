"""
docs/api/openapi.json 에서 ApiOperation 노드를 추출하는 추출기.

추출 대상: paths[path][method].operationId
정확히 44개 operation 을 추출한다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tools.traceability.config import get_config
from tools.traceability.extractors import register
from tools.traceability.model import TraceIndex, TraceNode

# 처리할 HTTP 메서드 집합
_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "options", "head"}


@register("openapi")
def extract(repo_root: Path, index: TraceIndex) -> None:
    """
    OpenAPI JSON 에서 ApiOperation 노드를 추출해 index 에 추가한다.

    각 노드:
    - id: operationId
    - attrs: path, method, tags

    Args:
        repo_root: 리포지토리 루트 경로
        index: 트레이서빌리티 인덱스
    """
    openapi_rel_path = get_config(repo_root).path("openapi")
    openapi_path = repo_root / openapi_rel_path
    if not openapi_path.exists():
        print(
            f"[openapi] 경고: {openapi_rel_path} 파일 없음 — 건너뜀",
            file=sys.stderr,
        )
        return

    try:
        data = json.loads(openapi_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(
            f"[openapi] 경고: {openapi_rel_path} 파싱 실패: {exc}",
            file=sys.stderr,
        )
        return

    paths = data.get("paths", {})
    # 결정성 보장: path 이름 기준 정렬
    for path in sorted(paths.keys()):
        methods = paths[path]
        # 결정성 보장: method 이름 기준 정렬
        for method in sorted(methods.keys()):
            if method.lower() not in _HTTP_METHODS:
                continue
            op_info = methods[method]
            if not isinstance(op_info, dict):
                continue

            operation_id = op_info.get("operationId")
            if not operation_id:
                print(
                    f"[openapi] 경고: {path} {method} 에 operationId 없음 — 건너뜀",
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
