"""
traceability 그래프의 공통 데이터 모델.

TraceNode, TraceEdge, TraceFinding, TraceIndex 를 정의한다.
외부 의존성 없이 stdlib dataclasses + json 만 사용한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TraceNode:
    """
    트레이서빌리티 그래프의 노드 하나를 나타낸다.

    Attributes:
        id: 안정 노드 ID (예: REQ-102, ADR-0006).
        type: schema.md 에 정의된 노드 타입 (예: Requirement, ADR).
        source_file: 추출 원본 파일 경로 (repo root 기준 상대 경로).
        source_loc: 파일 내 위치 (예: 'L107' 또는 frontmatter anchor).
        title: 사람이 읽을 수 있는 제목.
        attrs: 추가 속성 딕셔너리 (예: status, priority).
    """

    id: str
    type: str
    source_file: str
    source_loc: str | None = None
    title: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """안정적인 직렬화 딕셔너리로 변환."""
        return {
            "id": self.id,
            "type": self.type,
            "source_file": self.source_file,
            "source_loc": self.source_loc,
            "title": self.title,
            "attrs": self.attrs,
        }


@dataclass
class TraceEdge:
    """
    트레이서빌리티 그래프의 엣지 하나를 나타낸다.

    Attributes:
        type: schema.md 에 정의된 edge 타입 (예: references, refines).
        source: 출발 노드 ID.
        target: 목적 노드 ID.
        origin: 'auto' (추출기가 자동 생성) 또는 'manual' (수동 입력).
        evidence: 추출 근거 위치 (예: 'prd.md:L107').
    """

    type: str
    source: str
    target: str
    origin: str = "auto"
    evidence: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """안정적인 직렬화 딕셔너리로 변환."""
        return {
            "type": self.type,
            "source": self.source,
            "target": self.target,
            "origin": self.origin,
            "evidence": self.evidence,
        }


@dataclass
class TraceFinding:
    """
    검증 또는 추출 중 발견된 이슈 하나를 나타낸다.

    category 값:
    - 'deterministic': CI hard gate — 자동으로 판단 가능한 명확한 오류.
    - 'semantic_candidate': agent review — 의미적 판단이 필요한 후보.

    Attributes:
        severity: 'error', 'warn', 'info' 중 하나.
        kind: 찾은 이슈 종류 (예: 'broken_reference', 'dangling_edge').
        message: 사람이 읽을 수 있는 설명 메시지.
        subject: 관련 노드 또는 엣지 ID.
        location: 발생 파일/위치.
        category: 'deterministic' 또는 'semantic_candidate'.
    """

    severity: str
    kind: str
    message: str
    subject: str | None = None
    location: str | None = None
    category: str = "deterministic"

    def to_dict(self) -> dict[str, Any]:
        """안정적인 직렬화 딕셔너리로 변환."""
        return {
            "severity": self.severity,
            "kind": self.kind,
            "message": self.message,
            "subject": self.subject,
            "location": self.location,
            "category": self.category,
        }


# 엣지 중복 판단 키 타입: (type, source, target, origin)
_EdgeKey = tuple[str, str, str, str]


class TraceIndex:
    """
    모든 TraceNode, TraceEdge 를 보관하는 인덱스.

    노드 ID 충돌 정책 (keep-first):
    - 동일 ID로 add_node 가 두 번 호출되면 먼저 등록된 노드를 유지하고
      새 노드는 무시한다. 무시된 사실을 내부 warnings 에 기록한다.
    - 이 정책은 동일 ID 가 여러 추출기에서 중복 추출될 경우를 대비한
      방어적 설계다.

    엣지 중복 정책 (keep-first, keyed by (type, source, target, origin)):
    - 동일한 (type, source, target, origin) 조합으로 add_edge 가 두 번
      호출되면 먼저 등록된 엣지를 유지한다.
    - run_all() 을 두 번 호출해도 엣지 수가 증가하지 않는다.
    - origin 이 다른 경우(예: 'auto' vs 'manual')는 별개 엣지로 취급한다.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, TraceNode] = {}  # id → TraceNode
        self._edges: list[TraceEdge] = []
        # 엣지 dedup 키 집합: (type, source, target, origin)
        self._edge_keys: set[_EdgeKey] = set()
        self._warnings: list[str] = []

    def add_node(self, node: TraceNode) -> None:
        """
        노드를 인덱스에 추가한다.

        충돌 시 keep-first 정책 적용: 기존 노드 유지, 새 노드 무시.
        """
        if node.id in self._nodes:
            # keep-first: 기존 노드 유지, 중복 사실을 경고로 기록
            self._warnings.append(
                f"노드 ID 충돌 (keep-first): '{node.id}' "
                f"기존={self._nodes[node.id].source_file}, "
                f"신규={node.source_file} → 기존 노드 유지"
            )
            return
        self._nodes[node.id] = node

    def add_edge(self, edge: TraceEdge) -> None:
        """
        엣지를 인덱스에 추가한다.

        keep-first 중복 정책: (type, source, target, origin) 가 동일한
        엣지가 이미 존재하면 새 엣지를 무시한다.
        이 정책으로 run_all() 을 여러 번 호출해도 엣지가 중복되지 않는다.
        """
        key: _EdgeKey = (edge.type, edge.source, edge.target, edge.origin)
        if key in self._edge_keys:
            # keep-first: 기존 엣지 유지, 중복 무시
            return
        self._edge_keys.add(key)
        self._edges.append(edge)

    def node_ids(self) -> list[str]:
        """등록된 모든 노드 ID 목록을 정렬하여 반환한다."""
        return sorted(self._nodes.keys())

    def nodes(self) -> list[TraceNode]:
        """등록된 모든 노드를 ID 기준 정렬 후 반환한다."""
        return [self._nodes[k] for k in sorted(self._nodes.keys())]

    def edges(self) -> list[TraceEdge]:
        """등록된 모든 엣지를 (type, source, target) 기준 정렬 후 반환한다."""
        return sorted(
            self._edges,
            key=lambda e: (e.type, e.source, e.target),
        )

    def to_dict(self) -> dict[str, Any]:
        """인덱스 전체를 안정 정렬된 딕셔너리로 변환한다."""
        return {
            "nodes": [n.to_dict() for n in self.nodes()],
            "edges": [e.to_dict() for e in self.edges()],
            "warnings": sorted(self._warnings),
        }

    def to_json(self, indent: int = 2) -> str:
        """인덱스를 JSON 문자열로 직렬화한다."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)
