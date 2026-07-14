---
name: traceability-init
description: Analyze a software project and bootstrap a traceability ontology (docs/ontology + tools/traceability). Use when the user asks to "set up traceability", "bootstrap the ontology", "apply traceability to this project", "build an ontology from this repo", or when traceability-check reports an unconfigured repo. Runs inventory scan, profile decision, ontology scaffolding, toolkit vendoring, agent-driven ontology authoring, and a verify loop until it passes.
---

# Traceability Init (project analysis + ontology bootstrap)

Bootstraps the traceability consistency gate for a project that has *any* documentation or code assets — regardless of format. Deterministic extractors handle standard formats (ADR frontmatter, OpenAPI JSON, Mermaid sequences); everything they cannot parse — ADRs written as `# ADR-001:` headings, a Markdown API spec, a prose design note, a decision recorded only in a code comment — is captured by an agent into a committed `ontology.yml`. This is what makes the skill resource-agnostic.

This skill's directory ships the execution assets alongside this SKILL.md:

- `references/traceability-ontology.md` — the spec of record. **Read it before starting.** Node/edge types, profiles, the trace-config.yml and ontology.yml schemas, and the vendoring procedure all live there.
- `scripts/inventory.py` — the step-1 inventory scanner.
- `toolkit/` — the config-driven toolkit to vendor (includes the `agent_ontology` extractor).

## When to use

- Introducing traceability to a new or existing project.
- When `traceability-check` reports that `tools/traceability/` or `docs/ontology/` is missing.
- **Do not use** for: verifying an already-configured repo (use `traceability-check`), or a project with no documentation and no code worth tracing (step 2 returns `not-ready`).

## Procedure

### 1. Inventory scan

```sh
python3 <this skill dir>/scripts/inventory.py [repo_root]
```

Repo root is resolved via `git rev-parse` when the argument is omitted (worktree-safe). The scan does not assume a directory layout — it finds ADR/specs/ontology directories and PRD/OpenAPI files by tree-walking (depth 4) on names. Read the `suggested_profile` from the JSON output.

### 2. Profile decision and user confirmation

- `already-initialized` → stop, hand off to `traceability-check`.
- `not-ready` → returned **only when there are neither decision/requirement docs nor meaningful code**. Report why and suggest prerequisites; do not mass-generate documents without a request.
- `docs-only` / `backend-api` / `full-stack` → present the node types and extractors to activate, and confirm. **Format does not gate adoption**: even if the ADRs/API specs are in a non-standard format, proceed — the agent step (5) captures them. Ask here whether the scan missed any assets (unusual names/extensions, decisions living in code comments).

### 3. Scaffold docs/ontology/

Per the reference spec, create:

- `trace-config.yml` — filled with the paths actually detected in step 1. The toolkit reads this directly (config-driven).
- `schema.md` — a subset describing only the active node/edge types, in the project's vocabulary.
- `conventions.md` — canonical id regexes, marker syntax for this project's languages, file-placement rules.
- `manual-edges.yml`, `seed-traces.yml`, `api-exclusions.yml` — empty stubs (one format example each, commented).
- `ontology.yml` — the agent-authored node/edge file (populated in step 5).

### 4. Vendor the toolkit + write config

The toolkit is config-driven. **Do not edit path constants in code — write `trace-config.yml` only** (see the reference's "vendoring procedure").

1. Copy this skill's `toolkit/` to the target repo's `tools/traceability/` (exclude `__pycache__`).
2. Fill `trace-config.yml`'s `paths` with the target project's real paths. Only keys that differ from the defaults need to be listed; unspecified keys fall back to defaults.
3. Set unused document types to `false` under `extractors` (a missing source is auto-skipped, but an explicit `false` documents intent). Adjust `priority.must` if the PRD uses a different vocabulary.
4. **Only when the document format itself differs** (e.g. a different REQ table structure) edit that extractor's regex. If only the path differs and the format matches, no code change is needed.

### 5. Author ontology.yml (agent semantic extraction) — the resource-agnostic step

This is where format-independence happens. For every asset the deterministic extractors cannot parse, read it and record the nodes and edges explicitly.

1. Run `python3 tools/traceability/build_index.py` once and inspect the node counts per extractor. Any decision/requirement/API doc that exists but yields 0 nodes is a gap to fill here.
2. Read those assets directly — ADRs in any heading style, Markdown/prose API specs, design notes, even decisions embedded in code comments — and decide which nodes (Requirement, ADR, ApiOperation, …) exist and how they relate to already-extracted `CodeSymbol`/`TestCase` nodes.
3. Record them in `docs/ontology/ontology.yml` using the schema in the reference. Edge `source`/`target` must resolve to real node ids — use the exact ids `build_index` produced for code symbols (`path/file.py:symbol`) and tests. Unresolved ids surface as `broken_reference` in verify, which is the built-in correctness check on your authoring.
4. Keep `ontology.yml` committed. Extraction is done once by the agent when authoring a change; CI re-verifies the committed graph deterministically, with no model in the loop.

Do not duplicate what a deterministic extractor already captures — `ontology.yml` is for what they miss, not a parallel copy.

### 6. Assign canonical ids (minimally invasive)

- If ADR files lack a canonical id, add one. If an existing numbering scheme is present, **keep it** and match `conventions.md`'s regex to it — fit the convention to the docs, not the docs to the convention. (You may instead reference the ADR from `ontology.yml` without touching the file.)
- If the PRD has no REQ-id table, add an id column to the existing list. Do not rewrite content.
- Do not add test markers here (coverage is not a hard gate). Define the syntax in `conventions.md` so later development can adopt it incrementally.

### 7. Verify loop (max 3 iterations)

```sh
python3 tools/traceability/build_index.py && python3 tools/traceability/verify.py
```

- Fix defects until exit 0. Fix at the source (docs, trace-config, manual/agent edges), never in the generated index.
- **After 3 iterations, stop** and escalate the remaining defects (`kind`, `subject`, `location`). Exceeding the loop signals the assets are immature for the profile — propose lowering it (`backend-api` → `docs-only`).
- exit 2 (environment error) is not a defect to fix — resolve the dependency (PyYAML, etc.) via the repo's Python convention (uv/poetry/venv).

### 8. Register in repo instructions

- Add a consistency-check section to **every instruction file the team's runtimes actually load**: `AGENTS.md` (Codex, Antigravity, Gemini, Cursor) **and** `CLAUDE.md` (Claude Code does **not** auto-load AGENTS.md — a section only there is invisible to it; measured in an A/B eval where agents never discovered the graph until CLAUDE.md carried the pointer). Keep one file canonical and have the other reference it. Include: the three commands, exit-code meanings, a pointer to `docs/ontology/`, and where the prebuilt graph lives (`scratch/traceability/index.json`) with a query example — agents should query it with a script, not read the raw JSON wholesale.
- Ensure `scratch/traceability/` is gitignored.
- Propose CI registration (a `verify.py` step in cloudbuild/GitHub Actions) but only apply it with user approval.

## Multi-repo (umbrella) mode

Use when several repos of one service family are gathered under a single directory and the questions cross repo boundaries ("which services consume this shared type package?", "what breaks if this topic changes?"). Cross-repo references rarely appear in any single repo's prose docs, and grep cannot see the neighboring repo — this is where the graph has unique value that single-repo mode measurably lacks.

Differences from the single-repo procedure (full conventions: reference's "Umbrella (multi-repo) mode" section):

1. The umbrella directory is the repo root: place `tools/traceability/` and `docs/ontology/` at its top. It usually is not a git repo — verify runs as a scheduled observation job (cron: `build_index && verify`) rather than a CI gate, unless you make it a meta-repo.
2. **Disable all deterministic extractors** in trace-config.yml. Per-repo layouts and ADR formats vary, and single-string path keys (`adr_dir`, `openapi`) cannot span repos — the graph is authored entirely via `ontology.yml`.
3. **Namespace every id with a repo prefix** (`xb:ADR-001`, `dmp:ADR-001`): ADR numbering restarts per repo, and the index's keep-first policy silently drops colliding ids.
4. Model repos as `Service` nodes and shared integration points (Pub/Sub topics, shared DB tables, type packages) as `Resource` nodes; add `ext:` placeholder Service nodes for dependencies outside the umbrella.
5. **Pin one canonical worktree per repo** (`{repo}/main/` or `master`/`develop`) in node sources; never index multiple worktrees of the same repo (duplicate nodes) or trash/backup copies.
6. State evidence strength honestly: an edge whose binding lives in infra (e.g. a Pub/Sub subscription defined in deployment config, not code) must say so in `evidence`.

## Rationalizations

| Excuse | Rebuttal |
|---|---|
| "The ADRs aren't in the standard format, so this project is not-ready." | Format never gates adoption. not-ready means no assets at all. Non-standard assets are exactly what step 5 exists for. |
| "I'll edit the extractor code to match this project's format." | The toolkit is config-driven; paths go in trace-config.yml. For a genuinely different *format*, prefer capturing nodes in ontology.yml over rewriting regex. |
| "build_index produced nodes, so we're done." | Nodes without edges trace nothing and verify passes vacuously. Check edge count; fill relationships in ontology.yml. |
| "verify exit 2 but the ontology docs got created, so I'll report done." | Not done until exit 0 is measured. Recover the environment error. |
| "The existing ADR numbers differ from the convention, so I'll rename them all." | Fit the convention to the docs. Mass rename is a destructive, link-breaking change. |

## Red Flags

- Editing `scratch/traceability/index.json` to fix a defect — the generated index is an output; go back to the source.
- A 4th verify iteration without re-examining the profile.
- Editing extractor code to change a path — paths belong in trace-config.yml.
- A repo full of ADRs/API docs ends with **0 edges** — the agent step (5) was skipped; the graph traces nothing.

## Verification

- [ ] `python3 tools/traceability/build_index.py && python3 tools/traceability/verify.py` measured at exit 0
- [ ] `docs/ontology/` has the expected files (trace-config.yml, schema.md, conventions.md, ontology.yml, the three yml stubs)
- [ ] `trace-config.yml`'s `paths` point at the target project's real paths (cross-check via build_index node counts — 0 means a path is wrong)
- [ ] Edge count > 0, and decision/requirement/API nodes are present (not just code/test nodes)
- [ ] Did not edit extractor code constants (unless the document format genuinely differed)
- [ ] The repo instructions (AGENTS.md/CLAUDE.md) have a consistency-check section
- [ ] `scratch/traceability/` is gitignored
