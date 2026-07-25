# Replay Data Contract

This v1 contract defines the immutable browser replay artifact. Canonical
Parquet remains unchanged, preserves each native source cadence, and is never
rewritten as part of delivery. Track distance, cumulative race progress,
position, dynamic leaderboard order, and gaps are browser-derived rather than
canonical observations.

## Time and chunks

- All serialized timestamps are absolute integer milliseconds.
- The race window begins at the earliest Lap 1 start; UI time is relative only
  for display.
- `timeMs` is a shared exact-union timeline; arrays are aligned by index.
- Chunk ownership is half-open `[startMs, endMs)`.
- Entries before `authoritativeStartIndex` and overlap arrays are reference-only.
- Production uses 10,000 ms chunks and 1,000 ms overlap; the committed
  deterministic fixture keeps its historical 2,000/500 ms dimensions.

```json
{
  "timeMs": [3599911, 3600120],
  "authoritativeStartIndex": 0,
  "overlap": {"kind": "none"}
}
```

## v1 fields and compatibility

Driver columns retain the nullable v1 shape, including
`trackDistanceMeters`, `gapToLeaderMs`, `position`, and the optional browser-
derived `isFinished` field. `isFinished` is a nullable boolean array when
present; `true` marks a conservatively derived genuine completion, while
`null` means that completion is unavailable. The field may be absent from old
chunks, which remain valid and replayable. Consumers must not replace `null`
with zero, a previous value, a categorical guess, or a fabricated retirement.

`lapStarts` is optional manifest navigation metadata. Each `{lap, startMs}`
entry is immutable, has a positive lap and non-negative absolute timestamp,
and is ordered by strictly increasing lap and nondecreasing timestamp. The
pipeline records the first shared-timeline timestamp at which the displayed
leader enters each indexed lap, so leader changes follow the same dynamic order
used by the browser replay. Consumers must continue to accept manifests without
this index. The JSON Schema validates each marker's structure; the pipeline
manifest/publication models and browser guard enforce cross-entry ordering and
require every timestamp to fall inside the half-open replay interval.

### Optional timeline summary

The manifest may include a compact, optional `timelineSummary` reference. It
lets the browser render full-race status intervals and DNF markers without
loading telemetry chunks:

```json
"timelineSummary": {
  "path": "timeline-summary.json",
  "schemaId": "urn:f1-cache-replay:schema:replay-data:v1:timeline-summary",
  "sha256": "<64 lowercase hexadecimal characters>"
}
```

The referenced `timeline-summary.json` is a `v1` object whose bounds and
entries use absolute integer milliseconds:

```json
{
  "contractVersion": "v1",
  "fixtureId": "bahrain-2024",
  "startMs": 1000,
  "endMs": 2000,
  "intervals": [{"kind": "yellow", "startMs": 1100, "endMs": 1200}],
  "dnfMarkers": [{"driverId": "VER", "timeMs": 1500}]
}
```

- Intervals are half-open, `[startMs, endMs)`, and must fit within the
  summary bounds. Status codes map to `yellow` (2), `sc` (4), `red` (5), and
  `vsc` (6 or 7). Adjacent intervals of the same kind are merged and all
  intervals are clipped to the replay bounds.
- DNF markers come from final non-completion results and the established
  terminal-time derivation, not transient position status. Each driver has at
  most one marker, within the replay bounds.
- Intervals are deterministically ordered by `startMs`, `endMs`, then `kind`;
  markers by `timeMs`, then `driverId`. Publication serializes the artifact
  deterministically and verifies its SHA-256 digest against the manifest
  reference before schema and semantic validation.
- The summary contains no lap markers and no telemetry samples. It is optional:
  older deliveries and manifests without `timelineSummary` remain valid and
  loadable.

The production `projection-quality-gate-v1` assessment is per generation and
source-lap-excluding. Holdout evidence uses native position samples capped at
32 endpoint-inclusive points per lap. It fails closed for insufficient,
malformed, or poor geometry: derived columns remain null and the stable
classified-results fallback order is used. The quality assessment is currently
internal `BrowserDeliveryBuild` provenance; it is not serialized in the v1
manifest in this phase.

Projection rules are metre centerline segment projection, 75 m maximum
residual, one local branch for adjacent segments, and continuity-required
resolution for non-adjacent candidates within a 5 m residual difference.
`geometric-wrap-v1` requires final/initial track ratios of 90%/10% and a
minimum 80% circuit-length decrease, with at most one wrap per timing lap.
Timing-lap boundaries and geometric origin may differ.

## Derived ranking and status semantics

- Race progress is `(lap - 1) * circuitLengthMeters + trackDistanceMeters`.
- The monotonic progress envelope makes ranking deterministic: ties use prior
  order, then `driver_id`; null progress is omitted.
- Pit and terminal modes freeze progress and receive no artificial penalty.
- Active projection freshness is `< 1,000 ms`; at the 1,000 ms stale boundary
  progress becomes null. `OffTrack` is not terminal.
- Finish inference is conservative: a driver is marked finished only when
  final results identify a completion and completed-lap data supplies the
  corresponding finish boundary. The derived finish time follows the final
  position sample; `isFinished` is distinct from `OUT` and does not rewrite
  the raw `status`.
- `status` is exactly `position_telemetry.status`. It is not a retirement
  classifier, and the UI must not fabricate `OUT`.

Gap is the current leader's equivalent-progress crossing time with linear time
interpolation through leader history. There is no constant-speed heuristic. The
leader gap is zero; gaps are null when leader history is insufficient.

## Runtime interpretation

Continuous fields interpolate only between valid same-driver bounds. Display
coordinates (`x`, `y`) permit bounds up to 1,500 ms to bridge bounded global
position-telemetry gaps; `trackDistanceMeters`, speed, throttle, brake, and gap
retain the 1,000 ms cap. Track distance uses circular interpolation across an approved
wrap; an invalid large backward jump returns null. Position, order, lap,
status, pit state, tyre, `isFinished`, and other discrete/categorical fields use
previous-value semantics. Thus, once sampled true, `isFinished` remains true
for later times; it is never interpolated. The sampled current leader is
normalized to zero gap. Direct sample, playback, and seek at the same absolute
time must agree.

## Events and arrays

Events remain sparse point records and are never interpolated. Every driver and
global array stays aligned to `timeMs`; derived arrays follow the same rule.
Chunk ownership and overlap are unchanged, and overlap samples are never new
authoritative observations.

## Consumer example

```ts
const snapshot = sampleReplayAt(replay, absoluteSessionTimeMs)
const row = snapshot.drivers['HAM']
// row.isFinished may be null when the optional field is unavailable.
// A finished row keeps its progress and dynamic order.
if (row.isFinished === true) renderFinishFlag()
```

The dedicated responsive leaderboard uses live order when available, preserves
finished drivers in that order, and renders an accessible finish flag when
`isFinished === true` instead of `GAP`, `Leader`, or `Interval`. Otherwise it
uses `PIT` from `isInPitLane`, raw exact status, and explicit unavailable
markers. Finished is not `OUT`; stale or later `OffTrack` telemetry must not
displace a finished driver or lower a finished leader from P1.

## Current limitations

The one-race Bahrain calibration is provisional pending a multi-circuit corpus.
Gap results depend on available leader history. Finish timing is inferred only
after the final position sample and requires both completion evidence and
completed-lap data. These limits do not invalidate legacy chunks that omit the
optional `isFinished` field.
