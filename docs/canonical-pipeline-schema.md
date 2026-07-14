# Canonical pipeline schema and policies

This is the Phase 1 logical contract for native-cadence tables produced from
FastF1. It is separate from the [Phase 0 browser chunk
contract](replay-data-contract.md): canonical tables preserve source rows;
browser chunks align data for delivery and may support render-time
interpolation.

## Time policy

`session_time_ms` is an `Int64` containing FastF1 `SessionTime` elapsed from the
session start. It is not relative to the first driver sample.

For a non-negative source duration, convert without floating-point epoch
arithmetic and round half up:

```python
session_time_ms = (session_time_nanoseconds + 500_000) // 1_000_000
# 12.4995 seconds -> 12500; 12.4994 seconds -> 12499
```

`session_time_ms` must be non-negative. A missing or invalid timestamp rejects
the row; timestamps are never invented from neighboring rows.

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

## Native cadence and interpolation

- Read `Session.car_data` and `Session.pos_data` as separate sources.
- Do not use merged telemetry as a substitute for either native table when
  preserving source cadence is the goal.
- Do not resample either table to a common frequency.
- Do not interpolate canonical rows or fields. A later browser chunk may use
  the Phase 0 rules for visual interpolation, but those derived values are not
  canonical observations.
- Cadence is descriptive, not a fixed period: timestamp gaps and duplicate
  source timestamps are valid input conditions.

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
   and retain the lexicographically smallest value tuple in the table's
   declared column order.
4. If tuples are identical, the rows are equivalent and any one is valid; the
   emitted row is identical.

The final sort happens after this selection. This rule is independent of input
row order and therefore stable under input permutation. Tests must assert both
the retained values and the final order.

## Logical versus byte determinism

This foundation defines the inputs to logical determinism for a fixed input:

- explicit column names and Polars dtypes;
- explicit column order;
- normalized scalar and null representation;
- the sort and retained-row rules above; and
- the schema, ordered rows, and scalar/null values that a future logical hash
  must encode.

It does not define or implement logical hash encoding yet. The writer PR will
specify the canonical scalar/null encoding and hash algorithm alongside Parquet
writing, atomic output, and the checksum manifest. It also will not guarantee
identical Parquet bytes: writer version, compression, row-group boundaries,
metadata, and footer details can change bytes while the logical table stays
equal.

## Boundary with browser chunks

Canonical tables are native-cadence source tables. Phase 0 browser chunks are
derived, column-oriented delivery artifacts with a shared `timeMs` array,
half-open chunk ownership, overlap handoffs, and browser interpolation rules.
Generating those chunks is out of scope for this foundation; their rules must
not be applied backward to canonical source rows.
