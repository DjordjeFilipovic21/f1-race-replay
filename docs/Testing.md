# Testing

This project uses `pytest` for automated tests. The supported Python matrix is
3.11–3.13; run all Python commands with the project virtual environment
(`.venv/bin/python`).

## Install test dependencies

For local development, create and activate a virtual environment first:

    python3 -m venv .venv
    source .venv/bin/activate

Install the lightweight modern test dependencies and the separately packaged
canonical pipeline, consuming the committed pip-tools constraints artifact:

    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install --constraint pipeline/constraints.txt --constraint pipeline/test-constraints.txt pytest pytest-mock "jsonschema[format-nongpl]>=4.26,<5"

    .venv/bin/python -m pip install --constraint pipeline/constraints.txt --editable ./pipeline

The desktop application's dependencies remain isolated under
`legacy/requirements-dev.txt`.

Install them only when working on the desktop application, again consuming the
committed legacy constraints artifact:

    .venv/bin/python -m pip install --constraint legacy/constraints.txt -r legacy/requirements-dev.txt

## Regenerate dependency constraints

`pipeline/constraints.txt` and `legacy/constraints.txt` are generated with
pip-tools against the lowest supported matrix member (Python 3.11). Regenerate
them whenever the declared dependency ranges or the supported matrix changes:

    .venv/bin/python -m pip install --upgrade pip-tools
    .venv/bin/python -m piptools compile --resolver=backtracking \
      --output-file=pipeline/constraints.txt pipeline/pyproject.toml
    .venv/bin/python -m piptools compile --resolver=backtracking \
      --output-file=legacy/constraints.txt legacy/requirements-dev.txt

Commit the refreshed constraints files; they are constraints, not replacements
for `pipeline/pyproject.toml` or `legacy/requirements*.txt`.

## Python lint and type-check

Phase 3 adds minimal, scoped static validation for the pipeline package only.
The gate checks the explicit Phase 3 touched Python surface (the three changed
sources and five new integrity tests listed under "Commands" below), not the
whole `pipeline/` tree; untouched pipeline lint/type debt is intentionally
outside this Phase 3 gate. The supported Python matrix remains 3.11–3.13;
commands are deterministic and run from the repository root against `.venv/bin`.

### Tool selection (evidence-based)

The repository had **no existing lint or type-check configuration** before this
change: no `ruff.toml`, `pyrightconfig.json`, `mypy.ini`, `setup.cfg`,
`.flake8`, or `tox.ini` anywhere, and no lint/type-check step in CI. Two tools
were selected because they are the minimal pair that gives both a fast syntax
and import-sanity gate and a real type gate for the heavily annotated pipeline
package:

- **ruff** for linting, using ruff's default rule set (`E4`, `E7`, `E9`, `F`)
  pinned explicitly in `pipeline/pyproject.toml` so the gate is deterministic
  across ruff releases. The default set (pycodestyle errors + Pyflakes) was
  verified against the current pipeline sources: no bare `except:`, no
  `== None`/`== True` comparisons, no `import *`, and no lambda assignments.
- **pyright** for type-checking, via `pipeline/pyrightconfig.json` covering
  `pipeline/src` and `pipeline/tests` (with `extraPaths` pointing back at both
  `src` and `tests` so package and test modules resolve), `typeCheckingMode:
  "basic"` (a real type gate without strict-mode churn), and
  `reportMissingTypeStubs: "none"` so third-party libraries without stubs
  (polars, fastf1, boto3) do not fail the gate.

mypy was considered and rejected: the selected pair covers the need with less
tooling, and current external guidance (fetched 2026-08-09 into
`.tmp/external-context/ruff-phase3.md` and `pyright-phase3.md`) already
documents the exact scoped config and CLI flags for ruff and pyright.

**No new dev dependencies were added.** ruff and pyright are standalone
developer tools installed on demand, not pipeline runtime or test extras, so
`pipeline/constraints.txt` and `pipeline/test-constraints.txt` are unchanged
and were not regenerated. They are deliberately not added to the pip-tools
constraints artifacts: `pipeline/test-constraints.txt` mirrors
`legacy/constraints.txt` pins exactly (enforced by
`pipeline/tests/reproducibility/test_dependency_constraints.py`), and the
legacy desktop package is out of scope for this static-validation work.

**Legacy scope decision:** `legacy/` is out of scope for lint/type-check. The
legacy desktop application has its own dependency manifest
(`legacy/requirements-dev.txt`) and constraints artifact; no evidence requires
extending static validation there in Phase 3, and doing so would add churn to
an already-separate package.

### Install the lint/type-check tools

The tools are installed into the existing project virtual environment (not
declared as project dependencies):

    .venv/bin/python -m pip install ruff==0.16.2 pyright==1.1.411

### Commands (single source of truth)

Scope: the explicit Phase 3 touched Python surface only — the three changed
sources and the five new integrity tests listed below. Untouched pipeline
lint/type debt is intentionally outside this Phase 3 gate; widening the gate
back to the whole `pipeline/` tree is a separate follow-up. Run both from the
repository root:

    .venv/bin/python -m ruff check \
      pipeline/src/f1_replay_pipeline/delivery/browser/browser_delivery_orchestration.py \
      pipeline/src/f1_replay_pipeline/delivery/browser/browser_weather_sidecar.py \
      pipeline/src/f1_replay_pipeline/storage/generation_publication.py \
      pipeline/tests/delivery/browser/test_browser_weather_diagnostics.py \
      pipeline/tests/delivery/browser/test_browser_weather_delivery_diagnostics.py \
      pipeline/tests/storage/test_pointer_manifest_integrity.py \
      pipeline/tests/storage/test_recovery_lease_boundaries.py \
      pipeline/tests/storage/test_symlink_path_traversal_defenses.py

    PY_EXE="$(.venv/bin/python -c 'import sys; print(sys.executable)')"
    .venv/bin/python -m pyright -p pipeline/pyrightconfig.json --pythonpath "$PY_EXE" \
      pipeline/src/f1_replay_pipeline/delivery/browser/browser_delivery_orchestration.py \
      pipeline/src/f1_replay_pipeline/delivery/browser/browser_weather_sidecar.py \
      pipeline/src/f1_replay_pipeline/storage/generation_publication.py \
      pipeline/tests/delivery/browser/test_browser_weather_diagnostics.py \
      pipeline/tests/delivery/browser/test_browser_weather_delivery_diagnostics.py \
      pipeline/tests/storage/test_pointer_manifest_integrity.py \
      pipeline/tests/storage/test_recovery_lease_boundaries.py \
      pipeline/tests/storage/test_symlink_path_traversal_defenses.py

`ruff check` reads `[tool.ruff]` from `pipeline/pyproject.toml` (the closest
config for every file under `pipeline/`); the ruff rule set is unchanged.
`pyright -p` reads `pipeline/pyrightconfig.json` for settings, while the
explicit file arguments override the config's `include`, limiting analysis to
the Phase 3 touched surface. Both forms are literally module-based
(`python -m ruff` / `python -m pyright`) and pass the active interpreter to
pyright's `--pythonpath` as the value of `sys.executable` (`$PY_EXE` above), so
pyright resolves the interpreter's environment (pytest, polars, fastf1, boto3)
instead of guessing from `PATH`. Both commands must exit 0; the CI wiring in
`.github/workflows/tests.yml` (Phase 3) mirrors exactly this file list, the
pinned install (`ruff==0.16.2`, `pyright==1.1.411`), and this semantics, using
the matrix interpreter in place of `.venv/bin/python` for the `python -m`
invocations and the same `sys.executable`-derived `--pythonpath`.

## Run the test suite

Run all modern tests with:

    .venv/bin/python -m pytest

Run only the lightweight unit tests with:

    .venv/bin/python -m pytest pipeline/tests

Run the offline replay contract checks with:

    .venv/bin/python -m pytest tests/contracts/test_replay_contract.py

Run the lightweight pipeline suite with:

    .venv/bin/python -m pytest pipeline/tests

Run the complete lightweight CI-equivalent suite with:

    .venv/bin/python -m pytest tests/contracts pipeline/tests

Run the legacy desktop tests separately from the repository root with:

    .venv/bin/python -m pytest legacy/tests

Alternatively, change into `legacy/` and run `../.venv/bin/python -m pytest`.

Run the focused browser-sidecar web checks from the repository root with:

    cd web
    npm run typecheck
    npm run test -- --run tests/data/replay/sidecar-loading.test.ts tests/features/replay/selectors/pit-loss-selectors.test.ts tests/features/replay/panels/pit-loss-position-panel.test.tsx

Run the complete web validation with:

    cd web
    npm run ci

Run the curated pit-loss baseline catalog and resolver tests with:

    .venv/bin/python -m pytest pipeline/tests/delivery/browser/test_browser_pit_loss_baseline_catalog.py pipeline/tests/delivery/browser/test_browser_pit_loss_baseline_resolver.py

Run the curated sidecar publication, Australia fixture, and legacy-compatibility
tests with:

    .venv/bin/python -m pytest pipeline/tests/delivery/browser/test_browser_pit_loss_baseline_publication.py pipeline/tests/delivery/browser/test_browser_pit_loss_australia_fixture.py pipeline/tests/delivery/browser/test_browser_pit_loss_legacy_compatibility.py

The complete lightweight CI-equivalent **Python** command
`.venv/bin/python -m pytest tests/contracts pipeline/tests` covers all of the
Python checks above (the modern contract and pipeline suites); it does not
cover the web (`npm`) checks, which run separately. The curated catalog tests
run fully offline: the resolver has no network-capable dependencies and
resolution performs no network I/O, so no test downloads a circuit statistic.
None of the validation commands modify `templates/` or publish to R2; the
untracked `templates/` directory and R2 publication behavior are unchanged.

These contract tests validate the committed active v2 deterministic fixture in
`contracts/replay-data/v2/fixtures/deterministic-race/` without FastF1 session
loading or network access. The frozen v1 fixture under
`contracts/replay-data/v1/fixtures/deterministic-race/` is referenced read-only
as historical material to assert that v1 identities are rejected; there are no
v1 runtime readers, fallbacks, or mixed-version payloads. The active v2 fixture
is also intended for TypeScript replay tests as a shared offline contract.

Pipeline tests use only committed synthetic inputs. They do not load FastF1
session data, access the network, open GUI windows, or require an OpenGL
context.

## Test strategy

The initial test suite focuses on lightweight modules that do not require:

- live FastF1 data downloads
- opening GUI windows
- an OpenGL context
- a running race replay session

The current suite includes:

- contract tests for the deterministic browser replay fixture
- canonical pipeline unit and integration tests
- curated pit-loss baseline catalog and resolver tests covering the 26-circuit
  2024-2026 physical-circuit union, stable per-circuit identity reuse
  (Bahrain/Sakhir vs Sepang, Barcelona vs Madrid), per-status
  direct/derived/proxy metadata, provenance/evidence/confidence, non-universal
  discounts, the `SC <= VSC <= Green` invariant, and fully offline generation
- curated sidecar publication, Australia fixture, and legacy-compatibility
  tests, including fail-closed unknown-track behavior with no 22 s fallback
- status-mapping and fail-closed tests for unsupported or mixed status
  intervals, curated sidecars that must resolve all three status values, and
  catalog-only `sourceStatus` that is never serialized
- pit-loss estimate sidecar contract/publication tests, including absent and
  unavailable Safety Car/VSC status semantics
- web loader, status-aware causal selector, and panel status-label tests
  (including no `Baseline` label for curated values)

Legacy-only checks cover time formatting, tyre compound mapping, season
detection, settings persistence, and desktop module import smoke tests.

Some import smoke tests may be skipped locally when optional runtime dependencies are not installed.
