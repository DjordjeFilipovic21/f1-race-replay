# Testing

The repository uses `pytest` for Python checks and Vitest, TypeScript, Vite, and
Playwright for the web application. The supported Python matrix is 3.11–3.13;
the web package requires Node.js `>=22.12.0` (CI currently uses Node 24).

All local Python test commands must use the project virtual environment:
`.venv/bin/python`. Do not use the system Python to run tests.

## Install test dependencies

Create the environment once, then use its interpreter for every Python command:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install \
  --constraint pipeline/constraints.txt \
  --constraint pipeline/test-constraints.txt \
  pytest pytest-mock "jsonschema[format-nongpl]>=4.26,<5"
.venv/bin/python -m pip install \
  --constraint pipeline/constraints.txt --editable ./pipeline
```

The desktop application's dependencies are separate:

```bash
.venv/bin/python -m pip install \
  --constraint legacy/constraints.txt -r legacy/requirements-dev.txt
```

Regenerate the committed pip-tools constraints only when declared dependency
ranges or the supported Python matrix changes:

```bash
.venv/bin/python -m pip install --upgrade pip-tools
.venv/bin/python -m piptools compile --resolver=backtracking \
  --output-file=pipeline/constraints.txt pipeline/pyproject.toml
.venv/bin/python -m piptools compile --resolver=backtracking \
  --output-file=legacy/constraints.txt legacy/requirements-dev.txt
```

## Static checks

Phase 3 static validation is intentionally limited to the three changed
pipeline sources and five integrity tests. The tools are standalone developer
tools, not project runtime dependencies:

```bash
.venv/bin/python -m pip install ruff==0.16.2 pyright==1.1.411
```

Run the exact scoped commands from the repository root:

```bash
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
```

## Focused test suites

Use focused checks while developing:

```bash
# Browser/replay contract
.venv/bin/python -m pytest tests/contracts/test_replay_contract.py

# Pipeline tests
.venv/bin/python -m pytest pipeline/tests

# CI-equivalent modern Python suite
.venv/bin/python -m pytest tests/contracts pipeline/tests

# Legacy desktop tests (separate package)
.venv/bin/python -m pytest legacy/tests
```

For the web application, install from the lockfile and run the CI command from
`web/`:

```bash
cd web
npm ci
npm run ci
```

`npm run ci` runs the web type check, Vitest suite, and production build. The
browser smoke suite is a separate CI step and can be run locally with:

```bash
cd web
npx playwright install --with-deps chromium
npm run test:e2e
```

Useful focused web checks are:

```bash
cd web
npm run typecheck
npm run test -- --run \
  tests/data/replay/sidecar-loading.test.ts \
  tests/features/replay/selectors/pit-loss-selectors.test.ts \
  tests/features/replay/panels/pit-loss-position-panel.test.tsx
```

## Full-regression policy

Run focused checks during implementation. Before final review, run exactly one
full local regression for each affected validation boundary:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m pytest legacy/tests
cd web && npm run ci && npm run test:e2e
```

The Python CI workflow repeats the modern and full suites across Python 3.11,
3.12, and 3.13; the web workflow runs `npm ci`, `npm run ci`, and the
Playwright smoke suite on Node 24. CI therefore remains the matrix and
environment coverage, while the one-full-regression rule prevents repeated
full runs during local iteration.

## Test boundaries

The modern suites use committed synthetic inputs and offline v2 fixtures. They
do not download live FastF1 data, access the network, open GUI windows, or
require an OpenGL context. The frozen v1 fixture is read-only historical
material used to verify rejection; there are no v1 runtime readers, fallbacks,
or mixed-version payloads.

Legacy checks cover desktop formatting, tyre mapping, season detection,
settings persistence, and import smoke tests. Optional desktop dependencies may
cause import smoke tests to be skipped locally when they are not installed.
