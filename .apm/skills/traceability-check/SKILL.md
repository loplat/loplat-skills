---
name: traceability-check
description: traceability 온톨로지가 구성된 저장소(docs/ontology + tools/traceability 보유)의 정합성 hard gate를 실행한다. PRD·ADR·OpenAPI·시퀀스 다이어그램·ontology 문서 또는 tools/traceability 코드를 변경했거나, 사용자가 "정합성 체크", "traceability 검증", "일관성 검증", "ontology 변경 후 검증", "trace 인덱스/리포트 생성"을 요청할 때 사용한다. PRD가 인용한 canonical id 부재·manual edge 오류·시퀀스↔API 불일치 같은 deterministic 결함을 잡는다. 미구성 repo에서는 traceability-init으로 안내한다.
---

# Traceability Check (정합성 hard gate)

이 스킬은 **실행 wrapper**다. 정책·스키마·규약의 single source는 대상 repo 안 문서이며, 절차를 여기서 중복하지 않는다.
공통 규격(노드/엣지 타입, exit code, 안전 규칙): 함께 배포되는 `traceability-init` 스킬 디렉토리의 `references/traceability-ontology.md` (이 스킬과 같은 skills 루트).

## 전제 조건 (repo 판별)

repo root(`git rev-parse --show-toplevel`) 기준으로 확인한다:

- `tools/traceability/` 와 `docs/ontology/` 가 존재해야 한다.
- **둘 중 하나라도 없으면 이 스킬을 진행하지 않는다.** 온톨로지 미구성 상태이므로 `traceability-init` 스킬로 구성부터 제안한다.

## Source of Truth (대상 repo 기준)

- 절차 전문: repo의 `AGENTS.md` 정합성 섹션, `docs/ontology/agent-consistency-skill.md`(있는 경우)
- 스키마/규약: `docs/ontology/schema.md`, `docs/ontology/conventions.md`
- 적응 상태: `docs/ontology/trace-config.yml`(있는 경우 — 활성 extractor·경로 명세, 툴킷이 직접 읽는다)
- 도구 README: `tools/traceability/README.md`

## 언제 쓰나

- `trace-config.yml`의 `paths`에 해당하는 문서(PRD·ADR·specs·API 스펙), `docs/ontology/**`, `tools/traceability/**` 변경 후 커밋·PR 전 검증.
- 사용자가 정합성, 일관성, traceability 검증을 요청할 때.
- 사용자가 trace 인덱스, missing link, ontology graph, report 생성을 요청할 때.

## 실행

repo root 기준, 순서대로 실행한다.

```sh
python3 tools/traceability/build_index.py
python3 tools/traceability/verify.py
python3 tools/traceability/report.py
```

산출물:

- `scratch/traceability/index.json`
- `scratch/traceability/ci-summary.json`
- `scratch/traceability/report.md`
- `scratch/traceability/report.html`

검증만 하려면 `build_index.py` → `verify.py`까지면 충분하다. 사람이 읽을 리포트가 필요할 때만 `report.py`를 실행한다.

변경 파일 영향 분석:

```sh
python3 tools/traceability/report.py --changed <path> [<path> ...]
```

## Python 환경 fallback

root Python에 `PyYAML`이 없으면 fail-closed(exit 2) 될 수 있다. 그 경우 조용히 넘기지 말고 **그 repo의 Python 환경 규약**(uv/poetry/venv)으로 같은 도구를 실행한다. 예 — location-sharing은 backend `uv` 환경을 쓴다:

```sh
cd backend
uv run python ../tools/traceability/build_index.py
uv run python ../tools/traceability/verify.py
uv run python ../tools/traceability/report.py
```

## verify.py exit code 해석

| exit | 의미 | 대응 |
|---|---|---|
| `0` | deterministic error 없음 | 통과. `semantic_candidate`와 `coverage`는 사람·에이전트 검토용이며 hard gate가 아니다. |
| `1` | deterministic error 1건 이상 | CI 차단 결함. `ci-summary.json`의 `categories.deterministic.errors`를 보고 source 문서를 고친다. |
| `2` | 도구/환경 오류 | 의존성·trace-config 손상·버전 불일치 문제다. skip하지 말고 복구하거나 위 fallback을 쓴다. |

## 의존성

- `docs/ontology/`에 `trace-config.yml`, `manual-edges.yml`, `seed-traces.yml` 중 하나라도 있으면 `PyYAML 6.0.2+`가 필요하다.
- 미설치 시 build_index·verify는 exit 2로 종료해야 한다.

## tools/traceability 코드 변경 시 추가 게이트

repo의 lint 규약으로 검사하고 도구 테스트를 돌린다. 예 — location-sharing:

```sh
cd backend
uv run ruff check ../tools/traceability
uv run ruff format --check ../tools/traceability
cd ..
python3 tools/traceability/tests/run_tests.py
```

`uv run`이 lock 파일을 변경하면 의도한 dependency 변경이 아닌 한 원복한다.

## 평가 경계

- 팀 공통 gate는 plain python3 도구와 CI의 traceability verify step이다.
- 벡터 인덱싱류(SocratiCode/Qdrant/Ollama 등)는 팀 gate가 아니다. 로컬 UX 보조용 optional enrichment로만 본다. 없어도 이 스킬과 CI는 동작해야 한다.

## 보고 규칙

- deterministic error가 있으면 결함 목록의 `kind`, `subject`, `location`을 보고한다.
- 결함 수정은 생성된 인덱스가 아니라 source 문서, marker, manual edge, extractor 규칙에서 한다.
- 좌표, 전화번호, 토큰, 시크릿, 운영 로그 원문, 개인정보를 인덱스/리포트/scratch에 남기지 않는다.
