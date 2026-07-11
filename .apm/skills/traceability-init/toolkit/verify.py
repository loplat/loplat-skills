"""
traceability index 검증 진입점 (Phase 3 전면 구현).

repo root 에서 실행:
    python3 tools/traceability/verify.py [index_path]
    python3 tools/traceability/verify.py --selftest

산출물: scratch/traceability/ci-summary.json

exit code:
    0 — deterministic error 없음 (warn/semantic_candidate 만 존재 가능)
    1 — deterministic error 1개 이상 (CI hard gate 차단, pre-commit 커밋 차단)
    2 — 도구/사용 오류: index 파일 없음/읽기 실패, JSON 파싱 오류, 예기치 않은 예외.
        pre-commit은 exit 2 를 fail-open(pass-through) 처리한다.
        CI(set -ceu)는 exit 2 를 빌드 실패로 처리한다 — 도구 오류는 CI에서 loud fail.

검증 정책:
  hard fail (severity=error, category=deterministic, exit 1):
    - broken_reference: source 문서(prd/adr/spec/openapi)가 인용한 canonical id 부재
    - malformed_manual_edge: 허용 안 된 edge type 또는 필수 필드 누락
    - secret_in_index: 좌표/전화번호/토큰/API key 패턴

  warn (severity=warn, category=semantic_candidate 또는 coverage, exit 0):
    - orphan: Must requirement에 필요 edge 없음
    - superseded_in_use: superseded requirement/ADR이 active 노드의 primary source
    - api_unlinked: openapi operation이 sequence/usecase/test와 미연결
    - test_coverage_drift: 테스트 마커가 체크리스트에 없는 UC를 가리킴
    - coverage_gap: 체크리스트 PASS인데 test 노드 없음 (또는 역)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

# sys.path 부트스트랩
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT_CANDIDATE = _SCRIPT_DIR.parent.parent
if str(_REPO_ROOT_CANDIDATE) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT_CANDIDATE))

from tools.traceability.config import get_config  # noqa: E402
from tools.traceability.model import TraceFinding  # noqa: E402

# ────────────────────────────────────────────────────────────
# 허용 edge 타입 (schema.md 기준)
# ────────────────────────────────────────────────────────────
_ALLOWED_EDGE_TYPES: frozenset[str] = frozenset(
    [
        "refines",
        "references",
        "implements",
        "validates",
        "exercises",
        "step_calls",
        "routed_to",
        "governed_by",
        "supersedes",
        "conflicts_with",
        "depends_on",
    ]
)

# manual edge 필수 필드
_MANUAL_EDGE_REQUIRED_FIELDS: tuple[str, ...] = (
    "type",
    "source",
    "target",
    "reason",
    "owner",
    "last_verified_by_command",
)

# api_unlinked 제외 항목 필수 필드
_API_EXCLUSION_REQUIRED_FIELDS: tuple[str, ...] = (
    "operation_id",
    "reason",
    "owner",
    "last_verified_by_command",
)


def _find_repo_root() -> Path:
    """repo root 탐색."""
    return _SCRIPT_DIR.parent.parent


def _cfg():
    """현재 repo 의 trace-config (캐시됨). 부재 시 기본값."""
    return get_config(_find_repo_root())


def _ontology_path(filename: str) -> Path:
    """docs/ontology(config 로 재정의 가능) 하위 파일의 절대 경로."""
    root = _find_repo_root()
    return root / _cfg().path("ontology_dir") / filename


def _yaml_dependency_error(
    manual_edges_path: Path,
    seed_traces_path: Path,
    api_exclusions_path: Path | None = None,
) -> str | None:
    """YAML 파일이 존재하는데 pyyaml import 불가면 에러 메시지 반환, 아니면 None."""
    try:
        import yaml  # noqa: F401

        return None
    except ImportError:
        candidates = [manual_edges_path, seed_traces_path]
        if api_exclusions_path is not None:
            candidates.append(api_exclusions_path)
        present = [str(p) for p in candidates if p.exists()]
        if present:
            return "pyyaml 미설치 — fail-closed: " + ", ".join(present)
        return None


def _normalize_path(path: str) -> str:
    """
    HTTP path의 path parameter placeholder를 {} 로 정규화한다.

    phase-2 이월 사항: {deviceId} 와 {device_id} 같은 이름 차이로
    false mismatch가 발생하지 않도록 모든 {…} 를 {} 로 치환한다.

    Args:
        path: 원본 HTTP path 문자열.

    Returns:
        placeholder 이름을 제거한 정규화 경로.
    """
    return re.sub(r"\{[^}]+\}", "{}", path)


def _extract_http_call(raw: str) -> tuple[str, str] | None:
    """
    sequence step raw 텍스트에서 (HTTP_METHOD, path) 를 추출한다.

    Args:
        raw: Mermaid 메시지 raw 텍스트 (예: 'App->>API: DELETE /api/v1/…').

    Returns:
        (method, path) 튜플 또는 None (HTTP 호출이 아닌 경우).
    """
    # ": METHOD /path" 패턴 추출
    m = re.search(
        r":\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[^\s]*)",
        raw,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).upper(), m.group(2)
    return None


def _build_openapi_lookup(
    nodes: list[dict[str, Any]],
) -> dict[tuple[str, str], str]:
    """
    OpenAPI 노드로부터 (method, normalized_path) → operation_id 매핑을 생성한다.

    Args:
        nodes: index.json 의 nodes 목록.

    Returns:
        (method, normalized_path) → operationId 딕셔너리.
    """
    lookup: dict[tuple[str, str], str] = {}
    for node in nodes:
        if node.get("type") != "ApiOperation":
            continue
        attrs = node.get("attrs", {})
        path = attrs.get("path", "")
        method = attrs.get("method", "").upper()
        if path and method:
            key = (method, _normalize_path(path))
            lookup[key] = node["id"]
    return lookup


def _load_api_exclusions(
    api_exclusions_path: Path | None = None,
) -> tuple[set[str], list[TraceFinding]]:
    """
    api_unlinked 제외 목록을 로드한다.

    제외는 API가 제품/사용자 시나리오가 아닌 시스템 엔드포인트일 때만 허용한다.
    잘못된 제외 항목은 CI에서 보이도록 deterministic finding 으로 반환한다.
    """
    path = api_exclusions_path
    if path is None:
        path = _ontology_path("api-exclusions.yml")

    if not path.exists():
        return set(), []

    findings: list[TraceFinding] = []
    excluded: set[str] = set()

    try:
        import yaml  # type: ignore[import]
    except ImportError:
        findings.append(
            TraceFinding(
                severity="error",
                kind="malformed_api_exclusion",
                message="api-exclusions.yml exists but pyyaml is not installed",
                subject=str(path),
                location=str(path),
                category="deterministic",
            )
        )
        return excluded, findings

    try:
        with path.open(encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
    except Exception as exc:
        findings.append(
            TraceFinding(
                severity="error",
                kind="malformed_api_exclusion",
                message=f"api-exclusions.yml 파싱 실패: {exc}",
                subject=str(path),
                location=str(path),
                category="deterministic",
            )
        )
        return excluded, findings

    raw_items = doc.get("api_operations", []) if isinstance(doc, dict) else []
    for idx, item in enumerate(raw_items or []):
        if not isinstance(item, dict):
            findings.append(
                TraceFinding(
                    severity="error",
                    kind="malformed_api_exclusion",
                    message=f"api exclusion [{idx}] is not a dict",
                    subject=f"api-exclusions[{idx}]",
                    location=str(path),
                    category="deterministic",
                )
            )
            continue

        operation_id = item.get("operation_id", "")
        if operation_id:
            excluded.add(operation_id)

        for field in _API_EXCLUSION_REQUIRED_FIELDS:
            if not item.get(field):
                findings.append(
                    TraceFinding(
                        severity="error",
                        kind="malformed_api_exclusion",
                        message=(
                            f"api exclusion [{idx}] "
                            f"(operation_id={operation_id or '?'}) missing required field '{field}'"
                        ),
                        subject=f"api-exclusions[{idx}]",
                        location=str(path),
                        category="deterministic",
                    )
                )

    return excluded, findings


def check_broken_references(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> list[TraceFinding]:
    """
    source 문서가 인용한 canonical id가 노드로 존재하지 않는 경우를 탐지한다.

    검사 범위:
    (a) ADR/spec/prd 본문의 `REQ-###` / `ADR-####` 인용 대상 부재 →
        edges(references, refines, supersedes, implements) 의 target 미존재
    (b) step_calls 엣지의 target 부재 (sequence → API operation 참조)

    단, exercises 엣지의 dangling target 은 test_coverage_drift 로 처리하므로
    여기서는 제외한다.

    Args:
        nodes: index.json 의 nodes 목록.
        edges: index.json 의 edges 목록.

    Returns:
        broken_reference TraceFinding 목록 (severity=error, category=deterministic).
    """
    findings: list[TraceFinding] = []
    node_ids: set[str] = {n["id"] for n in nodes}

    # exercises 엣지는 test_coverage_drift 처리 — 여기서 제외
    _SKIP_EDGE_TYPES = frozenset(["exercises"])

    for edge in edges:
        edge_type = edge.get("type", "")
        if edge_type in _SKIP_EDGE_TYPES:
            continue

        src = edge.get("source", "")
        tgt = edge.get("target", "")
        evidence = edge.get("evidence")

        if src and src not in node_ids:
            findings.append(
                TraceFinding(
                    severity="error",
                    kind="broken_reference",
                    message=(f"edge[{edge_type}] source '{src}' not in index (target={tgt})"),
                    subject=src,
                    location=evidence,
                    category="deterministic",
                )
            )

        if tgt and tgt not in node_ids:
            findings.append(
                TraceFinding(
                    severity="error",
                    kind="broken_reference",
                    message=(f"edge[{edge_type}] target '{tgt}' not in index (source={src})"),
                    subject=tgt,
                    location=evidence,
                    category="deterministic",
                )
            )

    return findings


def check_orphans(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> list[TraceFinding]:
    """
    Must 요구사항 중 카테고리별 필요 edge가 없는 orphan을 탐지한다.

    Must requirement 에 대해 아래 edge 카테고리별로 하나 이상 존재해야 warn 제외:
    - ADR/spec 연결: references/refines/implements edge (source 또는 target 이 해당 REQ)
    - usecase 연결: refines edge (UC → REQ)
    - API 연결: implements edge (ApiOperation → REQ)
    - test 연결: validates edge (TestCase → REQ 또는 연결된 UseCase)

    과도 강제 금지: 1개 카테고리라도 없으면 warn(hard fail 아님).

    Args:
        nodes: index.json 의 nodes 목록.
        edges: index.json 의 edges 목록.

    Returns:
        orphan TraceFinding 목록 (severity=warn, category=semantic_candidate).
    """
    findings: list[TraceFinding] = []

    # Must requirements 만 검사
    _must = _cfg().must_priority
    must_reqs = {
        n["id"]
        for n in nodes
        if n.get("type") == "Requirement" and n.get("attrs", {}).get("priority") == _must
    }

    usecase_ids = {n["id"] for n in nodes if n.get("type") == "UseCase"}

    # req_id → edge 종류별 집합
    req_connected: dict[str, set[str]] = {r: set() for r in must_reqs}
    usecase_to_reqs: dict[str, set[str]] = {u: set() for u in usecase_ids}
    reqs_validated_by_tests: set[str] = set()
    usecases_validated_by_tests: set[str] = set()

    for edge in edges:
        etype = edge.get("type", "")
        src = edge.get("source", "")
        tgt = edge.get("target", "")

        for req_id in must_reqs:
            if src == req_id or tgt == req_id:
                req_connected[req_id].add(etype)

        if etype in {"refines", "references"} and src in usecase_ids and tgt in must_reqs:
            usecase_to_reqs[src].add(tgt)

        if etype == "validates":
            if tgt in must_reqs:
                reqs_validated_by_tests.add(tgt)
            if tgt in usecase_ids:
                usecases_validated_by_tests.add(tgt)

    for uc_id in usecases_validated_by_tests:
        reqs_validated_by_tests.update(usecase_to_reqs.get(uc_id, set()))

    for req_id, connected_types in req_connected.items():
        missing: list[str] = []
        # 카테고리별 확인 (과도 강제 금지: 하나라도 없으면 warn)
        if not (connected_types & {"references", "refines", "implements"}):
            missing.append("adr/spec/code-link")
        if req_id not in reqs_validated_by_tests:
            missing.append("test-link")

        if missing:
            findings.append(
                TraceFinding(
                    severity="warn",
                    kind="orphan",
                    message=(f"Must requirement '{req_id}' missing edges: " + ", ".join(missing)),
                    subject=req_id,
                    location=_cfg().path("requirements"),
                    category="semantic_candidate",
                )
            )

    return findings


def check_superseded(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> list[TraceFinding]:
    """
    superseded 요구사항 또는 ADR이 active 노드의 primary source로 남아있는 경우를 탐지한다.

    - priority=Superseded 인 Requirement가 references/implements edge 의 target 으로 사용됨
    - status=Superseded 인 ADR 이 references edge 의 target 으로 사용됨

    Args:
        nodes: index.json 의 nodes 목록.
        edges: index.json 의 edges 목록.

    Returns:
        superseded_in_use TraceFinding 목록 (severity=warn, category=semantic_candidate).
    """
    findings: list[TraceFinding] = []

    # superseded requirements
    superseded_reqs = {
        n["id"]
        for n in nodes
        if n.get("type") == "Requirement" and n.get("attrs", {}).get("priority") == "Superseded"
    }

    # superseded ADRs (status=Superseded)
    superseded_adrs = {
        n["id"]
        for n in nodes
        if n.get("type") == "ADR" and n.get("attrs", {}).get("status", "").lower() == "superseded"
    }

    superseded_all = superseded_reqs | superseded_adrs

    # active 노드가 superseded 를 target 으로 참조하는 edge 탐지
    active_ref_types = frozenset(["references", "refines", "implements", "validates", "exercises"])

    for edge in edges:
        etype = edge.get("type", "")
        tgt = edge.get("target", "")
        src = edge.get("source", "")
        if etype in active_ref_types and tgt in superseded_all:
            findings.append(
                TraceFinding(
                    severity="warn",
                    kind="superseded_in_use",
                    message=(
                        f"superseded node '{tgt}' is still referenced as active "
                        f"target by '{src}' via edge[{etype}]"
                    ),
                    subject=tgt,
                    location=edge.get("evidence"),
                    category="semantic_candidate",
                )
            )

    return findings


def check_sequence_api(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    api_exclusions_path: Path | None = None,
) -> list[TraceFinding]:
    """
    sequence step HTTP 호출이 openapi에 없으면 error, openapi operation이 미연결이면 warn.

    path-param 정규화 (phase-2 이월):
    {deviceId} vs {device_id} 같은 placeholder 이름 차이로 인한
    false mismatch 방지를 위해 비교 전 모든 {…} → {} 로 정규화한다.

    hard fail:
    - sequence step이 HTTP 호출인데 정규화 후 method+path가 openapi에 없음

    warn:
    - openapi operation이 sequence/usecase/test와 연결되지 않음 (api_unlinked)

    Args:
        nodes: index.json 의 nodes 목록.
        edges: index.json 의 edges 목록.

    Returns:
        TraceFinding 목록.
    """
    findings: list[TraceFinding] = []

    # openapi lookup 구성: (method, normalized_path) → operationId
    openapi_lookup = _build_openapi_lookup(nodes)
    openapi_op_ids: set[str] = {n["id"] for n in nodes if n.get("type") == "ApiOperation"}
    excluded_api_ops, exclusion_findings = _load_api_exclusions(api_exclusions_path)
    findings.extend(exclusion_findings)

    for op_id in sorted(excluded_api_ops - openapi_op_ids):
        findings.append(
            TraceFinding(
                severity="error",
                kind="broken_reference",
                message=f"api exclusion operation_id '{op_id}' not found in openapi operations",
                subject=op_id,
                location=str(
                    api_exclusions_path
                    if api_exclusions_path is not None
                    else _ontology_path("api-exclusions.yml")
                ),
                category="deterministic",
            )
        )

    # sequence step → API operation 연결 여부 확인
    # step_calls, exercises 엣지의 target 이 ApiOperation 인 경우
    api_linked: set[str] = set()
    for edge in edges:
        if edge.get("type") in ("step_calls", "exercises", "validates", "implements"):
            tgt = edge.get("target", "")
            if tgt in openapi_op_ids:
                api_linked.add(tgt)

    # sequence HTTP step 의 path → openapi 매칭 검사
    seq_steps = [n for n in nodes if n.get("type") == "SequenceStep"]
    for step in seq_steps:
        raw = step.get("attrs", {}).get("raw", "")
        parsed = _extract_http_call(raw)
        if parsed is None:
            continue

        method, path = parsed
        norm_key = (method, _normalize_path(path))

        if norm_key not in openapi_lookup:
            # path에 path-param 없는 경우도 exact match 시도
            findings.append(
                TraceFinding(
                    severity="error",
                    kind="broken_reference",
                    message=(
                        f"sequence step HTTP call '{method} {path}' "
                        f"(normalized: {norm_key[1]}) not found in openapi"
                    ),
                    subject=step["id"],
                    location=step.get("source_file"),
                    category="deterministic",
                )
            )
        else:
            # 매칭됨 → linked 로 표시
            api_linked.add(openapi_lookup[norm_key])

    # api_unlinked: openapi operation이 sequence/usecase/test와 전혀 미연결
    for op_id in openapi_op_ids:
        if op_id in excluded_api_ops:
            continue
        if op_id not in api_linked:
            findings.append(
                TraceFinding(
                    severity="warn",
                    kind="api_unlinked",
                    message=f"openapi operation '{op_id}' not linked to any sequence/test",
                    subject=op_id,
                    location=_cfg().path("openapi"),
                    category="semantic_candidate",
                )
            )

    return findings


def check_usecase_test_coverage(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> list[TraceFinding]:
    """
    usecase-coverage-checklist 매트릭스 vs test 노드 커버리지 비교.

    - 테스트 validates target 이 체크리스트 UC, Requirement, PlatformRequirement 어디에도 없으면
      → test_coverage_drift warn
      (이것은 hard fail이 아님 — 의도된 검증 신호 또는 sub-variant UC)
    - 체크리스트 UC인데 validates edge로 연결된 test 없음 → coverage_gap warn

    Args:
        nodes: index.json 의 nodes 목록.
        edges: index.json 의 edges 목록.

    Returns:
        TraceFinding 목록 (severity=warn, category=coverage).
    """
    findings: list[TraceFinding] = []

    # 체크리스트 UC ids
    checklist_uc_ids: set[str] = {n["id"] for n in nodes if n.get("type") == "UseCase"}
    requirement_ids: set[str] = {
        n["id"] for n in nodes if n.get("type") in {"Requirement", "PlatformRequirement"}
    }

    # validates 엣지에서 UC 타겟 집합 수집
    # target 이 checklist_uc_ids 에 없으면 → test_coverage_drift
    validates_edges = [e for e in edges if e.get("type") == "validates"]
    uc_covered_by_tests: set[str] = set()
    drift_reported: set[str] = set()

    for edge in validates_edges:
        tgt = edge.get("target", "")
        src = edge.get("source", "")

        if tgt in requirement_ids:
            continue

        if tgt not in checklist_uc_ids:
            # dangling validates target → test_coverage_drift warn (hard fail 아님)
            if tgt not in drift_reported:
                drift_reported.add(tgt)
                findings.append(
                    TraceFinding(
                        severity="warn",
                        kind="test_coverage_drift",
                        message=(
                            f"test validates undocumented target '{tgt}' "
                            f"(not in checklist/requirements — may be sub-variant or regression marker)"
                        ),
                        subject=tgt,
                        location=src[:120] if src else None,
                        category="coverage",
                    )
                )
        else:
            uc_covered_by_tests.add(tgt)

    # coverage_gap: 체크리스트에 있는 UC가 test 로 cover 안 됨
    uncovered = checklist_uc_ids - uc_covered_by_tests
    for uc_id in sorted(uncovered):
        findings.append(
            TraceFinding(
                severity="warn",
                kind="coverage_gap",
                message=f"UseCase '{uc_id}' has no validates edge from any test",
                subject=uc_id,
                location=_cfg().path("usecase_checklist"),
                category="coverage",
            )
        )

    return findings


def check_manual_edges(
    nodes: list[dict[str, Any]],
    manual_edges_path: Path,
) -> list[TraceFinding]:
    """
    manual-edges.yml 을 로드해 type/필수 필드/source·target 존재를 검증한다.

    pyyaml lazy import: 없으면 graceful warn 반환.
    파일 자체가 없어도 graceful warn 반환.
    단, main() preflight(_yaml_dependency_error)가 파일 존재 시 먼저 exit 2(fail-closed)로 차단한다.

    hard fail:
    - source 또는 target 노드 미존재
    - 허용 안 된 edge type
    - 필수 필드 누락 (reason / owner / last_verified_by_command)

    Args:
        nodes: index.json 의 nodes 목록.
        manual_edges_path: manual-edges.yml 경로.

    Returns:
        TraceFinding 목록.
    """
    findings: list[TraceFinding] = []

    # pyyaml lazy import
    try:
        import yaml  # type: ignore[import]
    except ImportError:
        findings.append(
            TraceFinding(
                severity="warn",
                kind="manual_edge_check_skipped",
                message="pyyaml not installed; manual-edges.yml 검증을 건너뜁니다",
                subject=str(manual_edges_path),
                location=None,
                category="semantic_candidate",
            )
        )
        return findings

    # 파일 존재 확인
    if not manual_edges_path.exists():
        findings.append(
            TraceFinding(
                severity="warn",
                kind="manual_edge_check_skipped",
                message=f"manual-edges.yml 없음: {manual_edges_path}",
                subject=str(manual_edges_path),
                location=None,
                category="semantic_candidate",
            )
        )
        return findings

    # YAML 로드
    try:
        with manual_edges_path.open(encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
    except Exception as exc:
        findings.append(
            TraceFinding(
                severity="error",
                kind="malformed_manual_edge",
                message=f"manual-edges.yml 파싱 실패: {exc}",
                subject=str(manual_edges_path),
                location=str(manual_edges_path),
                category="deterministic",
            )
        )
        return findings

    raw_edges = doc.get("edges", []) or []
    node_ids: set[str] = {n["id"] for n in nodes}

    for idx, edge in enumerate(raw_edges):
        if not isinstance(edge, dict):
            findings.append(
                TraceFinding(
                    severity="error",
                    kind="malformed_manual_edge",
                    message=f"manual edge [{idx}] is not a dict",
                    subject=f"manual-edges.yml[{idx}]",
                    location=str(manual_edges_path),
                    category="deterministic",
                )
            )
            continue

        etype = edge.get("type", "")
        src = edge.get("source", "")
        tgt = edge.get("target", "")

        # 허용 edge type 검사
        if etype not in _ALLOWED_EDGE_TYPES:
            findings.append(
                TraceFinding(
                    severity="error",
                    kind="malformed_manual_edge",
                    message=(
                        f"manual edge [{idx}] has invalid type '{etype}'; "
                        f"allowed: {sorted(_ALLOWED_EDGE_TYPES)}"
                    ),
                    subject=f"manual-edges[{idx}]:{etype}",
                    location=str(manual_edges_path),
                    category="deterministic",
                )
            )

        # 필수 필드 누락 검사
        for field in _MANUAL_EDGE_REQUIRED_FIELDS:
            if not edge.get(field):
                findings.append(
                    TraceFinding(
                        severity="error",
                        kind="malformed_manual_edge",
                        message=(
                            f"manual edge [{idx}] (type={etype or '?'}, "
                            f"source={src or '?'}, target={tgt or '?'}) "
                            f"missing required field '{field}'"
                        ),
                        subject=f"manual-edges[{idx}]",
                        location=str(manual_edges_path),
                        category="deterministic",
                    )
                )

        # source / target 존재 검사 (노드 id)
        if src and src not in node_ids:
            findings.append(
                TraceFinding(
                    severity="error",
                    kind="broken_reference",
                    message=(f"manual edge [{idx}] source '{src}' not in index"),
                    subject=src,
                    location=str(manual_edges_path),
                    category="deterministic",
                )
            )
        if tgt and tgt not in node_ids:
            findings.append(
                TraceFinding(
                    severity="error",
                    kind="broken_reference",
                    message=(f"manual edge [{idx}] target '{tgt}' not in index"),
                    subject=tgt,
                    location=str(manual_edges_path),
                    category="deterministic",
                )
            )

    return findings


# 시크릿 패턴 정의 (id/path/kind 만 출력 — 실제 값은 노출하지 않음)
_SECRET_PATTERNS: list[tuple[str, str]] = [
    # 한국 전화번호: 010-XXXX-XXXX, 01X-XXX(X)-XXXX
    (r"\b01[016789]-?\d{3,4}-?\d{4}\b", "korean_phone_number"),
    # OpenAI API key
    (r"sk-[A-Za-z0-9]{16,}", "openai_api_key"),
    # PEM 개인키/인증서
    (r"-----BEGIN\s+\w", "pem_credential"),
    # AWS access key
    (r"AKIA[0-9A-Z]{16}", "aws_access_key"),
    # 위도/경도 float pair — JSON key 형식: "lat": 37.XXXX 또는 "longitude": 127.XXXX
    (
        r'(?:"lat(?:itude)?"\s*:\s*[+-]?(?:[89]?\d|[1-8]\d)\.\d{4,}|'
        r'"lon(?:gitude)?"\s*:\s*[+-]?(?:1[0-7]\d|\d{1,2})\.\d{4,})',
        "gps_coordinate",
    ),
    # 위도/경도 bare decimal pair: 37.5665, 126.9780 (JSON key 없이 노출된 좌표)
    (
        r"[+-]?\d{1,3}\.\d{4,}\s*,\s*[+-]?\d{1,3}\.\d{4,}",
        "gps_coordinate",
    ),
    # JWT 토큰: eyJ<header>.<payload>.<signature>
    (
        r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
        "jwt_token",
    ),
    # CI/DI 형태의 64자 hex (개인식별자)
    (r"\b[0-9a-fA-F]{64}\b", "possible_ci_di_token"),
]


def check_secrets(index_text: str) -> list[TraceFinding]:
    """
    index 직렬화 텍스트에 시크릿/PII 패턴이 포함되어 있는지 검사한다.

    검증기 자체가 찾는 것을 출력에 노출하면 안 됨:
    finding message 에는 pattern_kind 와 match_location_hint 만 포함하고
    실제 매칭 값은 포함하지 않는다.

    Args:
        index_text: index.json 의 전체 직렬화 텍스트.

    Returns:
        secret_in_index TraceFinding 목록 (severity=error, category=deterministic).
    """
    findings: list[TraceFinding] = []
    reported_kinds: set[str] = set()

    for pattern, kind in _SECRET_PATTERNS:
        match = re.search(pattern, index_text)
        if match and kind not in reported_kinds:
            reported_kinds.add(kind)
            # 매칭 위치 힌트만 (실제 값 미포함)
            start = max(0, match.start() - 30)
            end = min(len(index_text), match.end() + 30)
            context_chars = end - start
            findings.append(
                TraceFinding(
                    severity="error",
                    kind="secret_in_index",
                    message=(
                        f"secret pattern '{kind}' detected in index "
                        f"(~char {match.start()}, context_len={context_chars})"
                    ),
                    subject=kind,
                    location="scratch/traceability/index.json",
                    category="deterministic",
                )
            )

    return findings


def _build_seed_trace_adjacency(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> dict[str, set[str]]:
    """
    seed trace 인접성 검사용 무방향 그래프를 만든다.

    실제 graph edge는 canonical 방향을 유지한다. seed trace는 사람이 읽는 end-to-end
    층 연결을 검증하므로, SequenceDiagram 이 내부 SequenceStep 을 포함하는 관계처럼
    source 문서 구조에서 파생되는 인접성만 추가로 인정한다.
    """
    node_ids = {n["id"] for n in nodes}
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_ids}

    def connect(a: str, b: str) -> None:
        if a in node_ids and b in node_ids:
            adjacency[a].add(b)
            adjacency[b].add(a)

    for edge in edges:
        connect(edge.get("source", ""), edge.get("target", ""))

    for node in nodes:
        node_id = node["id"]
        if node.get("type") != "SequenceStep" or ":step-" not in node_id:
            continue
        parent_id = node_id.split(":step-", 1)[0]
        connect(parent_id, node_id)

    return adjacency


def _has_seed_trace_path(
    start: str,
    goal: str,
    adjacency: dict[str, set[str]],
    max_hops: int = 2,
) -> bool:
    """
    seed trace layer 사이의 설명 가능한 짧은 경로가 있는지 확인한다.

    max_hops=2는 직접 edge와 "문서 블록 → 내부 step → API", "TestCase → API →
    CodeSymbol" 정도의 인접성을 허용하기 위한 한계다. 더 긴 경로는 seed가 너무
    느슨해지므로 gap으로 남긴다.
    """
    if start == goal:
        return True
    if start not in adjacency or goal not in adjacency:
        return False

    seen = {start}
    frontier: list[tuple[str, int]] = [(start, 0)]

    while frontier:
        node_id, depth = frontier.pop(0)
        if depth >= max_hops:
            continue
        for next_id in adjacency.get(node_id, set()):
            if next_id == goal:
                return True
            if next_id in seen:
                continue
            seen.add(next_id)
            frontier.append((next_id, depth + 1))

    return False


def check_seed_traces(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    seed_traces_path: Path,
) -> list[TraceFinding]:
    """
    seed-traces.yml 을 로드해 각 seed trace 의 유효성을 검사한다.

    pyyaml lazy import: 없으면 graceful warn 반환.
    파일 자체가 없어도 graceful warn 반환.
    단, main() preflight(_yaml_dependency_error)가 파일 존재 시 먼저 exit 2(fail-closed)로 차단한다.

    검사 항목:
    (a) 모든 layer node 가 인덱스에 존재 -- 없으면 broken_reference (deterministic error).
    (b) layers 수 >= 5 -- 미만이면 seed_trace_too_short (deterministic error).
    (c) 인접 layer 가 edge(auto 또는 manual)로 연결 -- 끊기면 seed_trace_gap (warn/coverage).

    Args:
        nodes: index.json 의 nodes 목록.
        edges: index.json 의 edges 목록 (auto + manual 병합 후).
        seed_traces_path: seed-traces.yml 경로.

    Returns:
        TraceFinding 목록.
    """
    findings: list[TraceFinding] = []

    # pyyaml lazy import
    try:
        import yaml  # type: ignore[import]
    except ImportError:
        findings.append(
            TraceFinding(
                severity="warn",
                kind="seed_trace_check_skipped",
                message="pyyaml not installed; seed-traces.yml 검증을 건너뜁니다",
                subject=str(seed_traces_path),
                location=None,
                category="semantic_candidate",
            )
        )
        return findings

    # 파일 존재 확인
    if not seed_traces_path.exists():
        findings.append(
            TraceFinding(
                severity="warn",
                kind="seed_trace_check_skipped",
                message=f"seed-traces.yml 없음: {seed_traces_path}",
                subject=str(seed_traces_path),
                location=None,
                category="semantic_candidate",
            )
        )
        return findings

    # YAML 로드
    try:
        with seed_traces_path.open(encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
    except Exception as exc:
        findings.append(
            TraceFinding(
                severity="error",
                kind="broken_reference",
                message=f"seed-traces.yml 파싱 실패: {exc}",
                subject=str(seed_traces_path),
                location=str(seed_traces_path),
                category="deterministic",
            )
        )
        return findings

    # traces 목록 추출
    raw = doc if isinstance(doc, dict) else {}
    seed_list = raw.get("traces", []) or []
    if isinstance(doc, list):
        seed_list = doc

    node_ids: set[str] = {n["id"] for n in nodes}

    adjacency = _build_seed_trace_adjacency(nodes, edges)

    for seed in seed_list:
        if not isinstance(seed, dict):
            continue

        seed_id = seed.get("id", "(unnamed)")
        layers = seed.get("layers", []) or []

        # (b) layers >= 5 검사
        if len(layers) < 5:
            findings.append(
                TraceFinding(
                    severity="error",
                    kind="seed_trace_too_short",
                    message=(f"seed '{seed_id}' has {len(layers)} layers (minimum 5 required)"),
                    subject=seed_id,
                    location=str(seed_traces_path),
                    category="deterministic",
                )
            )

        # 각 layer node 추출 (dict 또는 str)
        layer_nodes: list[str] = []
        for layer_item in layers:
            nid = layer_item.get("node", "") if isinstance(layer_item, dict) else str(layer_item)
            layer_nodes.append(nid)

        # (a) 모든 layer node 실재 검사
        for nid in layer_nodes:
            if nid and nid not in node_ids:
                findings.append(
                    TraceFinding(
                        severity="error",
                        kind="broken_reference",
                        message=(f"seed '{seed_id}' layer node '{nid}' not found in index"),
                        subject=nid,
                        location=str(seed_traces_path),
                        category="deterministic",
                    )
                )

        # (c) 인접 layer edge 연결 검사 (warn/coverage -- hard fail 아님)
        for i in range(len(layer_nodes) - 1):
            a = layer_nodes[i]
            b = layer_nodes[i + 1]
            if not a or not b:
                continue
            # 두 노드 중 하나라도 없으면 (a)에서 이미 에러 -- gap 체크 건너뜀
            if a not in node_ids or b not in node_ids:
                continue
            if not _has_seed_trace_path(a, b, adjacency):
                findings.append(
                    TraceFinding(
                        severity="warn",
                        kind="seed_trace_gap",
                        message=(
                            f"seed '{seed_id}' layer gap: '{a}' and '{b}' "
                            f"not connected by any edge (auto or manual)"
                        ),
                        subject=seed_id,
                        location=str(seed_traces_path),
                        category="coverage",
                    )
                )

    return findings


def run_all_checks(
    data: dict[str, Any],
    index_path: Path,
    manual_edges_path: Path,
    seed_traces_path: Path | None = None,
    api_exclusions_path: Path | None = None,
) -> list[TraceFinding]:
    """
    모든 검증 함수를 실행하고 findings 를 합산해 반환한다.

    Args:
        data: 로드된 index.json dict.
        index_path: index.json 경로 (secrets 검사용).
        manual_edges_path: manual-edges.yml 경로.
        seed_traces_path: seed-traces.yml 경로 (None 이면 repo root 기준 기본값 사용).
        api_exclusions_path: api-exclusions.yml 경로 (None 이면 repo root 기준 기본값 사용).

    Returns:
        전체 TraceFinding 목록.
    """
    nodes: list[dict[str, Any]] = data.get("nodes", [])
    edges: list[dict[str, Any]] = data.get("edges", [])

    findings: list[TraceFinding] = []

    # (a) broken references (edge 기반)
    findings.extend(check_broken_references(nodes, edges))

    # (b) orphan Must requirements
    findings.extend(check_orphans(nodes, edges))

    # (c) superseded in use
    findings.extend(check_superseded(nodes, edges))

    # (d) sequence ↔ API mismatch / api_unlinked
    findings.extend(check_sequence_api(nodes, edges, api_exclusions_path))

    # (e) usecase test coverage
    findings.extend(check_usecase_test_coverage(nodes, edges))

    # (f) manual edges
    findings.extend(check_manual_edges(nodes, manual_edges_path))

    # (g) secrets in index
    index_text = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    findings.extend(check_secrets(index_text))

    # (h) seed traces (Phase 7) — manual edges 병합 후 검사
    _seed_path = seed_traces_path
    if _seed_path is None:
        _seed_path = _ontology_path("seed-traces.yml")

    # manual edges를 edges에 병합해 gap 검사에 반영
    _merged_edges = list(edges)
    try:
        import yaml  # type: ignore[import]

        if manual_edges_path.exists():
            with manual_edges_path.open(encoding="utf-8") as _f:
                _me_doc = yaml.safe_load(_f) or {}
            for _me in _me_doc.get("edges") or []:
                if isinstance(_me, dict) and _me.get("source") and _me.get("target"):
                    _merged_edges.append(
                        {
                            "type": _me.get("type", ""),
                            "source": _me["source"],
                            "target": _me["target"],
                            "origin": "manual",
                        }
                    )
    except Exception:
        pass  # yaml 파싱 실패 시 auto edges만으로 gap 검사 (pyyaml 부재는 main preflight가 exit 2로 선차단)

    findings.extend(check_seed_traces(nodes, _merged_edges, _seed_path))

    return findings


def _write_ci_summary(
    findings: list[TraceFinding],
    out_path: Path,
    node_count: int,
    edge_count: int,
) -> None:
    """
    ci-summary.json 을 작성한다.

    출력 내용: category별 카운트 + finding 목록 (kind/severity/subject/location 만, PII 미포함).

    Args:
        findings: 전체 TraceFinding 목록.
        out_path: 출력 파일 경로.
        node_count: 노드 수.
        edge_count: 엣지 수.
    """
    det_errors = [f for f in findings if f.category == "deterministic" and f.severity == "error"]
    det_warns = [f for f in findings if f.category == "deterministic" and f.severity == "warn"]
    sem_candidates = [f for f in findings if f.category == "semantic_candidate"]
    coverage = [f for f in findings if f.category == "coverage"]

    # finding 요약 (message 포함하되 PII 없이 — message는 id/path/kind만 담도록 구현됨)
    def _summarize(f: TraceFinding) -> dict[str, Any]:
        return {
            "kind": f.kind,
            "severity": f.severity,
            "category": f.category,
            "subject": f.subject,
            "location": f.location,
            "message": f.message,
        }

    summary = {
        "graph": {
            "node_count": node_count,
            "edge_count": edge_count,
        },
        "deterministic_error_count": len(det_errors),
        "deterministic_warning_count": len(det_warns),
        "semantic_candidate_count": len(sem_candidates),
        "coverage_count": len(coverage),
        "total_findings": len(findings),
        "categories": {
            "deterministic": {
                "errors": [
                    _summarize(f)
                    for f in sorted(det_errors, key=lambda x: (x.kind, x.subject or ""))
                ],
                "warnings": [
                    _summarize(f)
                    for f in sorted(det_warns, key=lambda x: (x.kind, x.subject or ""))
                ],
            },
            "semantic_candidate": [
                _summarize(f)
                for f in sorted(sem_candidates, key=lambda x: (x.kind, x.subject or ""))
            ],
            "coverage": [
                _summarize(f) for f in sorted(coverage, key=lambda x: (x.kind, x.subject or ""))
            ],
        },
        "summary": {
            "deterministic_errors": len(det_errors),
            "semantic_candidates": len(sem_candidates),
            "coverage_warnings": len(coverage) + len(det_warns),
            # exit_code 1=hard-fail, 0=clean (tool error = 2, 여기서 기록 안 함)
            "exit_code": 1 if det_errors else 0,
        },
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def _run_selftest() -> int:
    """
    --selftest 모드: 인메모리 broken 입력으로 탐지 데모.

    broken_reference 와 malformed_manual_edge 를 탐지하면 성공(exit 0),
    탐지 실패 시 exit 1 반환.

    Returns:
        성공 시 0, 실패 시 1.
    """
    print("[selftest] broken_reference 및 malformed_manual_edge 탐지 데모 시작")

    # ── 테스트 1: broken_reference ──────────────────────────────
    fake_nodes = [
        {
            "id": "REQ-001",
            "type": "Requirement",
            "source_file": "prd.md",
            "source_loc": None,
            "title": "test req",
            "attrs": {"priority": "Must"},
        },
        {
            "id": "ADR-0001",
            "type": "ADR",
            "source_file": "adr/0001.md",
            "source_loc": None,
            "title": "test adr",
            "attrs": {"status": "Accepted"},
        },
    ]
    fake_edges_broken = [
        {
            "type": "references",
            "source": "ADR-0001",
            "target": "REQ-NONEXISTENT",  # 존재하지 않는 id
            "origin": "auto",
            "evidence": "adr/0001.md:L5",
        }
    ]
    findings_br = check_broken_references(fake_nodes, fake_edges_broken)
    det_br = [f for f in findings_br if f.category == "deterministic" and f.severity == "error"]

    if det_br:
        print(
            f"[selftest] DETECTED broken_reference: {det_br[0].subject} "
            f"(kind={det_br[0].kind}, severity={det_br[0].severity})"
        )
    else:
        print("[selftest] FAIL: broken_reference 미탐지")
        return 1

    # ── 테스트 2: malformed_manual_edge (reason 누락) ───────────
    import importlib.util
    import os
    import tempfile

    yaml_available = importlib.util.find_spec("yaml") is not None

    if not yaml_available:
        print("[selftest] pyyaml 없음 — malformed_manual_edge 테스트 건너뜀")
    else:
        bad_yaml_content = """schema_version: "1"
edges:
  - type: governed_by
    source: "REQ-001"
    target: "REQ-001"
    owner: "test-team"
    last_verified_by_command: "echo ok"
"""
        # reason 누락 → malformed_manual_edge error 기대
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(bad_yaml_content)
            tmp_path = Path(tmp.name)

        try:
            findings_me = check_manual_edges(fake_nodes, tmp_path)
            det_me = [
                f for f in findings_me if f.category == "deterministic" and f.severity == "error"
            ]
            if det_me:
                print(
                    f"[selftest] DETECTED malformed_manual_edge: "
                    f"{det_me[0].subject} (kind={det_me[0].kind})"
                )
            else:
                print("[selftest] FAIL: malformed_manual_edge 미탐지")
                return 1
        finally:
            os.unlink(tmp_path)

    # ── 테스트 3: malformed_manual_edge (잘못된 type) ──────────
    if yaml_available:
        bad_type_yaml = """schema_version: "1"
edges:
  - type: invalid_type_xyz
    source: "REQ-001"
    target: "REQ-001"
    reason: "test reason"
    owner: "test-team"
    last_verified_by_command: "echo ok"
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(bad_type_yaml)
            tmp_path2 = Path(tmp.name)

        try:
            findings_type = check_manual_edges(fake_nodes, tmp_path2)
            det_type = [
                f
                for f in findings_type
                if f.category == "deterministic"
                and f.severity == "error"
                and f.kind == "malformed_manual_edge"
            ]
            if det_type:
                print(
                    f"[selftest] DETECTED malformed_manual_edge (invalid type): "
                    f"{det_type[0].subject}"
                )
            else:
                print("[selftest] FAIL: invalid type malformed_manual_edge 미탐지")
                return 1
        finally:
            os.unlink(tmp_path2)

    # ── 테스트 4: path-param 정규화 (smp-559 DELETE push-tokens) ──
    fake_nodes_api = [
        {
            "id": "unregister_push_token_api_v1_devices__device_id__push_tokens_delete",
            "type": "ApiOperation",
            "source_file": "docs/api/openapi.json",
            "source_loc": None,
            "title": "Unregister push token",
            "attrs": {
                "path": "/api/v1/devices/{device_id}/push-tokens",
                "method": "DELETE",
            },
        }
    ]
    fake_step = {
        "id": "smp-559#seq-2:step-2",
        "type": "SequenceStep",
        "source_file": "docs/specs/smp-559.md",
        "source_loc": "L87",
        "title": "App->>API: DELETE /api/v1/devices/{deviceId}/push-tokens",
        "attrs": {"raw": "App->>API: DELETE /api/v1/devices/{deviceId}/push-tokens"},
    }
    norm_result = _normalize_path("/api/v1/devices/{deviceId}/push-tokens")
    norm_expected = _normalize_path("/api/v1/devices/{device_id}/push-tokens")
    if norm_result == norm_expected:
        print(
            f"[selftest] DETECTED path-param normalization OK: "
            f"{{deviceId}} == {{device_id}} after normalize → '{norm_result}'"
        )
    else:
        print(f"[selftest] FAIL: normalization mismatch '{norm_result}' != '{norm_expected}'")
        return 1

    # fake_step 도 포함해서 확인 (path normalization broken_reference 미발생 확인)
    findings_norm2 = check_sequence_api(fake_nodes_api + [fake_step], [])
    broken_norm = [
        f
        for f in findings_norm2
        if f.kind == "broken_reference" and "deviceId" in (f.message or "")
    ]
    if not broken_norm:
        print(
            "[selftest] DETECTED smp-559 DELETE push-tokens: "
            "no false broken_reference after path-param normalization"
        )
    else:
        print("[selftest] FAIL: false broken_reference for smp-559 push-tokens normalization")
        return 1

    print("[selftest] 모든 탐지 케이스 PASS")
    return 0


def main() -> int:
    """
    검증 메인 로직. exit code 를 반환한다.

    CLI:
        python3 verify.py [index_path] [--selftest]

    exit code:
        0 — deterministic error 없음 (clean)
        1 — deterministic error 1개 이상 (hard gate 차단)
        2 — 도구/사용 오류 (파일 없음, JSON 파싱 실패, 예기치 않은 예외)
            pre-commit 은 2를 fail-open 으로 처리.
            CI(set -ceu)는 2를 loud fail 로 처리.
    """
    repo_root = _find_repo_root()
    args = sys.argv[1:]

    # --selftest 모드 — 도구 오류와 무관하게 0/1 만 반환
    if "--selftest" in args:
        result = _run_selftest()
        print(f"[selftest] exit={result}")
        return result

    try:
        # index 경로 선택 (기본값 또는 인자)
        index_path_arg = next((a for a in args if not a.startswith("--")), None)
        if index_path_arg:
            index_path = Path(index_path_arg)
        else:
            index_path = repo_root / "scratch" / "traceability" / "index.json"

        # 파일 없음 → exit 2 (도구/사용 오류)
        if not index_path.exists():
            print(
                f"[verify] ERROR: index.json 없음: {index_path}\n"
                "build_index.py 를 먼저 실행하세요.",
                file=sys.stderr,
            )
            return 2

        # JSON 파싱 — 실패 시 exit 2
        try:
            with index_path.open(encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            print(
                f"[verify] ERROR: index.json JSON 파싱 실패: {exc}",
                file=sys.stderr,
            )
            return 2

        # ontology yml 경로 (docs/ontology 는 trace-config 로 재정의 가능)
        _ontology_dir = repo_root / get_config(repo_root).path("ontology_dir")
        manual_edges_path = _ontology_dir / "manual-edges.yml"
        seed_traces_path = _ontology_dir / "seed-traces.yml"
        api_exclusions_path = _ontology_dir / "api-exclusions.yml"

        # pyyaml 설치 여부 사전 검사 (fail-closed) — YAML 파일 존재 시 미설치면 exit 2
        _dep_err = _yaml_dependency_error(manual_edges_path, seed_traces_path, api_exclusions_path)
        if _dep_err:
            print(
                f"[verify] ERROR: {_dep_err} — 'pip install pyyaml==6.0.2' 후 재실행.",
                file=sys.stderr,
            )
            return 2

        # 전체 검증 실행
        findings = run_all_checks(
            data, index_path, manual_edges_path, api_exclusions_path=api_exclusions_path
        )

        nodes = data.get("nodes", [])
        edges = data.get("edges", [])

        # 분류
        det_errors = [
            f for f in findings if f.category == "deterministic" and f.severity == "error"
        ]
        det_warns = [f for f in findings if f.category == "deterministic" and f.severity == "warn"]
        sem_candidates = [f for f in findings if f.category == "semantic_candidate"]
        coverage = [f for f in findings if f.category == "coverage"]

        # ci-summary.json 저장
        out_dir = repo_root / "scratch" / "traceability"
        summary_path = out_dir / "ci-summary.json"
        _write_ci_summary(findings, summary_path, len(nodes), len(edges))

        # stdout 요약 출력
        print(f"[verify] nodes={len(nodes)}, edges={len(edges)}, findings={len(findings)}")
        print(
            f"[verify] deterministic errors={len(det_errors)}, "
            f"deterministic warnings={len(det_warns)}, "
            f"semantic_candidates={len(sem_candidates)}, "
            f"coverage={len(coverage)}"
        )

        if det_errors:
            print("[verify] HARD FAIL — deterministic errors:")
            for f in sorted(det_errors, key=lambda x: (x.kind, x.subject or ""))[:20]:
                print(f"  [{f.kind}] {f.subject} @ {f.location}")
        else:
            print("[verify] OK — no deterministic errors")

        print(f"[verify] → {summary_path}")

        # exit 1 = deterministic error 있음 / exit 0 = clean
        return 1 if det_errors else 0

    except Exception as exc:
        # 예기치 않은 예외 → exit 2 (도구 오류, pre-commit fail-open)
        print(
            f"[verify] ERROR: 예기치 않은 예외 발생: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
