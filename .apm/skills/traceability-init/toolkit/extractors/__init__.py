"""
Extractor registry.

Each extractor implements an extract function with the following signature:
    def extract(repo_root: Path, index: TraceIndex) -> None

When adding a new extractor in Phase 2, add a new module file to this
package directory and attach the @register("name") decorator to the
function. This file (extractors/__init__.py) does not need to be modified —
pkgutil.iter_modules auto-discovers and imports submodules, which triggers
self-registration.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from pathlib import Path

from tools.traceability.config import get_config
from tools.traceability.model import TraceIndex

# Extractor type definition
ExtractorFn = Callable[[Path, TraceIndex], None]

# Extractor registry — the @register(...) decorator appends here as each module is imported
EXTRACTORS: list[tuple[str, ExtractorFn]] = []


def register(name: str) -> Callable[[ExtractorFn], ExtractorFn]:
    """
    Decorator that registers an extractor in the registry.

    Usage:
        @register("requirements")
        def extract(repo_root: Path, index: TraceIndex) -> None:
            ...

    Files added in Phase 2 are auto-discovered, imported, and self-registered.
    Idempotent: if the same name is already registered, skip re-adding it
    (safe across reloads).
    """

    def decorator(fn: ExtractorFn) -> ExtractorFn:
        # idempotent: do not add a duplicate if the same name is already registered
        registered_names = {n for n, _ in EXTRACTORS}
        if name not in registered_names:
            EXTRACTORS.append((name, fn))
        return fn

    return decorator


def _discover_and_load() -> None:
    """
    Auto-discover this package's (__path__) submodules via pkgutil.iter_modules
    and import them. On import, the @register(...) decorator runs and
    self-registers into EXTRACTORS.

    __init__ itself is skipped.

    Test isolation handling: if EXTRACTORS is cleared externally, a module
    already cached in sys.modules won't re-run @register, so run_all would
    return an empty result. To prevent this, if a module has already been
    imported but its name is missing from EXTRACTORS, it is re-imported via
    importlib.reload() so @register runs again.
    """
    import sys

    package_name = __name__  # 'tools.traceability.extractors'
    registered_names = {n for n, _ in EXTRACTORS}

    for _finder, module_name, _ispkg in pkgutil.iter_modules(__path__):
        if module_name == "__init__":
            continue
        full_name = f"{package_name}.{module_name}"
        if full_name not in sys.modules:
            # First import — the @register decorator runs automatically
            importlib.import_module(full_name)
        elif module_name not in registered_names:
            # Module is cached but missing from EXTRACTORS (e.g. cleared after test isolation)
            # reload to re-run @register and re-register it
            importlib.reload(sys.modules[full_name])


def run_all(repo_root: Path, index: TraceIndex) -> dict[str, tuple[int, int]]:
    """
    Run every registered extractor and return (nodes_added, edges_added) per extractor.

    Adding a new extractor file requires no changes to this function — it
    automatically picks up and runs the new extractor. Extractors explicitly
    set to false under extractors in trace-config.yml are not run and are
    excluded from stats.

    Returns:
        A dict mapping extractor name to a (nodes added, edges added) tuple.
    """
    # Auto-discover and import submodules → registered into EXTRACTORS via @register decorator
    _discover_and_load()

    cfg = get_config(repo_root)
    stats: dict[str, tuple[int, int]] = {}
    for name, fn in EXTRACTORS:
        if not cfg.extractor_enabled(name):
            continue
        before_nodes = len(index.node_ids())
        before_edges = len(index.edges())
        fn(repo_root, index)
        nodes_added = len(index.node_ids()) - before_nodes
        edges_added = len(index.edges()) - before_edges
        stats[name] = (nodes_added, edges_added)
    return stats
