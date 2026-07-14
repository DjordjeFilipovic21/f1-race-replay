# Testing

This project uses `pytest` for automated tests.

## Install test dependencies

For local development, create and activate a virtual environment first:

    python3 -m venv .venv
    source .venv/bin/activate

Then install the development requirements:

    python -m pip install --upgrade pip
    python -m pip install -r requirements-dev.txt

Install the separately packaged canonical pipeline in editable mode:

    python -m pip install --editable ./pipeline

## Run the test suite

Run all tests with:

    python -m pytest

Run only the lightweight unit tests with:

    python -m pytest tests/lib

Run the offline replay contract checks with:

    python -m pytest tests/contracts/test_replay_contract.py

Run the lightweight pipeline suite with:

    python -m pytest pipeline/tests

Run the complete lightweight CI-equivalent suite with:

    python -m pytest tests/contracts tests/lib pipeline/tests

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

- unit tests for time formatting and parsing
- unit tests for tyre compound mapping
- unit tests for season detection
- unit tests for settings persistence with temporary files
- smoke import tests for project modules

Some import smoke tests may be skipped locally when optional runtime dependencies are not installed.
