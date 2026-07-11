"""
backend/tests/**/*.py 에서 TestCase 노드와 validates 엣지를 추출하는 추출기.

- TestCase 노드: id = {repo 상대 경로}::{함수명}
- validates 엣지: @pytest.mark.uc("UC-...") 마커가 있을 때
  → TestCase → UseCase (target UseCase 노드가 없어도 엣지는 생성 — dangling 허용)
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

from tools.traceability.config import get_config
from tools.traceability.extractors import register
from tools.traceability.model import TraceEdge, TraceIndex, TraceNode

# @pytest.mark.uc("...") 패턴 — AST 파싱 실패 시 정규식 fallback 용
_UC_MARKER_RE = re.compile(r'@pytest\.mark\.uc\(\s*["\']([^"\']+)["\']\s*\)')


def _extract_uc_markers_from_decorators(
    decorators: list[ast.expr],
) -> list[str]:
    """
    함수의 데코레이터 목록에서 pytest.mark.uc("UC-...") 값을 추출한다.

    Args:
        decorators: AST 데코레이터 노드 목록

    Returns:
        UC ID 문자열 목록
    """
    uc_ids: list[str] = []
    for dec in decorators:
        # @pytest.mark.uc("UC-X-Y-NN") 패턴
        if not isinstance(dec, ast.Call):
            continue
        func = dec.func
        # pytest.mark.uc(...) 형태
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr != "uc":
            continue
        # func.value 가 pytest.mark 인지 확인
        if isinstance(func.value, ast.Attribute):
            if func.value.attr != "mark":
                continue
        elif not isinstance(func.value, ast.Name):
            continue

        # 인자에서 UC ID 추출
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
    단일 테스트 파일을 파싱해 TestCase 노드와 exercises 엣지를 추출한다.

    AST 파싱에 실패하면 정규식 fallback 으로 처리한다.

    Args:
        repo_root: 리포지토리 루트 경로
        file_path: 파싱할 파일 절대 경로
        index: 트레이서빌리티 인덱스
    """
    rel_path = str(file_path.relative_to(repo_root))
    try:
        source = file_path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(
            f"[pytest_tests] 경고: {rel_path} 읽기 실패: {exc}",
            file=sys.stderr,
        )
        return

    # AST 기반 파싱 시도
    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError as exc:
        print(
            f"[pytest_tests] 경고: {rel_path} AST 파싱 실패({exc}) — 정규식 fallback",
            file=sys.stderr,
        )
        _process_file_regex(repo_root, file_path, rel_path, source, index)
        return

    # 최상위 함수 및 클래스 내 함수를 순회
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # 함수명이 test_ 로 시작하는 것만 처리
        func_name = node.name
        if not func_name.startswith("test_"):
            continue

        # TestCase 노드 ID: {상대경로}::{함수명}
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

        # validates 엣지: @pytest.mark.uc("UC-...") 데코레이터에서 추출
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
    AST 파싱 실패 시 정규식으로 TestCase 와 uc 마커를 추출하는 fallback.

    Args:
        repo_root: 리포지토리 루트 경로 (미사용)
        file_path: 파싱할 파일 절대 경로 (미사용)
        rel_path: repo root 기준 상대 경로
        source: 파일 소스 텍스트
        index: 트레이서빌리티 인덱스
    """
    lines = source.splitlines()

    # 최근 uc 마커 축적 — def 전에 있는 마커들
    pending_uc: list[str] = []

    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()

        # @pytest.mark.uc(...) 데코레이터 수집
        m = _UC_MARKER_RE.search(stripped)
        if m:
            pending_uc.append(m.group(1))
            continue

        # def test_... 함수 정의
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
            # 데코레이터도 def 도 아닌 줄이 오면 pending 초기화
            # (단, 빈 줄/주석은 무시)
            if not stripped.startswith("#"):
                pending_uc = []


@register("pytest_tests")
def extract(repo_root: Path, index: TraceIndex) -> None:
    """
    backend/tests/**/*.py 파일을 순회해 TestCase 노드와 validates 엣지를
    추출한다.

    Args:
        repo_root: 리포지토리 루트 경로
        index: 트레이서빌리티 인덱스
    """
    pytest_dir = get_config(repo_root).path("pytest_dir")
    test_dir = repo_root / pytest_dir
    if not test_dir.exists():
        print(
            f"[pytest_tests] 경고: {pytest_dir} 디렉토리 없음 — 건너뜀",
            file=sys.stderr,
        )
        return

    # 결정성 보장: 파일명 기준 정렬
    py_files = sorted(test_dir.rglob("*.py"))

    for file_path in py_files:
        try:
            _process_file(repo_root, file_path, index)
        except Exception as exc:  # noqa: BLE001
            rel = str(file_path.relative_to(repo_root))
            print(
                f"[pytest_tests] 경고: {rel} 처리 실패: {exc}",
                file=sys.stderr,
            )
