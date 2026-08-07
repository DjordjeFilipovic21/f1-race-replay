# Testing

This project uses `pytest` for automated tests.

## Install test dependencies

For local development, create and activate a virtual environment first:

    python3 -m venv .venv
    source .venv/bin/activate

Install the lightweight modern test dependencies and the separately packaged
canonical pipeline:

    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install pytest pytest-mock "jsonschema[format-nongpl]>=4.26,<5"

    .venv/bin/python -m pip install --editable ./pipeline

The desktop application's dependencies remain isolated under
`legacy/requirements-dev.txt`.

Install them only when working on the desktop application:

    .venv/bin/python -m pip install -r legacy/requirements-dev.txt

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

The complete lightweight CI-equivalent command
`.venv/bin/python -m pytest tests/contracts pipeline/tests` covers all of the
above. The curated catalog tests run fully offline: the resolver has no
network-capable dependencies and resolution performs no network I/O, so no
test downloads a circuit statistic. None of the validation commands modify
`templates/` or publish to R2; the untracked `templates/` directory and R2
publication behavior are unchanged.

These contract tests validate the committed deterministic fixture in
`contracts/replay-data/v1/fixtures/deterministic-race/` without FastF1 session
loading or network access. The same fixture is intended for future TypeScript
replay tests as a shared offline contract.

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
