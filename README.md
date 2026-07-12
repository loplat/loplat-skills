# loplat-skills

loplat 사내 범용 AI agent 스킬 모음. [SKILL.md 오픈 표준](https://cursor.com/docs/skills)을 따르므로 Claude Code, Codex CLI, Antigravity(agy), Gemini CLI, Cursor 등 런타임 종류에 관계없이 동작한다.

## 스킬 목록

| 스킬 | 용도 |
|---|---|
| `traceability-init` | 프로젝트를 분석해 traceability 온톨로지 기반(docs/ontology + tools/traceability)을 구성. config-driven 툴킷 동봉 |
| `traceability-check` | 온톨로지가 구성된 repo의 정합성 hard gate 실행 (build_index → verify → report) |

## 설치

**설치 스코프는 사용자가 판단한다.** 특정 프로젝트에서만 쓸 스킬은 프로젝트 스코프로, 모든 작업에서 쓸 스킬은 사용자 스코프로 설치한다.

```sh
git clone <이 repo> && cd loplat-skills

# 사용자 스코프 (~/.claude/skills, ~/.agents/skills)
./install.sh --user

# 프로젝트 스코프 (<dir>/.claude/skills, <dir>/.agents/skills, <dir>/.cursor/skills)
./install.sh --project ~/dev-workspace/my-service/main

# 특정 스킬만 / symlink 모드(git pull만으로 갱신) / 미리보기
./install.sh --user --link traceability-init traceability-check
./install.sh --project . --dry-run
./install.sh --list
```

| 런타임 | 읽는 위치 (사용자 / 프로젝트) |
|---|---|
| Claude Code | `~/.claude/skills/` / `.claude/skills/` |
| Codex · Antigravity · Gemini · OpenCode | `~/.agents/skills/` / `.agents/skills/` |
| Cursor | (프로젝트 스코프만) `.cursor/skills/` |
| 스킬 미지원 런타임 | 스킬이 프로젝트에 심는 `AGENTS.md` 섹션 + `docs/ontology/` 문서로 동작 |

**APM 사용자**: 이 repo는 APM 패키지이기도 하다.

```sh
apm install -g <이 repo의 로컬 경로 또는 git ref> -t claude,codex,gemini,opencode,agent-skills
```

## 갱신

- copy 설치: `git pull` 후 `./install.sh` 재실행.
- `--link` 설치: `git pull`만으로 갱신된다.

## 새 스킬 추가 규약

1. `.apm/skills/<이름>/SKILL.md` 생성. frontmatter `name`, `description`(트리거 패턴 명시) 필수.
2. **경로 이식성**: 홈 디렉토리·특정 머신의 절대 경로를 쓰지 않는다. 스킬이 참조하는 스크립트·정본·자산은 스킬 디렉토리 내부(`scripts/`, `references/`, `toolkit/` 등)에 두고 "이 스킬 디렉토리 기준" 상대 참조로 서술한다 — 설치 스코프·런타임마다 배포 위치가 다르기 때문이다.
3. **runtime 중립성**: 특정 런타임 전용 도구(예: Claude 전용 Task/Workflow)를 절차의 필수 단계로 넣지 않는다. 필요하면 "가능한 runtime에서는 …" 조건부로 서술한다.
4. 절차형 스킬은 Rationalizations(스킵 핑계+반박) / Red Flags / Verification 섹션을 포함한다 (`traceability-init/SKILL.md`가 예시).
5. 이름 충돌 확인: 기존 스킬 목록(`./install.sh --list`)과 각 런타임의 slash command.

## 라이선스

[Apache License 2.0](LICENSE) — Copyright 2026 Loplat Inc. 툴킷을 다른 프로젝트에 vendoring할 때는 라이선스·저작권 고지를 유지하면 된다(NOTICE 참조).

## 툴킷 스냅샷 갱신 (traceability)

traceability 툴킷의 upstream 개발·테스트는 참조 구현 repo(`tools/traceability/`, 테스트 포함)에서 진행한다. 릴리즈 시:

```sh
rsync -a --delete --exclude '__pycache__' --exclude 'tests' --exclude 'conftest.py' \
  <upstream-repo>/tools/traceability/ .apm/skills/traceability-init/toolkit/
```

갱신 후 이 repo에 태그를 남기고, vendoring된 프로젝트는 `trace-config.yml`의 `toolkit.vendored_at`으로 drift를 추적한다.
