"""
traceability index verification entry point (Phase 3 full implementation).

Run from repo root:
    python3 tools/traceability/verify.py [index_path]
    python3 tools/traceability/verify.py --selftest

Output artifact: scratch/traceability/ci-summary.json

exit code:
    0 — no deterministic errors (warn/semantic_candidate findings may still exist)
    1 — one or more deterministic errors (blocks the CI hard gate and pre-commit)
    2 — tool/usage error: index file missing/unreadable, JSON parse error, unexpected exception.
        pre-commit treats exit 2 as fail-open (pass-through).
        CI (set -ceu) treats exit 2 as a build failure — tool errors are a loud fail in CI.

Verification policy:
  hard fail (severity=error, category=deterministic, exit 1):
    - broken_reference: a source document (prd/adr/spec/openapi) cites a canonical id that doesn't exist
    - malformed_manual_edge: disallowed edge type or missing required field
    - secret_in_index: coordinate/phone number/token/API key pattern

  warn (severity=warn, category=semantic_candidate or coverage, exit 0):
    - orphan: a Must requirement is missing a required edge
    - superseded_in_use: a superseded requirement/ADR is still the primary source for an active node
    - api_unlinked: an openapi operation isn't linked to any sequence/usecase/test
    - test_coverage_drift: a test marker points to a UC not in the checklist
    - coverage_gap: checklist shows PASS but no test node exists (or vice versa)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

# sys.path bootstrap
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT_CANDIDATE = _SCRIPT_DIR.parent.parent
if str(_REPO_ROOT_CANDIDATE) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT_CANDIDATE))

from tools.traceability.config import get_config  # noqa: E402
from tools.traceability.model import TraceFinding  # noqa: E402

# ────────────────────────────────────────────────────────────
# Allowed edge types (per schema.md)
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

# manual edge required fields
_MANUAL_EDGE_REQUIRED_FIELDS: tuple[str, ...] = (
    "type",
    "source",
    "target",
    "reason",
    "owner",
    "last_verified_by_command",
)

# api_unlinked exclusion entry required fields
_API_EXCLUSION_REQUIRED_FIELDS: tuple[str, ...] = (
    "operation_id",
    "reason",
    "owner",
    "last_verified_by_command",
)


def _find_repo_root() -> Path:
    """Locate the repo root."""
    return _SCRIPT_DIR.parent.parent


def _cfg():
    """The current repo's trace-config (cached). Falls back to defaults if absent."""
    return get_config(_find_repo_root())


def _ontology_path(filename: str) -> Path:
    """Absolute path to a file under docs/ontology (can be redefined via config)."""
    root = _find_repo_root()
    return root / _cfg().path("ontology_dir") / filename


def _yaml_dependency_error(
    manual_edges_path: Path,
    seed_traces_path: Path,
    api_exclusions_path: Path | None = None,
) -> str | None:
    """Return an error message if a YAML file exists but pyyaml can't be imported, else None."""
    try:
        import yaml  # noqa: F401

        return None
    except ImportError:
        candidates = [manual_edges_path, seed_traces_path]
        if api_exclusions_path is not None:
            candidates.append(api_exclusions_path)
        present = [str(p) for p in candidates if p.exists()]
        if present:
            return "pyyaml not installed — fail-closed: " + ", ".join(present)
        return None


def _normalize_path(path: str) -> str:
    """
    Normalize HTTP path path-parameter placeholders to {}.

    Carried over from phase 2: replace every {…} with {} so that naming
    differences like {deviceId} vs {device_id} don't cause a false mismatch.

    Args:
        path: The original HTTP path string.

    Returns:
        The normalized path with placeholder names stripped.
    """
    return re.sub(r"\{[^}]+\}", "{}", path)


def _extract_http_call(raw: str) -> tuple[str, str] | None:
    """
    Extract (HTTP_METHOD, path) from a sequence step's raw text.

    Args:
        raw: The raw Mermaid message text (e.g. 'App->>API: DELETE /api/v1/…').

    Returns:
        A (method, path) tuple, or None if this isn't an HTTP call.
    """
    # Extract the ": METHOD /path" pattern
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
    Build a (method, normalized_path) → operation_id mapping from OpenAPI nodes.

    Args:
        nodes: The nodes list from index.json.

    Returns:
        A dict mapping (method, normalized_path) → operationId.
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
    Load the api_unlinked exclusion list.

    An exclusion is only allowed when the API is a system endpoint rather
    than a product/user-facing scenario. Invalid exclusion entries are
    returned as deterministic findings so they're visible in CI.
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
                message=f"failed to parse api-exclusions.yml: {exc}",
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
    Detect canonical ids cited by source documents that don't exist as nodes.

    Scope:
    (a) A `REQ-###` / `ADR-####` cited in an ADR/spec/prd body has no target →
        the target of an edge (references, refines, supersedes, implements) is missing
    (b) A step_calls edge target is missing (sequence → API operation reference)

    Note: a dangling target on an exercises edge is handled as test_coverage_drift,
    so it's excluded here.

    Args:
        nodes: The nodes list from index.json.
        edges: The edges list from index.json.

    Returns:
        A list of broken_reference TraceFindings (severity=error, category=deterministic).
    """
    findings: list[TraceFinding] = []
    node_ids: set[str] = {n["id"] for n in nodes}

    # exercises edges are handled as test_coverage_drift — excluded here
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
    Detect orphaned Must requirements missing a required edge in some category.

    A Must requirement must have at least one edge in each of the following
    categories to be excluded from the warning:
    - ADR/spec link: a references/refines/implements edge (the REQ is source or target)
    - usecase link: a refines edge (UC → REQ)
    - API link: an implements edge (ApiOperation → REQ)
    - test link: a validates edge (TestCase → REQ, or to a linked UseCase)

    Not over-enforced: missing even one category is a warn, not a hard fail.

    Args:
        nodes: The nodes list from index.json.
        edges: The edges list from index.json.

    Returns:
        A list of orphan TraceFindings (severity=warn, category=semantic_candidate).
    """
    findings: list[TraceFinding] = []

    # Only check Must requirements
    _must = _cfg().must_priority
    must_reqs = {
        n["id"]
        for n in nodes
        if n.get("type") == "Requirement" and n.get("attrs", {}).get("priority") == _must
    }

    usecase_ids = {n["id"] for n in nodes if n.get("type") == "UseCase"}

    # req_id → set of edge types
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
        # Check per category (not over-enforced: missing even one is a warn)
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
    Detect a superseded requirement or ADR still acting as an active node's primary source.

    - A Requirement with priority=Superseded is used as the target of a references/implements edge
    - An ADR with status=Superseded is used as the target of a references edge

    Args:
        nodes: The nodes list from index.json.
        edges: The edges list from index.json.

    Returns:
        A list of superseded_in_use TraceFindings (severity=warn, category=semantic_candidate).
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

    # Detect edges where an active node references a superseded node as target
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
    Error if a sequence step's HTTP call is missing from openapi; warn if an openapi operation is unlinked.

    path-param normalization (carried over from phase 2):
    normalize every {…} → {} before comparing, to prevent a false mismatch from
    placeholder naming differences like {deviceId} vs {device_id}.

    hard fail:
    - a sequence step is an HTTP call whose normalized method+path isn't in openapi

    warn:
    - an openapi operation isn't linked to any sequence/usecase/test (api_unlinked)

    Args:
        nodes: The nodes list from index.json.
        edges: The edges list from index.json.

    Returns:
        A list of TraceFindings.
    """
    findings: list[TraceFinding] = []

    # Build the openapi lookup: (method, normalized_path) → operationId
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

    # Check whether sequence steps are linked to an API operation
    # (when a step_calls or exercises edge target is an ApiOperation)
    api_linked: set[str] = set()
    for edge in edges:
        if edge.get("type") in ("step_calls", "exercises", "validates", "implements"):
            tgt = edge.get("target", "")
            if tgt in openapi_op_ids:
                api_linked.add(tgt)

    # Check whether sequence HTTP step paths match an openapi operation
    seq_steps = [n for n in nodes if n.get("type") == "SequenceStep"]
    for step in seq_steps:
        raw = step.get("attrs", {}).get("raw", "")
        parsed = _extract_http_call(raw)
        if parsed is None:
            continue

        method, path = parsed
        norm_key = (method, _normalize_path(path))

        if norm_key not in openapi_lookup:
            # Also attempt an exact match for paths without path-params
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
            # Matched → mark as linked
            api_linked.add(openapi_lookup[norm_key])

    # api_unlinked: an openapi operation isn't linked to any sequence/usecase/test
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
    Compare the usecase-coverage-checklist matrix against test node coverage.

    - If a test's validates target is in none of checklist UC, Requirement, or
      PlatformRequirement → test_coverage_drift warn
      (this is not a hard fail — it may be an intentional verification signal or a sub-variant UC)
    - If a checklist UC has no test connected via a validates edge → coverage_gap warn

    Args:
        nodes: The nodes list from index.json.
        edges: The edges list from index.json.

    Returns:
        A list of TraceFindings (severity=warn, category=coverage).
    """
    findings: list[TraceFinding] = []

    # Checklist UC ids
    checklist_uc_ids: set[str] = {n["id"] for n in nodes if n.get("type") == "UseCase"}
    requirement_ids: set[str] = {
        n["id"] for n in nodes if n.get("type") in {"Requirement", "PlatformRequirement"}
    }

    # Collect UC targets from validates edges
    # if the target isn't in checklist_uc_ids → test_coverage_drift
    validates_edges = [e for e in edges if e.get("type") == "validates"]
    uc_covered_by_tests: set[str] = set()
    drift_reported: set[str] = set()

    for edge in validates_edges:
        tgt = edge.get("target", "")
        src = edge.get("source", "")

        if tgt in requirement_ids:
            continue

        if tgt not in checklist_uc_ids:
            # dangling validates target → test_coverage_drift warn (not a hard fail)
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

    # coverage_gap: a checklist UC isn't covered by any test
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
    Load manual-edges.yml and validate its type/required fields/source·target existence.

    pyyaml lazy import: returns a graceful warn if unavailable.
    Also returns a graceful warn if the file itself is missing.
    Note: main()'s preflight (_yaml_dependency_error) blocks earlier with exit 2
    (fail-closed) if the file exists but pyyaml is missing.

    hard fail:
    - the source or target node doesn't exist
    - a disallowed edge type
    - a missing required field (reason / owner / last_verified_by_command)

    Args:
        nodes: The nodes list from index.json.
        manual_edges_path: Path to manual-edges.yml.

    Returns:
        A list of TraceFindings.
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
                message="pyyaml not installed; skipping manual-edges.yml validation",
                subject=str(manual_edges_path),
                location=None,
                category="semantic_candidate",
            )
        )
        return findings

    # Check whether the file exists
    if not manual_edges_path.exists():
        findings.append(
            TraceFinding(
                severity="warn",
                kind="manual_edge_check_skipped",
                message=f"manual-edges.yml not found: {manual_edges_path}",
                subject=str(manual_edges_path),
                location=None,
                category="semantic_candidate",
            )
        )
        return findings

    # Load the YAML
    try:
        with manual_edges_path.open(encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
    except Exception as exc:
        findings.append(
            TraceFinding(
                severity="error",
                kind="malformed_manual_edge",
                message=f"failed to parse manual-edges.yml: {exc}",
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

        # Check that the edge type is allowed
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

        # Check for missing required fields
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

        # Check that source / target exist (as node ids)
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


# Secret pattern definitions (only id/path/kind are emitted — actual values are never exposed)
_SECRET_PATTERNS: list[tuple[str, str]] = [
    # Korean phone number: 010-XXXX-XXXX, 01X-XXX(X)-XXXX
    (r"\b01[016789]-?\d{3,4}-?\d{4}\b", "korean_phone_number"),
    # OpenAI API key
    (r"sk-[A-Za-z0-9]{16,}", "openai_api_key"),
    # PEM private key/certificate
    (r"-----BEGIN\s+\w", "pem_credential"),
    # AWS access key
    (r"AKIA[0-9A-Z]{16}", "aws_access_key"),
    # lat/lon float pair — JSON key form: "lat": 37.XXXX or "longitude": 127.XXXX
    (
        r'(?:"lat(?:itude)?"\s*:\s*[+-]?(?:[89]?\d|[1-8]\d)\.\d{4,}|'
        r'"lon(?:gitude)?"\s*:\s*[+-]?(?:1[0-7]\d|\d{1,2})\.\d{4,})',
        "gps_coordinate",
    ),
    # lat/lon bare decimal pair: 37.5665, 126.9780 (coordinates exposed without a JSON key)
    (
        r"[+-]?\d{1,3}\.\d{4,}\s*,\s*[+-]?\d{1,3}\.\d{4,}",
        "gps_coordinate",
    ),
    # JWT token: eyJ<header>.<payload>.<signature>
    (
        r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
        "jwt_token",
    ),
    # 64-char hex in CI/DI shape (personal identifier)
    (r"\b[0-9a-fA-F]{64}\b", "possible_ci_di_token"),
]


def check_secrets(index_text: str) -> list[TraceFinding]:
    """
    Check whether the index's serialized text contains a secret/PII pattern.

    The verifier itself must not expose what it found in its output:
    the finding message contains only pattern_kind and a match_location_hint,
    never the actual matched value.

    Args:
        index_text: The full serialized text of index.json.

    Returns:
        A list of secret_in_index TraceFindings (severity=error, category=deterministic).
    """
    findings: list[TraceFinding] = []
    reported_kinds: set[str] = set()

    for pattern, kind in _SECRET_PATTERNS:
        match = re.search(pattern, index_text)
        if match and kind not in reported_kinds:
            reported_kinds.add(kind)
            # Location hint only (no actual value included)
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
    Build an undirected graph for seed trace adjacency checks.

    Actual graph edges retain their canonical direction. Since a seed trace
    verifies a human-readable end-to-end layer connection, we additionally
    accept only adjacency that's derived from the source document structure —
    e.g. the relationship of a SequenceDiagram containing an internal SequenceStep.
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
    Check whether there's a short, explainable path between two seed trace layers.

    max_hops=2 is a limit that allows direct edges plus adjacency roughly like
    "document block → internal step → API" or "TestCase → API → CodeSymbol".
    Longer paths would make the seed too loose, so they're left as a gap.
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
    Load seed-traces.yml and validate each seed trace.

    pyyaml lazy import: returns a graceful warn if unavailable.
    Also returns a graceful warn if the file itself is missing.
    Note: main()'s preflight (_yaml_dependency_error) blocks earlier with exit 2
    (fail-closed) if the file exists but pyyaml is missing.

    Checks:
    (a) Every layer node exists in the index -- else broken_reference (deterministic error).
    (b) Number of layers >= 5 -- else seed_trace_too_short (deterministic error).
    (c) Adjacent layers are connected by an edge (auto or manual) -- else seed_trace_gap (warn/coverage).

    Args:
        nodes: The nodes list from index.json.
        edges: The edges list from index.json (after merging auto + manual).
        seed_traces_path: Path to seed-traces.yml.

    Returns:
        A list of TraceFindings.
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
                message="pyyaml not installed; skipping seed-traces.yml validation",
                subject=str(seed_traces_path),
                location=None,
                category="semantic_candidate",
            )
        )
        return findings

    # Check whether the file exists
    if not seed_traces_path.exists():
        findings.append(
            TraceFinding(
                severity="warn",
                kind="seed_trace_check_skipped",
                message=f"seed-traces.yml not found: {seed_traces_path}",
                subject=str(seed_traces_path),
                location=None,
                category="semantic_candidate",
            )
        )
        return findings

    # Load the YAML
    try:
        with seed_traces_path.open(encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
    except Exception as exc:
        findings.append(
            TraceFinding(
                severity="error",
                kind="broken_reference",
                message=f"failed to parse seed-traces.yml: {exc}",
                subject=str(seed_traces_path),
                location=str(seed_traces_path),
                category="deterministic",
            )
        )
        return findings

    # Extract the traces list
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

        # (b) check layers >= 5
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

        # Extract each layer node (dict or str)
        layer_nodes: list[str] = []
        for layer_item in layers:
            nid = layer_item.get("node", "") if isinstance(layer_item, dict) else str(layer_item)
            layer_nodes.append(nid)

        # (a) check that every layer node actually exists
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

        # (c) check adjacent-layer edge connectivity (warn/coverage -- not a hard fail)
        for i in range(len(layer_nodes) - 1):
            a = layer_nodes[i]
            b = layer_nodes[i + 1]
            if not a or not b:
                continue
            # If either node is missing, (a) already reported an error -- skip the gap check
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
    Run every verification function and return the combined findings.

    Args:
        data: The loaded index.json dict.
        index_path: Path to index.json (for the secrets check).
        manual_edges_path: Path to manual-edges.yml.
        seed_traces_path: Path to seed-traces.yml (None uses the repo-root-relative default).
        api_exclusions_path: Path to api-exclusions.yml (None uses the repo-root-relative default).

    Returns:
        The full list of TraceFindings.
    """
    nodes: list[dict[str, Any]] = data.get("nodes", [])
    edges: list[dict[str, Any]] = data.get("edges", [])

    findings: list[TraceFinding] = []

    # (a) broken references (edge-based)
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

    # (h) seed traces (Phase 7) — checked after merging manual edges
    _seed_path = seed_traces_path
    if _seed_path is None:
        _seed_path = _ontology_path("seed-traces.yml")

    # Merge manual edges into edges so the gap check accounts for them
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
        pass  # if YAML parsing fails, run the gap check with auto edges only
        # (a missing pyyaml is already blocked earlier by main's preflight with exit 2)

    findings.extend(check_seed_traces(nodes, _merged_edges, _seed_path))

    return findings


def _write_ci_summary(
    findings: list[TraceFinding],
    out_path: Path,
    node_count: int,
    edge_count: int,
) -> None:
    """
    Write ci-summary.json.

    Output content: per-category counts + a finding list (kind/severity/subject/location only, no PII).

    Args:
        findings: The full list of TraceFindings.
        out_path: Output file path.
        node_count: Number of nodes.
        edge_count: Number of edges.
    """
    det_errors = [f for f in findings if f.category == "deterministic" and f.severity == "error"]
    det_warns = [f for f in findings if f.category == "deterministic" and f.severity == "warn"]
    sem_candidates = [f for f in findings if f.category == "semantic_candidate"]
    coverage = [f for f in findings if f.category == "coverage"]

    # Finding summary (includes message but no PII — message is implemented to hold only id/path/kind)
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
            # exit_code 1=hard-fail, 0=clean (tool error = 2, not recorded here)
            "exit_code": 1 if det_errors else 0,
        },
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def _run_selftest() -> int:
    """
    --selftest mode: demonstrates detection using an in-memory broken input.

    Succeeds (exit 0) if broken_reference and malformed_manual_edge are detected,
    returns exit 1 if detection fails.

    Returns:
        0 on success, 1 on failure.
    """
    print("[selftest] starting broken_reference and malformed_manual_edge detection demo")

    # ── Test 1: broken_reference ──────────────────────────────
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
            "target": "REQ-NONEXISTENT",  # id that doesn't exist
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
        print("[selftest] FAIL: broken_reference not detected")
        return 1

    # ── Test 2: malformed_manual_edge (missing reason) ───────────
    import importlib.util
    import os
    import tempfile

    yaml_available = importlib.util.find_spec("yaml") is not None

    if not yaml_available:
        print("[selftest] pyyaml not installed — skipping malformed_manual_edge test")
    else:
        bad_yaml_content = """schema_version: "1"
edges:
  - type: governed_by
    source: "REQ-001"
    target: "REQ-001"
    owner: "test-team"
    last_verified_by_command: "echo ok"
"""
        # missing reason → expect a malformed_manual_edge error
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
                print("[selftest] FAIL: malformed_manual_edge not detected")
                return 1
        finally:
            os.unlink(tmp_path)

    # ── Test 3: malformed_manual_edge (invalid type) ──────────
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
                print("[selftest] FAIL: invalid type malformed_manual_edge not detected")
                return 1
        finally:
            os.unlink(tmp_path2)

    # ── Test 4: path-param normalization (camelCase vs snake_case) ──
    fake_nodes_api = [
        {
            "id": "revoke_access_token_api_v1_users__user_id__access_tokens_delete",
            "type": "ApiOperation",
            "source_file": "docs/api/openapi.json",
            "source_loc": None,
            "title": "Revoke access token",
            "attrs": {
                "path": "/api/v1/users/{user_id}/access-tokens",
                "method": "DELETE",
            },
        }
    ]
    fake_step = {
        "id": "ex-001#seq-2:step-2",
        "type": "SequenceStep",
        "source_file": "docs/specs/ex-001.md",
        "source_loc": "L87",
        "title": "App->>API: DELETE /api/v1/users/{userId}/access-tokens",
        "attrs": {"raw": "App->>API: DELETE /api/v1/users/{userId}/access-tokens"},
    }
    norm_result = _normalize_path("/api/v1/users/{userId}/access-tokens")
    norm_expected = _normalize_path("/api/v1/users/{user_id}/access-tokens")
    if norm_result == norm_expected:
        print(
            f"[selftest] DETECTED path-param normalization OK: "
            f"{{userId}} == {{user_id}} after normalize → '{norm_result}'"
        )
    else:
        print(f"[selftest] FAIL: normalization mismatch '{norm_result}' != '{norm_expected}'")
        return 1

    # Also include fake_step to confirm no broken_reference occurs after path normalization
    findings_norm2 = check_sequence_api(fake_nodes_api + [fake_step], [])
    broken_norm = [
        f for f in findings_norm2 if f.kind == "broken_reference" and "userId" in (f.message or "")
    ]
    if not broken_norm:
        print(
            "[selftest] DETECTED DELETE access-tokens: "
            "no false broken_reference after path-param normalization"
        )
    else:
        print("[selftest] FAIL: false broken_reference for access-tokens path-param normalization")
        return 1

    print("[selftest] all detection cases PASS")
    return 0


def main() -> int:
    """
    Main verification logic. Returns an exit code.

    CLI:
        python3 verify.py [index_path] [--selftest]

    exit code:
        0 — no deterministic errors (clean)
        1 — one or more deterministic errors (blocks the hard gate)
        2 — tool/usage error (file missing, JSON parse failure, unexpected exception)
            pre-commit treats 2 as fail-open.
            CI (set -ceu) treats 2 as a loud fail.
    """
    repo_root = _find_repo_root()
    args = sys.argv[1:]

    # --selftest mode — returns only 0/1, regardless of tool errors
    if "--selftest" in args:
        result = _run_selftest()
        print(f"[selftest] exit={result}")
        return result

    try:
        # Choose the index path (default or argument)
        index_path_arg = next((a for a in args if not a.startswith("--")), None)
        if index_path_arg:
            index_path = Path(index_path_arg)
        else:
            index_path = repo_root / "scratch" / "traceability" / "index.json"

        # File missing → exit 2 (tool/usage error)
        if not index_path.exists():
            print(
                f"[verify] ERROR: index.json not found: {index_path}\nRun build_index.py first.",
                file=sys.stderr,
            )
            return 2

        # JSON parsing — exit 2 on failure
        try:
            with index_path.open(encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            print(
                f"[verify] ERROR: failed to parse index.json as JSON: {exc}",
                file=sys.stderr,
            )
            return 2

        # ontology yml path (docs/ontology can be redefined via trace-config)
        _ontology_dir = repo_root / get_config(repo_root).path("ontology_dir")
        manual_edges_path = _ontology_dir / "manual-edges.yml"
        seed_traces_path = _ontology_dir / "seed-traces.yml"
        api_exclusions_path = _ontology_dir / "api-exclusions.yml"

        # Preflight check for pyyaml (fail-closed) — exit 2 if a YAML file exists but pyyaml is missing
        _dep_err = _yaml_dependency_error(manual_edges_path, seed_traces_path, api_exclusions_path)
        if _dep_err:
            print(
                f"[verify] ERROR: {_dep_err} — run 'pip install pyyaml==6.0.2' and retry.",
                file=sys.stderr,
            )
            return 2

        # Run all verification checks
        findings = run_all_checks(
            data, index_path, manual_edges_path, api_exclusions_path=api_exclusions_path
        )

        nodes = data.get("nodes", [])
        edges = data.get("edges", [])

        # Classify
        det_errors = [
            f for f in findings if f.category == "deterministic" and f.severity == "error"
        ]
        det_warns = [f for f in findings if f.category == "deterministic" and f.severity == "warn"]
        sem_candidates = [f for f in findings if f.category == "semantic_candidate"]
        coverage = [f for f in findings if f.category == "coverage"]

        # Save ci-summary.json
        out_dir = repo_root / "scratch" / "traceability"
        summary_path = out_dir / "ci-summary.json"
        _write_ci_summary(findings, summary_path, len(nodes), len(edges))

        # Print the stdout summary
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

        # exit 1 = deterministic errors present / exit 0 = clean
        return 1 if det_errors else 0

    except Exception as exc:
        # Unexpected exception → exit 2 (tool error, pre-commit fail-open)
        print(
            f"[verify] ERROR: unexpected exception occurred: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
