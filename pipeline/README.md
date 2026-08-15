# F1 Replay Pipeline

`f1-replay-pipeline` is the isolated Python pipeline for turning FastF1 session
data into deterministic canonical replay tables and validated browser delivery.
It is separate from the preserved legacy desktop application in `legacy/`.

The browser boundary is **replay-data v2 only**. Canonical Parquet remains a
private pipeline/recovery representation; browser publication derives JSON
artifacts from a validated canonical generation and does not modify or copy the
canonical tables.

## Requirements and installation

- Python 3.11, 3.12, or 3.13
- FastF1 `>=3.8.1,<3.9` (installed by the package constraints)
- A local virtual environment at the repository root, conventionally `.venv`

Install from the repository root:

```bash
.venv/bin/python -m pip install --constraint pipeline/constraints.txt pipeline/
```

The same installation from inside `pipeline/` is:

```bash
../.venv/bin/python -m pip install --constraint constraints.txt .
```

`pipeline/constraints.txt` is the committed pip-tools resolver output, not a
replacement for `pipeline/pyproject.toml`. Regenerate it after changing the
declared dependencies or supported Python matrix:

```bash
.venv/bin/python -m pip install --upgrade pip-tools
.venv/bin/python -m piptools compile --resolver=backtracking \
  --output-file=pipeline/constraints.txt pipeline/pyproject.toml
```

## Pipeline boundaries

The adapters consume either an already loaded session or an injected
zero-argument session factory. A preloaded session is not loaded again; a
factory is called once with `laps`, `telemetry`, `weather`, and `messages`
enabled. The factory path can use FastF1 cache/network behavior, so it is the
only normal data-loading boundary.

The canonical generation contains exactly these ten tables:

`session_metadata`, `drivers`, `car_telemetry`, `position_telemetry`, `laps`,
`stints`, `weather`, `track_status_intervals`, `race_control_messages`, and
`results`.

Car and position telemetry retain their native cadences. Canonical rows are not
resampled or interpolated; browser-time alignment is a later delivery policy.
See [the canonical schema](../docs/canonical-pipeline-schema.md) for columns,
ordering, nulls, and deterministic deduplication.

## CLI quick start

Run these commands from the repository root through the virtual environment's
installed console script. Using the explicit path is safe in a fresh shell and
does not require the environment to be activated.

### Build one canonical generation

`race` requires a positive season year, session, output directory, and exactly
one of `--round` or `--event`:

```bash
.venv/bin/f1-replay-pipeline race \
  --year 2026 --round 3 --session R --output artifacts/canonical
```

Use `--event "Australian Grand Prix"` instead of `--round 3` when selecting by
exact event name. Supported race backends are `fastf1`, `f1timing`, and
`ergast`; the backend is optional.

Testing sessions use the dedicated testing selector and positive numbers:

```bash
.venv/bin/f1-replay-pipeline testing \
  --year 2026 --test-number 1 --session-number 2 \
  --output artifacts/canonical --backend f1timing
```

Both commands validate all ten canonical tables and publish one immutable
generation below the requested output parent. `--generation-id` supplies a safe
deterministic path component; otherwise the CLI creates a UTC timestamp ID.
The resulting `current.json` is the canonical reader visibility boundary.

### Derive browser delivery locally

The browser command reads the generation selected by the canonical parent's
validated `current.json`, validates it, and writes a separate browser parent:

```bash
.venv/bin/f1-replay-pipeline browser \
  --canonical artifacts/canonical \
  --output artifacts/browser \
  --delivery-version 2026-round-03-race-v2 \
  --schema-root contracts/replay-data/v2/schemas
```

It prints `delivery_version=...` on success and atomically replaces only
`artifacts/browser/browser-current.json`. It performs no FastF1 or network
loading. The browser manifest records the source canonical generation and
manifest digest, and all payloads are validated against the local v2 schemas.
Standalone `artifacts/canonical` and `artifacts/browser` outputs do not create
the season catalog required by the web landing flow; local web users should use
the `generate` section below.

### Generate a season batch

`generate` creates canonical and browser forms locally. Select one or more
rounds by repeating `--round`, or select all ordinary championship rounds with
`--all`:

```bash
.venv/bin/f1-replay-pipeline generate \
  --year 2024 --round 3 --session R --resume
```

By default the season root is `artifacts/seasons/<year>/`; use `--output` and
`--browser-output` to override its canonical and browser parents. `--resume`
skips only validated existing outputs, `--force` regenerates selected races, and
`--continue-on-error` keeps processing while returning a nonzero final status if
any race failed.

Deep-verify all catalog-referenced local artifacts with:

```bash
.venv/bin/f1-replay-pipeline verify --year 2024
```

### Publish to R2 explicitly

Normal generation is local-only. R2 access occurs only when `--publish-r2` is
passed to `generate`, after local generation and validation:

```bash
export R2_ENDPOINT_URL=https://ACCOUNT_ID.r2.cloudflarestorage.com
export R2_BUCKET=f1-race-replay-data
export AWS_PROFILE=f1-r2

.venv/bin/f1-replay-pipeline generate \
  --year 2024 --round 3 --session R --resume --publish-r2
```

Use a dedicated AWS profile and never put credentials in the repository, `.env`
files, command arguments, or shell history. See
[R2 production publishing](../docs/r2-production-publishing.md) for production
object layout, validation, caching, and recovery guidance.

## Output and exit status

Successful canonical commands print `generation_id=...`; browser publication
prints `delivery_version=...`; batch mode prints each race outcome and, when
enabled, R2 upload/reuse counters. Expected failures print one `error: ...`
line to stderr without a traceback.

- `0`: requested publication or verification succeeded
- `1`: application, resolution, normalization, validation, or publication
  failure
- `2`: command-line usage or argument validation failure
- `130`: generation or browser publication was cancelled with `Ctrl-C`

The pipeline is non-interactive and has no GUI or legacy desktop integration.
Offline tests inject fake sessions, resolvers, and publishers; they do not rely
on remote FastF1 responses. See [testing guidance](../docs/Testing.md) and the
[replay data contract](../docs/replay-data-contract.md) for adjacent contracts.
