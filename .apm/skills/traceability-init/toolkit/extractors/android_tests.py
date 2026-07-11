"""
android/app/src/test/**/*.kt (및 androidTest) 에서 TestCase 노드와 validates 엣지를 추출한다.

- TestCase 노드: id = {repo 상대 경로}::{함수명}
- validates 엣지: @Test 직전 주석의 UC ID (// UC-10-N-06: 설명)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from tools.traceability.config import get_config
from tools.traceability.extractors import register
from tools.traceability.model import TraceEdge, TraceIndex, TraceNode

_UC_COMMENT_RE = re.compile(r"//\s*(UC-\d{1,2}-[CMN]-\d{2})\b")
_TEST_ANNOTATION_RE = re.compile(r"@Test\b")
_FUN_TEST_RE = re.compile(r"^\s*fun\s+(?:`([^`]+)`|(\w+))\s*\(")


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
            f"[android_tests] 경고: {rel_path} 읽기 실패: {exc}",
            file=sys.stderr,
        )
        return

    lines = source.splitlines()
    pending_uc: list[str] = []
    expect_fun = False

    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()

        uc_match = _UC_COMMENT_RE.search(stripped)
        if uc_match:
            pending_uc.append(uc_match.group(1))
            continue

        if _TEST_ANNOTATION_RE.search(stripped):
            expect_fun = True
            continue

        if expect_fun:
            fun_match = _FUN_TEST_RE.match(line)
            if fun_match:
                func_name = fun_match.group(1) or fun_match.group(2)
                tc_id = f"{rel_path}::{func_name}"

                tc_node = TraceNode(
                    id=tc_id,
                    type="TestCase",
                    source_file=rel_path,
                    source_loc=f"L{lineno}",
                    title=func_name,
                    attrs={"platform": "android"},
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
                expect_fun = False
            elif stripped and not stripped.startswith("//"):
                expect_fun = False
            continue

        if stripped and not stripped.startswith("//") and not stripped.startswith("@"):
            pending_uc = []


@register("android_tests")
def extract(repo_root: Path, index: TraceIndex) -> None:
    """
    Android 단위/계측 테스트에서 TestCase 노드와 validates 엣지를 추출한다.
    """
    kt_files: list[Path] = []
    for test_dir in get_config(repo_root).path_list("android_test_dirs"):
        root = repo_root / test_dir
        if not root.exists():
            continue
        kt_files.extend(root.rglob("*.kt"))

    for file_path in sorted(kt_files):
        try:
            _process_file(repo_root, file_path, index)
        except Exception as exc:  # noqa: BLE001
            rel = str(file_path.relative_to(repo_root))
            print(
                f"[android_tests] 경고: {rel} 처리 실패: {exc}",
                file=sys.stderr,
            )
