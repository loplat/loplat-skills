# traceability — Trace Index & Verification Tool

`tools/traceability/` is a Python toolset that statically verifies the links between PRD, ADR, OpenAPI, and sequence diagrams.

## trace-config.yml (project adaptation)

The extraction target paths, vocabulary, and active extractors are configured in `docs/ontology/trace-config.yml`. **If the file does not exist, the tool falls back to the reference implementation defaults**, so existing behavior is unaffected. When another project vendors this toolkit, don't modify the code — just write this config.

```yaml
version: 1                      # supported version (currently 1). exit 2 on mismatch
paths:                          # unspecified keys fall back to defaults
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
  ios_test_dirs: [ios/App/AppUnitTests, ios/App/AppUITests]
  ios_ui_test_dir: ios/App/AppUITests/
  ontology_dir: docs/ontology
priority:
  must: Must                    # vocabulary verify.py uses to judge mandatory Requirements
extractors:                     # only extractors explicitly set to false are disabled (unspecified = active)
  ios: false
  ios_tests: false
```

- `paths`: the paths each extractor scans. An extractor whose source files don't exist is silently skipped, so if the document format matches, you can reuse it just by changing the path.
- `extractors`: extractors set to `false` are not run by `build_index`, and the list of disabled extractors is printed to stdout. If the document format itself differs, either delete the corresponding extractor file or add a new one.
- If a config file exists but pyyaml is missing, the YAML is malformed, or the version doesn't match, the tool exits with **exit 2 (fail-closed)**.

## Dependencies

**pyyaml 6.0.2 or later is required** when `manual-edges.yml` or `seed-traces.yml` exists.

```sh
pip install "pyyaml==6.0.2"
```

Running build/verify while a YAML file exists but pyyaml is not installed **exits with 2 (environment error)**, fail-closed. This is by design, so an incomplete verification is never mistaken for a success.

The backend uv environment already includes pyyaml, so no separate installation is needed there.

## Local commands

```sh
# 0. Confirm pyyaml is installed (required in this repo since manual-edges.yml / seed-traces.yml exist)
pip install "pyyaml==6.0.2"

# 1. Build the index — generates scratch/traceability/index.json
python3 tools/traceability/build_index.py

# 2. Run verification — generates scratch/traceability/ci-summary.json
python3 tools/traceability/verify.py
# exit 0: OK (only warn/coverage items may be present)
# exit 1: deterministic error — subject to the CI hard gate
# exit 2: environment/tooling error (pyyaml not installed, index missing, etc.) — verification itself is incomplete, so fail-closed

# 3. Generate a report (optional)
python3 tools/traceability/report.py

# 4. self-test (detection demo using an in-memory broken case)
python3 tools/traceability/verify.py --selftest

# 5. lint/format check (requires the backend uv environment)
cd backend && uv run ruff check ../tools/traceability
cd backend && uv run ruff format --check ../tools/traceability

# 6. Quick verification limited to changed files (if the --changed flag is implemented)
# python3 tools/traceability/build_index.py --changed <file>
```

## Exit code meanings

| exit code | meaning | CI behavior |
|-----------|------|---------|
| `0` | no deterministic errors (clean) | CI passes |
| `1` | one or more deterministic errors (content defect) | CI hard gate blocks, pre-commit blocks the commit |
| `2` | environment/tooling error (pyyaml not installed, index missing, JSON parse failure, etc.) | CI (`set -ceu`) loud fail; pre-commit fails open |

**A missing pyyaml is an environment problem** → exit 2. If verification is skipped while a YAML file exists, the index/verification becomes incomplete, so it must never be mistaken for success.

## CI gate behavior

The `traceability-verify` step in `cloudbuild-ci.yaml` (PR CI only):

1. `pip install pyyaml` — for parsing manual-edges.yml (does not access the production DB or Secret Manager)
2. `python tools/traceability/build_index.py` — build the index
3. `python tools/traceability/verify.py` — hard gate (deterministic error → exit 1 → CI fail)
4. `python tools/traceability/tests/run_tests.py` — fixture regression tests

There is no traceability step in CD (`cloudbuild.yaml`). The gate is scoped to PR CI only so that doc issues never block a deployment.

## Lightweight vs. full mode

| Mode | Description | When it runs |
|------|------|-----------|
| **lightweight** (backend-only) | Automatically triggered by pre-commit on `backend/app/` changes | Quick check of API endpoint/sequence consistency |
| **full** (docs/spec) | Triggered by pre-commit and the full CI run on `docs/`, `tools/traceability/` changes | Full verification of PRD, ADR, OpenAPI, manual-edges |

To run full mode manually and locally, run build_index followed by verify.

## hard-fail vs. warn policy

| Level | Kind | CI behavior |
|------|------|------|
| **hard fail** (exit 1) | `broken_reference`, `malformed_manual_edge`, `secret_in_index` | Blocks PR CI |
| **warn** (exit 0) | `orphan`, `superseded_in_use`, `api_unlinked`, `coverage_gap`, `test_coverage_drift` | CI passes, recorded in the report |

See `scratch/traceability/ci-summary.json` for the detailed results.

## pre-commit behavior

`scripts/git-hooks/pre-commit` includes the traceability guard.

Enable it (once per clone):
```sh
git config core.hooksPath scripts/git-hooks
```

**guard condition**: runs only when staged files include `docs/`, `ios/`, `design/`, `tools/traceability/`, or `backend/app/`.

**fail-open**: if python3 is not installed or build/verify raises an exception, it just prints a notice and allows the commit. The commit is blocked only when verify reports `exit 1` (a deterministic error).

## Running tests without pytest

Even if pytest isn't installed on the system python3, you can run the fixture regression tests directly with the command below.

```sh
python3 tools/traceability/tests/run_tests.py
```

exit 0: all passed / exit 1: one or more failures.

In a pytest environment, you can also run:
```sh
cd <repo-root>
python -m pytest tools/traceability/tests/test_verify.py -v
```

## File structure

```
tools/traceability/
├── build_index.py        index build entry point
├── verify.py             verification entry point (hard gate + warn)
├── config.py             trace-config.yml loader (defaults if absent, fail-closed if malformed)
├── model.py              TraceNode / TraceEdge / TraceFinding / TraceIndex
├── report.py             report generation
├── conftest.py           pytest sys.path bootstrap
├── extractors/           per-source extractors
└── tests/
    ├── fixtures/         4 broken/clean fixture files
    ├── test_verify.py    pytest-style test functions
    ├── test_config.py    config loader tests
    └── run_tests.py      plain python3 runner for pytest-less environments
```
