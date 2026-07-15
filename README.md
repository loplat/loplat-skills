# loplat-skills

Reusable, cross-runtime AI agent skills. Built on the [SKILL.md open standard](https://cursor.com/docs/skills), so they work in Claude Code, Codex CLI, Antigravity (agy), Gemini CLI, Cursor, and others regardless of runtime.

## Skills

| Skill | Purpose |
|---|---|
| `traceability-init` | Analyze a project and bootstrap a traceability ontology (docs/ontology + tools/traceability). Ships the config-driven toolkit; resource-agnostic via an agent-authored `ontology.yml`. |
| `traceability-check` | Run the consistency hard gate on an ontology-configured repo (build_index → verify → report). |

## What it is good for — and what it is not

We A/B-tested this (80 headless agent sessions, two rounds, identical tasks with and without the ontology) before recommending it. Honest results:

**Proven value**

- **Docs↔code drift gate.** Rename a function that implements a recorded decision and `verify` fails the commit with a `broken_reference` — deterministically, with no model in the loop. Prose documentation silently rots; the graph does not. Running in production as a pre-commit/CI gate on a 3,600-node repo.
- **Multi-repo dependency map (umbrella mode).** Cross-repo facts — which services pin which version of a shared package, who consumes a Pub/Sub topic — appear in no single repo's docs and are invisible to grep. Building the map for one 17-repo family surfaced a version-pin mismatch and a hardcoded credential.

**Measured non-value**

- **Not an agent search/efficiency booster.** On a well-documented repo, agents with the graph answered retrieval questions no more accurately (recall tied at 1.0) and no cheaper (cost within noise) than agents using grep. Good prose docs already cross-reference well inside one repo. Do not adopt this expecting faster agents.

**Adopt when**: the repo has decision/requirement docs (ADR/PRD culture) and doc-code drift is costly — multi-platform, regulated, or long-lived services — or you need a service-family umbrella map.

**Skip when**: the repo has no documentation assets (`traceability-init` returns `not-ready` by design rather than scaffolding empty docs), or nobody will curate `ontology.yml` — verification is free, but growing the graph is not.

## Install

**You decide the install scope.** Install a project-specific skill at project scope; install a skill you want everywhere at user scope.

```sh
git clone <this repo> && cd loplat-skills

# User scope (~/.claude/skills, ~/.agents/skills)
./install.sh --user

# Project scope (<dir>/.claude/skills, <dir>/.agents/skills, <dir>/.cursor/skills)
./install.sh --project ~/dev-workspace/my-service/main

# A single skill / symlink mode (updates on git pull alone) / preview
./install.sh --user --link traceability-init traceability-check
./install.sh --project . --dry-run
./install.sh --list
```

| Runtime | Read location (user / project) |
|---|---|
| Claude Code | `~/.claude/skills/` / `.claude/skills/` |
| Codex · Antigravity · Gemini · OpenCode | `~/.agents/skills/` / `.agents/skills/` |
| Cursor | (project scope only) `.cursor/skills/` |
| Skill-less runtimes | via the `AGENTS.md` section and `docs/ontology/` docs the skill plants in the project |

**APM users**: this repo is also an APM package.

```sh
apm install -g <local path or git ref of this repo> -t claude,codex,gemini,opencode,agent-skills
```

## Updating

- Copy install: `git pull`, then re-run `./install.sh`.
- `--link` install: updates on `git pull` alone.

## Conventions for adding a skill

1. Create `.apm/skills/<name>/SKILL.md`. `name` and `description` (with trigger patterns) are required in the frontmatter.
2. **Path portability**: never use a home directory or machine-specific absolute path. Keep the scripts/specs/assets a skill references inside the skill directory (`scripts/`, `references/`, `toolkit/`, …) and refer to them relative to "this skill directory" — install scope and runtime change the deployed location.
3. **Runtime neutrality**: do not make a runtime-specific tool (e.g. Claude-only Task/Workflow) a required step. If needed, phrase it conditionally ("on runtimes that support …").
4. Procedural skills should include Rationalizations / Red Flags / Verification sections (`traceability-init/SKILL.md` is an example).
5. Check for name collisions against the existing skills (`./install.sh --list`) and each runtime's slash commands.

## Refreshing the toolkit snapshot (traceability)

The traceability toolkit's upstream development and tests happen in the reference-implementation repo (`tools/traceability/`, tests included). At release time:

```sh
rsync -a --delete --exclude '__pycache__' --exclude 'tests' --exclude 'conftest.py' \
  <upstream-repo>/tools/traceability/ .apm/skills/traceability-init/toolkit/
```

After refreshing, tag this repo; vendoring projects track drift via `trace-config.yml`'s `toolkit.vendored_at`.

## License

[Apache License 2.0](LICENSE) — Copyright 2026 Loplat Inc. When vendoring the toolkit into another project, keep the license and copyright notice (see NOTICE).
