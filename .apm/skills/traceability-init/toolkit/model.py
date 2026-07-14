"""
Common data model for the traceability graph.

Defines TraceNode, TraceEdge, TraceFinding, and TraceIndex.
Uses only stdlib dataclasses + json, with no external dependencies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TraceNode:
    """
    Represents a single node in the traceability graph.

    Attributes:
        id: Stable node ID (e.g. REQ-102, ADR-0006).
        type: Node type as defined in schema.md (e.g. Requirement, ADR).
        source_file: Path to the source file it was extracted from (relative to repo root).
        source_loc: Location within the file (e.g. 'L107' or a frontmatter anchor).
        title: Human-readable title.
        attrs: Dictionary of additional attributes (e.g. status, priority).
    """

    id: str
    type: str
    source_file: str
    source_loc: str | None = None
    title: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to a stable serializable dictionary."""
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
    Represents a single edge in the traceability graph.

    Attributes:
        type: Edge type as defined in schema.md (e.g. references, refines).
        source: Source node ID.
        target: Target node ID.
        origin: 'auto' (generated automatically by an extractor) or 'manual' (manually entered).
        evidence: Location that grounds the extraction (e.g. 'prd.md:L107').
    """

    type: str
    source: str
    target: str
    origin: str = "auto"
    evidence: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to a stable serializable dictionary."""
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
    Represents a single issue found during verification or extraction.

    category values:
    - 'deterministic': CI hard gate — a clear-cut error that can be judged automatically.
    - 'semantic_candidate': agent review — a candidate that requires semantic judgment.

    Attributes:
        severity: One of 'error', 'warn', 'info'.
        kind: The kind of issue found (e.g. 'broken_reference', 'dangling_edge').
        message: Human-readable description.
        subject: The related node or edge ID.
        location: File/location where it occurred.
        category: 'deterministic' or 'semantic_candidate'.
    """

    severity: str
    kind: str
    message: str
    subject: str | None = None
    location: str | None = None
    category: str = "deterministic"

    def to_dict(self) -> dict[str, Any]:
        """Convert to a stable serializable dictionary."""
        return {
            "severity": self.severity,
            "kind": self.kind,
            "message": self.message,
            "subject": self.subject,
            "location": self.location,
            "category": self.category,
        }


# Key type used to detect duplicate edges: (type, source, target, origin)
_EdgeKey = tuple[str, str, str, str]


class TraceIndex:
    """
    Index holding all TraceNode and TraceEdge instances.

    Node ID conflict policy (keep-first):
    - If add_node is called twice with the same ID, the first-registered
      node is kept and the new one is discarded. The discard is recorded
      in the internal warnings list.
    - This policy defends against the same ID being extracted redundantly
      by multiple extractors.

    Edge duplicate policy (keep-first, keyed by (type, source, target, origin)):
    - If add_edge is called twice with the same (type, source, target, origin)
      combination, the first-registered edge is kept.
    - This means calling run_all() twice does not increase the edge count.
    - Edges with a different origin (e.g. 'auto' vs 'manual') are treated
      as distinct edges.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, TraceNode] = {}  # id → TraceNode
        self._edges: list[TraceEdge] = []
        # Set of edge dedup keys: (type, source, target, origin)
        self._edge_keys: set[_EdgeKey] = set()
        self._warnings: list[str] = []

    def add_node(self, node: TraceNode) -> None:
        """
        Add a node to the index.

        Applies the keep-first policy on conflict: keeps the existing
        node and discards the new one.
        """
        if node.id in self._nodes:
            # keep-first: keep the existing node, record the duplicate as a warning
            self._warnings.append(
                f"Node ID conflict (keep-first): '{node.id}' "
                f"existing={self._nodes[node.id].source_file}, "
                f"new={node.source_file} → keeping the existing node"
            )
            return
        self._nodes[node.id] = node

    def add_edge(self, edge: TraceEdge) -> None:
        """
        Add an edge to the index.

        keep-first duplicate policy: if an edge with the same
        (type, source, target, origin) already exists, the new edge is discarded.
        This policy ensures calling run_all() multiple times does not duplicate edges.
        """
        key: _EdgeKey = (edge.type, edge.source, edge.target, edge.origin)
        if key in self._edge_keys:
            # keep-first: keep the existing edge, discard the duplicate
            return
        self._edge_keys.add(key)
        self._edges.append(edge)

    def node_ids(self) -> list[str]:
        """Return all registered node IDs, sorted."""
        return sorted(self._nodes.keys())

    def nodes(self) -> list[TraceNode]:
        """Return all registered nodes, sorted by ID."""
        return [self._nodes[k] for k in sorted(self._nodes.keys())]

    def edges(self) -> list[TraceEdge]:
        """Return all registered edges, sorted by (type, source, target)."""
        return sorted(
            self._edges,
            key=lambda e: (e.type, e.source, e.target),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert the entire index to a stably sorted dictionary."""
        return {
            "nodes": [n.to_dict() for n in self.nodes()],
            "edges": [e.to_dict() for e in self.edges()],
            "warnings": sorted(self._warnings),
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize the index to a JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)
