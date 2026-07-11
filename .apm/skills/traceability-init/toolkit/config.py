"""trace-config.yml 로더.

docs/ontology/trace-config.yml 이 존재하면 경로·활성 추출기 설정을 읽고,
없으면 location-sharing 기본값으로 동작한다(하위 호환). 다른 프로젝트가
이 툴킷을 vendoring 할 때는 코드 수정 없이 config 파일만 작성하면 된다.

config 파일이 존재하는데 PyYAML 이 없거나 파일이 손상됐으면
TraceConfigError 를 던진다 — 호출측(build_index/verify/report)은 이를
exit 2(fail-closed)로 변환한다. manual-edges.yml 의 기존 정책과 동일하다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

CONFIG_REL_PATH = "docs/ontology/trace-config.yml"

_SUPPORTED_VERSION = 1

# location-sharing 기본값 — config 부재 시 이 값으로 동작한다.
# 키 이름은 추출기 모듈명과 정렬한다 (vendoring 적응 명세서 역할).
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
        "ios/sgsg/sgsgUnitTests",
        "ios/sgsg/sgsgUITests",
    ],
    "ios_ui_test_dir": "ios/sgsg/sgsgUITests/",
    "ontology_dir": "docs/ontology",
}

# verify.py 의 필수 요구(Requirement) 판정 우선순위 값
_DEFAULT_MUST_PRIORITY = "Must"


class TraceConfigError(RuntimeError):
    """config 파일 로드 실패 — 호출측에서 exit 2 로 처리한다."""


class TraceConfig:
    """로드된 trace-config 접근자. 미지정 키는 기본값으로 폴백한다."""

    def __init__(self, raw: dict[str, Any] | None = None) -> None:
        raw = raw or {}
        self._paths: dict[str, Any] = {**_DEFAULT_PATHS, **(raw.get("paths") or {})}
        self._extractors: dict[str, bool] = {
            str(k): bool(v) for k, v in (raw.get("extractors") or {}).items()
        }
        priority = raw.get("priority") or {}
        self.must_priority: str = str(priority.get("must", _DEFAULT_MUST_PRIORITY))

    def path(self, key: str) -> str:
        """단일 경로 키 조회 (repo root 상대 문자열)."""
        value = self._paths[key]
        if not isinstance(value, str):
            raise TraceConfigError(f"trace-config paths.{key} 는 문자열이어야 한다: {value!r}")
        return value

    def path_list(self, key: str) -> list[str]:
        """경로 목록 키 조회."""
        value = self._paths[key]
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
            return list(value)
        raise TraceConfigError(f"trace-config paths.{key} 는 문자열 목록이어야 한다: {value!r}")

    def extractor_enabled(self, name: str) -> bool:
        """추출기 활성 여부 — config 에 명시적으로 false 인 경우만 비활성."""
        return self._extractors.get(name, True)

    def disabled_extractor_names(self) -> list[str]:
        """config 에서 명시적으로 비활성화된 추출기 이름 목록."""
        return sorted(n for n, enabled in self._extractors.items() if not enabled)


_CACHE: dict[Path, TraceConfig] = {}


def get_config(repo_root: Path) -> TraceConfig:
    """repo root 의 trace-config 를 반환한다(경로별 캐시).

    config 파일이 없으면 기본값 TraceConfig 를 반환한다. 파일이 있는데
    PyYAML 미설치·YAML 손상·version 불일치면 TraceConfigError (fail-closed).
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
                f"{CONFIG_REL_PATH} 존재하나 pyyaml 미설치 — fail-closed. "
                "'pip install pyyaml==6.0.2' 후 재실행."
            ) from exc
        try:
            with config_path.open(encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        except Exception as exc:
            raise TraceConfigError(f"{CONFIG_REL_PATH} 파싱 실패: {exc}") from exc
        if not isinstance(raw, dict):
            raise TraceConfigError(f"{CONFIG_REL_PATH} 최상위는 매핑이어야 한다")
        version = raw.get("version", _SUPPORTED_VERSION)
        if version != _SUPPORTED_VERSION:
            raise TraceConfigError(
                f"trace-config version {version} 은 지원하지 않는다 (지원: {_SUPPORTED_VERSION})"
            )
        cfg = TraceConfig(raw)

    _CACHE[root] = cfg
    return cfg


def clear_cache() -> None:
    """테스트용 — 캐시 초기화."""
    _CACHE.clear()
