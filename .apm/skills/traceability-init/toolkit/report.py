"""
Traceability report generation entry point (Phase 4 full implementation).

Run from repo root:
    python3 tools/traceability/report.py
    python3 tools/traceability/report.py --changed docs/requirements/prd.md

Outputs:
    scratch/traceability/report.md  -- human-readable Markdown report
    scratch/traceability/report.html -- self-contained interactive HTML report

CLI options:
    --changed <path> [<path> ...] -- populates the changed-file impact section
        (nodes with the matching source_file + 1-hop connected nodes/edges)
        No direct git calls -- accepted only as arguments.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# sys.path bootstrap
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT_CANDIDATE = _SCRIPT_DIR.parent.parent
if str(_REPO_ROOT_CANDIDATE) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT_CANDIDATE))

from tools.traceability.config import get_config  # noqa: E402

# Reuses the same seed-trace adjacency/path logic as verify.py so that report's "connection
# status" display stays consistent with verify's seed_trace_gap judgment (<=2-hop).
from tools.traceability.verify import (  # noqa: E402
    _build_seed_trace_adjacency,
    _has_seed_trace_path,
)


def _ontology_path(filename: str, repo_root: Path | None = None) -> Path:
    """Absolute path to a file under docs/ontology (overridable via config)."""
    root = repo_root or _find_repo_root()
    return root / get_config(root).path("ontology_dir") / filename


def _find_repo_root() -> Path:
    """Locate the repo root."""
    return _SCRIPT_DIR.parent.parent


# ────────────────────────────────────────────────────────────────────────
# Internal utilities
# ────────────────────────────────────────────────────────────────────────


def _escape_md(text: str) -> str:
    """Escape Markdown pipe characters."""
    return text.replace("|", "\\|")


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


# ────────────────────────────────────────────────────────────────────────
# Build sample trace path (based on the first seed in seed-traces.yml)
# ────────────────────────────────────────────────────────────────────────


def _build_sample_trace(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    repo_root: Path | None = None,
) -> list[dict[str, str]]:
    """
    Renders the first seed in seed-traces.yml as the core trace path.

    Walks the seed's layers in order and looks up each adjacent step pair in edges to
    reflect the actual connection status (direct edge / manual edge / unlinked) in
    edge_label/note. When connected, note is left empty so the HTML/MD render shows "linked".

    If there is no seed (e.g. another project hasn't written seed-traces.yml yet), it
    returns an empty list and the caller omits the section. No domain-specific id is hardcoded.

    Returns:
        List of trace nodes (includes id, type, source_file, title, edge_label, note, layer).
    """
    node_map: dict[str, dict[str, Any]] = {n["id"]: n for n in nodes}

    # Build (source, target, origin) pairs from edges -- store both directions
    auto_pairs: set[tuple[str, str]] = set()
    manual_pairs: set[tuple[str, str]] = set()
    for e in edges:
        s, t = e.get("source", ""), e.get("target", "")
        if not s or not t:
            continue
        if e.get("origin") == "manual":
            manual_pairs.add((s, t))
            manual_pairs.add((t, s))
        else:
            auto_pairs.add((s, t))
            auto_pairs.add((t, s))

    def _edge_status(prev_id: str, cur_id: str) -> tuple[str, str]:
        """Return (edge_label, note) for an adjacent pair. note='' when linked, filled when unlinked."""
        if (prev_id, cur_id) in auto_pairs or (cur_id, prev_id) in auto_pairs:
            return f"{prev_id} → {cur_id} [direct edge]", ""
        if (prev_id, cur_id) in manual_pairs or (cur_id, prev_id) in manual_pairs:
            return f"{prev_id} → {cur_id} [manual edge]", ""
        return f"(unlinked: no direct edge with {prev_id})", "(unlinked)"

    # Use the first seed's layers as the trace source
    seeds = _load_seed_traces(repo_root)
    if not seeds:
        return []
    first = seeds[0]
    layers = first.get("layers", []) or [] if isinstance(first, dict) else []

    trace: list[dict[str, str]] = []
    prev_id = ""
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        node_id = layer.get("node", "")
        if not node_id:
            continue
        layer_name = layer.get("layer", "") or ""
        if prev_id:
            edge_label, note = _edge_status(prev_id, node_id)
        else:
            edge_label, note = "", ""
        n = node_map.get(node_id)
        if n is None:
            trace.append(
                {
                    "id": node_id,
                    "type": "?",
                    "source_file": "(not in index)",
                    "title": node_id,
                    "edge_label": edge_label,
                    "note": note,
                    "layer": layer_name,
                }
            )
        else:
            trace.append(
                {
                    "id": node_id,
                    "type": n.get("type", ""),
                    "source_file": n.get("source_file", ""),
                    "title": n.get("title") or node_id,
                    "edge_label": edge_label,
                    "note": note,
                    "layer": layer_name,
                }
            )
        prev_id = node_id

    return trace


# ────────────────────────────────────────────────────────────────────────
# Seed Traces section rendering (Phase 7)
# ────────────────────────────────────────────────────────────────────────


def _load_seed_traces(repo_root: Path | None = None) -> list[dict]:
    """
    Loads seed-traces.yml and returns the trace list.

    Returns an empty list if pyyaml is missing or the file doesn't exist (graceful degradation).

    Args:
        repo_root: repo root Path (inferred from script location if None).

    Returns:
        List of seed trace dicts.
    """
    if repo_root is None:
        repo_root = _find_repo_root()
    seed_path = _ontology_path("seed-traces.yml", repo_root)
    if not seed_path.exists():
        return []
    try:
        import yaml  # type: ignore[import]

        with seed_path.open(encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
        if isinstance(doc, list):
            return doc
        return doc.get("traces", []) or []
    except Exception:
        return []


def _load_manual_edges(repo_root: Path | None = None) -> list[dict]:
    """
    Loads manual-edges.yml and returns a list of edge dicts.

    Args:
        repo_root: repo root Path.

    Returns:
        List of edge dicts.
    """
    if repo_root is None:
        repo_root = _find_repo_root()
    me_path = _ontology_path("manual-edges.yml", repo_root)
    if not me_path.exists():
        return []
    try:
        import yaml  # type: ignore[import]

        with me_path.open(encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
        return doc.get("edges", []) or []
    except Exception:
        return []


def _build_seed_connection_status(
    node_a: str,
    node_b: str,
    auto_pairs: set[tuple[str, str]],
    manual_pairs: set[tuple[str, str]],
    node_ids: set[str],
    adjacency: dict[str, set[str]] | None = None,
) -> str:
    """
    Returns the connection status between two adjacent layer nodes.

    Even without a 1-hop direct edge, reports 'indirect' (connected) if a <=2-hop path exists,
    matching verify.py's seed_trace_gap judgment. If adjacency isn't given, only 1-hop is judged.

    Returns:
        'direct' | 'manual' | 'indirect' | 'gap' | 'missing_node'
    """
    if node_a not in node_ids or node_b not in node_ids:
        return "missing_node"
    if (node_a, node_b) in auto_pairs or (node_b, node_a) in auto_pairs:
        return "direct"
    if (node_a, node_b) in manual_pairs or (node_b, node_a) in manual_pairs:
        return "manual"
    if adjacency is not None and _has_seed_trace_path(node_a, node_b, adjacency):
        return "indirect"
    return "gap"


def _build_seed_traces_section(
    nodes: list[dict],
    edges: list[dict],
    ci: dict,
    repo_root: Path | None = None,
) -> list[str]:
    """
    Returns the Markdown line list for the Seed Traces section.

    Args:
        nodes: index.json nodes list.
        edges: index.json edges list (auto).
        ci: ci-summary.json dict.
        repo_root: repo root Path.

    Returns:
        List of Markdown lines.
    """
    lines: list[str] = []
    seed_list = _load_seed_traces(repo_root)
    manual_edge_list = _load_manual_edges(repo_root)

    if not seed_list:
        lines.append("_(seed-traces.yml missing or failed to load)_")
        return lines

    node_map: dict = {n["id"]: n for n in nodes}
    node_ids: set = {n["id"] for n in nodes}

    # auto edges pair set
    auto_pairs: set = set()
    for e in edges:
        s, t = e.get("source", ""), e.get("target", "")
        if s and t:
            auto_pairs.add((s, t))
            auto_pairs.add((t, s))

    # manual edges pair set
    manual_pairs: set = set()
    for me in manual_edge_list:
        if not isinstance(me, dict):
            continue
        s, t = me.get("source", ""), me.get("target", "")
        if s and t:
            manual_pairs.add((s, t))
            manual_pairs.add((t, s))

    adjacency = _build_seed_trace_adjacency(nodes, edges)

    for seed in seed_list:
        if not isinstance(seed, dict):
            continue
        seed_id = seed.get("id", "(unnamed)")
        title = seed.get("title", seed_id)
        layers = seed.get("layers", []) or []

        lines.append(f"### {seed_id}: {_escape_md(title)}\n")
        lines.append("| Layer | Node id | Source file | Connection status |")
        lines.append("|---|---|---|---|")

        layer_nodes: list[str] = []
        for item in layers:
            nid = item.get("node", "") if isinstance(item, dict) else str(item)
            layer_nodes.append(nid)

        for i, nid in enumerate(layer_nodes):
            layer_item = layers[i] if i < len(layers) else {}
            layer_name = (
                layer_item.get("layer", f"L{i + 1}")
                if isinstance(layer_item, dict)
                else f"L{i + 1}"
            )
            n = node_map.get(nid)
            sf = n.get("source_file", "(not in index)") if n else "(not in index)"
            nid_esc = _escape_md(nid)
            sf_esc = _escape_md(sf)

            if nid not in node_ids:
                status = "**BROKEN (node missing)**"
            elif i == 0:
                status = "start"
            else:
                prev = layer_nodes[i - 1]
                conn = _build_seed_connection_status(
                    prev, nid, auto_pairs, manual_pairs, node_ids, adjacency
                )
                if conn == "direct":
                    status = "direct edge"
                elif conn == "manual":
                    status = "manual edge"
                elif conn == "indirect":
                    status = "indirect edge (<=2-hop)"
                elif conn == "gap":
                    status = "_(gap — no edge)_"
                else:
                    status = "_(no previous node)_"

            lines.append(f"| {_escape_md(layer_name)} | `{nid_esc}` | `{sf_esc}` | {status} |")

        lines.append("")

    # Summarize seed-related findings from ci-summary
    all_findings = []
    for cat_key in ("deterministic", "coverage", "semantic_candidate"):
        cat = ci.get("categories", {}).get(cat_key, [])
        if isinstance(cat, dict):
            all_findings.extend(cat.get("errors", []))
            all_findings.extend(cat.get("warnings", []))
        else:
            all_findings.extend(cat)

    seed_findings = [
        f
        for f in all_findings
        if f.get("kind") in ("broken_reference", "seed_trace_too_short", "seed_trace_gap")
        and f.get("location", "").endswith("seed-traces.yml")
    ]
    if seed_findings:
        lines.append("### Seed Trace Findings\n")
        lines.append("| kind | subject | message |")
        lines.append("|---|---|---|")
        for f in seed_findings[:20]:
            kind = f.get("kind", "")
            subject = _escape_md(f.get("subject") or "")
            msg = _escape_md(f.get("message") or "")
            lines.append(f"| {kind} | {subject} | {msg} |")
        lines.append("")

    return lines


def _build_seed_traces_html(
    nodes: list[dict],
    edges: list[dict],
    ci: dict,
    repo_root: Path | None = None,
) -> str:
    """
    Returns the Seed Traces HTML block string.

    Args:
        nodes: index.json nodes list.
        edges: index.json edges list (auto).
        ci: ci-summary.json dict.
        repo_root: repo root Path.

    Returns:
        HTML string.
    """
    seed_list = _load_seed_traces(repo_root)
    manual_edge_list = _load_manual_edges(repo_root)

    if not seed_list:
        return "<p class='muted'>(seed-traces.yml missing or failed to load)</p>"

    node_map: dict = {n["id"]: n for n in nodes}
    node_ids: set = {n["id"] for n in nodes}

    auto_pairs: set = set()
    for e in edges:
        s, t = e.get("source", ""), e.get("target", "")
        if s and t:
            auto_pairs.add((s, t))
            auto_pairs.add((t, s))

    manual_pairs: set = set()
    for me in manual_edge_list:
        if not isinstance(me, dict):
            continue
        s, t = me.get("source", ""), me.get("target", "")
        if s and t:
            manual_pairs.add((s, t))
            manual_pairs.add((t, s))

    adjacency = _build_seed_trace_adjacency(nodes, edges)

    html_parts: list[str] = []

    for seed in seed_list:
        if not isinstance(seed, dict):
            continue
        seed_id = _escape_html(seed.get("id", "(unnamed)"))
        title = _escape_html(seed.get("title", seed.get("id", "")))
        layers = seed.get("layers", []) or []

        layer_nodes: list[str] = []
        for item in layers:
            nid = item.get("node", "") if isinstance(item, dict) else str(item)
            layer_nodes.append(nid)

        html_parts.append(
            "<h3 style='margin-top:16px'>" + "<code>" + seed_id + "</code>: " + title + "</h3>"
        )
        html_parts.append(
            "<table><thead><tr>"
            + "<th>Layer</th><th>Node id</th><th>Source file</th><th>Connection status</th>"
            + "</tr></thead><tbody>"
        )

        for i, nid in enumerate(layer_nodes):
            layer_item = layers[i] if i < len(layers) else {}
            layer_name = (
                layer_item.get("layer", f"L{i + 1}")
                if isinstance(layer_item, dict)
                else f"L{i + 1}"
            )
            n = node_map.get(nid)
            sf = n.get("source_file", "(not in index)") if n else "(not in index)"

            nid_esc = _escape_html(nid)
            sf_esc = _escape_html(sf)
            layer_esc = _escape_html(layer_name)

            if nid not in node_ids:
                status_class = "unlinked"
                status_text = "BROKEN (node missing)"
            elif i == 0:
                status_class = "linked"
                status_text = "start"
            else:
                prev = layer_nodes[i - 1]
                conn = _build_seed_connection_status(
                    prev, nid, auto_pairs, manual_pairs, node_ids, adjacency
                )
                if conn == "direct":
                    status_class = "linked"
                    status_text = "direct edge"
                elif conn == "manual":
                    status_class = "linked"
                    status_text = "manual edge"
                elif conn == "indirect":
                    status_class = "linked"
                    status_text = "indirect edge (<=2-hop)"
                elif conn == "gap":
                    status_class = "unlinked"
                    status_text = "gap — no edge"
                else:
                    status_class = "unlinked"
                    status_text = "no previous node"

            html_parts.append(
                "<tr>"
                + "<td class='layer'>"
                + layer_esc
                + "</td>"
                + "<td><code class='node-id'>"
                + nid_esc
                + "</code></td>"
                + "<td><span class='path'>"
                + sf_esc
                + "</span></td>"
                + "<td class='"
                + status_class
                + "'>"
                + status_text
                + "</td>"
                + "</tr>"
            )

        html_parts.append("</tbody></table>")

    return "\n".join(html_parts)


def _ci_findings(ci: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten the finding list from ci-summary.json into a single list."""
    categories = ci.get("categories", {})
    findings: list[dict[str, Any]] = []

    deterministic = categories.get("deterministic", {})
    if isinstance(deterministic, dict):
        findings.extend(deterministic.get("errors", []))
        findings.extend(deterministic.get("warnings", []))
    elif isinstance(deterministic, list):
        findings.extend(deterministic)

    for key in ("semantic_candidate", "coverage"):
        category = categories.get(key, [])
        if isinstance(category, dict):
            findings.extend(category.get("errors", []))
            findings.extend(category.get("warnings", []))
        elif isinstance(category, list):
            findings.extend(category)

    return [f for f in findings if isinstance(f, dict)]


def _seed_id_from_finding(finding: dict[str, Any]) -> str:
    """Extract the target seed id from a seed_trace finding."""
    subject = finding.get("subject") or ""
    if isinstance(subject, str) and subject.startswith("SEED-"):
        return subject

    message = finding.get("message") or ""
    if isinstance(message, str) and "seed '" in message:
        seed_part = message.split("seed '", 1)[1]
        return seed_part.split("'", 1)[0]

    return ""


def _normalize_excerpt(text: str, limit: int = 520) -> str:
    """Trim the source excerpt into a short paragraph suitable for the HTML payload."""
    cleaned = " ".join(text.replace("|", " ").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def _node_excerpt_terms(node: dict[str, Any]) -> list[str]:
    """Return candidate strings used to search a node's source text."""
    terms: list[str] = []
    node_id = node.get("id", "")
    title = node.get("title", "")
    node_type = node.get("type", "")

    if title:
        terms.append(str(title))
    if node_id:
        terms.append(str(node_id))

    if "::" in node_id:
        terms.append(node_id.rsplit("::", 1)[-1])
    if ":" in node_id:
        terms.append(node_id.rsplit(":", 1)[-1])
    if "#" in node_id:
        terms.append(node_id.rsplit("#", 1)[-1].replace("-", " "))
    if node_type == "SequenceDiagram":
        terms.append("sequenceDiagram")

    # Searching for longer strings first improves hit rate against real body text like REQ/UC table rows.
    unique_terms = []
    seen = set()
    for term in sorted((t for t in terms if len(t) >= 3), key=len, reverse=True):
        if term not in seen:
            unique_terms.append(term)
            seen.add(term)
    return unique_terms


def _build_node_excerpt(
    node: dict[str, Any],
    repo_root: Path | None = None,
    context_lines: int = 2,
) -> str:
    """
    Extracts a source excerpt around the node from its source file.

    This is report-only best-effort logic to compensate for cases where the index's title
    alone isn't enough to understand meaning. Failure never blocks report generation -- it
    returns an empty string instead.
    """
    if repo_root is None:
        repo_root = _find_repo_root()

    source_file = node.get("source_file", "")
    if not source_file:
        return ""

    source_path = Path(source_file)
    if not source_path.is_absolute():
        source_path = repo_root / source_path
    if not source_path.exists() or not source_path.is_file():
        return ""

    try:
        text = source_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    lines = text.splitlines()
    if not lines:
        return ""

    terms = _node_excerpt_terms(node)
    lower_terms = [term.lower() for term in terms]
    hit_index: int | None = None

    if source_path.suffix.lower() in (".md", ".markdown"):
        for i, line in enumerate(lines):
            stripped = line.lstrip()
            line_lower = line.lower()
            if stripped.startswith("#") and (
                any(term in line for term in terms)
                or any(term in line_lower for term in lower_terms)
            ):
                hit_index = i
                break

    for i, line in enumerate(lines):
        if hit_index is not None:
            break
        line_lower = line.lower()
        if any(term in line for term in terms) or any(term in line_lower for term in lower_terms):
            hit_index = i
            break

    if hit_index is None:
        title = node.get("title") or node.get("id") or ""
        return _normalize_excerpt(str(title))

    hit_line = lines[hit_index]
    if "|" in hit_line:
        start = hit_index
        end = hit_index + 1
    elif hit_line.lstrip().startswith("#"):
        start = hit_index
        end = min(len(lines), hit_index + context_lines + 4)
    else:
        start = max(0, hit_index - context_lines)
        end = min(len(lines), hit_index + context_lines + 1)
    excerpt_lines = [line.strip() for line in lines[start:end] if line.strip()]
    return _normalize_excerpt(" ".join(excerpt_lines))


def _build_seed_graph_payload(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    ci: dict[str, Any],
    repo_root: Path | None = None,
) -> list[dict[str, Any]]:
    """
    Builds the seed trace graph payload used by the HTML SVG renderer.

    Carries only the seed-trace-centered subgraph instead of the full ontology graph.
    Each adjacent layer edge is marked as direct/manual/gap/missing_node.
    """
    seed_list = _load_seed_traces(repo_root)
    manual_edge_list = _load_manual_edges(repo_root)

    if not seed_list:
        return []

    node_map: dict[str, dict[str, Any]] = {n["id"]: n for n in nodes}
    node_ids: set[str] = set(node_map.keys())

    auto_pairs: set[tuple[str, str]] = set()
    manual_pairs: set[tuple[str, str]] = set()

    for edge in edges:
        source = edge.get("source", "")
        target = edge.get("target", "")
        if not source or not target:
            continue
        pair_set = manual_pairs if edge.get("origin") == "manual" else auto_pairs
        pair_set.add((source, target))
        pair_set.add((target, source))

    for manual_edge in manual_edge_list:
        if not isinstance(manual_edge, dict):
            continue
        source = manual_edge.get("source", "")
        target = manual_edge.get("target", "")
        if source and target:
            manual_pairs.add((source, target))
            manual_pairs.add((target, source))

    adjacency = _build_seed_trace_adjacency(nodes, edges)

    seed_findings: dict[str, list[dict[str, Any]]] = {}
    for finding in _ci_findings(ci):
        if finding.get("kind") not in (
            "broken_reference",
            "seed_trace_too_short",
            "seed_trace_gap",
        ):
            continue
        seed_id = _seed_id_from_finding(finding)
        if not seed_id:
            continue
        seed_findings.setdefault(seed_id, []).append(
            {
                "kind": finding.get("kind", ""),
                "severity": finding.get("severity", ""),
                "subject": finding.get("subject") or "",
                "location": finding.get("location") or "",
                "message": finding.get("message") or "",
            }
        )

    graphs: list[dict[str, Any]] = []
    for seed in seed_list:
        if not isinstance(seed, dict):
            continue

        seed_id = seed.get("id", "(unnamed)")
        layers = seed.get("layers", []) or []
        layer_payload: list[dict[str, Any]] = []

        for i, item in enumerate(layers):
            node_id = item.get("node", "") if isinstance(item, dict) else str(item)
            layer_name = item.get("layer", f"L{i + 1}") if isinstance(item, dict) else f"L{i + 1}"
            node = node_map.get(node_id)
            exists = node_id in node_ids
            layer_payload.append(
                {
                    "layer": layer_name,
                    "node": node_id,
                    "type": node.get("type", "?") if node else "?",
                    "source_file": node.get("source_file", "(not in index)")
                    if node
                    else "(not in index)",
                    "title": (node.get("title") or node_id) if node else node_id,
                    "excerpt": _build_node_excerpt(node, repo_root) if node else "",
                    "exists": exists,
                }
            )

        links: list[dict[str, str]] = []
        for i in range(1, len(layer_payload)):
            source = layer_payload[i - 1]["node"]
            target = layer_payload[i]["node"]
            status = _build_seed_connection_status(
                source,
                target,
                auto_pairs,
                manual_pairs,
                node_ids,
                adjacency,
            )
            links.append({"from": source, "to": target, "status": status})

        status_counts = Counter(link["status"] for link in links)
        graphs.append(
            {
                "id": seed_id,
                "title": seed.get("title", seed_id),
                "layers": layer_payload,
                "links": links,
                "findings": seed_findings.get(seed_id, []),
                "summary": {
                    "nodes": len(layer_payload),
                    "links": len(links),
                    "direct": status_counts.get("direct", 0),
                    "manual": status_counts.get("manual", 0),
                    "indirect": status_counts.get("indirect", 0),
                    "gap": status_counts.get("gap", 0),
                    "missing_node": status_counts.get("missing_node", 0),
                },
            }
        )

    return graphs


# ────────────────────────────────────────────────────────────────────────
# Changed-file impact analysis
# ────────────────────────────────────────────────────────────────────────


def _compute_impact(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    changed_paths: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Returns nodes directly connected to the changed-file set (direct) and 1-hop neighbor nodes (neighbor).

    Args:
        nodes: Full node list.
        edges: Full edge list.
        changed_paths: List of changed file paths (accepted only as an argument, no direct git calls).

    Returns:
        (direct_nodes, neighbor_nodes) tuple.
    """
    changed_set = set(changed_paths)

    direct_ids: set[str] = set()
    direct_nodes: list[dict[str, Any]] = []
    for n in nodes:
        sf = n.get("source_file", "")
        if sf in changed_set or any(sf.endswith(p) or p.endswith(sf) for p in changed_set):
            direct_ids.add(n["id"])
            direct_nodes.append(n)

    node_map: dict[str, dict[str, Any]] = {n["id"]: n for n in nodes}
    neighbor_ids: set[str] = set()
    for e in edges:
        src, tgt = e.get("source", ""), e.get("target", "")
        if src in direct_ids and tgt not in direct_ids:
            neighbor_ids.add(tgt)
        if tgt in direct_ids and src not in direct_ids:
            neighbor_ids.add(src)

    neighbor_nodes = [node_map[nid] for nid in sorted(neighbor_ids) if nid in node_map]
    return direct_nodes, neighbor_nodes


# ────────────────────────────────────────────────────────────────────────
# Markdown report generation
# ────────────────────────────────────────────────────────────────────────


def _build_markdown(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    ci: dict[str, Any],
    changed_paths: list[str],
    repo_root: Path | None = None,
) -> str:
    """
    Generates the Markdown report string.

    Args:
        nodes: nodes list from index.json.
        edges: edges list from index.json.
        ci: Full ci-summary.json dict.
        changed_paths: File path list received via the --changed argument.

    Returns:
        Markdown report string.
    """
    lines: list[str] = []

    # Header
    lines.append("# Traceability Report (Phase 4)\n")

    # 1. Summary
    node_type_count: Counter[str] = Counter(n["type"] for n in nodes)
    edge_type_count: Counter[str] = Counter(e["type"] for e in edges)
    det_err = ci.get("deterministic_error_count", 0)
    det_warn = ci.get("deterministic_warning_count", 0)
    sem_cand = ci.get("semantic_candidate_count", 0)
    cov_count = ci.get("coverage_count", 0)
    total_f = ci.get("total_findings", 0)
    verify_exit = ci.get("summary", {}).get("exit_code", 0)

    lines.append("## Summary\n")
    lines.append("| Item | Count |")
    lines.append("|---|---|")
    lines.append(f"| Total nodes | {len(nodes)} |")
    lines.append(f"| Total edges | {len(edges)} |")
    lines.append(f"| deterministic errors | {det_err} |")
    lines.append(f"| deterministic warnings | {det_warn} |")
    lines.append(f"| semantic candidates | {sem_cand} |")
    lines.append(f"| coverage findings | {cov_count} |")
    lines.append(f"| total findings | {total_f} |")
    lines.append(f"| verify exit code | {verify_exit} |")
    lines.append("")

    lines.append("### Count by Node Type\n")
    lines.append("| Node type | Count |")
    lines.append("|---|---|")
    for nt, cnt in sorted(node_type_count.items()):
        lines.append(f"| {nt} | {cnt} |")
    lines.append("")

    lines.append("### Count by Edge Type\n")
    lines.append("| Edge type | Count |")
    lines.append("|---|---|")
    for et, cnt in sorted(edge_type_count.items()):
        lines.append(f"| {et} | {cnt} |")
    lines.append("")

    # 2. Failure findings (CI hard gate)
    lines.append("## Failure Findings (CI Hard Gate)\n")
    det_errors = ci.get("categories", {}).get("deterministic", {}).get("errors", [])
    if not det_errors:
        lines.append("> **0 findings, CI green** -- no deterministic errors. CI gate passed.")
    else:
        lines.append("| kind | subject | source location | message |")
        lines.append("|---|---|---|---|")
        for f in det_errors:
            subject = _escape_md(f.get("subject") or "")
            loc = _escape_md(f.get("location") or "")
            msg = _escape_md(f.get("message") or "")
            kind = f.get("kind", "")
            lines.append(f"| {kind} | {subject} | {loc} | {msg} |")
    lines.append("")

    # 3. Semantic drift candidates (agent review)
    lines.append("## Semantic Drift Candidates (Agent Review)\n")
    lines.append(
        "> Not a CI failure -- item for agent/human review. Does not affect the hard gate.\n"
    )
    sem_candidates = ci.get("categories", {}).get("semantic_candidate", [])
    orphans = [f for f in sem_candidates if f.get("kind") == "orphan"]
    api_unlinked = [f for f in sem_candidates if f.get("kind") == "api_unlinked"]
    others = [f for f in sem_candidates if f.get("kind") not in ("orphan", "api_unlinked")]

    if api_unlinked:
        lines.append(f"### API Unlinked ({len(api_unlinked)})\n")
        lines.append("| subject (node id) | source file | message |")
        lines.append("|---|---|---|")
        for f in api_unlinked[:30]:
            subject = _escape_md(f.get("subject") or "")
            loc = _escape_md(f.get("location") or "")
            msg = _escape_md(f.get("message") or "")
            lines.append(f"| {subject} | {loc} | {msg} |")
        if len(api_unlinked) > 30:
            lines.append(f"| _(+{len(api_unlinked) - 30} more omitted)_ | | |")
        lines.append("")

    if orphans:
        lines.append(f"### Orphan Must Requirements ({len(orphans)})\n")
        lines.append("| subject (node id) | source file | message |")
        lines.append("|---|---|---|")
        for f in orphans[:20]:
            subject = _escape_md(f.get("subject") or "")
            loc = _escape_md(f.get("location") or "")
            msg = _escape_md(f.get("message") or "")
            lines.append(f"| {subject} | {loc} | {msg} |")
        if len(orphans) > 20:
            lines.append(f"| _(+{len(orphans) - 20} more omitted)_ | | |")
        lines.append("")

    if others:
        lines.append(f"### Other semantic_candidate ({len(others)})\n")
        for f in others:
            subject = _escape_md(f.get("subject") or "")
            loc = _escape_md(f.get("location") or "")
            msg = _escape_md(f.get("message") or "")
            lines.append(f"- [{f.get('kind')}] **{subject}** @ `{loc}`: {msg}")
        lines.append("")

    # 4. Orphan list
    lines.append("## Orphan List (Must Requirements)\n")
    orphan_list = orphans
    if not orphan_list:
        lines.append("_(none)_")
    else:
        lines.append(f"Must requirements missing some edge category: **{len(orphan_list)}**\n")
        lines.append("| Node id | Source file | Missing edge category |")
        lines.append("|---|---|---|")
        for f in sorted(orphan_list, key=lambda x: x.get("subject") or ""):
            subject = _escape_md(f.get("subject") or "")
            loc = _escape_md(f.get("location") or "")
            msg = f.get("message") or ""
            missing_part = ""
            if "missing edges:" in msg:
                missing_part = _escape_md(msg.split("missing edges:")[-1].strip())
            lines.append(f"| {subject} | {loc} | {missing_part} |")
    lines.append("")

    # 5. Coverage
    lines.append("## Coverage\n")
    cov_findings = ci.get("categories", {}).get("coverage", [])
    coverage_gaps = [f for f in cov_findings if f.get("kind") == "coverage_gap"]
    drifts = [f for f in cov_findings if f.get("kind") == "test_coverage_drift"]

    lines.append(f"- coverage_gap: **{len(coverage_gaps)}** (checklist UC has no validates edge)")
    lines.append(
        f"- test_coverage_drift: **{len(drifts)}** (test marker references a UC not on the checklist)\n"
    )

    if drifts:
        lines.append(f"### Test Coverage Drift (marker drift) -- {len(drifts)}\n")
        lines.append("| subject (UC id) | source (test node id) | message |")
        lines.append("|---|---|---|")
        for f in drifts:
            subject = _escape_md(f.get("subject") or "")
            loc = _escape_md(f.get("location") or "")
            msg = _escape_md(f.get("message") or "")
            lines.append(f"| {subject} | {loc} | {msg} |")
        lines.append("")

    if coverage_gaps:
        lines.append(f"### Coverage Gap ({len(coverage_gaps)})\n")
        lines.append("| Node id (UseCase) | Source file | message |")
        lines.append("|---|---|---|")
        for f in coverage_gaps[:30]:
            subject = _escape_md(f.get("subject") or "")
            loc = _escape_md(f.get("location") or "")
            msg = _escape_md(f.get("message") or "")
            lines.append(f"| {subject} | {loc} | {msg} |")
        if len(coverage_gaps) > 30:
            lines.append(f"| _(+{len(coverage_gaps) - 30} more omitted)_ | | |")
        lines.append("")

    # 6. Changed-file impact
    lines.append("## Changed-File Impact Candidates\n")
    if not changed_paths:
        lines.append(
            "_(No --changed argument. "
            "Filled in when run with `python3 tools/traceability/report.py --changed <path>`.)_"
        )
    else:
        direct_nodes, neighbor_nodes = _compute_impact(nodes, edges, changed_paths)
        cp_str = "`, `".join(changed_paths)
        lines.append(f"Changed files: `{cp_str}`\n")
        lines.append(f"- Directly connected nodes (direct): **{len(direct_nodes)}**")
        lines.append(f"- 1-hop neighbor nodes (neighbor): **{len(neighbor_nodes)}**\n")

        if direct_nodes:
            lines.append("### Directly Affected Nodes (Direct)\n")
            lines.append("| Node id | Type | Source file |")
            lines.append("|---|---|---|")
            for n in sorted(direct_nodes, key=lambda x: x["id"])[:30]:
                nid = _escape_md(n["id"])
                ntype = n.get("type", "")
                sf = _escape_md(n.get("source_file", ""))
                lines.append(f"| {nid} | {ntype} | {sf} |")
            if len(direct_nodes) > 30:
                lines.append(f"| _(+{len(direct_nodes) - 30} more omitted)_ | | |")
            lines.append("")

        if neighbor_nodes:
            lines.append("### 1-Hop Neighbor Nodes (Neighbor)\n")
            lines.append("| Node id | Type | Source file |")
            lines.append("|---|---|---|")
            for n in sorted(neighbor_nodes, key=lambda x: x["id"])[:20]:
                nid = _escape_md(n["id"])
                ntype = n.get("type", "")
                sf = _escape_md(n.get("source_file", ""))
                lines.append(f"| {nid} | {ntype} | {sf} |")
            if len(neighbor_nodes) > 20:
                lines.append(f"| _(+{len(neighbor_nodes) - 20} more omitted)_ | | |")
            lines.append("")

    # 7. Seed Traces (Phase 7)
    lines.append("## Seed Traces\n")
    lines.append(
        "> Phase 7 core trace seed -- each seed's layer node id + source path + connection status\n"
    )
    _seed_traces_section = _build_seed_traces_section(nodes, edges, ci, repo_root)
    lines.extend(_seed_traces_section)
    lines.append("")

    # 8. Sample Trace Path (based on the first seed in seed-traces.yml)
    trace = _build_sample_trace(nodes, edges, repo_root)
    if trace:
        lines.append("## Sample Trace Path\n")
        lines.append(
            "> Core path based on the first seed trace -- includes node id + source file path\n"
        )
        lines.append(
            "> _(unlinked)_ = the node for that layer exists in the index but isn't connected by a direct edge.\n"
        )

        for i, step in enumerate(trace):
            prefix = "|-" if i < len(trace) - 1 else "+-"
            note = step.get("note", "")
            note_str = f" **{note}**" if note else ""
            lines.append(f"{prefix} **{step['id']}** `[{step['type']}]`{note_str}")
            lines.append(f"   - source: `{step['source_file']}`")
            edge_label = step.get("edge_label", "")
            if edge_label:
                lines.append(f"   - edge: _{edge_label}_")
            lines.append("")

        lines.append("### Connection Status Summary by Trace Layer\n")
        lines.append("| Layer | Node id | Type | Source file | Connection status |")
        lines.append("|---|---|---|---|---|")
        for i, step in enumerate(trace):
            layer = step.get("layer", "") or f"L{i + 1}"
            nid = _escape_md(step["id"])
            ntype = _escape_md(step["type"])
            sf = _escape_md(step["source_file"])
            note = step.get("note", "")
            status = note if note else "direct edge connected"
            lines.append(f"| {_escape_md(layer)} | {nid} | {ntype} | {sf} | {status} |")
        lines.append("")

    lines.append("---")
    lines.append("_Phase 4 report. Generated by: `python3 tools/traceability/report.py`_")

    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────────
# HTML report generation (self-contained)
# ────────────────────────────────────────────────────────────────────────


def _finding_rows_html(findings: list[dict[str, Any]], limit: int = 30) -> str:
    """Converts the finding list into HTML table rows."""
    rows = []
    for f in findings[:limit]:
        kind = _escape_html(f.get("kind", ""))
        subject = _escape_html(f.get("subject") or "")
        loc = _escape_html(f.get("location") or "")
        msg = _escape_html(f.get("message") or "")
        rows.append(
            "<tr><td><code>"
            + kind
            + "</code></td>"
            + "<td><code>"
            + subject
            + "</code></td>"
            + "<td><span class='path'>"
            + loc
            + "</span></td>"
            + "<td>"
            + msg
            + "</td></tr>"
        )
    if len(findings) > limit:
        rows.append(
            "<tr><td colspan='4' class='muted'>... "
            + str(len(findings) - limit)
            + " more omitted</td></tr>"
        )
    return "\n".join(rows)


def _build_html(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    ci: dict[str, Any],
    changed_paths: list[str],
    repo_root: Path | None = None,
) -> str:
    """
    Generates a self-contained interactive HTML report.

    Zero external CDN/network dependency. CSS/JS inlined.
    Data is inlined at build time (no runtime fetch).

    Args:
        nodes: nodes list from index.json.
        edges: edges list from index.json.
        ci: Full ci-summary.json dict.
        changed_paths: File path list from the --changed argument.

    Returns:
        HTML string.
    """
    # Statistics
    node_type_count: Counter[str] = Counter(n["type"] for n in nodes)
    edge_type_count: Counter[str] = Counter(e["type"] for e in edges)
    det_err = ci.get("deterministic_error_count", 0)
    sem_cand = ci.get("semantic_candidate_count", 0)
    cov_count = ci.get("coverage_count", 0)
    total_f = ci.get("total_findings", 0)
    verify_exit = ci.get("summary", {}).get("exit_code", 0)

    # Classify findings
    det_errors = ci.get("categories", {}).get("deterministic", {}).get("errors", [])
    sem_candidates = ci.get("categories", {}).get("semantic_candidate", [])
    cov_findings = ci.get("categories", {}).get("coverage", [])
    orphans = [f for f in sem_candidates if f.get("kind") == "orphan"]
    api_unlinked = [f for f in sem_candidates if f.get("kind") == "api_unlinked"]
    coverage_gaps = [f for f in cov_findings if f.get("kind") == "coverage_gap"]
    drifts = [f for f in cov_findings if f.get("kind") == "test_coverage_drift"]

    # Trace data
    trace = _build_sample_trace(nodes, edges, repo_root)

    # changed-file impact
    direct_nodes: list[dict[str, Any]] = []
    neighbor_nodes: list[dict[str, Any]] = []
    if changed_paths:
        direct_nodes, neighbor_nodes = _compute_impact(nodes, edges, changed_paths)

    # Inline data JSON (build time)
    all_node_types = sorted(node_type_count.keys())
    nodes_for_js = [
        {
            "id": n["id"],
            "type": n["type"],
            "source_file": n.get("source_file", ""),
            "title": (n.get("title") or "")[:120],
        }
        for n in nodes
    ]
    nodes_json = json.dumps(nodes_for_js, ensure_ascii=False)
    all_types_json = json.dumps(all_node_types, ensure_ascii=False)
    trace_json = json.dumps(trace, ensure_ascii=False)
    seed_graphs_json = json.dumps(
        _build_seed_graph_payload(nodes, edges, ci, repo_root),
        ensure_ascii=False,
    )
    changed_paths_json = json.dumps(changed_paths, ensure_ascii=False)
    direct_nodes_json = json.dumps(
        [
            {"id": n["id"], "type": n.get("type", ""), "source_file": n.get("source_file", "")}
            for n in direct_nodes[:30]
        ],
        ensure_ascii=False,
    )
    neighbor_nodes_json = json.dumps(
        [
            {"id": n["id"], "type": n.get("type", ""), "source_file": n.get("source_file", "")}
            for n in neighbor_nodes[:20]
        ],
        ensure_ascii=False,
    )

    # CI finding rows
    ci_error_rows = (
        _finding_rows_html(det_errors)
        if det_errors
        else "<tr><td colspan='4' class='green'>0 findings, CI green</td></tr>"
    )
    api_unlinked_rows = _finding_rows_html(api_unlinked, 25)
    orphan_rows = _finding_rows_html(orphans, 25)
    drift_rows = _finding_rows_html(drifts)
    gap_rows = _finding_rows_html(coverage_gaps, 30)

    # Trace HTML rows (server-side render)
    trace_html_rows = []
    for i, step in enumerate(trace):
        layer = step.get("layer", "") or f"L{i + 1}"
        note = step.get("note", "")
        status_class = "unlinked" if note else "linked"
        status_text = _escape_html(note) if note else "direct edge connected"
        step_id = _escape_html(step.get("id", ""))
        step_type = step.get("type", "")
        step_type_esc = _escape_html(step_type)
        step_type_badge = step_type.lower()[:12]
        step_sf = _escape_html(step.get("source_file", ""))
        layer_esc = _escape_html(layer)
        row = (
            "<tr>"
            + "<td class='layer'>"
            + layer_esc
            + "</td>"
            + "<td><code class='node-id'>"
            + step_id
            + "</code></td>"
            + "<td><span class='badge badge-"
            + step_type_badge
            + "'>"
            + step_type_esc
            + "</span></td>"
            + "<td><span class='path'>"
            + step_sf
            + "</span></td>"
            + "<td class='"
            + status_class
            + "'>"
            + status_text
            + "</td>"
            + "</tr>"
        )
        trace_html_rows.append(row)
    trace_html = "\n".join(trace_html_rows)

    # changed-file impact HTML
    impact_changed_str = _escape_html(", ".join(changed_paths)) if changed_paths else "&#8212;"
    impact_html_rows: list[str] = []
    if direct_nodes:
        impact_html_rows.append(
            "<tr><th colspan='3' class='section-header'>Directly Affected Nodes (Direct)</th></tr>"
        )
        for n in direct_nodes[:30]:
            impact_html_rows.append(
                "<tr><td><code>"
                + _escape_html(n["id"])
                + "</code></td>"
                + "<td>"
                + _escape_html(n.get("type", ""))
                + "</td>"
                + "<td><span class='path'>"
                + _escape_html(n.get("source_file", ""))
                + "</span></td></tr>"
            )
    if neighbor_nodes:
        impact_html_rows.append(
            "<tr><th colspan='3' class='section-header'>1-Hop Neighbor Nodes (Neighbor)</th></tr>"
        )
        for n in neighbor_nodes[:20]:
            impact_html_rows.append(
                "<tr><td><code>"
                + _escape_html(n["id"])
                + "</code></td>"
                + "<td>"
                + _escape_html(n.get("type", ""))
                + "</td>"
                + "<td><span class='path'>"
                + _escape_html(n.get("source_file", ""))
                + "</span></td></tr>"
            )
    impact_html = (
        "\n".join(impact_html_rows)
        if impact_html_rows
        else "<tr><td colspan='3' class='muted'>No --changed argument</td></tr>"
    )

    # Bar chart HTML (pure CSS)
    total_nodes = len(nodes)
    bar_rows_html = []
    for nt in sorted(node_type_count.keys()):
        cnt = node_type_count[nt]
        pct = int(cnt / total_nodes * 100) if total_nodes else 0
        bar_rows_html.append(
            "<div class='bar-row'>"
            + "<span class='bar-label'>"
            + _escape_html(nt)
            + "</span>"
            + "<div class='bar-track'>"
            + "<div class='bar-fill' style='width:"
            + str(pct)
            + "%'></div>"
            + "</div>"
            + "<span class='bar-cnt'>"
            + str(cnt)
            + "</span>"
            + "</div>"
        )
    bar_html = "\n".join(bar_rows_html)

    # Edge type table rows
    edge_rows_html = []
    for et, cnt in sorted(edge_type_count.items()):
        edge_rows_html.append(
            "<tr><td><code>" + _escape_html(et) + "</code></td><td>" + str(cnt) + "</td></tr>"
        )
    edge_table_rows = "\n".join(edge_rows_html)

    # CI status color
    det_err_class = "val-red" if det_err > 0 else "val-green"
    exit_class = "val-green" if verify_exit == 0 else "val-red"
    ci_status_text = "0 findings, CI green" if det_err == 0 else (str(det_err) + " errors")

    # HTML template
    parts: list[str] = []
    parts.append("<!doctype html>")
    parts.append('<html lang="ko">')
    parts.append("<head>")
    parts.append('<meta charset="utf-8">')
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    parts.append("<title>Traceability Report -- Phase 4</title>")
    parts.append("<style>")
    parts.append("""
:root {
  --bg: #f5f2ea;
  --surface: #fffdf8;
  --surface2: #f0ece2;
  --ink: #242526;
  --muted: #6d6a61;
  --line: #d8d0c1;
  --teal: #16736b;
  --green: #4d7f35;
  --amber: #a96513;
  --red: #b63f38;
  --blue: #356d9a;
  --ci-bg: #fff5f5;
  --ci-border: #f5c6c6;
  --agent-bg: #f0f9f0;
  --agent-border: #b8ddb8;
  --shadow: 0 2px 8px rgba(0,0,0,0.08);
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
  font-size: 14px;
  line-height: 1.6;
}
a { color: var(--blue); text-decoration: none; }
code {
  background: #ebe5d8;
  border: 1px solid var(--line);
  border-radius: 4px;
  padding: 1px 4px;
  font-size: 0.88em;
  font-family: "SFMono-Regular", Consolas, Menlo, monospace;
  word-break: break-all;
}
.path {
  font-family: "SFMono-Regular", Consolas, Menlo, monospace;
  font-size: 0.82em;
  color: var(--muted);
  word-break: break-all;
}
header {
  background: var(--surface);
  border-bottom: 2px solid var(--line);
  padding: 20px 32px;
}
header h1 { font-size: 22px; font-weight: 700; color: var(--teal); }
header .meta { color: var(--muted); font-size: 12px; margin-top: 4px; }
nav {
  background: var(--surface2);
  border-bottom: 1px solid var(--line);
  padding: 8px 32px;
  display: flex; gap: 16px; flex-wrap: wrap;
}
nav a {
  font-size: 12px; font-weight: 600; color: var(--muted);
  padding: 2px 8px; border-radius: 4px; transition: background 0.15s;
}
nav a:hover { background: var(--line); color: var(--ink); }
.container { max-width: 1280px; margin: 0 auto; padding: 24px 32px; }
section {
  background: var(--surface); border: 1px solid var(--line);
  border-radius: 8px; padding: 20px 24px; margin-bottom: 20px;
  box-shadow: var(--shadow);
}
section.ci-section { border-color: var(--ci-border); background: var(--ci-bg); }
section.agent-section { border-color: var(--agent-border); background: var(--agent-bg); }
section h2 {
  font-size: 16px; font-weight: 700; margin-bottom: 14px;
  display: flex; align-items: center; gap: 8px;
}
.badge-section {
  font-size: 11px; font-weight: 700; padding: 2px 8px;
  border-radius: 10px; text-transform: uppercase;
}
.badge-ci { background: #fde8e8; color: var(--red); }
.badge-agent { background: #daf0da; color: var(--green); }
.badge-coverage { background: #fff3cd; color: var(--amber); }
.badge-info { background: #e8eef7; color: var(--blue); }
section h3 {
  font-size: 13px; font-weight: 700; margin: 16px 0 8px;
  color: var(--muted); text-transform: uppercase; letter-spacing: 0.03em;
}
table { width: 100%; border-collapse: collapse; font-size: 13px; margin-bottom: 12px; }
th {
  background: var(--surface2); text-align: left;
  padding: 6px 10px; font-size: 11px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.04em;
  border-bottom: 1px solid var(--line);
}
td { padding: 5px 10px; border-bottom: 1px solid #ebe6db; vertical-align: top; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: #faf8f3; }
.green { color: var(--green); font-weight: 700; }
.muted { color: var(--muted); font-style: italic; }
.linked { color: var(--green); font-weight: 600; }
.unlinked { color: var(--amber); font-style: italic; }
.node-id { font-size: 0.8em; }
.layer { font-size: 11px; color: var(--muted); white-space: nowrap; }
.section-header { background: var(--surface2); font-weight: 700; font-size: 12px; color: var(--muted); }
.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 12px; margin-bottom: 16px;
}
.stat-card {
  background: var(--surface2); border: 1px solid var(--line);
  border-radius: 6px; padding: 12px 14px; text-align: center;
}
.stat-card .val { font-size: 26px; font-weight: 800; line-height: 1.1; }
.stat-card .lbl { font-size: 11px; color: var(--muted); margin-top: 2px; }
.val-green { color: var(--green); }
.val-red { color: var(--red); }
.val-amber { color: var(--amber); }
.val-blue { color: var(--blue); }
.filter-panel {
  background: var(--surface2); border: 1px solid var(--line);
  border-radius: 6px; padding: 12px 16px; margin-bottom: 16px;
}
.filter-panel label {
  display: inline-flex; align-items: center; gap: 4px;
  margin: 3px 6px 3px 0; font-size: 12px; cursor: pointer; user-select: none;
}
.filter-panel label input[type=checkbox] { cursor: pointer; }
.filter-actions { margin-top: 8px; display: flex; gap: 8px; }
.btn-sm {
  font-size: 11px; padding: 3px 10px;
  border: 1px solid var(--line); border-radius: 4px;
  background: var(--surface); cursor: pointer; font-weight: 600;
}
.btn-sm:hover { background: var(--line); }
.node-row.hidden { display: none; }
.badge {
  display: inline-block; font-size: 10px; font-weight: 700;
  padding: 1px 6px; border-radius: 8px; text-transform: uppercase; letter-spacing: 0.04em;
}
.badge-requirement { background: #e8eef7; color: #356d9a; }
.badge-adr { background: #f3e8ff; color: #6d4c85; }
.badge-apioperati { background: #fff0e6; color: #a96513; }
.badge-codesymbol { background: #e8f4e8; color: #4d7f35; }
.badge-testcase { background: #e8f4ff; color: #356d9a; }
.badge-usecase { background: #fef9e8; color: #a96513; }
.badge-sequencest { background: #f5f0ff; color: #6d4c85; }
.badge-sequencedi { background: #f5f0ff; color: #6d4c85; }
.badge-platformre { background: #ffe8e8; color: #b63f38; }
.bar-row { display: flex; align-items: center; margin-bottom: 5px; gap: 8px; }
.bar-label { width: 200px; font-size: 12px; text-align: right; color: var(--muted); flex-shrink: 0; }
.bar-track { flex: 1; height: 14px; background: var(--surface2); border-radius: 3px; overflow: hidden; }
.bar-fill { height: 100%; background: var(--teal); border-radius: 3px; min-width: 2px; }
.bar-cnt { font-size: 12px; font-weight: 700; width: 40px; text-align: right; flex-shrink: 0; }
.trace-step { padding: 4px 0; border-bottom: 1px dotted var(--line); font-family: "SFMono-Regular", Consolas, Menlo, monospace; font-size: 12px; }
.trace-step:last-child { border-bottom: none; }
.trace-chain {
  background: var(--surface2); border: 1px solid var(--line);
  border-radius: 6px; padding: 12px 16px; margin-bottom: 16px;
}
.seed-graph-toolbar {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; flex-wrap: wrap; margin-bottom: 12px;
}
.seed-select-label {
  display: flex; align-items: center; gap: 8px;
  font-size: 12px; font-weight: 700; color: var(--muted);
}
.seed-select-label select {
  min-width: 320px; max-width: 100%;
  border: 1px solid var(--line); border-radius: 4px;
  background: var(--surface); color: var(--ink);
  padding: 5px 8px; font-size: 13px;
}
.seed-graph-legend {
  display: flex; gap: 10px; flex-wrap: wrap;
  font-size: 12px; color: var(--muted);
}
.legend-item { display: inline-flex; align-items: center; gap: 5px; }
.legend-line { width: 24px; border-top: 3px solid var(--green); }
.legend-manual { border-top-color: var(--blue); }
.legend-gap { border-top-color: var(--red); border-top-style: dashed; }
.seed-graph-summary {
  display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px;
}
.graph-pill {
  border: 1px solid var(--line); border-radius: 12px;
  background: var(--surface2); padding: 2px 8px;
  font-size: 11px; font-weight: 700;
}
.graph-pill.ok { color: var(--green); }
.graph-pill.manual { color: var(--blue); }
.graph-pill.warn { color: var(--red); }
.seed-graph-frame {
  overflow-x: auto; border: 1px solid var(--line); border-radius: 6px;
  background: #fbfaf6; padding: 10px;
}
.seed-graph-svg {
  display: block; width: 100%; min-width: 760px; height: 300px;
}
.graph-node rect {
  fill: var(--surface); stroke: var(--line); stroke-width: 1.2; rx: 6;
}
.graph-node { cursor: pointer; }
.graph-node.selected rect {
  stroke: var(--teal); stroke-width: 2.4;
}
.graph-node.missing rect {
  fill: #fff3f1; stroke: var(--red); stroke-dasharray: 4 3;
}
.graph-node-type { font-size: 10px; fill: var(--muted); font-weight: 700; }
.graph-node-id { font-size: 11px; fill: var(--ink); font-weight: 700; }
.graph-node-source { font-size: 10px; fill: var(--muted); }
.graph-link { fill: none; stroke-width: 2.5; }
.graph-link.direct { stroke: var(--green); }
.graph-link.manual { stroke: var(--blue); }
.graph-link.gap, .graph-link.missing_node {
  stroke: var(--red); stroke-dasharray: 6 4;
}
.graph-link-label { font-size: 10px; fill: var(--muted); font-weight: 700; }
.seed-graph-findings {
  margin-top: 10px; font-size: 12px; color: var(--muted);
}
.seed-graph-findings ul { margin-left: 18px; }
.seed-graph-empty { color: var(--muted); font-style: italic; }
.seed-node-detail {
  margin-top: 10px; border: 1px solid var(--line); border-radius: 6px;
  background: var(--surface2); padding: 12px 14px;
}
.seed-node-detail h3 {
  margin: 0 0 8px; color: var(--ink); text-transform: none;
  letter-spacing: 0; font-size: 14px;
}
.seed-node-detail-grid {
  display: grid; grid-template-columns: 90px 1fr;
  gap: 4px 10px; font-size: 12px; margin-bottom: 10px;
}
.seed-node-detail-grid .key {
  color: var(--muted); font-weight: 700; text-transform: uppercase; font-size: 10px;
}
.seed-node-excerpt {
  border-left: 3px solid var(--teal); padding: 8px 10px;
  background: var(--surface); color: var(--ink); font-size: 13px;
}
@media (max-width: 700px) {
  .container { padding: 12px; }
  .summary-grid { grid-template-columns: 1fr 1fr; }
  nav { padding: 8px 12px; }
  .seed-select-label { width: 100%; align-items: flex-start; flex-direction: column; }
  .seed-select-label select { min-width: 0; width: 100%; }
  .seed-node-detail-grid { grid-template-columns: 1fr; }
}
""")
    parts.append("</style>")
    parts.append("</head>")
    parts.append("<body>")
    parts.append("<header>")
    parts.append("<h1>Traceability Report -- Phase 4</h1>")
    parts.append(
        "<div class='meta'>nodes "
        + str(len(nodes))
        + " &middot; edges "
        + str(len(edges))
        + " &middot; findings "
        + str(total_f)
        + " &middot; verify exit "
        + str(verify_exit)
        + " &middot; generated: <code>python3 tools/traceability/report.py</code></div>"
    )
    parts.append("</header>")
    parts.append("<nav>")
    for anchor, label in [
        ("summary", "Summary"),
        ("ci-findings", "CI Hard Gate"),
        ("agent-findings", "Agent Review"),
        ("coverage", "Coverage"),
        ("impact", "Changed-file Impact"),
        ("seed-graph", "Seed Graph"),
        ("seed-traces", "Seed Traces"),
        ("trace", "Sample Trace"),
        ("nodes", "Node Explorer"),
    ]:
        parts.append('<a href="#' + anchor + '">' + label + "</a>")
    parts.append("</nav>")
    parts.append('<div class="container">')

    # Summary section
    parts.append('<section id="summary">')
    parts.append("<h2>Summary <span class='badge-section badge-info'>Overview</span></h2>")
    parts.append('<div class="summary-grid">')
    stat_cards = [
        (str(len(nodes)), "Total nodes", "val-blue"),
        (str(len(edges)), "Total edges", "val-blue"),
        (str(det_err), "CI errors", det_err_class),
        (str(sem_cand), "Semantic candidates", "val-amber"),
        (str(cov_count), "Coverage findings", "val-amber"),
        (str(verify_exit), "verify exit", exit_class),
    ]
    for val, lbl, cls in stat_cards:
        parts.append(
            "<div class='stat-card'>"
            + "<div class='val "
            + cls
            + "'>"
            + val
            + "</div>"
            + "<div class='lbl'>"
            + lbl
            + "</div>"
            + "</div>"
        )
    parts.append("</div>")
    parts.append("<h3>Distribution by Node Type</h3>")
    parts.append(bar_html)
    parts.append("<h3 style='margin-top:16px'>Count by Edge Type</h3>")
    parts.append("<table><thead><tr><th>Edge type</th><th>Count</th></tr></thead><tbody>")
    parts.append(edge_table_rows)
    parts.append("</tbody></table>")
    parts.append("</section>")

    # CI Hard Gate section
    parts.append('<section id="ci-findings" class="ci-section">')
    parts.append(
        "<h2>Failure Findings <span class='badge-section badge-ci'>CI Hard Gate</span></h2>"
    )
    parts.append(
        "<p style='font-size:12px;color:var(--muted);margin-bottom:12px;'>"
        + "<strong>deterministic</strong> category -- severity=error -&gt; CI failure (exit 1). Currently <strong>"
        + ci_status_text
        + "</strong>.</p>"
    )
    parts.append(
        "<table><thead><tr><th>kind</th><th>subject (node id)</th>"
        + "<th>source location</th><th>message</th></tr></thead><tbody>"
    )
    parts.append(ci_error_rows)
    parts.append("</tbody></table>")
    parts.append("</section>")

    # Agent Review section
    parts.append('<section id="agent-findings" class="agent-section">')
    parts.append(
        "<h2>Semantic Drift Candidates <span class='badge-section badge-agent'>Agent Review</span></h2>"
    )
    parts.append(
        "<p style='font-size:12px;color:var(--muted);margin-bottom:12px;'>"
        + "<strong>semantic_candidate</strong> category -- not a CI failure. Item for agent/human review. "
        + "api_unlinked: <strong>"
        + str(len(api_unlinked))
        + "</strong> &middot; "
        + "orphan: <strong>"
        + str(len(orphans))
        + "</strong></p>"
    )
    parts.append("<h3>API Unlinked (" + str(len(api_unlinked)) + ")</h3>")
    parts.append(
        "<table><thead><tr><th>kind</th><th>subject (node id)</th>"
        + "<th>source file</th><th>message</th></tr></thead><tbody>"
    )
    parts.append(api_unlinked_rows)
    parts.append("</tbody></table>")
    parts.append("<h3>Orphan Must Requirements (" + str(len(orphans)) + ")</h3>")
    parts.append(
        "<table><thead><tr><th>kind</th><th>subject (node id)</th>"
        + "<th>source file</th><th>missing edge</th></tr></thead><tbody>"
    )
    parts.append(orphan_rows)
    parts.append("</tbody></table>")
    parts.append("</section>")

    # Coverage section
    parts.append('<section id="coverage" class="agent-section">')
    parts.append("<h2>Coverage <span class='badge-section badge-coverage'>Coverage</span></h2>")
    parts.append(
        "<p style='font-size:12px;color:var(--muted);margin-bottom:12px;'>"
        + "coverage_gap: <strong>"
        + str(len(coverage_gaps))
        + "</strong> &middot; "
        + "test_coverage_drift (marker drift): <strong>"
        + str(len(drifts))
        + "</strong></p>"
    )
    parts.append("<h3>Test Coverage Drift -- " + str(len(drifts)) + "</h3>")
    parts.append(
        "<table><thead><tr><th>kind</th><th>subject (UC id)</th>"
        + "<th>source (test)</th><th>message</th></tr></thead><tbody>"
    )
    parts.append(drift_rows)
    parts.append("</tbody></table>")
    parts.append("<h3>Coverage Gap -- " + str(len(coverage_gaps)) + "</h3>")
    parts.append(
        "<table><thead><tr><th>kind</th><th>subject (UseCase id)</th>"
        + "<th>source file</th><th>message</th></tr></thead><tbody>"
    )
    parts.append(gap_rows)
    parts.append("</tbody></table>")
    parts.append("</section>")

    # Changed-file Impact section
    parts.append('<section id="impact">')
    parts.append(
        "<h2>Changed-File Impact Candidates <span class='badge-section badge-info'>--changed</span></h2>"
    )
    parts.append(
        "<p style='font-size:12px;color:var(--muted);margin-bottom:12px;'>"
        + "Changed files: <code id='changed-list'>"
        + impact_changed_str
        + "</code></p>"
    )
    parts.append(
        "<table><thead><tr><th>Node id</th><th>Type</th><th>Source file</th></tr></thead><tbody>"
    )
    parts.append(impact_html)
    parts.append("</tbody></table>")
    parts.append("</section>")

    # Seed Trace Graph section
    parts.append('<section id="seed-graph">')
    parts.append("<h2>Seed Trace Graph <span class='badge-section badge-info'>Visual</span></h2>")
    parts.append(
        "<p style='font-size:12px;color:var(--muted);margin-bottom:12px;'>"
        + "Displays seed-traces.yml's adjacent layer connections as an SVG graph. "
        + "The red dashed line marks missing link candidates and shares the same basis as the gap status in the table below.</p>"
    )
    parts.append('<div class="seed-graph-toolbar">')
    parts.append(
        '<label class="seed-select-label" for="seed-graph-select">'
        + "Seed"
        + '<select id="seed-graph-select"></select>'
        + "</label>"
    )
    parts.append(
        '<div class="seed-graph-legend">'
        + '<span class="legend-item"><span class="legend-line"></span>direct edge</span>'
        + '<span class="legend-item"><span class="legend-line legend-manual"></span>manual edge</span>'
        + '<span class="legend-item"><span class="legend-line legend-gap"></span>missing link</span>'
        + "</div>"
    )
    parts.append("</div>")
    parts.append('<div id="seed-graph-summary" class="seed-graph-summary"></div>')
    parts.append('<div class="seed-graph-frame">')
    parts.append(
        '<svg id="seed-graph-svg" class="seed-graph-svg" role="img" '
        + 'aria-label="Seed trace graph"></svg>'
    )
    parts.append("</div>")
    parts.append('<div id="seed-node-detail" class="seed-node-detail"></div>')
    parts.append('<div id="seed-graph-findings" class="seed-graph-findings"></div>')
    parts.append("</section>")

    # Seed Traces section (Phase 7)
    parts.append('<section id="seed-traces">')
    parts.append("<h2>Seed Traces <span class='badge-section badge-info'>Phase 7</span></h2>")
    parts.append(
        "<p style='font-size:12px;color:var(--muted);margin-bottom:12px;'>"
        + "7 core trace seeds. Each seed connects at least 5 of the layers "
        + "requirement → ADR → usecase/sequence → API → code → test. "
        + "<em>(gap)</em> = adjacent layers not connected by an edge (warn, not a CI failure).</p>"
    )
    # seed trace table (server-side render)
    _seed_rows_html = _build_seed_traces_html(nodes, edges, ci, repo_root)
    parts.append(_seed_rows_html)
    parts.append("</section>")

    # Sample Trace section (based on the first seed in seed-traces.yml; omitted if no seed)
    if trace:
        _trace_anchor = _escape_html(trace[0].get("id", ""))
        parts.append('<section id="trace">')
        parts.append(
            "<h2>Sample Trace Path "
            f"<span class='badge-section badge-info'>{_trace_anchor} start</span></h2>"
        )
        parts.append(
            "<p style='font-size:12px;color:var(--muted);margin-bottom:12px;'>"
            + "Core path based on the first seed trace. "
            + "<em>(unlinked)</em> = the node for that layer exists in the index but isn't connected by a direct edge.</p>"
        )
        parts.append('<div class="trace-chain" id="trace-chain">')
        parts.append('<div id="trace-steps-rendered"></div>')
        parts.append("</div>")
        parts.append(
            "<table><thead><tr><th>Layer</th><th>Node id</th><th>Type</th>"
            + "<th>Source file</th><th>Connection status</th></tr></thead><tbody>"
        )
        parts.append(trace_html)
        parts.append("</tbody></table>")
        parts.append("</section>")

    # Node Explorer section (filter)
    parts.append('<section id="nodes">')
    parts.append("<h2>Node Explorer <span class='badge-section badge-info'>Filter</span></h2>")
    parts.append(
        "<p style='font-size:12px;color:var(--muted);margin-bottom:8px;'>"
        + "Toggle node types to show/hide. Total <span id='visible-count'>"
        + str(len(nodes))
        + "</span> / "
        + str(len(nodes))
        + " nodes shown.</p>"
    )
    parts.append('<div class="filter-panel" id="filter-panel">')
    parts.append('<div id="filter-checkboxes"></div>')
    parts.append('<div class="filter-actions">')
    parts.append('<button class="btn-sm" onclick="setAll(true)">Select All</button>')
    parts.append('<button class="btn-sm" onclick="setAll(false)">Deselect All</button>')
    parts.append("</div></div>")
    parts.append(
        '<div id="node-search-wrap" style="margin-bottom:10px;">'
        + '<input id="node-search" type="text" placeholder="Search node id or file path..."'
        + ' style="width:100%;padding:6px 10px;border:1px solid var(--line);border-radius:4px;'
        + 'font-size:13px;background:var(--surface);" oninput="applyFilter()"></div>'
    )
    parts.append('<div style="overflow-x:auto;">')
    parts.append('<table id="node-table">')
    parts.append(
        "<thead><tr>"
        + "<th style='width:280px'>Node id</th>"
        + "<th style='width:130px'>Type</th>"
        + "<th>Source file</th></tr></thead>"
    )
    parts.append('<tbody id="node-tbody"></tbody>')
    parts.append("</table></div>")
    parts.append(
        '<p id="node-table-hint" style="font-size:11px;color:var(--muted);margin-top:6px;">'
        + "Showing up to 500 rows.</p>"
    )
    parts.append("</section>")
    parts.append("</div>")  # /container

    # Inline JS
    parts.append("<script>")
    parts.append("/* Build-time inlined data -- no runtime fetch */")
    parts.append("var NODES = " + nodes_json + ";")
    parts.append("var ALL_TYPES = " + all_types_json + ";")
    parts.append("var TRACE_DATA = " + trace_json + ";")
    parts.append("var SEED_GRAPHS = " + seed_graphs_json + ";")
    parts.append("var CHANGED_PATHS = " + changed_paths_json + ";")
    parts.append("var DIRECT_NODES = " + direct_nodes_json + ";")
    parts.append("var NEIGHBOR_NODES = " + neighbor_nodes_json + ";")
    parts.append("""
/* Node type filter UI */
var activeTypes = new Set(ALL_TYPES);
var searchQuery = "";

function buildCheckboxes() {
  var container = document.getElementById("filter-checkboxes");
  ALL_TYPES.forEach(function(t) {
    var label = document.createElement("label");
    var cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = true;
    cb.dataset.type = t;
    cb.addEventListener("change", function() {
      if (cb.checked) { activeTypes.add(t); } else { activeTypes.delete(t); }
      applyFilter();
    });
    label.appendChild(cb);
    label.appendChild(document.createTextNode(" " + t));
    container.appendChild(label);
  });
}

function setAll(state) {
  document.querySelectorAll("#filter-checkboxes input[type=checkbox]").forEach(function(cb) {
    cb.checked = state;
    if (state) { activeTypes.add(cb.dataset.type); } else { activeTypes.delete(cb.dataset.type); }
  });
  applyFilter();
}

function applyFilter() {
  searchQuery = (document.getElementById("node-search").value || "").toLowerCase();
  var rows = document.querySelectorAll("#node-tbody tr");
  var visible = 0;
  rows.forEach(function(row) {
    var t = row.dataset.type;
    var text = row.dataset.text || "";
    var show = activeTypes.has(t) && (!searchQuery || text.indexOf(searchQuery) !== -1);
    row.classList.toggle("hidden", !show);
    if (show) { visible++; }
  });
  document.getElementById("visible-count").textContent = visible;
}

/* Node table rendering */
function renderNodeTable() {
  var tbody = document.getElementById("node-tbody");
  var MAX = 500;
  var shown = NODES.slice(0, MAX);
  var frag = document.createDocumentFragment();
  shown.forEach(function(n) {
    var tr = document.createElement("tr");
    tr.className = "node-row";
    tr.dataset.type = n.type;
    tr.dataset.text = (n.id + " " + n.source_file + " " + (n.title || "")).toLowerCase();

    var tdId = document.createElement("td");
    var codeEl = document.createElement("code");
    codeEl.className = "node-id";
    codeEl.textContent = n.id;
    tdId.appendChild(codeEl);

    var tdType = document.createElement("td");
    var badge = document.createElement("span");
    badge.className = "badge badge-" + n.type.toLowerCase().replace(/[^a-z]/g, "").substring(0,10);
    badge.textContent = n.type;
    tdType.appendChild(badge);

    var tdSf = document.createElement("td");
    var span = document.createElement("span");
    span.className = "path";
    span.textContent = n.source_file;
    tdSf.appendChild(span);

    tr.appendChild(tdId);
    tr.appendChild(tdType);
    tr.appendChild(tdSf);
    frag.appendChild(tr);
  });
  tbody.appendChild(frag);
}

/* Trace chain rendering */
function renderTraceChain() {
  var container = document.getElementById("trace-steps-rendered");
  var layerLabels = [
    "L1 Requirement", "L2 ADR", "L3 ApiOperation",
    "L4a ConsentsService", "L4b AuthService",
    "L5a TestCase", "L5b TestCase"
  ];
  TRACE_DATA.forEach(function(step, i) {
    var div = document.createElement("div");
    div.className = "trace-step";
    var layer = layerLabels[i] || ("L" + (i+1));
    var note = step.note || "";
    var isLast = (i === TRACE_DATA.length - 1);
    var prefix = isLast ? "+-" : "|-";
    var noteHtml = note ? ' <em style="color:var(--amber)">' + escHtml(note) + "</em>" : "";
    div.innerHTML = (
      prefix + " <strong>" + escHtml(step.id) + "</strong>"
      + " [" + escHtml(step.type) + "]"
      + noteHtml
      + "<br>&nbsp;&nbsp;&nbsp;source: <span class='path'>" + escHtml(step.source_file) + "</span>"
    );
    container.appendChild(div);
  });
}

/* Seed trace graph rendering */
function clearElement(el) {
  while (el && el.firstChild) {
    el.removeChild(el.firstChild);
  }
}

function createSvgEl(name) {
  return document.createElementNS("http://www.w3.org/2000/svg", name);
}

function shortText(value, maxLen) {
  var text = String(value || "");
  if (text.length <= maxLen) { return text; }
  return text.substring(0, Math.max(0, maxLen - 3)) + "...";
}

function linkStatusLabel(status) {
  if (status === "direct") { return "direct edge"; }
  if (status === "manual") { return "manual edge"; }
  if (status === "indirect") { return "indirect edge (<=2-hop)"; }
  if (status === "gap") { return "missing edge"; }
  if (status === "missing_node") { return "missing node"; }
  return status || "unknown";
}

function addSummaryPill(container, label, value, className) {
  if (!container) { return; }
  var pill = document.createElement("span");
  pill.className = "graph-pill " + (className || "");
  pill.textContent = label + ": " + value;
  container.appendChild(pill);
}

function appendSvgText(parent, text, x, y, className) {
  var el = createSvgEl("text");
  el.setAttribute("x", String(x));
  el.setAttribute("y", String(y));
  el.setAttribute("class", className);
  el.textContent = text;
  parent.appendChild(el);
  return el;
}

function findLayerByNode(seed, nodeId) {
  var layers = seed.layers || [];
  for (var i = 0; i < layers.length; i++) {
    if (layers[i].node === nodeId) { return layers[i]; }
  }
  return layers[0] || null;
}

function renderSeedNodeDetail(seed, nodeId) {
  var detailEl = document.getElementById("seed-node-detail");
  if (!detailEl) { return; }
  clearElement(detailEl);

  var layer = findLayerByNode(seed, nodeId);
  if (!layer) {
    detailEl.className = "seed-node-detail seed-graph-empty";
    detailEl.textContent = "No node details to display.";
    return;
  }
  detailEl.className = "seed-node-detail";

  document.querySelectorAll("#seed-graph-svg .graph-node").forEach(function(nodeEl) {
    nodeEl.classList.toggle("selected", nodeEl.dataset.nodeId === layer.node);
  });

  var h3 = document.createElement("h3");
  h3.textContent = layer.title || layer.node;
  detailEl.appendChild(h3);

  var grid = document.createElement("div");
  grid.className = "seed-node-detail-grid";
  [
    ["layer", layer.layer],
    ["type", layer.type],
    ["node", layer.node],
    ["source", layer.source_file]
  ].forEach(function(row) {
    var key = document.createElement("div");
    key.className = "key";
    key.textContent = row[0];
    var value = document.createElement("div");
    value.textContent = row[1] || "";
    grid.appendChild(key);
    grid.appendChild(value);
  });
  detailEl.appendChild(grid);

  var excerpt = document.createElement("div");
  excerpt.className = "seed-node-excerpt";
  excerpt.textContent = layer.excerpt || layer.title || layer.node;
  detailEl.appendChild(excerpt);
}

function buildSeedGraphSelector() {
  var select = document.getElementById("seed-graph-select");
  if (!select) { return; }
  clearElement(select);

  if (!SEED_GRAPHS.length) {
    var emptyOption = document.createElement("option");
    emptyOption.textContent = "No seed trace";
    select.appendChild(emptyOption);
    return;
  }

  SEED_GRAPHS.forEach(function(seed, index) {
    var option = document.createElement("option");
    option.value = String(index);
    option.textContent = seed.id + " - " + seed.title;
    select.appendChild(option);
  });
  select.addEventListener("change", renderSeedGraph);
}

function renderSeedGraph() {
  var select = document.getElementById("seed-graph-select");
  var svg = document.getElementById("seed-graph-svg");
  var summaryEl = document.getElementById("seed-graph-summary");
  var findingsEl = document.getElementById("seed-graph-findings");
  var detailEl = document.getElementById("seed-node-detail");
  if (!select || !svg) { return; }

  clearElement(svg);
  clearElement(summaryEl);
  clearElement(findingsEl);
  clearElement(detailEl);

  if (!SEED_GRAPHS.length) {
    svg.setAttribute("viewBox", "0 0 900 120");
    var empty = createSvgEl("text");
    empty.setAttribute("x", "24");
    empty.setAttribute("y", "60");
    empty.setAttribute("class", "graph-node-source");
    empty.textContent = "seed-traces.yml missing or failed to load";
    svg.appendChild(empty);
    if (findingsEl) {
      findingsEl.className = "seed-graph-findings seed-graph-empty";
      findingsEl.textContent = "No seed graph to display.";
    }
    if (detailEl) {
      detailEl.className = "seed-node-detail seed-graph-empty";
      detailEl.textContent = "No node details to display.";
    }
    return;
  }

  var selectedIndex = parseInt(select.value || "0", 10);
  if (isNaN(selectedIndex)) { selectedIndex = 0; }
  var seed = SEED_GRAPHS[selectedIndex] || SEED_GRAPHS[0];
  var layers = seed.layers || [];
  var links = seed.links || [];
  var missingLinks = links.filter(function(link) {
    return link.status === "gap" || link.status === "missing_node";
  });
  var summary = seed.summary || {};

  addSummaryPill(summaryEl, "nodes", summary.nodes || layers.length, "");
  addSummaryPill(summaryEl, "links", summary.links || links.length, "");
  addSummaryPill(summaryEl, "direct", summary.direct || 0, "ok");
  addSummaryPill(summaryEl, "manual", summary.manual || 0, "manual");
  addSummaryPill(summaryEl, "indirect", summary.indirect || 0, "manual");
  addSummaryPill(summaryEl, "missing", missingLinks.length, missingLinks.length ? "warn" : "ok");

  var nodeW = 176;
  var nodeH = 98;
  var left = 28;
  var top = 96;
  var graphWidth = Math.max(980, layers.length * 210 + 80);
  var graphHeight = 340;
  var usableWidth = graphWidth - (left * 2) - nodeW;
  var stepX = layers.length > 1 ? usableWidth / (layers.length - 1) : 0;
  var positions = {};

  svg.setAttribute("viewBox", "0 0 " + graphWidth + " " + graphHeight);
  svg.setAttribute("aria-label", seed.id + " trace graph");

  layers.forEach(function(layer, index) {
    positions[layer.node] = {
      x: left + (stepX * index),
      y: top + (index % 2 === 1 ? 34 : 0)
    };
  });

  var linkLayer = createSvgEl("g");
  svg.appendChild(linkLayer);
  links.forEach(function(link) {
    var from = positions[link.from];
    var to = positions[link.to];
    if (!from || !to) { return; }

    var startX = from.x + nodeW;
    var startY = from.y + (nodeH / 2);
    var endX = to.x;
    var endY = to.y + (nodeH / 2);
    var midX = (startX + endX) / 2;

    var path = createSvgEl("path");
    path.setAttribute("class", "graph-link " + link.status);
    path.setAttribute(
      "d",
      "M " + startX + " " + startY
      + " C " + midX + " " + startY
      + ", " + midX + " " + endY
      + ", " + endX + " " + endY
    );
    var title = createSvgEl("title");
    title.textContent = link.from + " -> " + link.to + " (" + linkStatusLabel(link.status) + ")";
    path.appendChild(title);
    linkLayer.appendChild(path);

    var label = createSvgEl("text");
    label.setAttribute("x", String(midX));
    label.setAttribute("y", String(Math.min(startY, endY) - 8));
    label.setAttribute("text-anchor", "middle");
    label.setAttribute("class", "graph-link-label");
    label.textContent = linkStatusLabel(link.status);
    linkLayer.appendChild(label);
  });

  var nodeLayer = createSvgEl("g");
  svg.appendChild(nodeLayer);
  layers.forEach(function(layer) {
    var pos = positions[layer.node];
    if (!pos) { return; }

    var g = createSvgEl("g");
    g.setAttribute("class", "graph-node" + (layer.exists ? "" : " missing"));
    g.setAttribute("transform", "translate(" + pos.x + " " + pos.y + ")");
    g.setAttribute("tabindex", "0");
    g.dataset.nodeId = layer.node;

    var rect = createSvgEl("rect");
    rect.setAttribute("width", String(nodeW));
    rect.setAttribute("height", String(nodeH));
    g.appendChild(rect);

    var nodeTitle = createSvgEl("title");
    nodeTitle.textContent = layer.layer + "\\n" + layer.node + "\\n" + layer.source_file;
    g.appendChild(nodeTitle);

    appendSvgText(g, shortText(layer.layer, 24), 10, 17, "graph-node-type");
    appendSvgText(g, shortText(layer.node, 31), 10, 36, "graph-node-id");
    appendSvgText(g, shortText(layer.title || layer.type || "?", 34), 10, 56, "graph-node-type");
    appendSvgText(g, shortText(layer.source_file, 35), 10, 78, "graph-node-source");

    g.addEventListener("click", function() {
      renderSeedNodeDetail(seed, layer.node);
    });
    g.addEventListener("keydown", function(event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        renderSeedNodeDetail(seed, layer.node);
      }
    });

    nodeLayer.appendChild(g);
  });

  if (layers.length) {
    renderSeedNodeDetail(seed, layers[0].node);
  }

  if (!findingsEl) { return; }

  var missingTitle = document.createElement("div");
  var missingStrong = document.createElement("strong");
  missingStrong.textContent = "Missing links";
  missingTitle.appendChild(missingStrong);
  missingTitle.appendChild(document.createTextNode(": " + missingLinks.length));
  findingsEl.appendChild(missingTitle);

  if (missingLinks.length) {
    var missingList = document.createElement("ul");
    missingLinks.forEach(function(link) {
      var item = document.createElement("li");
      item.textContent = link.from + " -> " + link.to + " (" + linkStatusLabel(link.status) + ")";
      missingList.appendChild(item);
    });
    findingsEl.appendChild(missingList);
  }

  if (seed.findings && seed.findings.length) {
    var findingsTitle = document.createElement("div");
    var findingsStrong = document.createElement("strong");
    findingsStrong.textContent = "Verify findings";
    findingsTitle.appendChild(findingsStrong);
    findingsTitle.appendChild(document.createTextNode(": " + seed.findings.length));
    findingsEl.appendChild(findingsTitle);

    var verifyList = document.createElement("ul");
    seed.findings.forEach(function(finding) {
      var item = document.createElement("li");
      item.textContent = "[" + finding.kind + "] " + finding.message;
      verifyList.appendChild(item);
    });
    findingsEl.appendChild(verifyList);
  }
}

function escHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/* Initialize */
buildCheckboxes();
renderNodeTable();
renderTraceChain();
buildSeedGraphSelector();
renderSeedGraph();
applyFilter();
""")
    parts.append("</script>")
    parts.append("</body>")
    parts.append("</html>")

    return "\n".join(parts)


# ────────────────────────────────────────────────────────────────────────
# CLI entry point
# ────────────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str]) -> list[str]:
    """
    Parses the --changed <path> [<path> ...] argument.

    Returns:
        List of changed file paths (empty list if none).
    """
    changed: list[str] = []
    if "--changed" in argv:
        idx = argv.index("--changed")
        for a in argv[idx + 1 :]:
            if a.startswith("--"):
                break
            changed.append(a)
    return changed


def main() -> None:
    """Main report-generation logic."""
    repo_root = _find_repo_root()
    scratch_dir = repo_root / "scratch" / "traceability"
    index_path = scratch_dir / "index.json"
    ci_summary_path = scratch_dir / "ci-summary.json"

    if not index_path.exists():
        print(
            "[report] ERROR: index.json not found. Run build_index.py first.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Parse CLI arguments
    changed_paths = _parse_args(sys.argv[1:])

    # Load index.json
    with index_path.open(encoding="utf-8") as f:
        data = json.load(f)

    nodes = data.get("nodes", [])
    edges = data.get("edges", [])

    # Load ci-summary.json (defaults if missing)
    if ci_summary_path.exists():
        with ci_summary_path.open(encoding="utf-8") as f:
            ci = json.load(f)
    else:
        ci = {
            "deterministic_error_count": 0,
            "deterministic_warning_count": 0,
            "semantic_candidate_count": 0,
            "coverage_count": 0,
            "total_findings": 0,
            "summary": {"exit_code": 0},
            "categories": {
                "deterministic": {"errors": [], "warnings": []},
                "semantic_candidate": [],
                "coverage": [],
            },
        }

    # Create output directory
    scratch_dir.mkdir(parents=True, exist_ok=True)

    # Generate Markdown report
    md_text = _build_markdown(nodes, edges, ci, changed_paths, repo_root)
    md_path = scratch_dir / "report.md"
    md_path.write_text(md_text, encoding="utf-8")

    # Generate HTML report
    html_text = _build_html(nodes, edges, ci, changed_paths, repo_root)
    html_path = scratch_dir / "report.html"
    html_path.write_text(html_text, encoding="utf-8")

    # stdout summary
    print(
        "[report] nodes="
        + str(len(nodes))
        + ", edges="
        + str(len(edges))
        + ", findings="
        + str(ci.get("total_findings", 0))
    )
    if changed_paths:
        print("[report] --changed: " + str(changed_paths))
    print("[report] -> " + str(md_path))
    print("[report] -> " + str(html_path))


if __name__ == "__main__":
    main()
