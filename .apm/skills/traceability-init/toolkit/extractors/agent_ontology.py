"""Agent-authored ontology extractor (resource-agnostic extraction channel).

Deterministic extractors only parse fixed formats: ADR frontmatter with a 4-digit
id, OpenAPI JSON, Mermaid sequence diagrams. Any project whose assets use a
different convention -- ADRs as ``# ADR-001:`` headings, an API spec written as a
Markdown table, a design note in prose, a decision recorded only in a code comment
-- yields no nodes and no edges, so ``verify`` passes vacuously.

This extractor closes that gap. An agent reads arbitrary project resources, decides
which requirements / decisions / API operations / code symbols / tests exist and how
they relate, and records them as explicit nodes and edges in a structured file
(``docs/ontology/ontology.yml`` by default). This extractor injects them into the
index. Because the file is committed to the repo, verification stays fully
deterministic: extraction is done once by an agent when authoring a change, and CI
re-verifies the committed graph without any model in the loop.

Schema (version 1)::

    version: 1
    nodes:
      - id: ADR-001                       # canonical id (see conventions.md)
        type: ADR                         # node type (see schema.md)
        source: docs/adr/ADR-001-x.md     # repo-relative origin file
        loc: L1                           # optional location hint
        title: "GAE to GKE migration"     # optional
        attrs: {status: Accepted}         # optional
    edges:
      - type: implements                  # one of the allowed edge types
        source: campaign_engine/search_campaign.py:match_campaign
        target: ADR-015
        evidence: "docs/adr/ADR-015 rationale"   # optional

An edge's ``source``/``target`` may reference a node declared here or one produced
by any other extractor (e.g. a CodeSymbol id ``path/file.py:symbol``). Unresolved
ids surface as ``broken_reference`` in verify, exactly like auto-extracted edges.

The file is parsed with PyYAML (lazy import). If the file exists but PyYAML is
missing or the YAML is malformed, a TraceConfigError is raised so the build fails
closed -- consistent with the trace-config / manual-edges policy.
"""

from __future__ import annotations

from pathlib import Path

from tools.traceability.config import TraceConfigError, get_config
from tools.traceability.extractors import register
from tools.traceability.model import TraceEdge, TraceIndex, TraceNode


def _rel_source(repo_root: Path, source_path: Path) -> str:
    """Return the ontology.yml path relative to repo root for error messages."""
    try:
        return str(source_path.relative_to(repo_root))
    except ValueError:
        return str(source_path)


@register("agent_ontology")
def extract(repo_root: Path, index: TraceIndex) -> None:
    """Inject agent-authored nodes and edges from the ontology source file.

    No-op when the file is absent, so projects that rely solely on deterministic
    extractors are unaffected.
    """
    cfg = get_config(repo_root)
    source = repo_root / cfg.path("ontology_source")
    if not source.exists():
        return

    rel = _rel_source(repo_root, source)

    try:
        import yaml  # type: ignore[import]
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise TraceConfigError(
            f"{rel} exists but PyYAML is not installed -- fail-closed. "
            "Install with 'pip install pyyaml==6.0.2' and retry."
        ) from exc

    try:
        with source.open(encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
    except Exception as exc:  # noqa: BLE001 - surface any parse failure as fail-closed
        raise TraceConfigError(f"{rel} failed to parse: {exc}") from exc

    if not isinstance(doc, dict):
        raise TraceConfigError(f"{rel} top level must be a mapping")

    version = doc.get("version", 1)
    if version != 1:
        raise TraceConfigError(f"{rel} version {version} is unsupported (supported: 1)")

    _inject_nodes(doc.get("nodes") or [], rel, index)
    _inject_edges(doc.get("edges") or [], rel, index)


def _inject_nodes(raw_nodes: object, rel: str, index: TraceIndex) -> None:
    if not isinstance(raw_nodes, list):
        raise TraceConfigError(f"{rel} 'nodes' must be a list")
    for i, entry in enumerate(raw_nodes):
        if not isinstance(entry, dict):
            raise TraceConfigError(f"{rel} nodes[{i}] must be a mapping")
        try:
            node_id = str(entry["id"])
            node_type = str(entry["type"])
            node_source = str(entry["source"])
        except KeyError as exc:
            raise TraceConfigError(f"{rel} nodes[{i}] missing required field {exc}") from exc
        attrs = entry.get("attrs") or {}
        if not isinstance(attrs, dict):
            raise TraceConfigError(f"{rel} nodes[{i}].attrs must be a mapping")
        index.add_node(
            TraceNode(
                id=node_id,
                type=node_type,
                source_file=node_source,
                source_loc=(str(entry["loc"]) if entry.get("loc") is not None else None),
                title=(str(entry["title"]) if entry.get("title") is not None else None),
                attrs=attrs,
            )
        )


def _inject_edges(raw_edges: object, rel: str, index: TraceIndex) -> None:
    if not isinstance(raw_edges, list):
        raise TraceConfigError(f"{rel} 'edges' must be a list")
    for i, entry in enumerate(raw_edges):
        if not isinstance(entry, dict):
            raise TraceConfigError(f"{rel} edges[{i}] must be a mapping")
        try:
            edge_type = str(entry["type"])
            edge_source = str(entry["source"])
            edge_target = str(entry["target"])
        except KeyError as exc:
            raise TraceConfigError(f"{rel} edges[{i}] missing required field {exc}") from exc
        index.add_edge(
            TraceEdge(
                type=edge_type,
                source=edge_source,
                target=edge_target,
                origin="agent",
                evidence=(str(entry["evidence"]) if entry.get("evidence") is not None else rel),
            )
        )
