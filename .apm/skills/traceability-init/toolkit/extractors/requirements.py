"""
docs/requirements/prd.md 에서 Requirement 노드를 추출하는 추출기.

추출 대상: `| REQ-NNN | 설명 | Must/Should/... | 출처 |` 형식의 Markdown 표 행.
zero-pad 를 그대로 보존한다 (예: REQ-002, REQ-077, REQ-102).
"""

from __future__ import annotations

import re
from pathlib import Path

from tools.traceability.config import get_config
from tools.traceability.extractors import register
from tools.traceability.model import TraceIndex, TraceNode

# REQ 행 패턴: `| REQ-NNN | 설명 | 우선순위 | 출처 |`
# 첫 번째 컬럼이 REQ-로 시작하는 행만 매칭
_ROW_RE = re.compile(
    r"^\|\s*(REQ-\d+)\s*\|\s*(.*?)\s*\|\s*([\w/]+)\s*\|",
    re.MULTILINE,
)


@register("requirements")
def extract(repo_root: Path, index: TraceIndex) -> None:
    """
    PRD Markdown 표에서 Requirement 노드를 추출해 index 에 추가한다.

    각 노드의 id 는 표 첫 번째 컬럼 값 그대로 사용 (zero-pad 보존).
    attrs 에 priority 를 저장한다.
    """
    prd_rel_path = get_config(repo_root).path("requirements")
    prd_path = repo_root / prd_rel_path
    if not prd_path.exists():
        return  # 파일이 없으면 조용히 건너뜀

    text = prd_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    for lineno, line in enumerate(lines, start=1):
        m = _ROW_RE.match(line)
        if not m:
            continue

        req_id = m.group(1)  # 예: REQ-102
        title_raw = m.group(2)  # 설명 (앞뒤 공백 제거됨)
        priority = m.group(3)  # 예: Must, Should, Superseded

        # 설명에서 스트라이크스루(~~) 마크다운 제거
        title = re.sub(r"~~.*?~~", "", title_raw).strip()
        # Markdown 링크 제거: [text](url) → text
        title = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", title)
        # 긴 설명 잘라내기 (제목으로 사용하므로 120자 제한)
        if len(title) > 120:
            title = title[:120] + "…"

        node = TraceNode(
            id=req_id,
            type="Requirement",
            source_file=prd_rel_path,
            source_loc=f"L{lineno}",
            title=title if title else None,
            attrs={"priority": priority},
        )
        index.add_node(node)
