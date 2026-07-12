"""
ios/App/AppUnitTests/**/*.swift 및 ios/App/AppUITests/**/*.swift 에서
TestCase 노드와 validates 엣지를 추출한다.

- TestCase 노드: id = {repo 상대 경로}::{함수명}
- validates 엣지: 테스트 함수 앞의 요구사항 trace 주석에 포함된 IOS-REQ ID 또는 UC ID
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from tools.traceability.config import get_config
from tools.traceability.extractors import register
from tools.traceability.model import TraceEdge, TraceIndex, TraceNode

_IOS_REQ_RE = re.compile(r"\bIOS-REQ-(\d{3})(?:\s*~\s*(?:IOS-REQ-)?(\d{3}))?\b")
_UC_RE = re.compile(r"\bUC-\d{1,2}-[CMN]-\d{2}\b")
_TEST_ATTRIBUTE_RE = re.compile(r"@Test\b")
_FUNC_TEST_RE = re.compile(r"^\s*func\s+(?:`([^`]+)`|([A-Za-z_][A-Za-z0-9_]*))\s*\(")


def _extract_ios_req_ids(text: str) -> list[str]:
    """
    주석 문자열에서 IOS-REQ ID를 추출한다.

    IOS-REQ-038~041 같은 range 표기는 개별 ID로 확장한다.
    """
    req_ids: list[str] = []
    for match in _IOS_REQ_RE.finditer(text):
        start = int(match.group(1))
        end_text = match.group(2)
        if end_text is None:
            req_ids.append(f"IOS-REQ-{start:03d}")
            continue

        end = int(end_text)
        if end < start:
            req_ids.append(f"IOS-REQ-{start:03d}")
            continue

        req_ids.extend(f"IOS-REQ-{number:03d}" for number in range(start, end + 1))

    return req_ids


def _extract_uc_ids(text: str) -> list[str]:
    """주석 문자열에서 checklist UseCase ID를 추출한다."""
    return _UC_RE.findall(text)


def _extract_trace_target_ids(text: str) -> list[str]:
    return _extract_ios_req_ids(text) + _extract_uc_ids(text)


def _looks_like_test_attribute_continuation(stripped: str) -> bool:
    """Swift attribute argument 줄이면 @Test 이후 func 탐색을 계속한다."""
    return (
        stripped.startswith("@")
        or stripped.startswith(".")
        or stripped.startswith("(")
        or stripped.startswith(")")
        or stripped.endswith(",")
    )


def _add_test_case(
    index: TraceIndex,
    rel_path: str,
    func_name: str,
    lineno: int,
    evidence_line: int | None,
    target_ids: list[str],
    framework: str,
    kind: str,
) -> None:
    tc_id = f"{rel_path}::{func_name}"

    tc_node = TraceNode(
        id=tc_id,
        type="TestCase",
        source_file=rel_path,
        source_loc=f"L{lineno}",
        title=func_name,
        attrs={
            "platform": "ios",
            "framework": framework,
            "kind": kind,
        },
    )
    index.add_node(tc_node)

    for target_id in dict.fromkeys(target_ids):
        edge = TraceEdge(
            type="validates",
            source=tc_id,
            target=target_id,
            origin="auto",
            evidence=f"{rel_path}:L{evidence_line or lineno}",
        )
        index.add_edge(edge)


def _process_file(
    repo_root: Path,
    file_path: Path,
    index: TraceIndex,
) -> None:
    rel_path = str(file_path.relative_to(repo_root))
    try:
        source = file_path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(
            f"[ios_tests] 경고: {rel_path} 읽기 실패: {exc}",
            file=sys.stderr,
        )
        return

    lines = source.splitlines()
    is_ui_test_file = rel_path.startswith(get_config(repo_root).path("ios_ui_test_dir"))
    pending_target_ids: list[str] = []
    collecting_trace = False
    expect_func = False
    test_attribute_line: int | None = None

    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()

        if stripped.startswith("//"):
            if "요구사항 trace:" in stripped:
                pending_target_ids.extend(_extract_trace_target_ids(stripped))
                collecting_trace = True
            elif _extract_uc_ids(stripped):
                pending_target_ids.extend(_extract_uc_ids(stripped))
                collecting_trace = True
            elif collecting_trace:
                pending_target_ids.extend(_extract_trace_target_ids(stripped))
            continue

        if not stripped:
            continue

        if _TEST_ATTRIBUTE_RE.search(stripped):
            func_offset = line.find("func ")
            if func_offset >= 0:
                fun_match = _FUNC_TEST_RE.match(line[func_offset:])
                if fun_match:
                    func_name = fun_match.group(1) or fun_match.group(2)
                    _add_test_case(
                        index=index,
                        rel_path=rel_path,
                        func_name=func_name,
                        lineno=lineno,
                        evidence_line=lineno,
                        target_ids=pending_target_ids,
                        framework="SwiftTesting",
                        kind="unit",
                    )
                    pending_target_ids = []
                    collecting_trace = False
                    expect_func = False
                    test_attribute_line = None
                    continue

            expect_func = True
            collecting_trace = False
            test_attribute_line = lineno
            continue

        if expect_func:
            fun_match = _FUNC_TEST_RE.match(line)
            if fun_match:
                func_name = fun_match.group(1) or fun_match.group(2)
                _add_test_case(
                    index=index,
                    rel_path=rel_path,
                    func_name=func_name,
                    lineno=lineno,
                    evidence_line=test_attribute_line,
                    target_ids=pending_target_ids,
                    framework="SwiftTesting",
                    kind="unit",
                )

                pending_target_ids = []
                collecting_trace = False
                expect_func = False
                test_attribute_line = None
                continue

            if _looks_like_test_attribute_continuation(stripped):
                continue

            pending_target_ids = []
            collecting_trace = False
            expect_func = False
            test_attribute_line = None
            continue

        if stripped.startswith("@"):
            # @MainActor 같은 Swift attribute 는 trace 주석과 @Test 사이에 올 수 있다.
            continue

        if is_ui_test_file:
            fun_match = _FUNC_TEST_RE.match(line)
            if fun_match:
                func_name = fun_match.group(1) or fun_match.group(2)
                if func_name.startswith("test"):
                    _add_test_case(
                        index=index,
                        rel_path=rel_path,
                        func_name=func_name,
                        lineno=lineno,
                        evidence_line=lineno,
                        target_ids=pending_target_ids,
                        framework="XCUITest",
                        kind="ui",
                    )
                    pending_target_ids = []
                    collecting_trace = False
                    continue

        if collecting_trace:
            pending_target_ids = []
            collecting_trace = False


@register("ios_tests")
def extract(repo_root: Path, index: TraceIndex) -> None:
    """
    iOS Swift Testing 단위 테스트와 XCUITest UI 테스트에서 TestCase 노드와 validates 엣지를 추출한다.
    """
    swift_files: list[Path] = []
    for test_dir in get_config(repo_root).path_list("ios_test_dirs"):
        root = repo_root / test_dir
        if not root.exists():
            continue
        swift_files.extend(root.rglob("*.swift"))

    for file_path in sorted(swift_files):
        try:
            _process_file(repo_root, file_path, index)
        except Exception as exc:  # noqa: BLE001
            rel = str(file_path.relative_to(repo_root))
            print(
                f"[ios_tests] 경고: {rel} 처리 실패: {exc}",
                file=sys.stderr,
            )
