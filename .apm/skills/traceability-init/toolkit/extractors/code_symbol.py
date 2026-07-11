"""
backend/app/api/v1/*.py, backend/app/services/*.py,
backend/app/domain/**/*.py 에서 CodeSymbol 노드를 추출하는 추출기.

- CodeSymbol 노드: 최상위 class / def / async def
  id = {repo 상대 경로}:{심볼명}
- route decorator (@router.<method>(...)) 파싱:
  path + method 를 CodeSymbol attrs 에 기록
- routed_to 엣지: ApiOperation → CodeSymbol
  (path+method 가 openapi 와 일치할 때만, best-effort)
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

# HTTP 메서드 (router.METHOD 형태)
_ROUTE_METHODS = {"get", "post", "put", "delete", "patch", "options", "head"}


def _normalize_path(path: str) -> str:
    """
    path parameter placeholder 이름을 제거해 비교 가능한 형태로 정규화한다.

    {device_id} 와 {deviceId} 같은 이름 차이를 흡수하기 위해
    모든 {…} 를 {} 로 치환한다.

    Args:
        path: 원본 HTTP path 문자열

    Returns:
        placeholder 이름을 제거한 정규화 경로
    """
    return re.sub(r"\{[^}]+\}", "{}", path.rstrip("/"))


def _build_openapi_lookup(repo_root: Path) -> dict[tuple[str, str], str]:
    """
    openapi.json 에서 (normalized_path, method_upper) → operationId 매핑 생성.

    양측 path 를 _normalize_path 와 동일한 규칙으로 정규화해
    {deviceId} vs {device_id} 같은 이름 차이를 흡수한다.

    Args:
        repo_root: 리포지토리 루트 경로

    Returns:
        (normalized_path, method) → operationId 딕셔너리
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
                # OpenAPI 쪽도 동일한 정규화 적용 (양측 일치 보장)
                lookup[(_normalize_path(path), method.upper())] = op_id

    return lookup


def _collect_target_files(repo_root: Path) -> list[Path]:
    """
    대상 파일 목록을 수집하고 정렬해 반환한다.

    Args:
        repo_root: 리포지토리 루트 경로

    Returns:
        정렬된 파일 경로 목록
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
    함수의 데코레이터에서 route 정보를 추출한다.
    @router.METHOD(path_str, ...) 또는 @router.METHOD("path", ...)

    멀티라인 데코레이터도 AST 를 사용하므로 견고하게 처리된다.

    Args:
        decorator_list: AST 데코레이터 노드 목록

    Returns:
        [(path_str, method_upper), ...] 목록
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

        # 첫 번째 위치 인자가 path 문자열
        if not dec.args:
            continue
        path_arg = dec.args[0]
        if isinstance(path_arg, ast.Constant) and isinstance(path_arg.value, str):
            routes.append((path_arg.value, method.upper()))

    return routes


def _get_router_prefix(tree: ast.Module) -> str:
    """
    모듈에서 router = APIRouter(prefix="...") 의 prefix 를 추출한다.
    prefix 를 알 수 없으면 빈 문자열을 반환한다.

    Args:
        tree: AST 모듈 노드

    Returns:
        prefix 문자열 (없으면 "")
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        # router = APIRouter(...)
        if not isinstance(node.value, ast.Call):
            continue
        call = node.value
        # APIRouter 확인
        call_name = ""
        if isinstance(call.func, ast.Name):
            call_name = call.func.id
        elif isinstance(call.func, ast.Attribute):
            call_name = call.func.attr
        if call_name != "APIRouter":
            continue

        # prefix 키워드 인자 찾기
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
    단일 파일을 AST 로 파싱해 CodeSymbol 노드와 routed_to 엣지를 추출한다.

    Args:
        repo_root: 리포지토리 루트 경로
        file_path: 파싱할 파일 절대 경로
        index: 트레이서빌리티 인덱스
        openapi_lookup: (path, method) → operationId 매핑
    """
    rel_path = str(file_path.relative_to(repo_root))

    try:
        source = file_path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(
            f"[code_symbol] 경고: {rel_path} 읽기 실패: {exc}",
            file=sys.stderr,
        )
        return

    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError as exc:
        print(
            f"[code_symbol] 경고: {rel_path} AST 파싱 실패: {exc} — 건너뜀",
            file=sys.stderr,
        )
        return

    # 라우터 prefix 추출 (api/v1 파일에 사용)
    router_prefix = _get_router_prefix(tree)

    # 최상위 정의(class/def/async def)만 처리
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        symbol_name = node.name
        symbol_id = f"{rel_path}:{symbol_name}"

        # route 정보 추출 (함수/메서드에만 해당)
        route_info: list[tuple[str, str]] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            route_info = _extract_route_info(node.decorator_list)

        # route_path, route_method attrs 구성
        attrs: dict = {}
        if route_info:
            # 여러 데코레이터가 있을 경우 첫 번째 사용
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

        # routed_to 엣지 생성 (path+method 가 openapi 와 일치할 때)
        for r_path, r_method in route_info:
            # prefix 결합 후 정규화
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
    backend 코드에서 CodeSymbol 노드와 routed_to 엣지를 추출한다.

    ConsentsService, AuthService 를 포함한 모든 최상위 클래스/함수가 추출된다.

    Args:
        repo_root: 리포지토리 루트 경로
        index: 트레이서빌리티 인덱스
    """
    files = _collect_target_files(repo_root)
    if not files:
        print(
            "[code_symbol] 경고: 대상 파일 없음 — 건너뜀",
            file=sys.stderr,
        )
        return

    # OpenAPI 경로 → operationId 매핑 (routed_to 엣지 생성용)
    openapi_lookup = _build_openapi_lookup(repo_root)

    for file_path in files:
        try:
            _process_file(repo_root, file_path, index, openapi_lookup)
        except Exception as exc:  # noqa: BLE001
            rel = str(file_path.relative_to(repo_root))
            print(
                f"[code_symbol] 경고: {rel} 처리 실패: {exc}",
                file=sys.stderr,
            )
