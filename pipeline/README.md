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

This boundary explicitly excludes the legacy `src/` application, network-backed
CI or network-loading tests, browser chunks, and CLI orchestration. Those
concerns must not be inferred from the adapters documented here. Testing events
retain FastF1 round zero; they are selected through testing-event APIs, not
ordinary round lookup.

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

## Deferred work

Browser/CDN publishing, a CLI, memory-streaming optimization, and any
future consumer-side alignment remain deferred. This phase preserves native
rows and prioritizes deterministic correctness; it does not change the Phase 0
browser manifest schema or publish browser artifacts. The writer uses native
Polars without PyArrow, accepts already validated frames, does not load FastF1,
and remains separate from the Phase 0 browser pipeline and legacy `src/`.
