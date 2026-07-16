# F1 Replay Pipeline

`f1-replay-pipeline` is an isolated Python foundation for transforming FastF1
data into deterministic, canonical replay tables. It is separate from the
legacy desktop application and its `src/` modules.

## Installation

From this directory:

```bash
python -m pip install .
```

The package requires Python 3.11+ and installs FastF1 3.8.x and Polars 1.x.

## Foundation scope

This initial boundary establishes independent packaging and a deliberately
small top-level import surface. Importing `f1_replay_pipeline` does not load
FastF1 or Polars and performs no network, GUI, or OpenGL work. Import the
specific `canonical_schema`, `normalizers`, or `validators` module when that
capability is needed.

The foundation now provides explicit canonical schemas, pure time/identifier/
null normalizers, deterministic sort-and-deduplication, in-memory Polars
validation, and synthetic offline fixtures. It keeps car and position streams
at their separate native cadences; it neither resamples nor interpolates them.

## Normalize an injected session

The adapters do not discover, load, cache, or fetch FastF1 sessions. They are
I/O-free consumers of injected sessions. Inject either a pre-loaded session or
a zero-argument factory into `load_session`. A supplied `session` is treated as
already loaded and is never loaded again; a factory-created session is loaded
once with all four FastF1 data groups enabled. The factory path may invoke
FastF1 cache/network behavior.

```python
from f1_replay_pipeline.session_loader import load_session
from f1_replay_pipeline.session_metadata_adapter import (
    adapt_drivers, adapt_session_metadata,
)
from f1_replay_pipeline.car_telemetry_adapter import adapt_car_telemetry
from f1_replay_pipeline.position_telemetry_adapter import adapt_position_telemetry

session = load_session(session=my_preloaded_session)
# Or: session = load_session(session_factory=lambda: fastf1.get_session(...))

metadata = adapt_session_metadata(session)
session_id = metadata.item(0, "session_id")
drivers = adapt_drivers(session, session_id)
driver_ids = dict(drivers.select("source_driver_key", "driver_id").iter_rows())
cars = adapt_car_telemetry(session, session_id)
positions = adapt_position_telemetry(session, session_id, driver_ids)
```

The implemented adapters are in-memory and duck-typed: metadata/drivers,
native car and position telemetry, laps/stints, weather/track status, and
race-control messages/results. They return typed Polars frames; they do not
write output files.

For offline tests, inject a small fake session with the required attributes and
use `session=...`; its `load()` method is not called. To test the loading seam,
inject a factory and assert the fake session receives
`laps=True, telemetry=True, weather=True, messages=True`. Tests should use
deterministic fake tables and never require a remote FastF1 response.

## Canonical sources and boundaries

| Canonical table | FastF1 source |
| --- | --- |
| `session_metadata` | `event`, `session_info`, session start metadata |
| `drivers` | `drivers` / `get_driver()` |
| `car_telemetry` | `Session.car_data` |
| `position_telemetry` | `Session.pos_data` |
| `laps` | `Session.laps` |
| `stints` | Derived from canonical `laps` rows |
| `weather` | `Session.weather_data` |
| `track_status_intervals` | `Session.track_status` |
| `race_control_messages` | `Session.race_control_messages` |
| `results` | `Session.results` |

`car_data` and `pos_data` remain separate native streams. Canonical rows are
never interpolated or resampled; browser-time alignment is a later consumer
concern. Missing and non-finite optional values become typed nulls. The original
FastF1 driver-number mapping key is retained as `source_driver_key`; messages
and results may use that key as an alias before resolving canonical `driver_id`.
Source duplicate rows may be accepted, but duplicate canonical keys are reduced
only under the documented deterministic policy; invalid duplicate keys are
rejected. See [the canonical schema](../docs/canonical-pipeline-schema.md) for
columns, nulls, ordering, and deduplication.

This boundary explicitly excludes the legacy `src/` application and
network-backed CI or network-loading tests. Testing events
retain FastF1 round zero; they are selected through the explicit `testing`
command and testing-session API, never through ordinary round lookup.

## Run the pipeline

Install the package from `pipeline/` first:

```bash
python -m pip install .
```

The installed console command and the module entry point accept the same
non-interactive commands:

```bash
f1-replay-pipeline race \
  --year 2026 --round 3 --session R --output artifacts

python -m f1_replay_pipeline race \
  --year 2026 --event "Australian Grand Prix" --session R \
  --backend fastf1 --generation-id 2026-aus-race --output artifacts

f1-replay-pipeline testing \
  --year 2026 --test-number 1 --session-number 2 \
  --backend f1timing --output artifacts
```

### Selectors and backends

- `race` requires `--year`, `--session`, `--output`, and exactly one of
  `--round` or `--event`. `--round` must be a positive integer; `0` is not a
  testing selector. `--event` is an exact event name.
- Supported race session aliases are `fp1`, `fp2`, `fp3`, `q`, `s`, `ss`,
  `sq`, `r`, plus `practice 1`, `practice 2`, `practice 3`, `qualifying`,
  `sprint`, `sprint shootout`, `sprint qualifying`, and `race`.
- `testing` requires positive `--test-number` and positive `--session-number`.
  It has no race event or round selector and uses the dedicated testing API.
- `--backend` is optional, case-insensitive, and normalized to lowercase. Race
  accepts `fastf1`, `f1timing`, or `ergast`; testing accepts only `fastf1` or
  `f1timing`.

The default resolver imports FastF1 lazily, resolves one session, and loads it
once with laps, telemetry, weather, and messages enabled. This is a real
FastF1 path: its cache and network behavior still applies. The CLI has no
offline or fixture mode. Offline tests inject fake resolvers, sessions, and
publishers instead of using network, GUI, OpenGL, or real FastF1 loading.

### Output and status

The command normalizes and validates exactly the ten canonical tables, then
publishes one generation below the required `--output` directory. Supply
`--generation-id` for a safe deterministic path component; otherwise the CLI
generates a UTC timestamp ID. Successful stdout is intentionally stable:

```text
generation_id=2026-aus-race
```

Publication stages and validates the generation before atomically replacing
`<output>/current.json`; that pointer is the reader visibility boundary. The
pointer names the selected generation and manifest digest. A pre-commit
failure leaves the previous valid pointer in place; a post-commit durability
failure reports that the new generation may already be selected.

Exit behavior:

- `0`: publication succeeded; generation ID is printed to stdout.
- `1`: expected application/resolution/normalization/validation/publication
  failure; one `error: ...` line is printed to stderr, without a traceback.
- `2`: argparse usage or validation failure (including missing, abbreviated, or
  unknown options); argparse writes its error to stderr.

The command is deliberately limited to Phase 1 canonical generation
publication. It does not provide prompts, GUI integration, legacy `src/`
integration, browser chunks, CDN upload, interpolation, resampling, consumer
alignment, or a new CLI framework.

## Publish a canonical generation

The writer accepts exactly the ten already validated canonical Polars frames and
publishes them as one versioned generation. It does not load FastF1 or perform
network, GUI, OpenGL, resampling, or interpolation work.

```python
from pathlib import Path

from f1_replay_pipeline.canonical_writer import (
    publish_canonical_generation,
    resolve_published_canonical_generation,
)

# Build these with the adapters/validators first; this example stays offline.
validated_frames = {
    "session_metadata": session_metadata,
    "drivers": drivers,
    "car_telemetry": car_telemetry,
    "position_telemetry": positions,
    "laps": laps,
    "stints": stints,
    "weather": weather,
    "track_status_intervals": track_status_intervals,
    "race_control_messages": race_control_messages,
    "results": results,
}

published = publish_canonical_generation(
    frames=validated_frames,
    target_parent=Path("artifacts"),
    generation_id="2026-07-15T120000Z-example",
)
print(published.manifest_path, published.manifest_sha256)

# Readers validate current.json, the manifest, every table schema/row count,
# and both recorded hashes before returning this generation.
current = resolve_published_canonical_generation(Path("artifacts"))
```

`PublishedCanonicalGeneration` is an immutable result containing
`generation_id`, `generation_path`, `manifest_path`, `pointer_path`,
`manifest_sha256`, `committed`, and the outcome of every attempted directory
fsync. `current.json` replacement is the sole commit point. If a post-commit
durability step fails, publication raises a committed-but-durability-uncertain
error whose result identifies the selected generation rather than implying the
old pointer remains current. `generation_id` must be one safe path component. The
`filesystem`, `checkpoint`, and `publisher` arguments are optional injection
seams for deterministic failure tests; normal callers do not need them.

Publication refuses symlinked roots, ancestors, generation directories, and
files. The pointer temp is created beside `current.json` with exclusive
`O_CREAT|O_EXCL|O_NOFOLLOW` semantics. Publication and stale cleanup hold an
exclusive lock/lease and fail closed when ownership cannot be verified.

## On-disk layout

```text
artifacts/
├── current.json
└── generations/
    └── <generation-id>/
        ├── manifest.json
        └── tables/
            ├── session_metadata.parquet
            ├── drivers.parquet
            ├── car_telemetry.parquet
            ├── position_telemetry.parquet
            ├── laps.parquet
            ├── stints.parquet
            ├── weather.parquet
            ├── track_status_intervals.parquet
            ├── race_control_messages.parquet
            └── results.parquet
```

`current.json` is the sole reader visibility boundary. It names the generation,
manifest path, format version, and SHA-256 of the exact manifest bytes. A
generation is never selected merely because its directory exists.

## Logical identity and byte integrity

The manifest records two different hashes for every table:

- **`logical_sha256`** hashes the versioned canonical encoding of table name,
  exact schema, declared column order, row order, typed nulls, and values. It is
  independent of Parquet metadata, compression, and page layout.
- **`byte_sha256`** hashes the exact published Parquet file bytes. It detects
  corruption or unexpected changes to the artifact itself.

The native Polars writer uses the documented v1 settings (`use_pyarrow=False`,
Zstandard compression level 3, full statistics, and fixed row/page sizes). Those
settings improve repeatability but do **not** promise identical Parquet bytes
across Polars/Arrow versions, operating systems, filesystems, or other writer
environments. Compare logical hashes for canonical data identity; use byte hashes
for artifact integrity.

## Verification and recovery

Publication stages all tables and the manifest under a uniquely prefixed staging
directory, fsyncs files, validates the complete generation with the same
validator used by readers, renames it into `generations/`, and atomically
replaces `current.json` last. That replacement is the commit point. A failure
before it leaves the previous valid pointer and published generation untouched.
If replacement succeeds but the post-commit parent fsync fails, the writer
raises a committed-but-durability-uncertain error whose result identifies the
new selected generation; it does not claim the old pointer remains current.

`resolve_published_canonical_generation()` rejects malformed pointers, path or
generation mismatches, missing files, manifest checksum mismatches, invalid
schemas/row counts, and logical or byte hash mismatches. For startup cleanup,
import `recover_stale_staging` from
`f1_replay_pipeline.generation_publication`; it removes only directories whose
names begin with the writer’s known `.canonical-parquet-staging-` prefix, then
revalidates the current pointer. It never treats arbitrary directories as
staging and never deletes a published generation; cleanup failures are reported.

Recovery never selects a directory merely because it exists. Pointer topology,
manifest digest, table presence, schema, row count, logical hash, and byte hash
must pass the shared complete validator. Cleanup and lease-release failures are
aggregated with the primary publication error, or reported as a cleanup error
when publication itself succeeded.

## Durability and filesystem limits

Staging and the destination must share a filesystem: same-filesystem rename can
otherwise fail with `EXDEV`. Directory fsync attempts are recorded for parent
creation (when needed), `generations` creation, staging creation, staged or
selected generation, `generations`, and the target parent after commit; each is
reported as succeeded, unsupported, or failed. File and directory fsync provide
best-effort crash durability, not replication, signing, authorization, or a
database transaction. NFS can make rename/lock failures ambiguous; overlay and
network filesystems can weaken visibility or persistence. A crash can leave
staging residue, pointer temps, or an unselected complete generation. The
contract guarantees only one atomic `current.json` replacement where the
filesystem supports it—not multi-path transactionality.

## Derive browser delivery

Browser artifacts are a derived view of one **fully validated, resolved** canonical
generation. `read_validated_canonical_generation()` resolves `current.json`, runs
the complete pointer/manifest/schema/row-count/logical-hash/byte-hash validation,
and only then reads the ten Parquet tables. A directory name is never enough to
select a generation. The reader is read-only: it never mutates or republishes
canonical Parquet.

Canonical tables retain their source timestamps and native cadence. Browser
alignment is a delivery policy, not canonical resampling. `build_browser_delivery()`
uses one immutable validated snapshot and the sorted, unique union of native
driver, weather, track-status, and race-control timestamps. Exact telemetry is
never filled; interval and previous-value fields are evaluated explicitly.

### Field and time semantics

- `x`/`y` come from position telemetry; `speed`, `throttle`, `gear`, and `drs`
  come from car telemetry; `brake` is `0`/`1` and preserves `null`.
- `status` comes from position telemetry. `lap` and `tyreCompound` use the
  containing half-open lap interval. Pit state is `true` only in a known pit
  interval, `false` for a known non-pit interval, otherwise `null`.
- `trackDistanceMeters`, `gapToLeaderMs`, and `position` are always `null` in
  v1; no source exists from which to fabricate them.
- Leaderboard order comes from classified results, track status from its active
  interval, weather from the latest native observation, and race-control
  messages remain sparse events. Missing values remain `null`.
- At render time only continuous fields (`x`, `y`, `speed`, `throttle`, `brake`,
  `gapToLeaderMs`) may be linearly interpolated between two valid authoritative
  bounds for the same driver. Discrete, categorical, and boolean fields use
  previous-value semantics. Sparse `BrowserEvent` records remain point events
  and are never interpolated.

### Chunks and ownership

`build_browser_chunks()` emits exact observations without resampling or
interpolating. Production defaults are 10,000 ms chunks with a 1,000 ms handoff
overlap. Coverage is half-open, `[startMs, endMs)`: the first sample at or after
`startMs` owns the chunk, and earlier samples are overlap-only references.
`authoritative_start_index` identifies that first owned sample;
`overlap.authoritative_from_ms` equals `startMs`. Events belong only to the
authoritative chunk containing their timestamp. Consumers resolve duplicate
timestamps using the owning chunk, not the overlap copy.

### Deterministic publication

`publish_browser_delivery()` accepts only a `BrowserDeliveryBuild` already bound
to one immutable canonical snapshot. It validates all manifest/chunk identities,
hashes, alignment, ownership, overlap, and event invariants, verifies exact
staged bytes, and validates every artifact against a caller-supplied local v1
`schema_root` registry without remote retrieval. It then writes a version under:

```text
<browser_parent>/
├── browser-current.json
└── generations/<delivery-version>/
    ├── manifest.json
    ├── track-assets.json
    └── chunks/chunk-001.json ...
```

Publication rejects symlinked roots, ancestors, and generation directories and
uses descriptor-relative no-follow writes and cleanup. The complete staged
delivery is validated before `browser-current.json` is atomically replaced;
that replacement is the only browser visibility point.
The manifest records the exact source generation ID and manifest digest plus
artifact digests. Repeating the build with the same validated source and inputs
produces the same bytes and names. This boundary never edits canonical
`current.json`, mutates canonical tables, or copies/republishes canonical
Parquet. It does not perform network, GUI, or FastF1 loading.

### Minimal offline API example

The canonical and track-asset paths below are supplied explicitly. The track
asset file must validate against the replay-data v1 track-assets schema. No
network, GUI, FastF1 loading, or automatic canonical selection is implied.

```python
from pathlib import Path
import json

from f1_replay_pipeline.browser_delivery_orchestration import build_browser_delivery
from f1_replay_pipeline.browser_delivery_publication import publish_browser_delivery
from f1_replay_pipeline.browser_delivery_reader import read_validated_canonical_generation

canonical_parent = Path("artifacts/canonical")
browser_parent = Path("artifacts/browser")
snapshot = read_validated_canonical_generation(canonical_parent)
track_assets = json.loads(Path("track-assets.json").read_text(encoding="utf-8"))
delivery = build_browser_delivery(snapshot, track_assets)
published = publish_browser_delivery(
    browser_parent=browser_parent,
    delivery_version="example-v1",
    delivery=delivery,
    schema_root=Path("../contracts/replay-data/v1/schemas"),
)
print(published.manifest_path)
```
