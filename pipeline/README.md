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
CI or network-loading tests, Parquet writing, checksum/logical-hash manifests,
browser chunks, and CLI orchestration. Those concerns must not be inferred from
the adapters documented here. Testing events retain FastF1 round zero; they are
selected through testing-event APIs, not ordinary round lookup.

## Deferred work

Parquet writer responsibilities—including logical-hash encoding and
implementation, atomic builds, checksums, manifests, and byte-level output
controls—are intentionally deferred. The package currently does not fetch data
or write files. Telemetry performance optimization is also deferred to a later
PR; this phase preserves native rows and prioritizes deterministic correctness.
