"""
Extractor that pulls Requirement nodes from docs/requirements/prd.md.

Extracted items: Markdown table rows in the `| REQ-NNN | description | Must/Should/... | source |` format.
Zero-padding is preserved as-is (e.g. REQ-002, REQ-077, REQ-102).
"""

from __future__ import annotations

import re
from pathlib import Path

from tools.traceability.config import get_config
from tools.traceability.extractors import register
from tools.traceability.model import TraceIndex, TraceNode

# REQ row pattern: `| REQ-NNN | description | priority | source |`
# Matches only rows whose first column starts with REQ-
_ROW_RE = re.compile(
    r"^\|\s*(REQ-\d+)\s*\|\s*(.*?)\s*\|\s*([\w/]+)\s*\|",
    re.MULTILINE,
)


@register("requirements")
def extract(repo_root: Path, index: TraceIndex) -> None:
    """
    Extract Requirement nodes from the PRD Markdown table and add them to the index.

    Each node's id uses the table's first column value as-is (zero-padding preserved).
    priority is stored in attrs.
    """
    prd_rel_path = get_config(repo_root).path("requirements")
    prd_path = repo_root / prd_rel_path
    if not prd_path.exists():
        return  # Silently skip if the file doesn't exist

    text = prd_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    for lineno, line in enumerate(lines, start=1):
        m = _ROW_RE.match(line)
        if not m:
            continue

        req_id = m.group(1)  # e.g. REQ-102
        title_raw = m.group(2)  # description (surrounding whitespace stripped)
        priority = m.group(3)  # e.g. Must, Should, Superseded

        # Strip Markdown strikethrough (~~) from the description
        title = re.sub(r"~~.*?~~", "", title_raw).strip()
        # Strip Markdown links: [text](url) → text
        title = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", title)
        # Truncate long descriptions (limited to 120 chars since it's used as the title)
        if len(title) > 120:
            title = title[:120] + "…"

        node = TraceNode(
            id=req_id,
            type="Requirement",
            source_file=prd_rel_path,
            source_loc=f"L{lineno}",
            title=title if title else None,
            attrs={"priority": priority},
        )
        index.add_node(node)
