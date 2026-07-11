"""
docs/specs/*.md 에서 SpecSection, SequenceDiagram, SequenceStep 노드와
step_calls, references 엣지를 추출하는 추출기.

대상 파일: docs/specs/*.md (정렬)
- SpecSection: ## / ### 헤딩 단위
- SequenceDiagram: ```mermaid sequenceDiagram``` 블록 (`*sequence*.md`)
- SequenceStep: 블록 내 메시지/Note 단위 (`*sequence*.md`)
- edge step_calls: 메시지 텍스트에 METHOD /path 가 있고 openapi path 와 일치할 때
- edge references: SpecSection 본문 또는 Note/메시지에 REQ-NNN / ADR-NNNN 이 있을 때
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from tools.traceability.config import get_config
from tools.traceability.extractors import register
from tools.traceability.model import TraceEdge, TraceIndex, TraceNode

# 파싱 정규식
_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+)$")
_MERMAID_OPEN_RE = re.compile(r"^```mermaid\s*$")
_MERMAID_CLOSE_RE = re.compile(r"^```\s*$")
_SEQ_DIAGRAM_RE = re.compile(r"^\s*sequenceDiagram\s*$")

# 시퀀스 메시지 패턴: A->>B: 텍스트 또는 A-->>B: 텍스트
_MSG_RE = re.compile(r"^\s*(\w[\w\s]*?)(?:->>|-->|->|-->>)(\w[\w\s]*?):\s*(.+)$")
# Note 패턴: Note over A: 텍스트 / Note right of A: 텍스트
_NOTE_RE = re.compile(
    r"^\s*Note\s+(?:over|right\s+of|left\s+of)\s+([\w,\s]+?):\s*(.+)$",
    re.IGNORECASE,
)

# HTTP 메서드 + path 패턴 (예: POST /api/v1/...)
_HTTP_CALL_RE = re.compile(r"\b(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)\s+(/[^\s,;\"\']+)")

# REQ/ADR 참조 패턴
_REQ_REF_RE = re.compile(r"\bREQ-(\d+)\b")
_ADR_REF_RE = re.compile(r"\bADR-(\d{4})\b")


def _slug(text: str) -> str:
    """헤딩 텍스트를 anchor slug 로 변환한다."""
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    return s.strip("-")


def _collect_target_files(repo_root: Path) -> list[Path]:
    """docs/specs/*.md 파일 목록을 수집하고 정렬해 반환한다."""
    spec_dir = repo_root / get_config(repo_root).path("specs_dir")
    if not spec_dir.exists():
        return []

    return sorted(spec_dir.glob("*.md"))


def _extract_file(
    repo_root: Path,
    file_path: Path,
    index: TraceIndex,
    openapi_lookup: dict[tuple[str, str], str],
) -> None:
    """
    단일 spec 파일을 파싱해 SpecSection, SequenceDiagram, SequenceStep 노드와
    관련 엣지를 index 에 추가한다.

    Args:
        repo_root: 리포지토리 루트 경로
        file_path: 파싱할 파일 절대 경로
        index: 트레이서빌리티 인덱스
        openapi_lookup: (normalized_path, method) → operationId 매핑
    """
    rel_path = str(file_path.relative_to(repo_root))
    stem = file_path.stem

    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(
            f"[sequence] 경고: {rel_path} 읽기 실패: {exc}",
            file=sys.stderr,
        )
        return

    lines = text.splitlines()
    parse_sequences = "sequence" in file_path.name

    # ── SpecSection 노드 추출 (## / ### 헤딩) + 본문 REQ/ADR 참조 ─────────
    current_section_id: str | None = None
    in_fence = False
    for lineno, line in enumerate(lines, start=1):
        if line.startswith("```"):
            in_fence = not in_fence

        m = _HEADING_RE.match(line)
        if m:
            heading_text = m.group(2).strip()
            anchor = _slug(heading_text)
            section_id = f"{stem}#{anchor}"
            current_section_id = section_id

            node = TraceNode(
                id=section_id,
                type="SpecSection",
                source_file=rel_path,
                source_loc=f"L{lineno}",
                title=heading_text,
                attrs={"level": len(m.group(1))},
            )
            index.add_node(node)
            continue

        if current_section_id is None or in_fence:
            continue

        for req_m in _REQ_REF_RE.finditer(line):
            index.add_edge(
                TraceEdge(
                    type="references",
                    source=current_section_id,
                    target=f"REQ-{req_m.group(1)}",
                    origin="auto",
                    evidence=f"{rel_path}:L{lineno}",
                )
            )

        for adr_m in _ADR_REF_RE.finditer(line):
            index.add_edge(
                TraceEdge(
                    type="references",
                    source=current_section_id,
                    target=f"ADR-{adr_m.group(1)}",
                    origin="auto",
                    evidence=f"{rel_path}:L{lineno}",
                )
            )

    if not parse_sequences:
        return

    # ── SequenceDiagram, SequenceStep 노드 추출 ────────────────────────────
    seq_block_idx = 0  # 파일 내 sequenceDiagram 블록 순번 (0-based → 1-based)
    i = 0
    while i < len(lines):
        line = lines[i]

        # ```mermaid 블록 시작 감지
        if not _MERMAID_OPEN_RE.match(line):
            i += 1
            continue

        # ```mermaid 다음 줄이 sequenceDiagram 인지 확인
        j = i + 1
        if j >= len(lines):
            i += 1
            continue

        if not _SEQ_DIAGRAM_RE.match(lines[j]):
            i += 1
            continue

        # sequenceDiagram 블록 발견
        seq_block_idx += 1
        seq_id = f"{stem}#seq-{seq_block_idx}"
        block_start_lineno = i + 1  # 1-based

        node = TraceNode(
            id=seq_id,
            type="SequenceDiagram",
            source_file=rel_path,
            source_loc=f"L{block_start_lineno}",
            title=f"{stem} sequence {seq_block_idx}",
            attrs={"block_index": seq_block_idx},
        )
        index.add_node(node)

        # 블록 내용 파싱 (``` 닫히는 줄까지)
        step_idx = 0
        k = j + 1
        while k < len(lines):
            block_line = lines[k]

            # 블록 종료
            if _MERMAID_CLOSE_RE.match(block_line):
                break

            raw_text = block_line.strip()
            if not raw_text:
                k += 1
                continue

            # Note 또는 메시지 라인 파싱
            from_participant: str | None = None
            to_participant: str | None = None
            is_step = False

            note_m = _NOTE_RE.match(block_line)
            msg_m = _MSG_RE.match(block_line)

            if note_m:
                from_participant = note_m.group(1).strip()
                is_step = True
            elif msg_m:
                from_participant = msg_m.group(1).strip()
                to_participant = msg_m.group(2).strip()
                is_step = True
            elif raw_text and not any(
                raw_text.startswith(kw)
                for kw in (
                    "participant",
                    "actor",
                    "autonumber",
                    "alt",
                    "else",
                    "end",
                    "opt",
                    "loop",
                    "par",
                    "and",
                    "rect",
                    "%%",
                    "activate",
                    "deactivate",
                )
            ):
                # 기타 텍스트 라인은 건너뜀
                pass

            if is_step:
                step_idx += 1
                step_id = f"{seq_id}:step-{step_idx}"

                step_node = TraceNode(
                    id=step_id,
                    type="SequenceStep",
                    source_file=rel_path,
                    source_loc=f"L{k + 1}",
                    title=raw_text[:200] if len(raw_text) > 200 else raw_text,
                    attrs={
                        "from": from_participant,
                        "to": to_participant,
                        "raw": raw_text[:500] if len(raw_text) > 500 else raw_text,
                    },
                )
                index.add_node(step_node)

                # step_calls 엣지: HTTP METHOD /path 매칭
                for http_m in _HTTP_CALL_RE.finditer(raw_text):
                    method = http_m.group(1).upper()
                    path = http_m.group(2)
                    # 경로 정규화: /api/v1 prefix 처리
                    normalized = _normalize_path(path)
                    op_id = openapi_lookup.get((normalized, method))
                    if op_id:
                        edge = TraceEdge(
                            type="step_calls",
                            source=step_id,
                            target=op_id,
                            origin="auto",
                            evidence=f"{rel_path}:L{k + 1}",
                        )
                        index.add_edge(edge)

                # references 엣지: REQ-NNN / ADR-NNNN
                for req_m in _REQ_REF_RE.finditer(raw_text):
                    edge = TraceEdge(
                        type="references",
                        source=step_id,
                        target=f"REQ-{req_m.group(1)}",
                        origin="auto",
                        evidence=f"{rel_path}:L{k + 1}",
                    )
                    index.add_edge(edge)

                for adr_m in _ADR_REF_RE.finditer(raw_text):
                    edge = TraceEdge(
                        type="references",
                        source=step_id,
                        target=f"ADR-{adr_m.group(1)}",
                        origin="auto",
                        evidence=f"{rel_path}:L{k + 1}",
                    )
                    index.add_edge(edge)

            k += 1

        # Note 라인도 SequenceDiagram 노드에서 references 엣지 생성
        # (블록 내 텍스트 전체 스캔)
        block_text = "\n".join(lines[j:k])
        for req_m in _REQ_REF_RE.finditer(block_text):
            edge = TraceEdge(
                type="references",
                source=seq_id,
                target=f"REQ-{req_m.group(1)}",
                origin="auto",
                evidence=f"{rel_path}:L{j + 1}-L{k + 1}",
            )
            index.add_edge(edge)

        for adr_m in _ADR_REF_RE.finditer(block_text):
            edge = TraceEdge(
                type="references",
                source=seq_id,
                target=f"ADR-{adr_m.group(1)}",
                origin="auto",
                evidence=f"{rel_path}:L{j + 1}-L{k + 1}",
            )
            index.add_edge(edge)

        i = k + 1


def _normalize_path(path: str) -> str:
    """
    시퀀스 다이어그램 메시지의 path 를 OpenAPI paths 키 형식으로 정규화한다.

    {deviceId} 와 {device_id} 같은 path parameter 이름 차이로
    인한 false mismatch 를 방지하기 위해 모든 {…} 를 {} 로 치환한다.
    verify.py 의 _normalize_path 와 동일한 동작을 보장한다.

    예: /api/v1/devices/{deviceId}/push-tokens
         → /api/v1/devices/{}/push-tokens
    """
    # 쿼리스트링 제거
    path = path.split("?")[0]
    # 마지막 슬래시 제거
    path = path.rstrip("/")
    # path parameter placeholder 이름 제거: {deviceId} → {}
    path = re.sub(r"\{[^}]+\}", "{}", path)
    return path


def _build_openapi_lookup(repo_root: Path) -> dict[tuple[str, str], str]:
    """
    openapi.json 에서 (normalized_path, method) → operationId 매핑을 생성한다.
    extractors 간 의존성 없이 직접 파싱.

    양측 path 를 _normalize_path 와 동일한 규칙으로 정규화해
    {deviceId} vs {device_id} 같은 이름 차이를 흡수한다.
    """
    import json as _json

    openapi_path = repo_root / get_config(repo_root).path("openapi")
    if not openapi_path.exists():
        return {}

    try:
        data = _json.loads(openapi_path.read_text(encoding="utf-8"))
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
                normalized = _normalize_path(path)
                lookup[(normalized, method.upper())] = op_id

    return lookup


@register("sequence")
def extract(repo_root: Path, index: TraceIndex) -> None:
    """
    시퀀스 다이어그램 spec 파일들에서 SpecSection, SequenceDiagram,
    SequenceStep 노드와 step_calls, references 엣지를 추출한다.

    Args:
        repo_root: 리포지토리 루트 경로
        index: 트레이서빌리티 인덱스
    """
    files = _collect_target_files(repo_root)
    if not files:
        print(
            f"[sequence] 경고: {get_config(repo_root).path('specs_dir')} 에서 "
            "대상 파일 없음 — 건너뜀",
            file=sys.stderr,
        )
        return

    # OpenAPI 경로 → operationId 매핑 (step_calls 엣지 생성용)
    openapi_lookup = _build_openapi_lookup(repo_root)

    for file_path in files:
        try:
            _extract_file(repo_root, file_path, index, openapi_lookup)
        except Exception as exc:  # noqa: BLE001
            print(
                f"[sequence] 경고: {file_path.name} 처리 실패: {exc}",
                file=sys.stderr,
            )
