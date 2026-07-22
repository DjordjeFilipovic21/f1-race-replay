# Agent Instructions

## Python validation

- Always run Python tests through the project virtual environment with `.venv/bin/python`; do not use the system Python.
- Run the lightweight CI-equivalent Python suite with:

  ```bash
  .venv/bin/python -m pytest tests/contracts pipeline/tests
  ```
- Run targeted Python tests with `.venv/bin/python -m pytest <paths>`.
- Run the legacy desktop tests separately with `.venv/bin/python -m pytest legacy/tests`.
