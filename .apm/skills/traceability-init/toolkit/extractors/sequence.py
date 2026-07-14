"""
Extractor that pulls SpecSection, SequenceDiagram, and SequenceStep nodes,
plus step_calls and references edges, from docs/specs/*.md.

Target files: docs/specs/*.md (sorted)
- SpecSection: each ## / ### heading unit
- SequenceDiagram: ```mermaid sequenceDiagram``` blocks (in `*sequence*.md`)
- SequenceStep: each message/Note unit inside a block (in `*sequence*.md`)
- step_calls edge: when a message text contains METHOD /path matching an openapi path
- references edge: when a SpecSection body or a Note/message contains REQ-NNN / ADR-NNNN
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from tools.traceability.config import get_config
from tools.traceability.extractors import register
from tools.traceability.model import TraceEdge, TraceIndex, TraceNode

# Parsing regexes
_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+)$")
_MERMAID_OPEN_RE = re.compile(r"^```mermaid\s*$")
_MERMAID_CLOSE_RE = re.compile(r"^```\s*$")
_SEQ_DIAGRAM_RE = re.compile(r"^\s*sequenceDiagram\s*$")

# Sequence message pattern: A->>B: text or A-->>B: text
_MSG_RE = re.compile(r"^\s*(\w[\w\s]*?)(?:->>|-->|->|-->>)(\w[\w\s]*?):\s*(.+)$")
# Note pattern: Note over A: text / Note right of A: text
_NOTE_RE = re.compile(
    r"^\s*Note\s+(?:over|right\s+of|left\s+of)\s+([\w,\s]+?):\s*(.+)$",
    re.IGNORECASE,
)

# HTTP method + path pattern (e.g. POST /api/v1/...)
_HTTP_CALL_RE = re.compile(r"\b(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)\s+(/[^\s,;\"\']+)")

# REQ/ADR reference pattern
_REQ_REF_RE = re.compile(r"\bREQ-(\d+)\b")
_ADR_REF_RE = re.compile(r"\bADR-(\d{4})\b")


def _slug(text: str) -> str:
    """Convert a heading text into an anchor slug."""
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    return s.strip("-")


def _collect_target_files(repo_root: Path) -> list[Path]:
    """Collect and sort the docs/specs/*.md file list."""
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
    Parse a single spec file and add its SpecSection, SequenceDiagram, and
    SequenceStep nodes, along with related edges, to the index.

    Args:
        repo_root: repository root path
        file_path: absolute path of the file to parse
        index: traceability index
        openapi_lookup: (normalized_path, method) → operationId mapping
    """
    rel_path = str(file_path.relative_to(repo_root))
    stem = file_path.stem

    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(
            f"[sequence] warning: failed to read {rel_path}: {exc}",
            file=sys.stderr,
        )
        return

    lines = text.splitlines()
    parse_sequences = "sequence" in file_path.name

    # ── Extract SpecSection nodes (## / ### headings) + REQ/ADR references in the body ──
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

    # ── Extract SequenceDiagram and SequenceStep nodes ──────────────────────
    seq_block_idx = 0  # sequenceDiagram block index within the file (0-based → 1-based)
    i = 0
    while i < len(lines):
        line = lines[i]

        # Detect the start of a ```mermaid block
        if not _MERMAID_OPEN_RE.match(line):
            i += 1
            continue

        # Check whether the line after ```mermaid is sequenceDiagram
        j = i + 1
        if j >= len(lines):
            i += 1
            continue

        if not _SEQ_DIAGRAM_RE.match(lines[j]):
            i += 1
            continue

        # Found a sequenceDiagram block
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

        # Parse the block contents (until the closing ``` line)
        step_idx = 0
        k = j + 1
        while k < len(lines):
            block_line = lines[k]

            # End of block
            if _MERMAID_CLOSE_RE.match(block_line):
                break

            raw_text = block_line.strip()
            if not raw_text:
                k += 1
                continue

            # Parse a Note or message line
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
                # Skip any other text line
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

                # step_calls edge: match HTTP METHOD /path
                for http_m in _HTTP_CALL_RE.finditer(raw_text):
                    method = http_m.group(1).upper()
                    path = http_m.group(2)
                    # Normalize the path: handle the /api/v1 prefix
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

                # references edge: REQ-NNN / ADR-NNNN
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

        # Also generate references edges for Note lines from the SequenceDiagram node
        # (scan the whole block text)
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
    Normalize a sequence diagram message's path into the OpenAPI paths key format.

    Replace every {…} with {} to prevent false mismatches caused by path
    parameter name differences such as {deviceId} vs {device_id}.
    Guarantees the same behavior as verify.py's _normalize_path.

    Example: /api/v1/devices/{deviceId}/push-tokens
             → /api/v1/devices/{}/push-tokens
    """
    # Strip the query string
    path = path.split("?")[0]
    # Strip the trailing slash
    path = path.rstrip("/")
    # Strip path parameter placeholder names: {deviceId} → {}
    path = re.sub(r"\{[^}]+\}", "{}", path)
    return path


def _build_openapi_lookup(repo_root: Path) -> dict[tuple[str, str], str]:
    """
    Build a (normalized_path, method) → operationId mapping from openapi.json.
    Parsed directly to avoid a dependency on other extractors.

    Both sides' paths are normalized with the same rule as _normalize_path
    to absorb name differences such as {deviceId} vs {device_id}.
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
                # Apply the same normalization to the OpenAPI side (guarantees both sides match)
                normalized = _normalize_path(path)
                lookup[(normalized, method.upper())] = op_id

    return lookup


@register("sequence")
def extract(repo_root: Path, index: TraceIndex) -> None:
    """
    Extract SpecSection, SequenceDiagram, and SequenceStep nodes, plus
    step_calls and references edges, from sequence diagram spec files.

    Args:
        repo_root: repository root path
        index: traceability index
    """
    files = _collect_target_files(repo_root)
    if not files:
        print(
            f"[sequence] warning: no target files found under "
            f"{get_config(repo_root).path('specs_dir')} — skipping",
            file=sys.stderr,
        )
        return

    # OpenAPI path → operationId mapping (used to create step_calls edges)
    openapi_lookup = _build_openapi_lookup(repo_root)

    for file_path in files:
        try:
            _extract_file(repo_root, file_path, index, openapi_lookup)
        except Exception as exc:  # noqa: BLE001
            print(
                f"[sequence] warning: failed to process {file_path.name}: {exc}",
                file=sys.stderr,
            )
