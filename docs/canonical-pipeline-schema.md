# Canonical pipeline schema and policies

This document defines the active v2 Phase 1 foundation boundary for native-cadence
tables produced from FastF1. It is separate from the [Phase 0 browser chunk
contract](replay-data-contract.md): canonical tables preserve source rows;
browser chunks align data for delivery and may support render-time
interpolation. The v2 contract is the only supported canonical delivery target.

## Adapter boundary and FastF1 sources

Adapters consume an injected, already-loaded FastF1-compatible session. They are
I/O-free: they do not call `fastf1.get_session()`, access the network, or manage
the cache. Use `load_session` when an injected factory should perform the one
load, or pass an existing loaded session to skip loading entirely:

```python
from f1_replay_pipeline.adapters.fastf1.session_loader import load_session

loaded = load_session(session=preloaded_session)
# A factory-created session receives all four flags exactly once. The factory
# may invoke FastF1 cache/network behavior:
# loaded = load_session(session_factory=lambda: fastf1.get_session(...))
```

The implemented in-memory adapters map these sources to the canonical tables:

| Canonical table | Source or derivation | Adapter |
| --- | --- | --- |
| `session_metadata` | `event`, `session_info`, session start metadata | `adapt_session_metadata` |
| `drivers` | `drivers` / `get_driver()` | `adapt_drivers` |
| `car_telemetry` | `Session.car_data` | `adapt_car_telemetry` |
| `position_telemetry` | `Session.pos_data` | `adapt_position_telemetry` |
| `laps` | `Session.laps` | `adapt_laps` |
| `stints` | Derived from canonical `laps` | `adapt_stints` |
| `weather` | `Session.weather_data` | `adapt_weather` |
| `track_status_intervals` | `Session.track_status` | `adapt_track_status_intervals` |
| `race_control_messages` | `Session.race_control_messages` | `adapt_race_control_messages` |
| `results` | `Session.results` | `adapt_results` |

Each adapter returns an in-memory typed Polars frame. The canonical output
contract is [ADR-002](adr/002-canonical-parquet-writer.md) and the normative
[canonical Parquet writer contract](canonical-parquet-writer-contract.md); both
publish and validate the v2 generation format.

### Offline testing seam

Tests should inject a small fake session with deterministic attributes and call
`load_session(session=fake)`. The already-loaded path never calls `fake.load()`.
For the factory path, assert the single call uses
`laps=True, telemetry=True, weather=True, messages=True`; do not depend on a
remote FastF1 response or network availability. Fake `car_data` and `pos_data`
with different timestamps to verify that their native streams stay independent.

The boundary excludes the `legacy/src/` application, network-backed CI and
network-loading tests, Parquet writing, checksum/logical-hash manifests,
browser chunks, and CLI orchestration. Telemetry performance optimization
remains outside this foundation's scope; native-row correctness is the priority.

## Time policy

`session_time_ms` is an `Int64` containing FastF1 `SessionTime` elapsed from the
session start. It is not relative to the first driver sample. FastF1's
`session_start_time` is a duration to the official Started status, not an
absolute datetime; absolute metadata must come from real datetime fields such as
`session.t0_date` and session dates.

For a non-negative source duration, convert without floating-point epoch
arithmetic and round half up:

```python
session_time_ms = (session_time_nanoseconds + 500_000) // 1_000_000
# 12.4995 seconds -> 12500; 12.4994 seconds -> 12499
```

`session_time_ms` must be non-negative. A missing or invalid timestamp rejects
the row; timestamps are never invented from neighboring rows.

Race-control `Time` is normally an absolute UTC datetime. Normalize it as
`Time - session.t0_date`, then apply the same integer millisecond rounding.
Weather, track-status, car, and position `Time` values are session-relative
durations. A duration-shaped race-control value is accepted only as the
explicit compatibility branch.

## Canonical tables

All tables use the listed column order. Implementations must declare these
Polars dtypes explicitly rather than relying on inference.

### `car_telemetry`

One row is one native FastF1 car-stream observation for one driver.

| Column | Polars dtype | Policy |
| --- | --- | --- |
| `session_id` | `String` | Stable session identity |
| `driver_id` | `String` | Canonical driver identifier |
| `source_driver_key` | `String` | Original FastF1 driver-number key |
| `session_time_ms` | `Int64` | Canonical time; deduplication key |
| `speed_kph` | `Float64` | Null when unavailable |
| `rpm` | `Float64` | Null when unavailable |
| `gear` | `Int16` | Discrete; null when unavailable |
| `throttle_pct` | `Float64` | Null when unavailable |
| `brake` | `Boolean` | Null when unavailable |
| `drs` | `Int16` | Discrete; null when unavailable |
| `source` | `String` | FastF1 provenance, normally `car` |

### `position_telemetry`

One row is one native FastF1 position-stream observation for one driver.

| Column | Polars dtype | Policy |
| --- | --- | --- |
| `session_id` | `String` | Stable session identity |
| `driver_id` | `String` | Canonical driver identifier |
| `source_driver_key` | `String` | Original FastF1 driver-number key |
| `session_time_ms` | `Int64` | Canonical time; deduplication key |
| `x` | `Float64` | Null when unavailable |
| `y` | `Float64` | Null when unavailable |
| `z` | `Float64` | Null when unavailable |
| `status` | `String` | Categorical; null when unavailable |
| `source` | `String` | FastF1 provenance, normally `pos` |

Additional canonical tables must add a versioned schema section and retain the
same time, null, identifier, ordering, and determinism policies.

### `session_metadata`

One row describes the loaded session. Its canonical key is `session_id`; a
normalizer must reject zero or multiple rows for that key. The ordered schema
is `session_id:String`, `year:Int16`, `round_number:Int16`, `event_name:String`,
`session_name:String`, `session_type:String`, `session_mode:String`, and
`session_start_time_utc:Datetime(ms, UTC)`. `session_type` is the normalized
session mode: `practice`, `qualifying`, `race`, `sprint`,
`sprint-qualifying`, `sprint-shootout`, or `testing`. FastF1 aliases `FP1`/`FP2`/
`FP3` map to `practice`; the session identity retains the practice number so
those sessions do not collide. `session_mode` carries the same normalized value
as the explicit v2 mode identity used by generation validation and browser
gating. Only `session_id` is required;
all descriptive fields are nullable. Row order is `session_id` ascending.
Testing events preserve `round_number = 0`; select them through FastF1's
testing-event/session APIs rather than ordinary round lookup.

#### One typed nullable schema across modes

All seven normalized modes share the same typed nullable canonical tables
instead of forking per mode. Practice, qualifying, sprint, and race sessions
produce the same `laps`, `results`, telemetry, and support tables with the
same columns and dtypes; the normalized `session_type` in `session_metadata`
is the mode identity, and mode-specific capability is expressed only at
browser-delivery time through v2 artifacts gated on `sessionMode`
(`timelineSummary`/`pitLossModel` are race-like-only;
`qualifyingSummary`/`qualifyingLapStatus` are qualifying-like-only). A single
schema keeps writers, readers, and validation identical across modes, and
nullable columns carry what a mode does not produce rather than inventing
values — for example, qualifying sessions populate
`q1_time_ms`/`q2_time_ms`/`q3_time_ms` and leave race-only timing null.

### `drivers`

One row maps a source driver key to a canonical driver. Its key and ascending
row order are `(session_id, driver_id)`. The ordered schema is
`session_id:String`, `driver_id:String`, `source_driver_key:String`,
`driver_number:Int16`, `full_name:String`, `team_name:String`, and
`team_colour:String`. The first three fields are required; the remaining
source metadata is nullable. Duplicate keys are rejected, as are violations
of the one-to-one `source_driver_key`/`driver_id` mapping.

### `laps`

One row is a driver's timing-lap observation. Its key and ascending row order
are `(session_id, driver_id, lap_number)`. The ordered schema is
`session_id:String`, `driver_id:String`, `lap_number:Int16`,
`stint_number:Int16`, `lap_start_time_ms:Int64`, `lap_end_time_ms:Int64`,
`lap_duration_ms:Int64`, `pit_in_time_ms:Int64`, `pit_out_time_ms:Int64`,
`compound:String`, `tyre_life:Int16`, `is_fresh_tyre:Boolean`,
`track_status:String`, `is_accurate:Boolean`, `deleted:Boolean`,
`deleted_reason:String`, `sector_1_duration_ms:Int64`,
`sector_2_duration_ms:Int64`, `sector_3_duration_ms:Int64`,
`sector_1_session_time_ms:Int64`, `sector_2_session_time_ms:Int64`, and
`sector_3_session_time_ms:Int64`, `qualifying_phase:String`. The identity fields, `lap_number`, and
`lap_start_time_ms` are required; all other fields are nullable. Duplicate
keys are rejected because timing-lap rows are not measurement alternatives.
`qualifying_phase` is `Q1`, `Q2`, or `Q3` for qualifying-like v2 sessions and
null otherwise; it is assigned from the authoritative FastF1 phase partitions.

#### Sector timing columns (v2.1)

Added in the `browser-lap-sector-data` feature. The six sector columns are
`Int64` and nullable. They are derived from FastF1 `Sector1Time`,
`Sector2Time`, `Sector3Time`, `Sector1SessionTime`,
`Sector2SessionTime`, and `Sector3SessionTime` respectively, without
interpolation or inference:

| Column | Polars dtype | Policy |
| --- | --- | --- |
| `sector_1_duration_ms` | `Int64` | Sector 1 elapsed time in milliseconds; null when unavailable |
| `sector_2_duration_ms` | `Int64` | Sector 2 elapsed time in milliseconds; null when unavailable |
| `sector_3_duration_ms` | `Int64` | Sector 3 elapsed time in milliseconds; null when unavailable |
| `sector_1_session_time_ms` | `Int64` | Sector 1 completion session time in milliseconds; null when unavailable |
| `sector_2_session_time_ms` | `Int64` | Sector 2 completion session time in milliseconds; null when unavailable |
| `sector_3_session_time_ms` | `Int64` | Sector 3 completion session time in milliseconds; null when unavailable |

Durations and completion timestamps are both kept so consumers can reveal
sector timing records causally by replay time. Missing values are left as
null; adjacent lap or sector values must never be used to fill gaps.

### `stints`

One row summarizes one contiguous tyre stint for a driver. Its key and
ascending row order are `(session_id, driver_id, stint_number)`. The ordered
schema is `session_id:String`, `driver_id:String`, `stint_number:Int16`,
`start_lap_number:Int16`, `end_lap_number:Int16`, `start_time_ms:Int64`,
`end_time_ms:Int64`, `compound:String`, `tyre_life_at_start:Int16`, and
`is_fresh_tyre:Boolean`. The key and `start_lap_number` are required; all
other fields are nullable. Duplicate keys are rejected.

### `weather`

One row is one native session-weather observation. Its key and ascending row
order are `(session_id, session_time_ms)`. The ordered schema is
`session_id:String`, `session_time_ms:Int64`, `air_temperature_c:Float64`,
`humidity_pct:Float64`, `pressure_mbar:Float64`, `rainfall:Boolean`,
`track_temperature_c:Float64`, `wind_direction_deg:Float64`, and
`wind_speed_mps:Float64`. The key is required; every measurement is nullable.
Duplicate keys retain the row with the most non-null measurement values; a
remaining tie uses the lexicographically smallest declared-value tuple.

### `track_status_intervals`

One row represents a status beginning at `start_time_ms`; it does not invent
an end timestamp. `end_time_ms` is the next observed transition time when one
exists and is null for the terminal interval. Its key and ascending row order
are `(session_id, start_time_ms)`. The ordered schema is `session_id:String`,
`start_time_ms:Int64`, `end_time_ms:Int64`, `status:String`, and
`message:String`. `session_id`, `start_time_ms`, and `status` are required;
the end and message are nullable. Duplicate keys are rejected.

### `race_control_messages`

One row is a sparse race-control record and is never expanded into a timeline.
Its key and ascending row order are `(session_id, session_time_ms,
message_index)`. The ordered schema is `session_id:String`,
`session_time_ms:Int64`, `message_index:Int32`, `category:String`,
`flag:String`, `scope:String`, `message:String`, `driver_id:String`, and
`lap_number:Int16`. The key and `message` are required; all context fields are
 nullable. A non-null `driver_id` must resolve through `drivers`. Message/result
driver references may be driver-number aliases; resolve them through the
required `source_driver_key` mapping before emitting canonical `driver_id`.
Duplicate keys are rejected.

### `results`

One row is one driver's classified session result. Its key and ascending row
order are `(session_id, driver_id)`. The ordered schema is `session_id:String`,
`driver_id:String`, `classified_position:String`, `grid_position:Int16`,
`status:String`, `points:Float64`, `laps_completed:Int16`,
`result_time_ms:Int64`, `q1_time_ms:Int64`, `q2_time_ms:Int64`, and
`q3_time_ms:Int64`. The key is required; classification and timing fields are
nullable. `q1_time_ms`, `q2_time_ms`, and `q3_time_ms` are direct FastF1
qualifying-result durations; absent or `NaT` values remain null. Best-lap
timing is deliberately not duplicated here and remains derivable from valid
canonical `laps`. `classified_position` preserves FastF1 values such as `1`,
`R`, and `D`. `driver_id` must resolve through `drivers`. Duplicate keys are
rejected. At browser delivery, qualifying-like v2 sessions surface these
results through the optional `qualifyingSummary` artifact, which also carries
`bestLapNumber`/`bestLapTimeMs` derived from valid (non-deleted) canonical
laps.

## Native cadence and interpolation

- Read `Session.car_data` and `Session.pos_data` as separate sources.
- `car_telemetry` is adapted only from `car_data`; `position_telemetry` is
  adapted only from `pos_data`.
- Do not use merged telemetry as a substitute for either native table when
  preserving source cadence is the goal.
- Do not resample either table to a common frequency.
- Do not interpolate canonical rows or fields. A later browser chunk may use
  the Phase 0 rules for visual interpolation, but those derived values are not
  canonical observations.
- Cadence is descriptive, not a fixed period: timestamp gaps and duplicate
  source timestamps are valid input conditions.
- `source_driver_key` retains the original FastF1 driver-number mapping key;
  `driver_id` is the canonical join identity. Optional driver-number aliases
  are lookup inputs, not replacements for source provenance.

## Null and `NaN` policy

- Missing source values become typed Polars `null`.
- Floating-point `NaN`, positive infinity, and negative infinity become
  `null`; they are not valid measurements.
- Nulls remain null through normalization, sorting, deduplication, and logical
  hashing. Do not fill with zero, empty text, a previous value, or an
  interpolated value.
- Required identity/time fields are not nullable. A row missing a valid
  `session_id`, `driver_id`, or `session_time_ms` is rejected rather than
  emitted with a placeholder.
- `session_id` and `source_driver_key` must be non-empty, non-whitespace
  strings. `driver_id` must be either an uppercase three-letter abbreviation or
  a normalized fallback of the form `D<number>` (without leading zeroes).
- Every required `*_time_ms` field is non-negative. A nullable time is null
  when absent from the source, never inferred from adjacent records.
  `session_start_time_utc` is nullable and, when present, is UTC with
  millisecond precision.

## Driver identifier policy

1. Prefer FastF1's stable three-letter driver abbreviation.
2. Trim surrounding whitespace and normalize ASCII letters to uppercase.
3. Reject an abbreviation that is empty, contains unsupported characters, or
   collides with another driver in the session.
4. If no abbreviation exists, use `D` plus the normalized car number (for
   example, `D44`). Strip leading zeroes before forming the fallback, so `044`
   and `44` both become `D44`; reject missing car numbers and collisions.
5. Keep the original FastF1 dictionary key in `source_driver_key`; it is
   provenance, not the join identity.

The identifier is session-scoped. A driver change or abbreviation correction
must be represented by a new normalized session input, not by silently
changing an identifier during row processing.

Within a session, `source_driver_key` and `driver_id` form a one-to-one mapping:
one source key cannot identify multiple canonical drivers, and one canonical
driver cannot have multiple source keys. The validator rejects either conflict.

## Stable ordering and deduplication

### Sort order

After normalization and deduplication, sort each telemetry table by this
complete key, in ascending order:

```text
session_id, driver_id, session_time_ms
```

This is the persisted logical row order. No grouping, join, hash-map, or
parallel execution order is authoritative.

### Duplicate key

Within each table, rows sharing
`(session_id, driver_id, session_time_ms)` are one duplicate group. Retain
exactly one row using this deterministic rule:

1. Prefer the row with the greatest count of non-null measurement fields.
2. If tied, prefer the row whose `source` has the highest declared provenance
   priority: native stream (`car` or `pos`) before any non-native source.
3. If still tied, canonicalize the remaining field values (including nulls)
   and retain the lexicographically smallest type-aware value tuple in the
   table's declared column order. The scalar order is null, boolean, integer,
   float, then string; it is not Python's mixed-type comparison.
4. If tuples are identical, the rows are equivalent and any one is valid; the
   emitted row is identical.

The final sort happens after this selection. This rule is independent of input
row order and therefore stable under input permutation. Tests must assert both
the retained values and the final order.

For the non-telemetry tables, the table sections above define the canonical
key and policy. Unless a section explicitly states otherwise, duplicate keys
are invalid and must fail with an actionable error rather than depending on
source row order. Accepted source duplicates therefore do not imply that
duplicate canonical keys are emitted. All empty tables retain their declared
ordered schema and typed nullable columns.

## Logical versus byte determinism

This foundation defines the inputs to logical determinism for a fixed input:

- explicit column names and Polars dtypes;
- explicit column order;
- normalized scalar and null representation;
- the sort and retained-row rules above; and
- the schema, ordered rows, and scalar/null values that a future logical hash
  must encode.

It did not originally define or implement logical hash encoding. The current
writer contract specifies the canonical scalar/null encoding, hash algorithm,
Parquet writing, atomic output, and checksum manifest. It does not guarantee
identical Parquet bytes: writer version, compression, row-group boundaries,
metadata, and footer details can change bytes while the logical table stays
equal.

## Boundary with browser chunks

Canonical tables are native-cadence source tables. Phase 0 browser chunks are
derived, column-oriented delivery artifacts with a shared `timeMs` array,
half-open chunk ownership, overlap handoffs, and browser interpolation rules.
Generating those chunks is out of scope for this foundation; their rules must
not be applied backward to canonical source rows.

## Curated pit-loss baseline catalog (browser delivery)

The curated pit-loss baseline catalog is a browser-delivery artifact, not a
canonical table. It is a data-only, repository-local model
(`browser_pit_loss_baseline_catalog.py`); canonical Parquet is never rewritten
for it. Generation resolves Green/VSC/SC values from the catalog fully
offline — no circuit statistic is fetched at runtime, and race observations
are not part of the model (`evidenceCount` is curated source evidence, not
current-race observations).

- **Identity.** One stable `trackId` per physical circuit in the 2024-2026
  calendar union (26 circuits). `browser_pit_loss_track_identity.py` maps
  fixture, track-asset, and alias names onto physical circuits; `bahrain`
  (Sakhir) and `sepang`, and `barcelona-catalunya` and `madring` (Madrid),
  are distinct circuits, and bare names shared by two circuits (for example
  `spanish-grand-prix`) resolve only with a season qualifier. Unknown or
  ambiguous bindings raise instead of guessing.
- **Per-status metadata.** Every Green/VSC/SC value carries `sourceStatus`
  (`direct`/`derived`/`proxy`), a `metricDefinition`
  (`f1-com-lane-plus-stationary`, `measured-total-cost`, or
  `fia-stop-duration`), per-status `provenance`, `evidenceCount`,
  `confidence`, and — for derived/proxy values — an explicit `derivation`
  record. `sourceStatus` is catalog-only and is never serialized into the
  sidecar payload or the frontend.
- **Validation.** The immutable catalog boundary enforces the monotonic
  `SC <= VSC <= Green` invariant, unique `(trackId, fixtureId)` identities,
  non-negative Int64 values, lowercase kebab-case identifiers, HTTPS
  provenance URLs, and the rule that derived/proxy statuses require a
  derivation while direct statuses forbid one. Discounts are not bounded by
  the legacy 5-10 s window; provenance justifies every value outside it.
- **V2 method identity.** `track-status-median-v1` is a method identifier inside
  the v2 pit-loss estimate artifact, not a v1 replay-contract identity. It does
  not authorize v1 artifacts, readers, or mixed-version payloads. Canonical
  Parquet is never rewritten for this browser-delivery artifact. Current-race
  gap-difference estimates are diagnostic-only and never become production
  values.
