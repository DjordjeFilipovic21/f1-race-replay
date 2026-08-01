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
- Production browser-delivery publication:
  [`docs/r2-production-publishing.md`](docs/r2-production-publishing.md).
- Legacy desktop setup and usage: [`legacy/README.md`](legacy/README.md).
- Launch the legacy application from the repository root with:
  `.venv/bin/python legacy/main.py`.
- Install the pipeline from [`pipeline/`](pipeline/) and use its README for
  canonical generation commands.

The lightweight modern suite is offline and covers `tests/contracts` and
`pipeline/tests`. Legacy tests are run separately with
`.venv/bin/python -m pytest legacy/tests`.

## FastF1 2026 compatibility

The modern pipeline requires `fastf1>=3.8.1,<3.9`. FastF1 3.8.1 is the first
supported release that tolerates the missing raw DRS channel in 2026 timing
data; FastF1 3.8.3 still exposes `DRS`, but its 2026 values are all zero for
backward compatibility. Those zeros are not measured DRS-Off samples.

F1 does not publish Overtake Mode, active-aero, or ERS replacement telemetry to
FastF1. The canonical and browser `drs` field is therefore retained for
pre-2026 data and schema compatibility, but no factual `overtakeMode` channel
is synthesized. Browser manifests may optionally include `seasonMetadata` and
`telemetryCapabilities`; for 2026, the relevant capabilities should be marked
`not-published`. The UI should say that DRS/Overtake Mode telemetry is
unavailable or not published, rather than displaying `DRS: Off`. Older
manifests without this metadata remain readable with their existing behavior.

Sources: [FastF1 documentation](https://docs.fastf1.dev/), [FastF1 3.8.1
release](https://github.com/theOehrly/Fast-F1/releases/tag/v3.8.1), and the
maintainer's [2026 telemetry discussion](https://github.com/theOehrly/Fast-F1/discussions/861).
