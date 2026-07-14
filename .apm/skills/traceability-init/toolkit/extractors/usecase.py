"""
Extractor that pulls UseCaseCategory nodes, UseCase nodes, and optional
refines edges from docs/requirements/usecase-coverage-checklist.md.

- UseCaseCategory: UC-1 through UC-14 (## N. Title sections)
- UseCase: IDs in the UC-{N}-{C|M|N}-{NN} format (table rows)
- refines edge: only created when the row/cell contains a REQ-NNN citation (sparse is expected)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from tools.traceability.config import get_config
from tools.traceability.extractors import register
from tools.traceability.model import TraceEdge, TraceIndex, TraceNode

# UC-{N} category section heading pattern: "### 1. Identity Verification Result"
_CATEGORY_RE = re.compile(
    r"^###\s+(\d{1,2})\.\s+(.+)$",
    re.MULTILINE,
)

# UC ID pattern: UC-{N}-{C|M|N}-{NN}
_UC_ID_RE = re.compile(r"\b(UC-(\d{1,2})-([CMN])-(\d{2}))\b")

# REQ reference pattern
_REQ_REF_RE = re.compile(r"\bREQ-(\d+)\b")


def _anchor(text: str) -> str:
    """Convert a heading text into a slug anchor."""
    return re.sub(r"[^\w\s-]", "", text.lower()).strip().replace(" ", "-")


@register("usecase")
def extract(repo_root: Path, index: TraceIndex) -> None:
    """
    Extract UseCaseCategory and UseCase nodes, plus refines edges, from the
    use case checklist and add them to the index.

    Args:
        repo_root: repository root path
        index: traceability index
    """
    checklist_rel = get_config(repo_root).path("usecase_checklist")
    checklist_path = repo_root / checklist_rel
    if not checklist_path.exists():
        print(
            f"[usecase] warning: {checklist_rel} not found — skipping",
            file=sys.stderr,
        )
        return

    try:
        text = checklist_path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(
            f"[usecase] warning: failed to read {checklist_rel}: {exc}",
            file=sys.stderr,
        )
        return

    lines = text.splitlines()

    # ── 1. Extract UseCaseCategory nodes (### N. Title) ────────────────────
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

    # ── 2. Extract UseCase nodes + refines edges (table rows) ──────────────
    for lineno, line in enumerate(lines, start=1):
        # Only process table rows starting with a pipe
        if not line.strip().startswith("|"):
            continue

        # Find UC IDs in the row
        uc_matches = _UC_ID_RE.findall(line)
        for uc_id, entity_num, category, _seq in uc_matches:
            # Skip rows that aren't content rows (e.g. a "current status" row);
            # only process rows where the first column is the UC ID
            if not re.match(r"^\|\s*" + re.escape(uc_id), line):
                continue

            # UseCase node
            # Description text: second column
            cols = [c.strip() for c in line.split("|")]
            # cols[0]='', cols[1]=UC ID, cols[2]=kind, cols[3]=description, ...
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

            # refines edge: only when the same row has a REQ-NNN reference
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

    # ── 3. Also collect REQ references from UC verification matrix rows ────
    # Add a refines edge when a matrix row contains a REQ citation
    for lineno, line in enumerate(lines, start=1):
        if not line.strip().startswith("|"):
            continue
        # Matrix row pattern: | UC-X-* | ... REQ-NNN ... |
        uc_id_m = re.search(r"\b(UC-(\d{1,2})-[CMN]-\d{2})\b", line)
        if not uc_id_m:
            continue
        uc_id = uc_id_m.group(1)
        # Collect any additional REQ references regardless of whether the row was already processed above
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
