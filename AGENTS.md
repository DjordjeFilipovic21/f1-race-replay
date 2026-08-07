# Agent Instructions

## OpenAgent context

OpenAgent standards are global, not project-local. Load the applicable files from
`/home/fdjor/.opencode/context/` before executing work:

- Code: `core/standards/code-quality.md`
- Documentation: `core/standards/documentation.md`
- Tests: `core/standards/test-coverage.md`
- Review: `core/workflows/code-review.md`
- Delegation: `core/workflows/task-delegation-basics.md`

## Replay data contract policy

- **V2-only decision (2026-08-05):** v2 is the sole supported browser/replay
  data contract and the only migration target.
- V1 compatibility is intentionally not supported. Do not add v1 fallbacks,
  adapters, readers, publishers, fixtures, or mixed-version payloads.
- Weather sidecars, manifests, schema references, and loaders must use v2
  contract identities. Older v1 changes must be ported into the v2 model rather
  than preserved as a compatibility path.
- Canonical Parquet remains unchanged; version migration belongs at the
  delivery, validation, and browser boundaries.

## Python validation

- Always run Python tests through the project virtual environment with `.venv/bin/python`; do not use the system Python.
- Run the lightweight CI-equivalent Python suite with:

  ```bash
  .venv/bin/python -m pytest tests/contracts pipeline/tests
  ```
- Run targeted Python tests with `.venv/bin/python -m pytest <paths>`.
- Run the legacy desktop tests separately with `.venv/bin/python -m pytest legacy/tests`.
