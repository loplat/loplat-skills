"""
추출기 레지스트리.

각 추출기는 다음 시그니처의 extract 함수를 구현한다:
    def extract(repo_root: Path, index: TraceIndex) -> None

Phase 2 에서 새 추출기를 추가할 때는 이 패키지 디렉토리에
새 모듈 파일을 추가하고 함수에 @register("이름") 데코레이터만 달면 된다.
이 파일(extractors/__init__.py)은 수정할 필요 없다 — pkgutil.iter_modules
로 서브모듈을 자동 발견해 임포트하므로 자기 등록이 트리거된다.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from pathlib import Path

from tools.traceability.config import get_config
from tools.traceability.model import TraceIndex

# 추출기 타입 정의
ExtractorFn = Callable[[Path, TraceIndex], None]

# 추출기 레지스트리 — 각 모듈이 임포트될 때 @register(...) 데코레이터가 여기 추가한다
EXTRACTORS: list[tuple[str, ExtractorFn]] = []


def register(name: str) -> Callable[[ExtractorFn], ExtractorFn]:
    """
    추출기를 레지스트리에 등록하는 데코레이터.

    사용 예:
        @register("requirements")
        def extract(repo_root: Path, index: TraceIndex) -> None:
            ...

    Phase 2 에서 새 파일을 추가하면 auto-discovery 가 임포트해 자동 등록된다.
    중복 등록 방지: 동일한 이름이 이미 등록된 경우 건너뛴다 (reload 시 안전).
    """

    def decorator(fn: ExtractorFn) -> ExtractorFn:
        # idempotent: 동일한 이름이 이미 등록된 경우 중복 추가하지 않는다
        registered_names = {n for n, _ in EXTRACTORS}
        if name not in registered_names:
            EXTRACTORS.append((name, fn))
        return fn

    return decorator


def _discover_and_load() -> None:
    """
    이 패키지(__path__)의 서브모듈을 pkgutil.iter_modules 로 자동 발견해
    임포트한다. 임포트 시 @register(...) 데코레이터가 실행되어 EXTRACTORS 에
    자기 등록된다.

    __init__ 자신은 건너뛴다.

    테스트 격리 대응: EXTRACTORS 가 외부에서 지워진 경우 모듈이 sys.modules
    에 캐시되어 있어도 @register 가 재실행되지 않아 run_all 이 빈 결과를 반환한다.
    이를 방지하기 위해 모듈이 이미 임포트됐으나 EXTRACTORS 에 해당 이름이
    없을 때는 importlib.reload() 로 재임포트해 @register 를 재실행한다.
    """
    import sys

    package_name = __name__  # 'tools.traceability.extractors'
    registered_names = {n for n, _ in EXTRACTORS}

    for _finder, module_name, _ispkg in pkgutil.iter_modules(__path__):
        if module_name == "__init__":
            continue
        full_name = f"{package_name}.{module_name}"
        if full_name not in sys.modules:
            # 처음 임포트 — @register 데코레이터가 자동 실행됨
            importlib.import_module(full_name)
        elif module_name not in registered_names:
            # 모듈은 캐시됐으나 EXTRACTORS 에 없음 (테스트 격리 후 clear 등)
            # reload 로 @register 를 재실행해 재등록한다
            importlib.reload(sys.modules[full_name])


def run_all(repo_root: Path, index: TraceIndex) -> dict[str, tuple[int, int]]:
    """
    등록된 모든 추출기를 실행하고 추출기별 (nodes_added, edges_added) 를 반환한다.

    새 추출기 파일을 추가하면 이 함수는 수정 없이 자동으로 새 추출기를
    포함해 실행한다. trace-config.yml 의 extractors 에서 명시적으로 false 인
    추출기는 실행하지 않고 stats 에도 넣지 않는다.

    Returns:
        추출기 이름 → (추가된 노드 수, 추가된 엣지 수) 튜플 딕셔너리.
    """
    # 서브모듈 자동 발견 및 임포트 → @register 데코레이터로 EXTRACTORS 에 등록됨
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
