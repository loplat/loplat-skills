---
name: traceability-init
description: 개발 프로젝트를 분석해 traceability 온톨로지 기반(docs/ontology + tools/traceability)을 구성한다. 사용자가 "온톨로지 구성", "traceability 도입/초기화", "정합성 체크 셋업", "이 프로젝트에 traceability 적용", "프로젝트 분석해서 온톨로지 만들어줘"를 요청하거나, traceability-check가 미구성 repo를 만났을 때 사용한다. 인벤토리 스캔→프로파일 판정→온톨로지 스캐폴딩→툴킷 vendoring→verify 통과까지 진행한다.
---

# Traceability Init (프로젝트 분석 + 온톨로지 부트스트랩)

문서 자산(PRD/ADR/OpenAPI/스펙)이 있는 프로젝트에 traceability 정합성 체크 기반을 구성한다.

**이 스킬 디렉토리**(이 SKILL.md가 있는 디렉토리)에 실행 자산이 함께 배포된다:

- `references/traceability-ontology.md` — 규격 정본. **절차 시작 전 반드시 Read**한다. 노드/엣지 타입, 프로파일, trace-config.yml 규격, vendoring 절차가 모두 여기에 있다.
- `scripts/inventory.py` — 1단계 인벤토리 스캔 스크립트.
- `toolkit/` — vendoring용 정본 툴킷 (config-driven).

## 사용 시점

- 새 프로젝트 또는 기존 프로젝트에 정합성 체크(traceability)를 도입할 때.
- `traceability-check`가 `tools/traceability/` 또는 `docs/ontology/` 부재를 보고했을 때.
- **사용하지 않는 경우**: 이미 구성된 repo의 검증(→ `traceability-check`), 문서 자산이 전무한 프로젝트에 억지 도입(→ 2단계에서 보류 판정).

## 절차

### 1. 인벤토리 스캔

```sh
python3 <이 스킬 디렉토리>/scripts/inventory.py [repo_root]
```

repo root는 인자를 생략하면 스크립트가 `git rev-parse`로 확정한다(worktree 안전). 스캔은 디렉토리 구조를 가정하지 않는다 — ADR/specs/ontology 디렉토리와 PRD/OpenAPI 파일을 트리 탐색(depth 4)으로 이름 기반 탐지한다. 출력 JSON의 `suggested_profile`을 확인한다.

### 2. 프로파일 판정 및 사용자 확인

- `already-initialized` → 이 스킬 종료, `traceability-check`로 안내.
- `not-ready` → **도입을 보류**하고 이유를 보고한다. 선행 조건(ADR 디렉토리, PRD 문서 체계)을 제안하되 사용자 요청 없이 문서를 대량 생성하지 않는다.
- `docs-only` / `backend-api` / `full-stack` → 활성화할 노드 타입·extractor 목록을 제시하고 사용자 확인을 받는다. 스캔이 놓친 문서(비표준 확장자·명명)가 있는지 이때 함께 묻는다.

### 3. docs/ontology/ 스캐폴딩

reference의 규격에 따라 생성한다:

- `trace-config.yml` — 1단계에서 실제 탐지된 경로로 채운다. **툴킷이 이 파일을 직접 읽는다** — 4단계에서 코드가 아니라 이 config로 적응한다.
- `schema.md` — 활성 노드/엣지 타입만 남긴 subset. reference의 노드 타입 카탈로그를 기반으로 프로젝트 어휘로 서술한다.
- `conventions.md` — canonical id regex, marker 문법(이 프로젝트의 언어에 맞게), 파일 배치 규약.
- `manual-edges.yml`, `seed-traces.yml`, `api-exclusions.yml` — 빈 stub (주석으로 형식 예시 1건씩).

### 4. 툴킷 vendoring + config 작성

툴킷은 config-driven이다. **경로 상수를 직접 수정하지 않고 `trace-config.yml`만 작성**한다(reference 문서의 "vendoring 절차" 참조).

1. 이 스킬 디렉토리의 `toolkit/`을 대상 repo의 `tools/traceability/`로 복사한다(`__pycache__` 제외).
2. `trace-config.yml`의 `paths`를 대상 프로젝트 실제 경로로 채운다. 기본값과 다른 키만 명시하면 된다 — 미지정 키는 기본값으로 폴백한다.
3. 미사용 문서 타입은 `extractors`에서 `false`로 둔다(소스 파일이 없으면 자동 skip되지만 명시가 의도를 문서화한다). PRD 어휘가 다르면 `priority.must`를 조정한다.
4. **문서 포맷 자체가 다를 때만** 해당 extractor의 regex를 수정한다. 경로만 다르고 포맷이 같으면 코드 수정은 불필요하다.

### 5. 기존 문서에 canonical id 부여 (최소 침습)

- ADR 파일명·헤더에 `ADR-NNNN`이 없으면 부여한다. 기존 번호 체계가 있으면 **그 체계를 유지**하고 conventions.md의 regex를 거기에 맞춘다 — 문서를 규약에 맞추는 게 아니라 규약을 문서에 맞춘다.
- PRD에 REQ id 표가 없으면 기존 요구사항 목록에 id 열만 추가한다. 내용 재작성 금지.
- 테스트 marker는 이 단계에서 넣지 않는다(coverage는 hard gate가 아님). conventions.md에 문법만 정의해 두면 이후 개발에서 점진 적용된다.

### 6. 검증 루프 (최대 3회)

```sh
python3 tools/traceability/build_index.py && python3 tools/traceability/verify.py
```

- exit 0이 될 때까지 결함을 수정한다. 수정 위치는 source 문서·trace-config·manual edge이며 생성된 index가 아니다.
- **3회 초과 시 중단**하고 남은 결함 목록(`kind`, `subject`, `location`)을 정리해 사용자에게 에스컬레이션한다. 반복 초과는 문서 자산이 프로파일 대비 미성숙하다는 신호다 — 프로파일 하향(`backend-api`→`docs-only`)을 함께 제안한다.
- exit 2(환경 오류)는 결함 수정 대상이 아니다. PyYAML 등 의존성을 그 repo의 Python 환경 규약(uv/poetry/venv)으로 해결한다.

### 7. repo 지침 등록

- repo의 `AGENTS.md`(없으면 `CLAUDE.md`)에 정합성 체크 섹션을 추가한다: 실행 명령 3개, exit code 의미, `docs/ontology/` 참조. **이 섹션이 스킬 미지원 런타임(Cursor 등)에서의 진입점이므로 생략하지 않는다.**
- `scratch/traceability/`가 `.gitignore`에 있는지 확인하고 없으면 추가한다.
- CI 등록(cloudbuild/GitHub Actions에 `verify.py` step)은 제안만 하고 사용자 승인 후 진행한다.

## Rationalizations

| 핑계 | 반박 |
|---|---|
| "문서가 없으니 PRD/ADR을 먼저 만들어주고 온톨로지도 깔자" | not-ready 판정은 보류가 정답. 내용 없는 문서 뼈대 위의 온톨로지는 verify가 영원히 빈 통과만 한다. |
| "extractor 코드를 직접 고쳐 경로를 박겠다" | 툴킷은 config-driven이다. 코드 상수를 고치면 다음 vendoring에서 어긋난다. `trace-config.yml`에만 쓴다. |
| "verify exit 2인데 일단 온톨로지 문서는 만들어졌으니 완료 보고" | exit 0 실측 전에는 미완료다. 환경 오류는 fallback으로 복구한다. |
| "기존 ADR 번호가 규약과 달라서 전부 rename" | 규약을 문서에 맞춘다. 대량 rename은 링크를 깨는 파괴적 변경이다. |

## Red Flags

- verify 결함을 고치겠다며 `scratch/traceability/index.json`을 편집하고 있다 — 산출물 수정은 무의미, source로 돌아가라.
- 3회 루프를 넘겼는데 프로파일 재검토 없이 4회째 수정 중이다.
- 경로를 바꾸겠다며 extractor 코드를 편집하고 있다 — 툴킷은 config-driven이다. 경로는 `trace-config.yml`에서만 바꾼다.

## Verification

- [ ] `python3 tools/traceability/build_index.py && python3 tools/traceability/verify.py` exit 0 실측
- [ ] `docs/ontology/` 6종 파일 존재 (trace-config.yml, schema.md, conventions.md, yml stub 3종)
- [ ] `trace-config.yml`의 `paths`가 대상 프로젝트 실제 경로를 가리킴 (build_index 출력의 노드 수로 교차 확인 — 0이면 경로 오류)
- [ ] 비활성 extractor를 `extractors: {name: false}`로 명시했거나, build_index의 `disabled extractors:` 출력으로 확인
- [ ] extractor 코드 상수를 직접 편집하지 않았다 (포맷 차이로 regex를 고친 경우가 아니면)
- [ ] repo 지침(AGENTS.md/CLAUDE.md)에 정합성 체크 섹션 존재
- [ ] `scratch/traceability/` gitignore 처리 확인
