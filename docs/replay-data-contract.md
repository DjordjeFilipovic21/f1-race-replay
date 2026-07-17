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
`trackDistanceMeters`, `gapToLeaderMs`, and `position`. Existing null-only
generations remain valid and replayable. `null` means unavailable; consumers
must not replace it with zero, a previous value, a categorical guess, or a
fabricated retirement.

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
- Terminal inference from final results is conservative and follows the final
  position sample.
- `status` is exactly `position_telemetry.status`. It is not a retirement
  classifier, and the UI must not fabricate `OUT`.

Gap is the current leader's equivalent-progress crossing time with linear time
interpolation through leader history. There is no constant-speed heuristic. The
leader gap is zero; gaps are null when leader history is insufficient.

## Runtime interpretation

Continuous fields (`x`, `y`, `trackDistanceMeters`, `speed`, `throttle`,
`brake`, `gapToLeaderMs`) interpolate only between valid same-driver bounds
within 1,000 ms. Track distance uses circular interpolation across an approved
wrap; an invalid large backward jump returns null. Position, order, lap,
status, pit state, tyre, and other discrete/categorical fields use previous
semantics. The sampled current leader is normalized to zero gap. Direct sample,
playback, and seek at the same absolute time must agree.

## Events and arrays

Events remain sparse point records and are never interpolated. Every driver and
global array stays aligned to `timeMs`; derived arrays follow the same rule.
Chunk ownership and overlap are unchanged, and overlap samples are never new
authoritative observations.

## Consumer example

```ts
const snapshot = sampleReplayAt(replay, absoluteSessionTimeMs)
const row = snapshot.drivers['HAM']
// row.position and row.gapToLeaderMs may be null; preserve that state in UI.
```

The dedicated responsive leaderboard uses live order when available, `PIT` from
`isInPitLane`, raw exact status otherwise, and explicit unavailable markers.

## Current limitations

The one-race Bahrain calibration is provisional pending a multi-circuit corpus.
Gap results depend on available leader history. Terminal timing is inferred only
after the final position sample. These limits do not invalidate legacy null-only
v1 artifacts.
