"""Loader for trace-config.yml.

If docs/ontology/trace-config.yml exists, its path and active-extractor
settings are read; otherwise the tool falls back to the reference
implementation defaults (backward compatible). Other projects vendoring
this toolkit only need to write a config file — no code changes required.

If the config file exists but PyYAML is unavailable or the file is
corrupt, a TraceConfigError is raised — callers (build_index/verify/report)
convert this into exit 2 (fail-closed), matching the existing policy for
manual-edges.yml.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

CONFIG_REL_PATH = "docs/ontology/trace-config.yml"

_SUPPORTED_VERSION = 1

# Reference implementation defaults — used when the config file is absent.
# Key names are aligned with extractor module names (they double as a
# vendoring adaptation spec).
_DEFAULT_PATHS: dict[str, Any] = {
    "requirements": "docs/requirements/prd.md",
    "adr_dir": "docs/adr",
    "openapi": "docs/api/openapi.json",
    "specs_dir": "docs/specs",
    "usecase_checklist": "docs/requirements/usecase-coverage-checklist.md",
    "design_readme": "design/README.md",
    "code_globs": [
        "backend/app/api/v1/*.py",
        "backend/app/services/*.py",
        "backend/app/domain/**/*.py",
    ],
    "pytest_dir": "backend/tests",
    "android_test_dirs": [
        "android/app/src/test",
        "android/app/src/androidTest",
    ],
    "ios_req_docs": [
        "ios/docs/prd/01-platform.md",
        "ios/docs/prd/02-feature-ux.md",
        "ios/docs/prd/03-ops.md",
    ],
    "ios_adr_dir": "ios/docs/adr",
    "ios_test_dirs": [
        "ios/App/AppUnitTests",
        "ios/App/AppUITests",
    ],
    "ios_ui_test_dir": "ios/App/AppUITests/",
    "ontology_dir": "docs/ontology",
    # Agent-authored ontology file (resource-agnostic extraction channel).
    "ontology_source": "docs/ontology/ontology.yml",
}

# Priority value verify.py uses to identify "must-have" Requirements
_DEFAULT_MUST_PRIORITY = "Must"


class TraceConfigError(RuntimeError):
    """Config file failed to load — the caller treats this as exit 2."""


class TraceConfig:
    """Accessor for the loaded trace-config. Unspecified keys fall back to defaults."""

    def __init__(self, raw: dict[str, Any] | None = None) -> None:
        raw = raw or {}
        self._paths: dict[str, Any] = {**_DEFAULT_PATHS, **(raw.get("paths") or {})}
        self._extractors: dict[str, bool] = {
            str(k): bool(v) for k, v in (raw.get("extractors") or {}).items()
        }
        priority = raw.get("priority") or {}
        self.must_priority: str = str(priority.get("must", _DEFAULT_MUST_PRIORITY))

    def path(self, key: str) -> str:
        """Look up a single path key (a repo-root-relative string)."""
        value = self._paths[key]
        if not isinstance(value, str):
            raise TraceConfigError(f"trace-config paths.{key} must be a string: {value!r}")
        return value

    def path_list(self, key: str) -> list[str]:
        """Look up a path-list key."""
        value = self._paths[key]
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
            return list(value)
        raise TraceConfigError(f"trace-config paths.{key} must be a list of strings: {value!r}")

    def extractor_enabled(self, name: str) -> bool:
        """Whether an extractor is enabled — disabled only if config explicitly sets false."""
        return self._extractors.get(name, True)

    def disabled_extractor_names(self) -> list[str]:
        """Names of extractors explicitly disabled in the config."""
        return sorted(n for n, enabled in self._extractors.items() if not enabled)


_CACHE: dict[Path, TraceConfig] = {}


def get_config(repo_root: Path) -> TraceConfig:
    """Return the trace-config for the given repo root (cached per path).

    Returns a default TraceConfig if the config file is absent. Raises
    TraceConfigError (fail-closed) if the file exists but PyYAML is
    missing, the YAML is corrupt, or the version doesn't match.
    """
    root = Path(repo_root).resolve()
    cached = _CACHE.get(root)
    if cached is not None:
        return cached

    config_path = root / CONFIG_REL_PATH
    if not config_path.exists():
        cfg = TraceConfig()
    else:
        try:
            import yaml  # type: ignore[import]
        except ImportError as exc:
            raise TraceConfigError(
                f"{CONFIG_REL_PATH} exists but pyyaml is not installed — fail-closed. "
                "Run 'pip install pyyaml==6.0.2' and retry."
            ) from exc
        try:
            with config_path.open(encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        except Exception as exc:
            raise TraceConfigError(f"Failed to parse {CONFIG_REL_PATH}: {exc}") from exc
        if not isinstance(raw, dict):
            raise TraceConfigError(f"The top level of {CONFIG_REL_PATH} must be a mapping")
        version = raw.get("version", _SUPPORTED_VERSION)
        if version != _SUPPORTED_VERSION:
            raise TraceConfigError(
                f"trace-config version {version} is not supported (supported: {_SUPPORTED_VERSION})"
            )
        cfg = TraceConfig(raw)

    _CACHE[root] = cfg
    return cfg


def clear_cache() -> None:
    """For test use — clears the cache."""
    _CACHE.clear()
