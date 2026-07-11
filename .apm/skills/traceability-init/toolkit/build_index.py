"""
traceability index 빌드 진입점.

repo root 에서 실행:
    python3 tools/traceability/build_index.py

또는 임의의 디렉토리에서 실행해도 repo root 를 자동 탐색:
    python3 /path/to/tools/traceability/build_index.py

산출물: scratch/traceability/index.json
"""

from __future__ import annotations

import sys
from pathlib import Path

# -----------------------------------------------------------------------------
# sys.path 부트스트랩 — 직접 스크립트 실행 시 tools 패키지를 인식하게 한다.
# pytest -m 방식 실행에서는 conftest.py 가 처리하므로
# 아래 코드는 직접 스크립트 실행 경우에만 실질 효과가 있다.
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
    이 스크립트의 위치를 기준으로 repo root 를 탐색한다.

    스크립트가 tools/traceability/ 아래에 있다고 가정하므로
    두 단계 상위 디렉토리가 repo root 다.
    .git 디렉토리가 존재하는지 확인해 repo root 를 검증한다.
    """
    candidate = _SCRIPT_DIR.parent.parent  # tools/traceability → tools → repo root
    if (candidate / ".git").exists():
        return candidate
    # .git 이 없더라도 candidate 를 그대로 사용 (CI 환경 대비)
    return candidate


def main() -> None:
    """index 빌드 메인 로직."""
    repo_root = _find_repo_root()
    index = TraceIndex()

    # trace-config 로드 (부재 시 기본값). 손상·pyyaml 부재면 fail-closed(exit 2).
    try:
        cfg = get_config(repo_root)
    except TraceConfigError as exc:
        print(f"[build_index] ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    disabled = cfg.disabled_extractor_names()
    if disabled:
        print(f"[build_index] disabled extractors: {', '.join(disabled)}")

    # 모든 추출기 실행 — 반환값: dict[str, tuple[nodes_added, edges_added]]
    stats = run_all(repo_root, index)

    # manual-edges.yml 로드 — origin='manual' 엣지를 인덱스에 추가
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
            # manual-edges.yml 이 존재하는데 pyyaml 이 없으면 fail-closed (exit 2)
            print(
                "[build_index] ERROR: manual-edges.yml 존재하나 pyyaml 미설치 — "
                "fail-closed (exit 2). 'pip install pyyaml==6.0.2' 후 재실행.",
                file=sys.stderr,
            )
            sys.exit(2)
        except Exception as exc:
            print(f"[build_index] warn: manual-edges.yml 로드 실패: {exc}")

    # 산출 디렉토리 생성
    out_dir = repo_root / "scratch" / "traceability"
    out_dir.mkdir(parents=True, exist_ok=True)

    # index.json 저장
    out_path = out_dir / "index.json"
    out_path.write_text(index.to_json(), encoding="utf-8")

    # stdout 요약 출력
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
