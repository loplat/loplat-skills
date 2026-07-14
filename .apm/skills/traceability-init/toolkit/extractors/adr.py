"""
Extractor that pulls ADR nodes and ADR→REQ references edges from docs/adr/*.md.

Extracted items:
- frontmatter `id: ADR-NNNN` → ADR node
- `REQ-NNN` entries inside the frontmatter `related:` list → references edge (ADR → REQ)

Frontmatter is parsed with stdlib re, without a YAML parser (pyyaml).
Even if pyyaml is introduced in Phase 3, it should be wrapped in a lazy
import so build_index keeps working without pyyaml.
"""

from __future__ import annotations

import re
from pathlib import Path

from tools.traceability.config import get_config
from tools.traceability.extractors import register
from tools.traceability.model import TraceEdge, TraceIndex, TraceNode

# Extract the frontmatter block (between --- ... ---)
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)

# Extract the id field from frontmatter
_ID_RE = re.compile(r"^id:\s*(ADR-\d{4})\s*$", re.MULTILINE)

# Extract the title field from frontmatter
_TITLE_RE = re.compile(r"^title:\s*(.+?)\s*$", re.MULTILINE)

# Extract the status field from frontmatter
_STATUS_RE = re.compile(r"^status:\s*(.+?)\s*$", re.MULTILINE)

# Extract the date field from frontmatter
_DATE_RE = re.compile(r"^date:\s*(.+?)\s*$", re.MULTILINE)

# Extract REQ-NNN entries from the related block
_RELATED_BLOCK_RE = re.compile(r"^related:\s*\n((?:\s+-\s+\S+\n?)+)", re.MULTILINE)
_REQ_IN_RELATED_RE = re.compile(r"REQ-\d+")


@register("adr")
def extract(repo_root: Path, index: TraceIndex) -> None:
    """
    Walk docs/adr/*.md files and extract ADR nodes and references edges.

    Sorting by filename guarantees a deterministic order.
    """
    adr_dir = repo_root / get_config(repo_root).path("adr_dir")
    if not adr_dir.exists():
        return

    # Sort by filename to guarantee a deterministic processing order
    adr_files = sorted(adr_dir.glob("*.md"))

    for adr_path in adr_files:
        # README.md is not an ADR, so skip it
        if adr_path.name.upper() == "README.MD":
            continue

        _process_adr_file(repo_root, adr_path, index)


def _process_adr_file(repo_root: Path, adr_path: Path, index: TraceIndex) -> None:
    """Parse a single ADR file and add its node and edges to the index."""
    text = adr_path.read_text(encoding="utf-8")
    rel_path = str(adr_path.relative_to(repo_root))

    # Extract the frontmatter block
    fm_match = _FRONTMATTER_RE.match(text)
    if not fm_match:
        return  # Skip files without frontmatter
    frontmatter = fm_match.group(1)

    # Extract the id field
    id_match = _ID_RE.search(frontmatter)
    if not id_match:
        return  # Skip ADRs without an id
    adr_id = id_match.group(1)  # e.g. ADR-0006

    # Extract title, status (None if absent)
    title_match = _TITLE_RE.search(frontmatter)
    title = title_match.group(1) if title_match else None

    status_match = _STATUS_RE.search(frontmatter)
    status = status_match.group(1) if status_match else None

    date_match = _DATE_RE.search(frontmatter)
    date = date_match.group(1) if date_match else None

    # Compute the frontmatter's ending line number (used as source_loc)
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

    # Extract REQ references from the related block → create references edges
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
