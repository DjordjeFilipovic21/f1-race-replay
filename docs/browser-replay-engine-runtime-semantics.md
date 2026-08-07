# Browser replay-engine runtime semantics

The runtime is a read-only interpretation of browser chunks. It does not
rewrite, resample, or publish delivery data. Canonical Parquet remains native
cadence and unchanged; track distance, cumulative progress, position, dynamic
order, and gaps are browser-derived fields.

The web reader is **v2-only**: it loads `browser-delivery-v2` pointers and `v2`
manifests (`contractVersion` `v2`) and rejects v1 pointers, manifests, and
sidecars. V1 fixtures remain in the repository only as frozen historical
baselines and are never loaded by the runtime.

## Timeline and ownership

Delivery starts at the earliest non-null Lap 1 `lap_start_time_ms`. For
non-race sessions (`practice`, qualifying-like, `testing`) there is no race
start: delivery starts at the earliest published lap start (the first chunk
boundary) and elapsed time is session-relative. Race-order controls are
disabled for those modes and no race gap, finish, DNF, or pit-loss claim may
be derived from them. Timestamps
remain absolute integer `sessionTimeMs`; controls display elapsed time relative
to the delivery start. Chunk ownership remains half-open `[startMs, endMs)`, and
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
falls back to stable classified-results order; null-only v2 generations remain
valid and replayable, and frozen v1 generations stay valid only as historical
fixtures (the reader is v2-only).

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
fabricate `OUT`; chunks without `isFinished` continue using the existing
fallback display.

Exact elapsed-time input uses `H:MM:SS.mmm`, is relative to the delivery start,
and rejects malformed or out-of-window values rather than clamping them. It
seeks the controller at `startMs + elapsedMs`. When optional manifest
`lapStarts` metadata is present, the lap control seeks its absolute timestamp;
without it, the lap control is disabled with an explanatory message while exact
time and range seeking remain available.

## Session modes and capabilities

The manifest `sessionMode` is one of `race`, `practice`, `qualifying`,
`sprint`, `sprint-qualifying`, `sprint-shootout`, or `testing`.
`createSessionCapabilities` derives the truthful UI surface from the mode and
the delivered artifacts:

- `isRaceLike` is `race` or `sprint`; `isQualifyingLike` is `qualifying`,
  `sprint-qualifying`, or `sprint-shootout`.
- Race controls are enabled only for race-like modes:
  `canShowRaceOrder` (always), `canShowRaceTimeline` (when `timelineSummary`
  is delivered), `canShowTyreStrategy` (when `stintSummary` is delivered), and
  `canShowPitLoss` (when `pitLossModel` is delivered).
- Qualifying controls are enabled only for qualifying-like modes:
  `canShowQualifyingClassification` (when `qualifyingSummary` is delivered),
  `canFilterQualifyingLapStatus` (when `qualifyingLapStatus` is delivered),
  and `canShowQualifyingTimeline` (when `qualifyingTimeline` is delivered).
- The loader guard rejects artifacts that a mode cannot truthfully carry:
  `timelineSummary`/`pitLossModel` for `practice`, `testing`, and
  qualifying-like modes; `qualifyingSummary`/`qualifyingLapStatus`/
  `qualifyingTimeline` for every non-qualifying-like mode.

Non-race sessions never fabricate race semantics: no race order, gap, finish,
DNF, or pit-loss claim is shown, and lap/sector timing or classification is
never presented as race gaps or finish order.

### Qualifying classification and lap status at runtime

- `qualifyingSummary` drives the qualifying classification panel. It is
  optional: when absent, `canShowQualifyingClassification` is false and the
  panel is hidden. Nullable columns (`qualifyingPosition`, `q1TimeMs`,
  `q2TimeMs`, `q3TimeMs`, `bestLapNumber`, `bestLapTimeMs`) render as
  unavailable; the runtime never fills them.
- `qualifyingLapStatus` is applied causally. An event is effective when
  `eventTimeMs <= replayTimeMs`: a `deleted` event hides the lap's timing,
  sectors, and bests, and a `reinstated` event restores them at its event
  time. Playback and arbitrary seeks use the same comparison.
- Unknown handling is explicit: `selectQualifyingLapStatus` returns `null`
  (unknown) for a driver or lap with no published record, which is distinct
  from `valid`. With the sidecar present, only laps whose causal status is
  `valid` contribute timing, sectors, and bests; deleted and unknown laps are
  hidden from timing and bests, and candidate filtering keeps only explicit
  valid evidence. With the sidecar absent, no invalidation filtering is
  applied (absence means no invalidation is known, not that every lap is
  valid).

### Qualifying flying-lap and phase-local finish at runtime

- The lap/sector sidecar's `lapKind` column is the authoritative per-lap
  classification for qualifying-like sessions (`flying`, `outlap`, `inlap`, or
  `unknown`). Only explicit `flying` laps contribute to qualifying bests,
  fastest-lap timing, charts, summaries, sector history, and live qualifying
  timing; `outlap`, `inlap`, and `unknown` laps never do.
- The column is aligned with the other per-driver columns. When it is absent
  from a qualifying-like sidecar the runtime fails closed: no lap may be
  treated as flying. Non-qualifying sidecars (race/sprint/practice) keep the
  legacy all-laps behavior because they never emitted the column.
- `unknown` is the fail-closed value for insufficient source evidence (for
  example an ambiguous non-pit cooldown lap that cannot be distinguished from
  a slow clean lap, or a lap carrying both pit signals). It is distinct from
  `false` or `OUT` and contributes no timing.
- Finish is phase-local and flying-lap-local: a driver is finished when the
  last `flying` lap in the active Q phase (from the sidecar `phaseBoundaries`
  and per-lap `qualifyingPhase`) has completed causally
  (`lapEndMs <= replayTimeMs`) with timing status not deleted/unknown. A later
  cooldown or pit-in lap never delays finish and never replaces the displayed
  qualifying time; the driver's ultimate session lap is irrelevant unless it
  is that flying lap. A driver progressing to Q2/Q3 still receives finish for
  their last flying lap in Q1/Q2 respectively.

### Qualifying timeline and incident markers at runtime

- `qualifyingTimeline` is applied causally like the race timeline but never
  carries race semantics. `intervals` render yellow/red track-status bands;
  `incidentMarkers` hide the corresponding track-map marker while
  `timeMs <= replayTimeMs`. Playback and arbitrary seeks use the same
  comparisons.
- Markers are visibility-only. Hiding a marker does not change the driver's
  classification, does not fabricate `OUT`/DNF, and is not derived from
  `PositionData` `OffTrack` (FastF1 backfills that value for missing samples
  and it is not a retirement signal).
- A marker `source` is either `race-control-car-event` (canonical CarEvent
  terminal evidence) or `red-flag-position-freeze` (visibility-only inference
  from position telemetry at the exclusive end of a finite red interval when a
  driver had valid pre-red x/y evidence but no meaningful post-red movement).
  Both sources hide the track-map marker identically; the source only records
  how the marker was derived. Unknown sources are rejected at parse time.
- When the artifact is absent, no intervals render and no markers hide
  (fail-closed); absence never means "no incident occurred".

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
the serialized browser manifest. Non-race mode gating is enforced by the
schema and loader guard; the committed deterministic fixture exercises the
race path, while qualifying-like artifacts are validated by the v2 schemas and
selectors rather than a committed fixture.
