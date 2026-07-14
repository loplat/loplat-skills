"""
Entry point for building the traceability index.

Run from the repo root:
    python3 tools/traceability/build_index.py

Or run from any directory — the repo root is auto-discovered:
    python3 /path/to/tools/traceability/build_index.py

Output: scratch/traceability/index.json
"""

from __future__ import annotations

import sys
from pathlib import Path

# -----------------------------------------------------------------------------
# sys.path bootstrap — makes the tools package importable when this script
# is run directly. When run via `pytest -m`, conftest.py already handles
# this, so the code below only has an effect for direct script execution.
# -----------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT_CANDIDATE = _SCRIPT_DIR.parent.parent  # tools/traceability → tools → repo root

if str(_REPO_ROOT_CANDIDATE) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT_CANDIDATE))

# -----------------------------------------------------------------------------

from tools.traceability.config import TraceConfigError, get_config  # noqa: E402
from tools.traceability.extractors import run_all  # noqa: E402
from tools.traceability.model import TraceEdge, TraceIndex  # noqa: E402


def _find_repo_root() -> Path:
    """
    Locate the repo root based on this script's location.

    Assumes the script lives under tools/traceability/, so the repo root
    is two levels up. Verifies the candidate by checking for a .git
    directory.
    """
    candidate = _SCRIPT_DIR.parent.parent  # tools/traceability → tools → repo root
    if (candidate / ".git").exists():
        return candidate
    # Fall back to the candidate even without .git (e.g. CI environments)
    return candidate


def main() -> None:
    """Main logic for building the index."""
    repo_root = _find_repo_root()
    index = TraceIndex()

    # Load trace-config (falls back to defaults if absent). Fail-closed
    # (exit 2) if the file is corrupt or pyyaml is missing.
    try:
        cfg = get_config(repo_root)
    except TraceConfigError as exc:
        print(f"[build_index] ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    disabled = cfg.disabled_extractor_names()
    if disabled:
        print(f"[build_index] disabled extractors: {', '.join(disabled)}")

    # Run all extractors — returns dict[str, tuple[nodes_added, edges_added]]
    stats = run_all(repo_root, index)

    # Load manual-edges.yml — adds origin='manual' edges to the index
    manual_edges_path = repo_root / cfg.path("ontology_dir") / "manual-edges.yml"
    manual_count = 0
    if manual_edges_path.exists():
        try:
            import yaml  # type: ignore[import]

            with manual_edges_path.open(encoding="utf-8") as _f:
                _me_doc = yaml.safe_load(_f) or {}
            for _me in _me_doc.get("edges") or []:
                if not isinstance(_me, dict):
                    continue
                etype = _me.get("type", "")
                src_id = _me.get("source", "")
                tgt_id = _me.get("target", "")
                if etype and src_id and tgt_id:
                    index.add_edge(
                        TraceEdge(
                            type=etype,
                            source=src_id,
                            target=tgt_id,
                            origin="manual",
                            evidence=str(manual_edges_path),
                        )
                    )
                    manual_count += 1
        except ImportError:
            # manual-edges.yml exists but pyyaml is missing — fail-closed (exit 2)
            print(
                "[build_index] ERROR: manual-edges.yml exists but pyyaml is not installed — "
                "fail-closed (exit 2). Run 'pip install pyyaml==6.0.2' and retry.",
                file=sys.stderr,
            )
            sys.exit(2)
        except Exception as exc:
            print(f"[build_index] warn: failed to load manual-edges.yml: {exc}")

    # Create the output directory
    out_dir = repo_root / "scratch" / "traceability"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Save index.json
    out_path = out_dir / "index.json"
    out_path.write_text(index.to_json(), encoding="utf-8")

    # Print a summary to stdout
    total_nodes = len(index.nodes())
    total_edges = len(index.edges())
    print(f"[build_index] repo_root={repo_root}")
    print(f"[build_index] nodes={total_nodes}, edges={total_edges}")
    for extractor_name, (n_added, e_added) in stats.items():
        print(f"  {extractor_name}: +{n_added} nodes, +{e_added} edges")
    if manual_count:
        print(f"  manual_edges: +0 nodes, +{manual_count} edges")
    print(f"[build_index] → {out_path}")


if __name__ == "__main__":
    main()
