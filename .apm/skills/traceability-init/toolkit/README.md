# traceability — 추적성 인덱스 & 검증 도구

`tools/traceability/`는 PRD·ADR·OpenAPI·시퀀스 다이어그램 간의 연결을 정적으로 검증하는 파이썬 도구 모음이다.

## trace-config.yml (프로젝트 적응)

추출 대상 경로·어휘·활성 추출기는 `docs/ontology/trace-config.yml`에서 조정한다. **파일이 없으면 이 저장소(location-sharing) 기본값으로 동작하므로**, 기존 동작에는 영향이 없다. 다른 프로젝트가 이 툴킷을 vendoring 할 때는 코드를 고치지 말고 이 config만 작성한다.

```yaml
version: 1                      # 지원 버전(현재 1). 불일치 시 exit 2
paths:                          # 미지정 키는 기본값으로 폴백
  requirements: docs/requirements/prd.md
  adr_dir: docs/adr
  openapi: docs/api/openapi.json
  specs_dir: docs/specs
  usecase_checklist: docs/requirements/usecase-coverage-checklist.md
  design_readme: design/README.md
  code_globs:
    - backend/app/api/v1/*.py
    - backend/app/services/*.py
  pytest_dir: backend/tests
  android_test_dirs: [android/app/src/test, android/app/src/androidTest]
  ios_req_docs: [ios/docs/prd/01-platform.md]
  ios_adr_dir: ios/docs/adr
  ios_test_dirs: [ios/sgsg/sgsgUnitTests, ios/sgsg/sgsgUITests]
  ios_ui_test_dir: ios/sgsg/sgsgUITests/
  ontology_dir: docs/ontology
priority:
  must: Must                    # verify.py 필수 요구(Requirement) 판정 어휘
extractors:                     # 명시적으로 false 인 추출기만 비활성 (미지정=활성)
  ios: false
  ios_tests: false
```

- `paths`: 각 추출기가 스캔하는 경로. 소스 파일이 없는 추출기는 조용히 skip되므로, 문서 포맷이 같다면 경로만 바꿔 재사용한다.
- `extractors`: `false` 인 추출기는 `build_index`가 실행하지 않고 비활성 목록을 stdout에 출력한다. 문서 포맷 자체가 다르면 해당 추출기 파일을 삭제하거나 신규 추출기를 추가한다.
- config 파일이 존재하는데 pyyaml 미설치·YAML 손상·version 불일치면 **exit 2(fail-closed)** 로 종료한다.

## 의존성

`manual-edges.yml` 또는 `seed-traces.yml` 이 존재하는 경우 **pyyaml 6.0.2 이상이 필요하다.**

```sh
pip install "pyyaml==6.0.2"
```

pyyaml 없이 YAML 파일이 존재하는 상태에서 빌드/검증을 실행하면 **exit 2(환경 오류)** 로 fail-closed 종료한다. 불완전한 검증이 성공으로 오판되지 않도록 설계됐다.

backend uv 환경에서는 이미 pyyaml 이 포함돼 있으므로 별도 설치가 필요 없다.

## 로컬 명령

```sh
# 0. pyyaml 설치 확인 (manual-edges.yml / seed-traces.yml 존재하는 이 repo 에서 필수)
pip install "pyyaml==6.0.2"

# 1. 인덱스 빌드 — scratch/traceability/index.json 생성
python3 tools/traceability/build_index.py

# 2. 검증 실행 — scratch/traceability/ci-summary.json 생성
python3 tools/traceability/verify.py
# exit 0: 정상 (warn/coverage 항목만 존재 가능)
# exit 1: deterministic error — CI hard gate 해당
# exit 2: 환경/도구 오류 (pyyaml 미설치, index 없음 등) — 검증 자체가 불완전하므로 fail-closed

# 3. 보고서 생성 (선택)
python3 tools/traceability/report.py

# 4. self-test (인메모리 broken 케이스로 탐지 데모)
python3 tools/traceability/verify.py --selftest

# 5. lint/format 검증 (backend uv 환경 필요)
cd backend && uv run ruff check ../tools/traceability
cd backend && uv run ruff format --check ../tools/traceability

# 6. 변경된 파일 기준 빠른 검증 (--changed 플래그가 구현된 경우)
# python3 tools/traceability/build_index.py --changed <file>
```

## exit code 의미

| exit code | 의미 | CI 동작 |
|-----------|------|---------|
| `0` | deterministic error 없음 (clean) | CI 통과 |
| `1` | deterministic error 1개 이상 (콘텐츠 결함) | CI hard gate 차단, pre-commit 커밋 차단 |
| `2` | 환경/도구 오류 (pyyaml 미설치, index 없음, JSON 파싱 실패 등) | CI(`set -ceu`) loud fail; pre-commit fail-open |

**pyyaml 부재는 환경 문제** → exit 2. YAML 파일이 존재하는데 검증을 건너뛰면 인덱스·검증이 불완전해지므로 성공으로 오판할 수 없다.

## CI 게이트 동작

`cloudbuild-ci.yaml`의 `traceability-verify` step(PR CI 전용):

1. `pip install pyyaml` — manual-edges.yml 파싱용 (운영 DB·Secret Manager 미접근)
2. `python tools/traceability/build_index.py` — 인덱스 빌드
3. `python tools/traceability/verify.py` — hard gate (deterministic error → exit 1 → CI fail)
4. `python tools/traceability/tests/run_tests.py` — fixture 회귀 테스트

CD(`cloudbuild.yaml`)에는 traceability step이 없다. doc 이슈로 배포를 막지 않기 위해 PR CI 에만 게이트를 둔다.

## lightweight vs full 모드

| 모드 | 내용 | 언제 사용 |
|------|------|-----------|
| **lightweight** (backend-only) | `backend/app/` 변경 시 pre-commit이 자동 트리거 | API 엔드포인트·시퀀스 정합성 빠른 확인 |
| **full** (docs/spec) | `docs/`, `tools/traceability/` 변경 시 pre-commit 트리거 + CI 전체 실행 | PRD·ADR·OpenAPI·manual-edges 전체 검증 |

로컬에서 full 모드를 수동 실행할 때는 build_index + verify 순서로 실행한다.

## hard-fail vs warn 정책

| 등급 | 종류 | CI 동작 |
|------|------|---------|
| **hard fail** (exit 1) | `broken_reference`, `malformed_manual_edge`, `secret_in_index` | PR CI 차단 |
| **warn** (exit 0) | `orphan`, `superseded_in_use`, `api_unlinked`, `coverage_gap`, `test_coverage_drift` | CI 통과, 리포트에 기록 |

상세 결과는 `scratch/traceability/ci-summary.json` 에서 확인한다.

## pre-commit 동작

`scripts/git-hooks/pre-commit`에 traceability 가드가 포함되어 있다.

활성화(클론마다 1회):
```sh
git config core.hooksPath scripts/git-hooks
```

**guard 조건**: `docs/`, `ios/`, `design/`, `tools/traceability/`, `backend/app/` 중 staged 파일이 있을 때만 실행.

**fail-open**: python3 미설치 또는 build/verify 예외 시 안내만 출력하고 커밋 허용. verify가 `exit 1`(deterministic error)을 보고할 때만 커밋 차단.

## pytest 없는 환경에서 테스트 실행

시스템 python3에 pytest가 없어도 아래 명령으로 fixture 회귀 테스트를 직접 실행할 수 있다.

```sh
python3 tools/traceability/tests/run_tests.py
```

exit 0: 전체 통과 / exit 1: 하나 이상 실패.

pytest 환경에서는 다음으로도 실행 가능:
```sh
cd <repo-root>
python -m pytest tools/traceability/tests/test_verify.py -v
```

## 파일 구조

```
tools/traceability/
├── build_index.py        인덱스 빌드 진입점
├── verify.py             검증 진입점 (hard gate + warn)
├── config.py             trace-config.yml 로더 (부재 시 기본값, 손상 시 fail-closed)
├── model.py              TraceNode / TraceEdge / TraceFinding / TraceIndex
├── report.py             보고서 생성
├── conftest.py           pytest sys.path 부트스트랩
├── extractors/           소스별 추출기 모음
└── tests/
    ├── fixtures/         4종 broken/clean fixture 파일
    ├── test_verify.py    pytest 스타일 테스트 함수
    ├── test_config.py    config 로더 테스트
    └── run_tests.py      pytest 없는 환경용 plain python3 러너
```
