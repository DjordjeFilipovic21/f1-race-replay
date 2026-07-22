# F1 Race Replay

This repository contains the modern browser replay and canonical data pipeline,
alongside the preserved desktop application migrated from upstream.

## Repository layout

- [`web/`](web/) — TypeScript browser replay and replay UI.
- [`pipeline/`](pipeline/) — packaged canonical FastF1-to-replay pipeline.
- [`contracts/`](contracts/) — versioned replay-data schemas and fixtures.
- [`docs/`](docs/) — modern architecture, contract, and delivery documentation.
- [`legacy/`](legacy/) — the upstream Python desktop application, its assets,
  dependencies, documentation, and tests.

## Setup and validation

- Modern Python checks and test commands: [`docs/Testing.md`](docs/Testing.md).
- Legacy desktop setup and usage: [`legacy/README.md`](legacy/README.md).
- Launch the legacy application from the repository root with:
  `.venv/bin/python legacy/main.py`.
- Install the pipeline from [`pipeline/`](pipeline/) and use its README for
  canonical generation commands.

The lightweight modern suite is offline and covers `tests/contracts` and
`pipeline/tests`. Legacy tests are run separately with
`.venv/bin/python -m pytest legacy/tests`.
