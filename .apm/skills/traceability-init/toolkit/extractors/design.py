"""
Extractor that pulls DesignScreen nodes from design/README.md.

- DesignScreen node: each ## / ### heading unit
  id = design:README#{anchor}
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from tools.traceability.config import get_config
from tools.traceability.extractors import register
from tools.traceability.model import TraceIndex, TraceNode

# ## / ### heading pattern
_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+)$")


def _slug(text: str) -> str:
    """Convert a heading text into an anchor slug."""
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    return s.strip("-")


@register("design")
def extract(repo_root: Path, index: TraceIndex) -> None:
    """
    Extract DesignScreen nodes from the ## / ### headings in design/README.md.

    Args:
        repo_root: repository root path
        index: traceability index
    """
    design_readme = get_config(repo_root).path("design_readme")
    readme_path = repo_root / design_readme
    if not readme_path.exists():
        print(
            f"[design] warning: {design_readme} not found — skipping",
            file=sys.stderr,
        )
        return

    try:
        text = readme_path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(
            f"[design] warning: failed to read {design_readme}: {exc}",
            file=sys.stderr,
        )
        return

    lines = text.splitlines()
    # Counter for handling duplicate anchors
    anchor_count: dict[str, int] = {}

    for lineno, line in enumerate(lines, start=1):
        m = _HEADING_RE.match(line)
        if not m:
            continue

        heading_text = m.group(2).strip()
        level = len(m.group(1))
        base_anchor = _slug(heading_text)

        # GitHub-style duplicate anchor handling (append -1, -2, ... from the second occurrence)
        if base_anchor not in anchor_count:
            anchor_count[base_anchor] = 0
            anchor = base_anchor
        else:
            anchor_count[base_anchor] += 1
            anchor = f"{base_anchor}-{anchor_count[base_anchor]}"

        screen_id = f"design:README#{anchor}"

        node = TraceNode(
            id=screen_id,
            type="DesignScreen",
            source_file=design_readme,
            source_loc=f"L{lineno}",
            title=heading_text,
            attrs={"level": level},
        )
        index.add_node(node)
