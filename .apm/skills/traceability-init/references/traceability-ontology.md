# Traceability Ontology Spec (shared reference)

The ontology spec shared by the `traceability-init` and `traceability-check` skills. Do not duplicate this content into the skill bodies.

## Canonical toolkit

- **Location**: the `toolkit/` directory inside the `traceability-init` skill. It ships with the skill, so it is always beside the skill regardless of install scope (user/project).
- To adopt it in another project, vendor (copy) this `toolkit/` into the target repo's `tools/traceability/`, then write `trace-config.yml` (see "Vendoring procedure").
- **Upstream**: toolkit development and tests happen in the reference-implementation repo (`tools/traceability/`, tests included); the snapshot here is refreshed at release time (see the loplat-skills README, "Refreshing the toolkit snapshot").

## Pipeline

```
build_index.py  # auto-discovers extractors (@register + pkgutil) -> scratch/traceability/index.json
verify.py       # deterministic hard gate -> ci-summary.json (exit 0/1/2)
report.py       # seed-trace-centered report -> report.md / report.html (optional)
```

Exit codes: `0` pass / `1` deterministic error (blocks CI) / `2` tool/environment error (never skip; must recover).

## Two layers: extraction and verification

The system separates **extraction** (documents/code → nodes/edges in `index.json`) from **verification** (deterministic checks over the index). Verification is already resource-agnostic — it reads only node `type`/`id`/`attrs` and edge `type`, not file formats. The only format bottleneck is extraction, which is addressed by two channels:

1. **Deterministic extractors** — parse fixed formats automatically (free, fast). Format-specific.
2. **Agent-authored ontology** (`ontology.yml`) — an agent reads arbitrary resources and records nodes/edges explicitly. Format-independent. Committed to the repo so CI stays deterministic.

## Node type catalog

**Core (applicable to most projects)**

| Type | Example canonical id | Source |
|---|---|---|
| `Requirement` | `REQ-012` | PRD tables/lists |
| `ADR` | `ADR-0004` (4-digit) | `docs/adr/` |
| `ApiOperation` | OpenAPI `operationId` | OpenAPI spec |
| `SpecSection` / `SequenceDiagram` | doc anchor | `docs/specs/` |
| `CodeSymbol` | `file:class` | code AST scan |
| `TestCase` | pytest node id, etc. | test markers |

**Platform extensions (only when that platform exists)**: `UseCase`/`UseCaseCategory` (`UC-{N}-{C|M|N}-{NN}`), `DesignScreen`, `PlatformRequirement` (`IOS-REQ-NNN`), `IOSADR`/`IOSDecision`, `ComplianceControl`, `OperationRunbook`.

**11 edge types**: `refines`, `references`, `implements`, `validates`, `exercises`, `step_calls`, `routed_to`, `governed_by`, `supersedes`, `conflicts_with`, `depends_on`. `origin` is `auto` (extracted), `manual` (`manual-edges.yml`), or `agent` (`ontology.yml`).

**Marker convention (test → requirement edges)**: pytest `@pytest.mark.uc("UC-…")`, Kotlin/Swift comment `// UC-…`. Per-project marker syntax is documented in that repo's `docs/ontology/conventions.md`.

## Profiles

Choose the adoption scope from the inventory scan. Do not force adoption — no assets, no ontology.

| Profile | Condition | Active node types |
|---|---|---|
| `docs-only` | only ADR or PRD exists | Requirement, ADR |
| `backend-api` | + OpenAPI, backend tests | + ApiOperation, CodeSymbol, TestCase |
| `full-stack` | + mobile/web/design assets | + UseCase, DesignScreen, Platform* |
| `not-ready` | no assets at all | Defer. Propose prerequisites (ADR/PRD scheme) first |

Format does not affect the profile — a project whose ADRs use a non-standard heading style is still `docs-only`, and its ADRs are captured via `ontology.yml`.

## trace-config.yml schema

`docs/ontology/trace-config.yml`. **The toolkit reads this file directly** (config-driven). Without it, the toolkit runs on reference-implementation defaults, so a vendoring project **writes this config instead of editing code**. If the file exists but PyYAML is missing, the YAML is malformed, or `version` mismatches, the tools exit 2 (fail-closed).

```yaml
version: 1                      # supported version (currently 1); mismatch -> exit 2
paths:                          # unspecified keys fall back to defaults
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
  ontology_source: docs/ontology/ontology.yml   # agent-authored nodes/edges
priority:
  must: Must                    # verify.py's must-have (Requirement) vocabulary
extractors:                     # only extractors set to false are disabled (unset = enabled)
  sequence: false
  usecase: false
  design: false
  ios: false
  ios_tests: false
  android_tests: false
```

## ontology.yml schema (agent-authored, resource-agnostic channel)

`docs/ontology/ontology.yml`, read by the `agent_ontology` extractor. An agent reads any resource the deterministic extractors cannot parse and records nodes/edges here explicitly. Committed to the repo; verification treats these exactly like auto-extracted nodes/edges.

```yaml
version: 1
nodes:
  - id: ADR-001                         # canonical id (see conventions.md)
    type: ADR                           # node type (see catalog above)
    source: docs/adr/ADR-001-x.md       # repo-relative origin file
    loc: L1                             # optional location hint
    title: "GAE to GKE migration"       # optional
    attrs: {status: Accepted}           # optional
edges:
  - type: implements                    # one of the 11 edge types
    source: campaign_engine/search.py:match   # a CodeSymbol id from another extractor
    target: ADR-015
    evidence: "rationale in ADR-015"    # optional; defaults to the ontology.yml path
```

Rules:
- Edge `source`/`target` may reference a node declared here or produced by any other extractor. Unresolved ids surface as `broken_reference` in verify — this is the built-in correctness check on agent authoring.
- Injected edges get `origin: agent`. Node/edge types must come from the catalog and the 11 allowed edge types.
- Missing required fields, unsupported `version`, or malformed YAML → the build fails closed (exit 2), consistent with trace-config/manual-edges.
- Use this channel for what deterministic extractors miss, not as a parallel copy of what they already capture.

## Vendoring procedure (config-driven)

Copy the `traceability-init` skill's `toolkit/` into the target repo's `tools/traceability/`, then:

1. **Write `docs/ontology/trace-config.yml`** — fill `paths` with the target project's real paths. Unspecified keys fall back to defaults, so only list what differs.
2. **Disable unused document types** under `extractors: {name: false}`. A missing source is auto-skipped, but explicit `false` documents intent.
3. **Adjust `priority.must`** if the PRD vocabulary differs (e.g. `P0`).
4. **Only if the document format itself differs** (REQ table structure, ADR id scheme, …), edit that extractor's regex or add a new extractor. Extractors are `@register` + auto-discovered, so a new type is one file. **If only the path differs and the format matches, no code change is needed.**
5. **Author `ontology.yml`** for anything the extractors cannot parse (non-standard ADRs, Markdown API specs, prose, code comments). This is the resource-agnostic channel.

Verify: after writing config and ontology, run `build_index.py` → `verify.py` to exit 0. The config cannot drift from code constants because the code reads the config.

## Umbrella (multi-repo) mode

For a directory bundling several repos of one service family. Validated on a 17-repo family (frontends, backend, mobile, shared type package, 7 campaign workers, 4 batch functions): 29 nodes / 26 cross-repo edges, verify exit 0, authored entirely through `ontology.yml`.

**Why it pays off here specifically**: an A/B eval on a single well-documented repo showed no retrieval gain — prose docs already cross-reference well inside one repo. Across repos they don't: no repo's docs cite a neighbor's `package.json` pin or a Pub/Sub topic's consumer. Cross-repo edges are information only the graph holds.

Layout and config:

- Umbrella root = repo root. `tools/traceability/` and `docs/ontology/` sit at the top; node `source` paths point into subrepos (`{repo}/{worktree}/...`).
- trace-config.yml disables **all** deterministic extractors (single-string path keys cannot span repos; formats vary per repo). `agent_ontology` stays on by default.

ontology.yml conventions (extends the base schema):

| Convention | Rule |
|---|---|
| Id namespacing | Prefix every id with a repo tag: `xb:ADR-001`, `dmp:ADR-001`. ADR numbering restarts per repo; keep-first dedup silently drops collisions. |
| Node types | `Service` (one per repo), `Resource` (shared topics/tables/type packages), `ext:` ids for services outside the umbrella. |
| Canonical worktree | Pin exactly one worktree per repo (main/master/develop) in `source` paths; exclude other worktrees and trash/backup copies. |
| Edge semantics | Stay within the allowed edge types; encode pub/sub and schema sharing as `routed_to` (producer→topic) and `depends_on` (consumer→topic/table), with the real semantics in `evidence`. |
| Evidence strength | If a binding lives in deployment config rather than code (e.g. Pub/Sub subscriptions), say so in `evidence` instead of implying a code-level link. |

Operations: the umbrella is usually not a git repo, so there is no commit-time CI gate. Run `build_index && verify` as a scheduled job — drift observation (a repo moved, a doc deleted, a dependency pin changed) instead of a hard gate. Making the umbrella a meta-repo restores the gate model.

## Common safety rules

- Fix defects at the source — documents, markers, manual/agent edges, extractor rules — never in the generated index.
- Never leave coordinates, phone numbers, tokens, secrets, raw operational logs, or personal data in the index/report/scratch.
- `scratch/traceability/` is an output directory. Confirm it is gitignored.
