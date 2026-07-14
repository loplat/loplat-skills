---
name: traceability-check
description: Run the traceability consistency hard gate on a repo that already has an ontology (docs/ontology + tools/traceability). Use after changing a PRD, ADR, OpenAPI spec, sequence diagram, ontology doc, or tools/traceability code, or when the user asks to "check consistency", "verify traceability", "validate consistency", "verify after an ontology change", or "generate the trace index/report". Catches deterministic defects like a canonical id cited by a PRD that does not exist, a malformed manual edge, or a sequence-vs-API mismatch. Directs unconfigured repos to traceability-init.
---

# Traceability Check (consistency hard gate)

This skill is an **execution wrapper**. The single source of policy/schema/convention lives in the target repo's own docs; the procedure is not duplicated here.
Shared spec (node/edge types, exit codes, safety rules): `references/traceability-ontology.md` in the co-installed `traceability-init` skill (same skills root).

## Precondition (repo detection)

Relative to the repo root (`git rev-parse --show-toplevel`):

- Both `tools/traceability/` and `docs/ontology/` must exist.
- **If either is missing, do not proceed.** The repo has no ontology yet — direct the user to the `traceability-init` skill to set one up first.

## Source of truth (in the target repo)

- Full procedure: the repo's `AGENTS.md` consistency section, `docs/ontology/agent-consistency-skill.md` (if present)
- Schema/convention: `docs/ontology/schema.md`, `docs/ontology/conventions.md`
- Adaptation state: `docs/ontology/trace-config.yml` (active extractors and paths — the toolkit reads this directly)
- Tool README: `tools/traceability/README.md`

## When to use

- Before committing/PR-ing after changes to documents covered by `trace-config.yml`'s `paths` (PRD, ADR, specs, API spec), `docs/ontology/**`, or `tools/traceability/**`.
- When the user asks to check consistency / traceability.
- When the user asks to generate the trace index, find missing links, build the ontology graph, or produce a report.

## Run

From the repo root, in order:

```sh
python3 tools/traceability/build_index.py
python3 tools/traceability/verify.py
python3 tools/traceability/report.py
```

Outputs:

- `scratch/traceability/index.json`
- `scratch/traceability/ci-summary.json`
- `scratch/traceability/report.md`
- `scratch/traceability/report.html`

For verification only, `build_index.py` → `verify.py` is enough. Run `report.py` only when a human-readable report is needed.

Changed-file impact analysis:

```sh
python3 tools/traceability/report.py --changed <path> [<path> ...]
```

## Python environment fallback

If the root Python lacks `PyYAML`, the tools may fail closed (exit 2). Do not silently skip — run the same tools via the repo's Python convention (uv/poetry/venv). For example, a repo whose backend uses `uv`:

```sh
cd backend
uv run python ../tools/traceability/build_index.py
uv run python ../tools/traceability/verify.py
uv run python ../tools/traceability/report.py
```

## verify.py exit codes

| exit | meaning | action |
|---|---|---|
| `0` | no deterministic errors | Pass. `semantic_candidate` and `coverage` are for human/agent review, not a hard gate. |
| `1` | one or more deterministic errors | CI-blocking defect. Read `ci-summary.json`'s `categories.deterministic.errors` and fix the source doc. |
| `2` | tool/environment error | Dependency, corrupt trace-config, or version mismatch. Do not skip — recover or use the fallback above. |

## Dependencies

- If `docs/ontology/` contains `trace-config.yml`, `ontology.yml`, `manual-edges.yml`, or `seed-traces.yml`, `PyYAML 6.0.2+` is required.
- When not installed, build_index/verify must exit 2.

## Extra gate when changing tools/traceability code

Lint with the repo's convention and run the tool tests. For example, a repo with a `uv` backend:

```sh
cd backend
uv run ruff check ../tools/traceability
uv run ruff format --check ../tools/traceability
cd ..
python3 tools/traceability/tests/run_tests.py
```

If `uv run` changes a lock file, revert it unless the dependency change was intended.

## Evaluation boundary

- The team-wide gate is the plain python3 tools and the CI traceability-verify step.
- Vector-indexing add-ons (SocratiCode/Qdrant/Ollama, etc.) are not the team gate — treat them as optional local UX enrichment. This skill and CI must work without them.

## Reporting rules

- On deterministic errors, report each defect's `kind`, `subject`, and `location`.
- Fix defects at the source document, marker, manual/agent edge, or extractor rule — never in the generated index.
- Never leave coordinates, phone numbers, tokens, secrets, raw operational logs, or personal data in the index/report/scratch.
