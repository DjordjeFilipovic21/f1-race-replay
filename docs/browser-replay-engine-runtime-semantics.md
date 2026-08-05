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
- position, lap, status, pit state, tyre, `isFinished`, and other
  discrete/categorical fields use previous-value semantics; no interpolation,
  forward fill, or invention occurs. `isFinished` is optional and nullable, so
  an old chunk without the field samples as unavailable rather than false;
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
then driver ID. Null progress is omitted, while pit and finished modes freeze
progress rather than receiving an artificial penalty. A finished driver remains
in dynamic leaderboard order; later stale or `OffTrack` telemetry cannot
displace it, including a finished leader that must remain P1. Active missing
projection freezes the last valid progress only before the 1,000 ms stale
boundary; at the boundary it becomes null. `OffTrack` is not terminal.

The browser-derived `isFinished` marker is conservative. It becomes true at a
finish time supported by final results identifying completion and completed-lap
data, with the boundary following the final position sample. It is not a raw
status and must not be converted to `OUT`. Once true, previous-value sampling
keeps it true for later samples; unavailable evidence remains `null`.

The serialized `status` remains the exact `position_telemetry.status` value.
The UI must not infer retirement or fabricate `OUT` from a missing status.

`gapToLeaderMs` is derived from the current leader's equivalent-progress
crossing time, using linear time interpolation through available leader
history. There is no constant-speed heuristic. The leader is zero; insufficient
leader history produces null. At sampling time, the current leader is normalized
to zero when its sampled position is 1, including after continuous interpolation.

## Pit-loss estimate semantics

When the optional `pitLossEstimateSidecar` is loaded, pit-loss position
prediction uses the replay snapshot's exact `trackStatusCode` and the static
generation-time values resolved from the versioned, repository-local pit-loss
baseline catalog:

- code `1` (All Clear) selects the Green value;
- code `4` selects the Safety Car value;
- codes `6` and `7` select the Virtual Safety Car value.

The catalog covers the 2024-2026 physical-circuit union (26 circuits) with one
stable entry per circuit, so the same Green/VSC/SC values resolve across
seasons; `bahrain` (Sakhir) and `sepang`, and `barcelona-catalunya` and
`madring` (Madrid), are distinct entries. Curated
(`curated-track-baseline-v1`) sidecars always carry Green, Safety Car, and
Virtual Safety Car values, each a single replay-start point, so known catalog
values are available from replay start even when Safety Car or Virtual Safety
Car did not occur in the current race. Australia resolves to Green 19300 ms,
VSC 12300 ms, SC 9300 ms.

Each catalog status is `direct`, `derived`, or `proxy` with its own metric
definition, provenance, evidence count, and confidence, but that internal
`sourceStatus` is catalog-only: it is never serialized into the sidecar
payload or the browser types/guards, and it is not rendered. Catalog provenance,
evidence count, confidence, and derivation details likewise remain internal;
the sidecar exposes only identity, method, and status timelines. The panel
renders the selected status label and produces no `Baseline` label for curated
values. Curated timelines never carry `observedSampleCount`, and current-race
observation counts are not fabricated for catalog values. Current-race
gap-difference estimates remain diagnostic-only and never change a resolved
value.

Unknown or unsupported statuses (yellow, red, missing, mixed) do not reuse the
Green value: for a curated sidecar they fail closed to unavailable, falling
back only to a legacy `pitLossModel` when the delivery still carries one. An
unknown track never receives a fabricated 22 s value — generation emits no
curated sidecar for tracks without a catalog entry, and the browser shows
pit-loss data as unavailable unless a legacy model is present.

Legacy `track-status-median-v1` sidecars remain readable: their
status-specific timelines are emitted only when the status occurred, and
unavailable or omitted statuses fall back to the race timeline. Older
deliveries without the sidecar continue to use the legacy pit-loss model or
show pit-loss data as unavailable; the replay sampler and controller remain
unchanged.

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
rows from live `leaderboardOrder` when available and, for a finished row,
renders an accessible finish flag instead of `GAP`, `Leader`, or `Interval`.
Otherwise it displays `PIT` from the pit flag, the raw exact status, and
unavailable markers for null position, gap, tyre, or status. It must not
fabricate `OUT`; old chunks without `isFinished` continue using the existing
fallback display.

Exact elapsed-time input uses `H:MM:SS.mmm`, is relative to the delivery start,
and rejects malformed or out-of-window values rather than clamping them. It
seeks the controller at `startMs + elapsedMs`. When optional manifest
`lapStarts` metadata is present, the lap control seeks its absolute timestamp;
without it, the lap control is disabled with an explanatory message while exact
time and range seeking remain available.

## Current limitations

The one-race Bahrain calibration is provisional pending a multi-circuit corpus.
Gap availability depends on leader-history coverage. Finish timing is inferred
after the final position sample from completion and completed-lap evidence, not
read as a canonical terminal timestamp. Legacy chunks may omit `isFinished`.
The curated pit-loss catalog covers the full 2024-2026 physical-circuit union
(26 circuits); low-evidence circuits carry explicit derived/proxy estimates
with provenance and low or medium confidence, and generation is fully offline
(no catalog value is fetched from the network at build time). Gap-difference
pit-loss estimates are diagnostic-only and never become production values.
Quality assessment is internal `BrowserDeliveryBuild` provenance and is not in
the serialized v1 manifest in this phase.
