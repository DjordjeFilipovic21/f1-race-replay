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

Legacy-only checks cover time formatting, tyre compound mapping, season
detection, settings persistence, and desktop module import smoke tests.

Some import smoke tests may be skipped locally when optional runtime dependencies are not installed.
