"""
design/README.md 에서 DesignScreen 노드를 추출하는 추출기.

- DesignScreen 노드: ## / ### 헤딩 단위
  id = design:README#{anchor}
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from tools.traceability.config import get_config
from tools.traceability.extractors import register
from tools.traceability.model import TraceIndex, TraceNode

# ## / ### 헤딩 패턴
_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+)$")


def _slug(text: str) -> str:
    """헤딩 텍스트를 anchor slug 로 변환한다."""
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    return s.strip("-")


@register("design")
def extract(repo_root: Path, index: TraceIndex) -> None:
    """
    design/README.md 의 ## / ### 헤딩에서 DesignScreen 노드를 추출한다.

    Args:
        repo_root: 리포지토리 루트 경로
        index: 트레이서빌리티 인덱스
    """
    design_readme = get_config(repo_root).path("design_readme")
    readme_path = repo_root / design_readme
    if not readme_path.exists():
        print(
            f"[design] 경고: {design_readme} 파일 없음 — 건너뜀",
            file=sys.stderr,
        )
        return

    try:
        text = readme_path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(
            f"[design] 경고: {design_readme} 읽기 실패: {exc}",
            file=sys.stderr,
        )
        return

    lines = text.splitlines()
    # anchor 중복 처리용 카운터
    anchor_count: dict[str, int] = {}

    for lineno, line in enumerate(lines, start=1):
        m = _HEADING_RE.match(line)
        if not m:
            continue

        heading_text = m.group(2).strip()
        level = len(m.group(1))
        base_anchor = _slug(heading_text)

        # GitHub 스타일 중복 anchor 처리 (두 번째부터 -1, -2, ...)
        if base_anchor not in anchor_count:
            anchor_count[base_anchor] = 0
            anchor = base_anchor
        else:
            anchor_count[base_anchor] += 1
            anchor = f"{base_anchor}-{anchor_count[base_anchor]}"

        screen_id = f"design:README#{anchor}"

        node = TraceNode(
            id=screen_id,
            type="DesignScreen",
            source_file=design_readme,
            source_loc=f"L{lineno}",
            title=heading_text,
            attrs={"level": level},
        )
        index.add_node(node)
