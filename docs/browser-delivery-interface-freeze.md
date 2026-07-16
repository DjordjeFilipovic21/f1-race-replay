# Browser delivery interface freeze

**Status:** Accepted · **Version:** browser-delivery-v1 · **Date:** 2026-07-15

This document freezes the Phase 2 boundary between validated canonical Parquet
and browser replay artifacts. Browser data is derived; canonical tables remain
the loss-minimizing source of truth.

## 1. Canonical-generation reader

The reader accepts only `target_parent: Path`. It resolves the canonical
`current.json` pointer, then calls the complete-generation validator before
reading any table. Invalid, incomplete, changed, or hash-mismatched generations
are rejected; a directory name is never sufficient selection evidence.

The result is an immutable `CanonicalGenerationSnapshot` containing:

```text
generation_id: str
manifest_sha256: str
frames: immutable mapping[str, validated Polars DataFrame]
```

The mapping and metadata are immutable, tables retain the declared canonical
schema and row order, and the reader never writes, resamples, interpolates, or
republishes Parquet. The ten tables are read with native Polars only after
pointer, manifest, schema, row-count, logical-hash, and byte-hash validation.

## 2. Browser fields and missing values

The delivery transform uses exact integer-millisecond timestamps. It does not
alter canonical rows.

| Browser field | Canonical source | Rule |
| --- | --- | --- |
| `x`, `y` | `position_telemetry.x/y` | Divide raw FastF1 decimetres by 10 to metres; exact timestamp alignment, otherwise `null`. |
| `speed` | `car_telemetry.speed_kph` | Preserve the canonical numeric value; otherwise `null`. |
| `throttle` | `car_telemetry.throttle_pct` | Preserve the canonical numeric value; otherwise `null`. |
| `brake` | `car_telemetry.brake` | Convert `false` to `0`, `true` to `1`, and preserve `null`. |
| `gear`, `drs` | `car_telemetry` | Exact nullable discrete value; otherwise `null`. |
| `status` | `position_telemetry.status` | Exact nullable categorical value; otherwise `null`. |
| `lap`, `tyreCompound` | `laps` | Use the row whose interval contains the time; no containing row means `null`. |
| `isInPitLane` | `laps` pit interval | `true` only inside a known pit interval, `false` only when a known non-pit interval applies; otherwise `null`. |
| `trackDistanceMeters`, `gapToLeaderMs`, `position` | No v1 canonical source | Always `null`; do not infer or fabricate values. |
| `trackStatusCode` | `track_status_intervals.status` | Use the active interval; map the documented status code, otherwise `null`. |
| `weatherState` | `weather` | Use the latest known native weather observation at or before the time; no observation means `null`. |
| `leaderboardOrder` | `results` | Stable classified-position order, with unresolved/tied entries retained deterministically by `driver_id`. |
| `events` | `race_control_messages` | Emit sparse point records at their source timestamp only. |

Null is preserved. A missing continuous value is not replaced by zero or a
previous value, and a missing discrete/categorical value is not invented. The
manifest’s driver metadata comes from `drivers`; static geometry comes from the
validated track-assets input associated with the delivery build.

## 3. Shared timeline and semantics

For each delivery, `timeMs` is the sorted, unique union of valid native
timestamps needed by the selected driver/global fields, represented as signed
64-bit integer milliseconds. Alignment is an exact-key, null-preserving left
join; it is not canonical resampling, upsampling, interpolation, or cadence
replacement. Source timestamps and native cadence remain unchanged in the
canonical generation.

At render time, consumers may linearly interpolate continuous numeric fields
(`x`, `y`, `speed`, `throttle`, `brake`, `gapToLeaderMs`) only between two valid
authoritative bounds for the same driver. Discrete, categorical, and boolean
fields (`lap`, `position`, `gear`, `drs`, tyre/status fields, pit state, weather,
track status, and leaderboard order) use previous-value semantics. Sparse events
are point records and are never interpolated. If either continuous bound is
missing, the rendered value is missing.

## 4. Chunks

Production chunks cover **10,000 ms** with a **1,000 ms** intentional handoff
overlap. Coverage is half-open: `[startMs, endMs)`. The first sample at or after
`startMs` is authoritative; samples before it are overlap-only references.
`authoritativeStartIndex` identifies that first authoritative sample, and
`overlap.authoritativeFromMs` equals `startMs`.

Chunks are ordered by ascending `sequence`, have contiguous boundaries, and are
named `chunks/chunk-{sequence:03d}.json` (`chunk-001.json`, `chunk-002.json`, …).
The manifest order is authoritative; filesystem or parallel-read order is not.
Events belong to the chunk whose authoritative interval contains their
timestamp. A consumer resolves duplicate timestamps using the owning chunk, not
the overlap reference.

The committed `deterministic-race` fixture is a compatibility fixture: its
existing two chunks, 2,000 ms boundaries, 500 ms overlap, golden snapshots, and
all bytes remain unchanged. Production chunk sizing must not rewrite that
fixture.

## 5. Immutable delivery artifacts

The public models are frozen value objects:

```text
BrowserManifest(
  fixture_id, fixture_name, drivers
)
BrowserDeliveryBuild(source_snapshot, manifest, track_assets, chunks)
BrowserChunk(
  chunk_id, sequence, start_ms, end_ms, overlap,
  time_ms, authoritative_start_index, drivers,
  leaderboard_order, track_status_code, weather_state, events
)
```

Arrays are ordered tuples (or equivalent immutable sequences), driver maps are
read-only, and no model exposes mutable publication state. Serialization uses
explicit field order and deterministic JSON: sorted object keys, compact
separators, UTF-8, no NaN/infinity, and exactly one trailing newline.
`BrowserDeliveryBuild` permanently binds output to the exact validated snapshot
used for derivation; publication never resolves the canonical pointer again.

## 6. Publication boundary

Browser output is published separately from canonical output:

```text
<browser_parent>/
├── browser-current.json
└── generations/<delivery-id>/
    ├── manifest.json
    ├── track-assets.json
    └── chunks/chunk-001.json ...
```

`delivery-id` is caller-supplied and safe as one path component. The browser
manifest records `delivery_version`, the exact source canonical
`generation_id` and `manifest_sha256`, ordered chunk metadata, and per-artifact
SHA-256 digests. Exact staged bytes, identities, hashes, aligned columns,
contiguous ownership, overlap, and event bounds are validated before
`browser-current.json` is atomically replaced. Publication rejects symlink
traversal and uses descriptor-relative no-follow writes and owned cleanup. The
caller supplies a local v1 schema root; manifest, track assets, and every chunk
must pass its Draft 2020-12 schema without remote retrieval. Pointer replacement
is the sole browser visibility/commit point.

This publication never edits canonical `current.json`, never selects an
unvalidated generation, and never copies or republishes canonical Parquet.
Rebuilding from the same validated source generation and inputs produces the
same manifest, chunk bytes, names, ordering, and digests.

## 7. v1 compatibility requirement

The contract remains **v1**. Its schemas must be generalized only to remove
fixture-specific constants: manifest metadata must support arbitrary valid
fixture identifiers and names, and `chunks` must support any non-empty ordered
chunk count with the declared sequencing and ownership invariants. The
committed deterministic fixture and its golden snapshots remain schema-valid
and unchanged. Breaking changes require a new contract-version directory.

## 8. Track generation and measured cadence

Track assets may be deterministically derived from the shortest accurate,
non-deleted, non-pit canonical lap with usable position telemetry. FastF1 raw
`X/Y` values are divided by 10 to metres, closed, arc-length resampled to 600
points, and offset by a fixed 20 m visual width. Boundaries are illustrative,
not surveyed circuit limits. Collinear, out-and-back, zero-area, and otherwise
degenerate geometry fails closed. A 2D crossing alone is not rejected because
grade-separated layouts such as Suzuka are legitimate.

The Bahrain 2024 race measured 72,015 exact union timestamps, 935 published
chunks, 126.48 MB raw JSON, and 7.61 MB of individually gzip-compressed chunks.
Median compressed chunk size was 10.5 KB and p95 was 11.5 KB. The MVP therefore
retains exact-union timestamps and relies on HTTP compression rather than
introducing a new sampling cadence.

See [Replay Data Contract](replay-data-contract.md), [Canonical Parquet Writer
Contract](canonical-parquet-writer-contract.md), [ADR-001](adr/001-canonical-pipeline-foundation.md),
and [ADR-002](adr/002-canonical-parquet-writer.md).
