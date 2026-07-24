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

### Modern source layout

- `pipeline/src/f1_replay_pipeline/app/` — CLI, orchestration, batch generation,
  and track-asset generation.
- `pipeline/src/f1_replay_pipeline/domain/` — canonical schemas, validation,
  normalization, manifests, and generation identity.
- `pipeline/src/f1_replay_pipeline/adapters/fastf1/` — FastF1 loading and
  source-data adapters.
- `pipeline/src/f1_replay_pipeline/storage/` — Parquet I/O, canonical writing,
  validation, and publication.
- `pipeline/src/f1_replay_pipeline/delivery/browser/` — browser manifests,
  chunks, publication, reading, and delivery services.
- `pipeline/src/f1_replay_pipeline/analysis/live_position/` — live-position
  progress, quality, projection, ranking, and calibration.
- `web/src/app/` — application shell and bootstrap.
- `web/src/data/replay/` — replay artifact loading and validation.
- `web/src/engine/replay/` — replay clock, sampling, cache, events, and state.
- `web/src/features/replay/` — playback controls, workspace, panels, and
  feature state.

Pipeline and web tests mirror these boundaries beneath `pipeline/tests/` and
`web/tests/`.

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
