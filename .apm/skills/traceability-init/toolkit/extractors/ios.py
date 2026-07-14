"""
Extractor that pulls iOS traceability nodes from ios/docs/prd/*.md and ios/docs/adr/*.md.

- PlatformRequirement node: IOS-REQ-### pattern
  id = IOS-REQ-### (as-is)
- IOSADR node: ADR-I-#### heading
- IOSDecision node: IOS-DEC-### decision table row
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from tools.traceability.config import get_config
from tools.traceability.extractors import register
from tools.traceability.model import TraceEdge, TraceIndex, TraceNode

# Full item line pattern: "- IOS-REQ-NNN: description" format
_IOS_REQ_LINE_RE = re.compile(r"^\s*-\s+(IOS-REQ-\d{3}):\s*(.+)$")
_IOS_REQ_REF_RE = re.compile(r"\bIOS-REQ-(\d{3})(?:\s*~\s*(?:IOS-REQ-)?(\d{3}))?\b")
_REQ_REF_RE = re.compile(r"(?<!IOS-)\bREQ-(\d{3})(?:\s*~\s*(?:REQ-)?(\d{3}))?\b")
_ADR_REF_RE = re.compile(r"\bADR-(\d{4})\b")
_IOS_ADR_REF_RE = re.compile(r"\bADR-I-(\d{4})\b")
_IOS_DEC_REF_RE = re.compile(r"\bIOS-DEC-(\d{3})\b")
_IOS_ADR_HEADING_RE = re.compile(r"^#\s+(ADR-I-\d{4}):\s*(.+?)\s*$")
# NOTE: The Korean literals below ("상태:", "관련 요구사항:", "공통 ADR:") are functional
# markers matched against real iOS ADR document content — do not translate them.
_STATUS_LINE_RE = re.compile(r"^\s*-\s*상태:\s*(.+?)\s*$")
_RELATED_REQUIREMENTS_LINE_RE = re.compile(r"^\s*-\s*관련 요구사항:\s*(.+?)\s*$")
_COMMON_ADR_LINE_RE = re.compile(r"^\s*-\s*공통 ADR:\s*(.+?)\s*$")
_IOS_DECISION_ROW_RE = re.compile(r"^\|\s*(IOS-DEC-\d{3})\s*\|\s*(.+?)\s*\|\s*$")


def _expand_prefixed_range(
    prefix: str,
    start_text: str,
    end_text: str | None,
) -> list[str]:
    start = int(start_text)
    if end_text is None:
        return [f"{prefix}-{start:03d}"]

    end = int(end_text)
    if end < start:
        return [f"{prefix}-{start:03d}"]

    return [f"{prefix}-{number:03d}" for number in range(start, end + 1)]


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _extract_ref_ids(text: str) -> list[str]:
    """Extract stable ID references from iOS documents."""
    ref_ids: list[str] = []
    for match in _IOS_REQ_REF_RE.finditer(text):
        ref_ids.extend(_expand_prefixed_range("IOS-REQ", match.group(1), match.group(2)))
    for match in _REQ_REF_RE.finditer(text):
        ref_ids.extend(_expand_prefixed_range("REQ", match.group(1), match.group(2)))
    ref_ids.extend(f"ADR-{match}" for match in _ADR_REF_RE.findall(text))
    ref_ids.extend(f"ADR-I-{match}" for match in _IOS_ADR_REF_RE.findall(text))
    ref_ids.extend(f"IOS-DEC-{match}" for match in _IOS_DEC_REF_RE.findall(text))
    return _dedupe(ref_ids)


def _add_reference_edges(
    index: TraceIndex,
    source_id: str,
    target_ids: list[str],
    evidence: str,
) -> None:
    for target_id in target_ids:
        if target_id == source_id:
            continue
        index.add_edge(
            TraceEdge(
                type="references",
                source=source_id,
                target=target_id,
                origin="auto",
                evidence=evidence,
            )
        )


@register("ios")
def extract(repo_root: Path, index: TraceIndex) -> None:
    """
    Extract PlatformRequirement, IOSADR, and IOSDecision nodes from the iOS PRD/ADR documents.

    Args:
        repo_root: repository root path
        index: traceability index
    """
    _extract_platform_requirements(repo_root, index)
    _extract_ios_adrs(repo_root, index)


def _extract_platform_requirements(repo_root: Path, index: TraceIndex) -> None:
    """Extract PlatformRequirement nodes and source ref edges from the iOS PRD documents."""
    for req_rel_path in get_config(repo_root).path_list("ios_req_docs"):
        req_path = repo_root / req_rel_path
        if not req_path.exists():
            print(
                f"[ios] warning: {req_rel_path} not found — skipping",
                file=sys.stderr,
            )
            continue

        try:
            text = req_path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            print(
                f"[ios] warning: failed to read {req_rel_path}: {exc}",
                file=sys.stderr,
            )
            continue

        lines = text.splitlines()

        for lineno, line in enumerate(lines, start=1):
            # Item line format: "- IOS-REQ-NNN: description"
            m = _IOS_REQ_LINE_RE.match(line)
            if not m:
                continue

            req_id = m.group(1)  # e.g. IOS-REQ-001
            description = m.group(2).strip()

            # Extract the source reference in parentheses from the description (recorded in attrs)
            source_refs: list[str] = re.findall(r"\(([^)]+)\)", description)
            source_ref_text = source_refs[-1] if source_refs else None
            # Strip the source portion from the description (the trailing parenthetical)
            title = re.sub(r"\s*\([^)]+\)\s*$", "", description).strip()
            if len(title) > 200:
                title = title[:200] + "…"

            node = TraceNode(
                id=req_id,
                type="PlatformRequirement",
                source_file=req_rel_path,
                source_loc=f"L{lineno}",
                title=title if title else None,
                attrs={
                    "source_refs": source_ref_text,
                },
            )
            index.add_node(node)

            if source_ref_text:
                _add_reference_edges(
                    index=index,
                    source_id=req_id,
                    target_ids=_extract_ref_ids(source_ref_text),
                    evidence=f"{req_rel_path}:L{lineno}",
                )


def _extract_ios_adrs(repo_root: Path, index: TraceIndex) -> None:
    """Extract IOSADR and IOSDecision nodes from the iOS ADR documents."""
    adr_dir = repo_root / get_config(repo_root).path("ios_adr_dir")
    if not adr_dir.exists():
        return

    for adr_path in sorted(adr_dir.glob("ADR-I-*.md")):
        try:
            text = adr_path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            rel_path = str(adr_path.relative_to(repo_root))
            print(
                f"[ios] warning: failed to read {rel_path}: {exc}",
                file=sys.stderr,
            )
            continue

        _process_ios_adr_file(repo_root, adr_path, text, index)


def _process_ios_adr_file(
    repo_root: Path,
    adr_path: Path,
    text: str,
    index: TraceIndex,
) -> None:
    rel_path = str(adr_path.relative_to(repo_root))
    lines = text.splitlines()
    if not lines:
        return

    heading_match = _IOS_ADR_HEADING_RE.match(lines[0])
    if not heading_match:
        return

    adr_id = heading_match.group(1)
    title = heading_match.group(2)
    status: str | None = None
    related_requirement_ids: list[str] = []
    common_adr_ids: list[str] = []

    for line in lines[:12]:
        if match := _STATUS_LINE_RE.match(line):
            status = match.group(1)
        if match := _RELATED_REQUIREMENTS_LINE_RE.match(line):
            related_requirement_ids = [
                ref_id
                for ref_id in _extract_ref_ids(match.group(1))
                if ref_id.startswith("IOS-REQ-")
            ]
        if match := _COMMON_ADR_LINE_RE.match(line):
            common_adr_ids = [
                ref_id for ref_id in _extract_ref_ids(match.group(1)) if ref_id.startswith("ADR-")
            ]

    index.add_node(
        TraceNode(
            id=adr_id,
            type="IOSADR",
            source_file=rel_path,
            source_loc="L1",
            title=title,
            attrs={
                k: v
                for k, v in [
                    ("status", status),
                    ("related_requirements", related_requirement_ids),
                    ("common_adrs", common_adr_ids),
                ]
                if v
            },
        )
    )
    _add_reference_edges(
        index=index,
        source_id=adr_id,
        target_ids=related_requirement_ids + common_adr_ids,
        evidence=f"{rel_path}:L1",
    )

    for lineno, line in enumerate(lines, start=1):
        decision_match = _IOS_DECISION_ROW_RE.match(line)
        if not decision_match:
            continue

        decision_id = decision_match.group(1)
        decision_text = decision_match.group(2).strip()
        index.add_node(
            TraceNode(
                id=decision_id,
                type="IOSDecision",
                source_file=rel_path,
                source_loc=f"L{lineno}",
                title=decision_text[:200] + ("…" if len(decision_text) > 200 else ""),
                attrs={"parent_adr": adr_id},
            )
        )
        _add_reference_edges(
            index=index,
            source_id=decision_id,
            target_ids=[adr_id],
            evidence=f"{rel_path}:L{lineno}",
        )
