# Browser replay-engine runtime semantics

The runtime is a read-only interpretation of browser chunks. It does not
rewrite, resample, or publish delivery data. Canonical Parquet remains native
cadence and unchanged; track distance, cumulative progress, position, dynamic
order, and gaps are browser-derived fields.

## Timeline and ownership

Delivery starts at the earliest non-null Lap 1 `lap_start_time_ms`. Timestamps
remain absolute integer `sessionTimeMs`; controls display elapsed time relative
to the race start. Chunk ownership remains half-open `[startMs, endMs)`, and
samples before `authoritativeStartIndex` are overlap-only references. Overlap
samples do not create duplicate authority.

The sampler searches valid values for the requested field, not merely adjacent
shared-timeline rows:

- continuous values interpolate only between same-driver valid bounds. Display
  coordinates (`x`, `y`) permit a bound interval up to 1,500 ms to bridge
  bounded global position-telemetry gaps; other continuous fields retain the
  1,000 ms limit. Longer intervals produce `null`;
- position, lap, status, pit state, tyre, and other discrete/categorical fields
  use previous-value semantics; no forward fill or invention occurs;
- sparse events are exact-time records.

Example: a null coordinate at `12,000` ms is not bridged from `10,000` to
`12,001` ms for a non-coordinate continuous field, because that field's valid
bound interval exceeds its 1,000 ms cap.

## Circular track-distance sampling

For `trackDistanceMeters`, interpolation follows the approved circular branch
when the lower value is in the final 10%, the upper value is in the initial
10%, and the backward decrease is at least 80% of circuit length. It adds one
circuit length for interpolation and wraps the result back into the circuit.
An invalid large backward jump returns `null`; it is not silently smoothed.

The same `geometric-wrap-v1` ratios govern production derivation. Projection is
onto metre centerline segments with a 75 m maximum residual; adjacent segments
are one local branch, while non-adjacent candidates within 5 m require prior
continuity. At most one wrap is allowed per timing lap, and timing-lap and
geometric-origin boundaries may differ.

## Live semantics

The production gate is per generation, source-lap-excluding, native-cadence,
and bounded to 32 samples per holdout lap. It is fail-closed. Failed,
insufficient, or malformed legacy geometry leaves derived arrays null and
falls back to stable classified-results order; old null-only v1 generations
remain valid and replayable.

Progress is monotonic through an envelope. Ranking ties resolve by prior order,
then driver ID. Null progress is omitted, while pit and terminal modes freeze
progress rather than receiving an artificial penalty. Active missing projection
freezes the last valid progress only before the 1,000 ms stale boundary; at the
boundary it becomes null. `OffTrack` is not terminal. Terminal inference from
final results is conservative and only follows the final position sample.

The serialized `status` remains the exact `position_telemetry.status` value.
The UI must not infer retirement or fabricate `OUT` from a missing status.

`gapToLeaderMs` is derived from the current leader's equivalent-progress
crossing time, using linear time interpolation through available leader
history. There is no constant-speed heuristic. The leader is zero; insufficient
leader history produces null. At sampling time, the current leader is normalized
to zero when its sampled position is 1, including after continuous interpolation.

## Deterministic control behavior

`sampleReplayAt`, playback, and seek use the same prepared sampler and therefore
produce the same snapshot for the same absolute time. Seeking clamps to bounds;
the frame elapsed-time cap is 1,000 ms; reverse playback is unsupported. A
backward seek resets event-crossing state, while exact-time events remain sparse.

```ts
const direct = sampleReplayAt(replay, timeMs)
controller.seek(timeMs)
// controller.getSnapshot().replay matches direct at the same timeMs.
```

The engine remains React-free; UI components subscribe to its immutable
snapshot rather than owning clock, cache, or sampling state.

## UI contract

The dedicated responsive leaderboard is semantic and accessible. It orders
rows from live `leaderboardOrder` when available, displays `PIT` from the pit
flag, otherwise displays the raw exact status, and uses unavailable markers for
null position, gap, tyre, or status. It must not fabricate `OUT`.

Exact elapsed-time input uses `H:MM:SS.mmm`, is relative to the delivery start,
and rejects malformed or out-of-window values rather than clamping them. It
seeks the controller at `startMs + elapsedMs`. When optional manifest
`lapStarts` metadata is present, the lap control seeks its absolute timestamp;
without it, the lap control is disabled with an explanatory message while exact
time and range seeking remain available.

## Current limitations

The one-race Bahrain calibration is provisional pending a multi-circuit corpus.
Gap availability depends on leader-history coverage. Terminal timing is inferred
after the final position sample, not read as a canonical terminal timestamp.
Quality assessment is internal `BrowserDeliveryBuild` provenance and is not in
the serialized v1 manifest in this phase.
