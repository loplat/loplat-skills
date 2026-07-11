"""
docs/requirements/usecase-coverage-checklist.md 에서
UseCaseCategory 노드와 UseCase 노드, 선택적 refines 엣지를 추출하는 추출기.

- UseCaseCategory: UC-1 ~ UC-14 (## N. 제목 섹션)
- UseCase: UC-{N}-{C|M|N}-{NN} 형식 ID (표 행)
- edge refines: 행/셀에 REQ-NNN 인용이 있을 때만 생성 (희소 정상)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from tools.traceability.config import get_config
from tools.traceability.extractors import register
from tools.traceability.model import TraceEdge, TraceIndex, TraceNode

# UC-{N} 카테고리 섹션 헤딩 패턴: "### 1. 본인인증 결과"
_CATEGORY_RE = re.compile(
    r"^###\s+(\d{1,2})\.\s+(.+)$",
    re.MULTILINE,
)

# UC ID 패턴: UC-{N}-{C|M|N}-{NN}
_UC_ID_RE = re.compile(r"\b(UC-(\d{1,2})-([CMN])-(\d{2}))\b")

# REQ 참조 패턴
_REQ_REF_RE = re.compile(r"\bREQ-(\d+)\b")


def _anchor(text: str) -> str:
    """헤딩 텍스트를 slug anchor 로 변환한다."""
    return re.sub(r"[^\w\s-]", "", text.lower()).strip().replace(" ", "-")


@register("usecase")
def extract(repo_root: Path, index: TraceIndex) -> None:
    """
    유스케이스 체크리스트에서 UseCaseCategory, UseCase 노드와
    refines 엣지를 추출해 index 에 추가한다.

    Args:
        repo_root: 리포지토리 루트 경로
        index: 트레이서빌리티 인덱스
    """
    checklist_rel = get_config(repo_root).path("usecase_checklist")
    checklist_path = repo_root / checklist_rel
    if not checklist_path.exists():
        print(
            f"[usecase] 경고: {checklist_rel} 파일 없음 — 건너뜀",
            file=sys.stderr,
        )
        return

    try:
        text = checklist_path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(
            f"[usecase] 경고: {checklist_rel} 읽기 실패: {exc}",
            file=sys.stderr,
        )
        return

    lines = text.splitlines()

    # ── 1. UseCaseCategory 노드 추출 (### N. 제목) ──────────────────────────
    for lineno, line in enumerate(lines, start=1):
        m = _CATEGORY_RE.match(line)
        if not m:
            continue
        entity_num = int(m.group(1))
        title = m.group(2).strip()
        cat_id = f"UC-{entity_num}"

        node = TraceNode(
            id=cat_id,
            type="UseCaseCategory",
            source_file=checklist_rel,
            source_loc=f"L{lineno}",
            title=title,
            attrs={"entity_num": entity_num},
        )
        index.add_node(node)

    # ── 2. UseCase 노드 + refines 엣지 추출 (표 행) ─────────────────────────
    for lineno, line in enumerate(lines, start=1):
        # 파이프로 시작하는 표 행만 처리
        if not line.strip().startswith("|"):
            continue

        # 행에서 UC ID 찾기
        uc_matches = _UC_ID_RE.findall(line)
        for uc_id, entity_num, category, _seq in uc_matches:
            # '현재 상태' 행은 내용 행이 아니므로 첫 컬럼이 UC ID 인 행만 처리
            # (표 첫 컬럼에 UC ID 가 있는 경우)
            if not re.match(r"^\|\s*" + re.escape(uc_id), line):
                continue

            # UseCase 노드
            # 설명 텍스트: 두 번째 컬럼
            cols = [c.strip() for c in line.split("|")]
            # cols[0]='', cols[1]=UC ID, cols[2]=구분, cols[3]=내용, ...
            desc = cols[3] if len(cols) > 3 else ""

            node = TraceNode(
                id=uc_id,
                type="UseCase",
                source_file=checklist_rel,
                source_loc=f"L{lineno}",
                title=desc if desc else None,
                attrs={
                    "category": f"UC-{entity_num}",
                    "kind": category,  # C/M/N
                },
            )
            index.add_node(node)

            # refines 엣지: 같은 행에 REQ-NNN 참조가 있을 때만
            for req_match in _REQ_REF_RE.finditer(line):
                req_id = f"REQ-{req_match.group(1)}"
                edge = TraceEdge(
                    type="refines",
                    source=uc_id,
                    target=req_id,
                    origin="auto",
                    evidence=f"{checklist_rel}:L{lineno}",
                )
                index.add_edge(edge)

    # ── 3. UC 검증 결과 매트릭스 행의 REQ 참조도 수집 ──────────────────────
    # 매트릭스 행에 REQ 인용이 있는 경우 refines 엣지 추가
    for lineno, line in enumerate(lines, start=1):
        if not line.strip().startswith("|"):
            continue
        # 매트릭스 행 패턴: | UC-X-* | ... REQ-NNN ... |
        uc_id_m = re.search(r"\b(UC-(\d{1,2})-[CMN]-\d{2})\b", line)
        if not uc_id_m:
            continue
        uc_id = uc_id_m.group(1)
        # 이미 위에서 처리한 행인지는 상관없이 추가 REQ 참조 수집
        for req_match in _REQ_REF_RE.finditer(line):
            req_id = f"REQ-{req_match.group(1)}"
            edge = TraceEdge(
                type="refines",
                source=uc_id,
                target=req_id,
                origin="auto",
                evidence=f"{checklist_rel}:L{lineno}",
            )
            index.add_edge(edge)
