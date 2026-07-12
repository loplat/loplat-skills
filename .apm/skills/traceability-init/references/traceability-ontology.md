# Traceability 온톨로지 규격 (공유 정본)

`traceability-init`·`traceability-check` 스킬이 공유하는 온톨로지 규격. 스킬 본문에 이 내용을 복제하지 않는다.

## 정본 툴킷 (canonical toolkit)

- **위치**: `traceability-init` 스킬 디렉토리의 `toolkit/` — 스킬과 함께 배포되므로 설치 스코프(사용자/프로젝트)와 무관하게 항상 스킬 옆에 있다.
- 다른 프로젝트에 도입할 때는 이 `toolkit/`을 대상 repo의 `tools/traceability/`로 vendoring(복사)한 뒤 `trace-config.yml`만 작성한다(아래 "vendoring 절차").
- **upstream**: 툴킷 개발·테스트는 참조 구현 repo(`tools/traceability/`, 테스트 포함)에서 진행하고, 릴리즈 시점에 이 `toolkit/`으로 스냅샷을 갱신한다(loplat-skills README의 "툴킷 스냅샷 갱신" 참조).

## 파이프라인

```
build_index.py  # extractor 자동 발견(@register + pkgutil) → scratch/traceability/index.json
verify.py       # deterministic hard gate → ci-summary.json (exit 0/1/2)
report.py       # seed trace 중심 리포트 → report.md / report.html (optional)
```

exit code: `0` 통과 / `1` deterministic error (CI 차단) / `2` 도구·환경 오류 (skip 금지, 복구 필수).

## 노드 타입 카탈로그

**코어 (대부분 프로젝트에 적용 가능)**

| 타입 | canonical id 예 | 소스 |
|---|---|---|
| `Requirement` | `REQ-012` | PRD 문서 표/목록 |
| `ADR` | `ADR-0004` (4자리) | `docs/adr/` |
| `ApiOperation` | OpenAPI `operationId` | OpenAPI 스펙 |
| `SpecSection` / `SequenceDiagram` | 문서 anchor | `docs/specs/` |
| `CodeSymbol` | `파일:클래스` | 코드 AST 스캔 |
| `TestCase` | pytest node id 등 | 테스트 marker |

**플랫폼 확장 (해당 플랫폼 있을 때만)**: `UseCase`/`UseCaseCategory`(`UC-{N}-{C|M|N}-{NN}`), `DesignScreen`, `PlatformRequirement`(`IOS-REQ-NNN`), `IOSADR`/`IOSDecision`, `ComplianceControl`, `OperationRunbook`.

**엣지 11종**: `refines`, `references`, `implements`, `validates`, `exercises`, `step_calls`, `routed_to`, `governed_by`, `supersedes`, `conflicts_with`, `depends_on`. origin은 `auto`(extractor 추출) / `manual`(`manual-edges.yml`).

**marker 규약(테스트→요구 엣지)**: pytest `@pytest.mark.uc("UC-…")`, Kotlin/Swift 주석 `// UC-…`. 프로젝트별 marker 문법은 그 repo의 `docs/ontology/conventions.md`에 명문화한다.

## 프로파일

인벤토리 스캔 결과에 따라 도입 범위를 정한다. 억지 도입 금지 — 문서 자산이 없으면 온톨로지도 없다.

| 프로파일 | 조건 | 활성 노드 타입 |
|---|---|---|
| `docs-only` | ADR 또는 PRD만 존재 | Requirement, ADR |
| `backend-api` | + OpenAPI, 백엔드 테스트 | + ApiOperation, CodeSymbol, TestCase |
| `full-stack` | + 모바일/웹/디자인 산출물 | + UseCase, DesignScreen, Platform* |
| `not-ready` | 문서 자산 없음 | 도입 보류. 선행 조건(ADR/PRD 체계)부터 제안 |

## trace-config.yml 규격

`docs/ontology/trace-config.yml`. **툴킷이 이 파일을 직접 읽는다**(config-driven). 파일이 없으면 툴킷은 기본값(참조 구현 경로)으로 동작하므로, vendoring 프로젝트는 **코드를 수정하지 않고 이 config만 작성**한다. 파일이 있는데 pyyaml 미설치·YAML 손상·`version` 불일치면 exit 2(fail-closed).

```yaml
version: 1                      # 지원 버전(현재 1). 불일치 시 exit 2
paths:                          # 미지정 키는 기본값으로 폴백
  requirements: docs/requirements/prd.md
  adr_dir: docs/adr
  openapi: docs/api/openapi.json
  specs_dir: docs/specs
  usecase_checklist: docs/requirements/usecase-coverage-checklist.md
  design_readme: design/README.md
  code_globs: ["src/**/*.py"]
  pytest_dir: tests
  android_test_dirs: [android/app/src/test, android/app/src/androidTest]
  ios_req_docs: [ios/docs/prd/01-platform.md]
  ios_adr_dir: ios/docs/adr
  ios_test_dirs: [ios/App/AppUnitTests, ios/App/AppUITests]
  ios_ui_test_dir: ios/App/AppUITests/
  ontology_dir: docs/ontology
priority:
  must: Must                    # verify.py 필수요구(Requirement) 판정 어휘
extractors:                     # 명시적 false 인 추출기만 비활성 (미지정=활성)
  sequence: false
  usecase: false
  design: false
  ios: false
  ios_tests: false
  android_tests: false
```

## vendoring 절차 (config-driven)

`traceability-init` 스킬 디렉토리의 `toolkit/`을 대상 repo의 `tools/traceability/`로 복사한 뒤:

1. **`docs/ontology/trace-config.yml` 작성** — 대상 프로젝트의 실제 경로로 `paths`를 채운다. 미지정 키는 기본값(참조 구현 경로)으로 폴백하므로, 다른 경로만 명시하면 된다.
2. **미사용 문서 타입은 `extractors`에서 `false`** — `build_index`가 skip하고 비활성 목록을 출력한다. 소스 파일이 없는 extractor는 자동 skip되므로 config 없이도 안전하지만, 명시적 `false`가 의도를 문서화한다.
3. **PRD 어휘가 다르면 `priority.must`** 조정(예: `P0`).
4. **문서 포맷 자체가 다르면**(REQ 표 구조, ADR id 체계 등) 해당 extractor의 regex를 그 프로젝트 규약에 맞게 수정하거나 신규 extractor를 추가한다. extractor는 `@register` + 자동 발견 구조라 파일 하나 추가로 확장된다. **경로만 다르고 포맷이 같으면 코드 수정은 불필요하다.**
5. `report.py`의 sample trace는 `seed-traces.yml`의 첫 seed에서 렌더되므로 도메인 id 하드코딩이 없다. seed가 없으면 해당 섹션은 생략된다.

검증: config 작성 후 `build_index.py` → `verify.py` exit 0 실측. config가 코드 상수와 어긋날 일이 없다(코드가 config를 읽으므로).

## 공통 안전 규칙

- 결함 수정은 생성된 인덱스가 아니라 source 문서·marker·manual edge·extractor 규칙에서 한다.
- 좌표, 전화번호, 토큰, 시크릿, 운영 로그 원문, 개인정보를 인덱스/리포트/scratch에 남기지 않는다.
- `scratch/traceability/`는 산출물 디렉토리다. `.gitignore` 대상인지 확인한다.
