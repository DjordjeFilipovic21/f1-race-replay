# ADR-001: Canonical pipeline foundation policies

- **Status:** Accepted
- **Date:** 2026-07-14
- **Scope:** Phase 1 canonical-data pipeline

## Context

Phase 0 defines browser-oriented JSON chunks: one shared timeline, aligned
arrays, chunk ownership, and render-time interpolation. The Phase 1 pipeline
needs a separate, loss-minimizing representation of FastF1 telemetry before
any browser shaping occurs. FastF1 car and position telemetry are independent
native streams with different timestamps and cadences; merged telemetry can
introduce resampling and interpolated values.

## Decision

1. **Use FastF1 `SessionTime` as the canonical clock.** Store non-negative
   integer milliseconds elapsed from the FastF1 session start. Convert the
   source duration to milliseconds using integer half-up rounding; do not
   subtract the first driver sample, first row, or any other observed origin.
   A source value of `12.4995 s` therefore becomes `12500`, while a source
   value of `12.4994 s` becomes `12499`.
   FastF1 `session_start_time` is a duration, not an absolute datetime. For
   absolute race-control timestamps, subtract `session.t0_date`; weather,
   track-status, car, and position times are duration-shaped inputs. A
   duration-shaped race-control value is supported only as an explicit
   compatibility branch.
2. **Keep native streams separate.** Car telemetry and position telemetry are
   separate canonical tables. Their native timestamps, duplicate timestamps,
   missing values, and cadences are preserved after normalization. The
   pipeline does not resample to 10 Hz, 25 Hz, or any other shared frequency.
3. **Do not interpolate canonical data.** No canonical row is synthesized by
   interpolation. Discrete, categorical, and boolean fields are never
   linearly interpolated. Browser interpolation remains a consumer/rendering
   concern described by the Phase 0 contract.
4. **Represent missingness explicitly.** Missing values and floating-point
   `NaN` become typed `null`; they never become zero, an empty string, or a
   fabricated previous value. Non-finite numeric values are invalid
   measurements and are normalized to `null` at the boundary.
5. **Make schema and ordering explicit.** Every canonical table has a declared
   Polars schema and stable column order. Source rows may contain duplicate
   observations; normalization accepts those rows and reduces duplicate
   *canonical keys* deterministically where the schema permits reduction.
   Telemetry retains the row with the most non-null measurements, then the
   highest declared native-source priority, then the lexicographically smallest
   type-aware scalar tuple in declared column order. Tables whose duplicate
   keys are invalid reject them instead. Rows are sorted by the documented
   total key after reduction.
6. **Use stable driver identifiers.** `driver_id` is the normalized uppercase
   FastF1 driver abbreviation when available. A missing abbreviation uses the
   collision-checked `D` + car-number fallback; the original FastF1 key remains
   available as source provenance.
7. **Separate logical and byte determinism.** This foundation fixes the schema,
   column order, row order, and scalar normalization needed for deterministic
   logical table content. It deliberately defers the logical hash encoding and
   implementation to the writer PR, and does not promise identical Parquet file
   bytes across writer versions or environments.
8. **Keep loading separate from adaptation.** Adapters are I/O-free: they
   consume an injected, already-loaded FastF1-compatible session. An injected
   factory passed to `load_session` performs the one session creation/load and
   may invoke FastF1 cache or network behavior. Passing an already-loaded
   `session` skips loading and never calls `session.load()` again.

## Consequences

- Consumers can compare and join native car/position data without mistaking
  synthesized samples for source observations.
- The canonical tables may have different row counts and timestamp sets.
- Consumers must handle nulls and must choose any later alignment or
  interpolation policy explicitly.
- The writer PR will define and test deterministic logical hashes before it
  publishes artifacts.
- Browser chunks remain a derived delivery format; they are not canonical
  tables and must not be used to redefine source cadence.
- FastF1 `car_data` and `pos_data` remain separate native streams. They are not
  joined, resampled, or interpolated by this foundation.

## Deferred to the next PR

This foundation does **not** implement or promise:

- Parquet writing settings, including codec, row groups, metadata, or writer
  implementation;
- temporary-file writes, flush/fsync, atomic replacement, or recovery behavior;
- a checksum manifest or byte-level Parquet hashes.
- logical hash scalar/null encoding, hash algorithm, or implementation.
- telemetry performance optimization; preserving correctness and native rows
  takes priority, and performance work is deferred to a later PR.

The next writer PR will define those artifact, transport, and logical-hash
policies. Its byte hashes must not be confused with logical-table identity.

## References

- [Canonical pipeline schema and policies](../canonical-pipeline-schema.md)
- [Phase 0 replay data contract](../replay-data-contract.md)
- FastF1 compatibility target: `fastf1>=3.8,<3.9`
- Polars compatibility target: `polars>=1.40,<2`
