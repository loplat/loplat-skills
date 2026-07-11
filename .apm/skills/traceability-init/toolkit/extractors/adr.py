"""
docs/adr/*.md 에서 ADR 노드와 ADR→REQ references 엣지를 추출하는 추출기.

추출 대상:
- frontmatter `id: ADR-NNNN` → ADR 노드
- frontmatter `related:` 목록 내 `REQ-NNN` 항목 → references 엣지 (ADR → REQ)

YAML 파서(pyyaml) 없이 stdlib re 를 사용해 frontmatter 를 파싱한다.
Phase 3 에서 pyyaml 이 도입되더라도 lazy import 로 감싸 build_index 는
pyyaml 없이도 동작하게 설계한다.
"""

from __future__ import annotations

import re
from pathlib import Path

from tools.traceability.config import get_config
from tools.traceability.extractors import register
from tools.traceability.model import TraceEdge, TraceIndex, TraceNode

# frontmatter 블록 추출 (--- ... --- 사이)
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)

# frontmatter 에서 id 필드 추출
_ID_RE = re.compile(r"^id:\s*(ADR-\d{4})\s*$", re.MULTILINE)

# frontmatter 에서 title 필드 추출
_TITLE_RE = re.compile(r"^title:\s*(.+?)\s*$", re.MULTILINE)

# frontmatter 에서 status 필드 추출
_STATUS_RE = re.compile(r"^status:\s*(.+?)\s*$", re.MULTILINE)

# frontmatter 에서 date 필드 추출
_DATE_RE = re.compile(r"^date:\s*(.+?)\s*$", re.MULTILINE)

# related 블록 내 REQ-NNN 항목 추출
_RELATED_BLOCK_RE = re.compile(r"^related:\s*\n((?:\s+-\s+\S+\n?)+)", re.MULTILINE)
_REQ_IN_RELATED_RE = re.compile(r"REQ-\d+")


@register("adr")
def extract(repo_root: Path, index: TraceIndex) -> None:
    """
    docs/adr/*.md 파일을 순회해 ADR 노드와 references 엣지를 추출한다.

    파일명 정렬로 결정론적 순서를 보장한다.
    """
    adr_dir = repo_root / get_config(repo_root).path("adr_dir")
    if not adr_dir.exists():
        return

    # 파일명 기준 정렬로 결정론적 처리 순서 보장
    adr_files = sorted(adr_dir.glob("*.md"))

    for adr_path in adr_files:
        # README.md 는 ADR 이 아니므로 건너뜀
        if adr_path.name.upper() == "README.MD":
            continue

        _process_adr_file(repo_root, adr_path, index)


def _process_adr_file(repo_root: Path, adr_path: Path, index: TraceIndex) -> None:
    """단일 ADR 파일을 파싱해 노드와 엣지를 index 에 추가한다."""
    text = adr_path.read_text(encoding="utf-8")
    rel_path = str(adr_path.relative_to(repo_root))

    # frontmatter 블록 추출
    fm_match = _FRONTMATTER_RE.match(text)
    if not fm_match:
        return  # frontmatter 없는 파일은 건너뜀
    frontmatter = fm_match.group(1)

    # id 필드 추출
    id_match = _ID_RE.search(frontmatter)
    if not id_match:
        return  # id 없는 ADR 은 건너뜀
    adr_id = id_match.group(1)  # 예: ADR-0006

    # title, status 추출 (없으면 None)
    title_match = _TITLE_RE.search(frontmatter)
    title = title_match.group(1) if title_match else None

    status_match = _STATUS_RE.search(frontmatter)
    status = status_match.group(1) if status_match else None

    date_match = _DATE_RE.search(frontmatter)
    date = date_match.group(1) if date_match else None

    # frontmatter 종료 라인 번호 계산 (source_loc 으로 사용)
    fm_end_lineno = text[: fm_match.end()].count("\n") + 1
    source_loc = f"L1-L{fm_end_lineno}"

    node = TraceNode(
        id=adr_id,
        type="ADR",
        source_file=rel_path,
        source_loc=source_loc,
        title=title,
        attrs={k: v for k, v in [("status", status), ("date", date)] if v is not None},
    )
    index.add_node(node)

    # related 블록에서 REQ 참조 추출 → references 엣지 생성
    related_match = _RELATED_BLOCK_RE.search(frontmatter)
    if related_match:
        related_block = related_match.group(1)
        for req_id in _REQ_IN_RELATED_RE.findall(related_block):
            edge = TraceEdge(
                type="references",
                source=adr_id,
                target=req_id,
                origin="auto",
                evidence=f"{rel_path} frontmatter related:",
            )
            index.add_edge(edge)
