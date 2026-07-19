# Browser delivery interface freeze

**Status:** Accepted · **Version:** browser-delivery-v1 · **Date:** 2026-07-17

This is the boundary between validated canonical Parquet and browser replay
artifacts. Canonical Parquet is unchanged and remains the native-cadence,
loss-minimizing source. Browser delivery derives presentation fields without
mutating, resampling, or republishing canonical rows.

## 1. Timeline and provenance

The race window begins at the earliest non-null canonical Lap 1
`lap_start_time_ms`. Serialized `sessionTimeMs` values remain absolute integer
milliseconds; the UI displays time relative to that race start. A missing Lap 1
start fails closed.

Canonical observations include coordinates, telemetry, lap data, exact
`position_telemetry.status`, and classified results. The following are always
browser-derived, not canonical observations:

- track distance and cumulative race progress;
- live position and dynamic `leaderboardOrder`; and
- `gapToLeaderMs`.

Canonical source cadence and source rows remain unchanged. `x`/`y` are converted
from FastF1 decimetres to metres at delivery; other source fields retain their
nullable values.

## 2. Nullable v1 shape

The existing nullable v1 shape remains backward compatible. Old null-only
generations are valid and replay normally. Derived columns are aligned arrays:

```json
{
  "trackDistanceMeters": [null, 42.5],
  "position": [null, 1],
  "gapToLeaderMs": [null, 0.0]
}
```

`null` means unavailable; it is not zero, a guessed status, or a fabricated
retirement. Failed, insufficient, or malformed legacy geometry produces null
derived columns and uses the stable classified-results fallback order. Replay
still works. Quality assessment is currently internal
`BrowserDeliveryBuild` provenance and is not added to the serialized v1
manifest in this phase.

## 3. Projection and quality gate

`projection-quality-gate-v1` runs independently for every generation. It is
source-lap-excluding: the deterministic reference lap used to create the track
asset is excluded from quality metrics. Each eligible holdout timing lap is
sampled at native timestamps, bounded to 32 endpoint-inclusive stratified
points per lap, and no telemetry merge or resampling is used. The gate is
fail-closed: at least 20 independent laps, 500 independent samples, residual
p95 at most 25 m, maximum residual at most 75 m, and valid continuity are
required. Pit laps are measured separately and do not enter clean-track
thresholds.

Production projection is a metre-coordinate centerline segment projection:

- residuals over 75 m are invalid;
- adjacent candidate segments form one local branch;
- non-adjacent candidates within a 5 m residual difference are ambiguous and
  require continuity from the previous accepted distance;
- unresolved or malformed geometry yields `null` derived values.

`geometric-wrap-v1` accepts at most one wrap per timing lap only when the prior
distance is in the final 10% (`>= 90%`), the next is in the initial 10%
(`<= 10%`), and the decrease is at least 80% of circuit length. A timing-lap
boundary and the geometric centerline origin may differ by native samples.

## 4. Progress, ranking, and gaps

- Progress uses `(lap - 1) * circuitLengthMeters + trackDistanceMeters`.
- An active observation is stale at the 1,000 ms boundary: missing projection
  may freeze the last valid progress only while younger than 1,000 ms; at
  `>= 1,000 ms` it becomes null.
- Pit and terminal modes freeze the last valid progress without an artificial
  ranking penalty. `OffTrack` is not terminal. Terminal inference from final
  results is conservative and occurs after the final valid position sample.
- Ranking applies a monotonic progress envelope. Ties use prior order, then
  `driver_id`; drivers with null progress are omitted from live ranking.
- Gap is the current leader's equivalent-progress crossing time, with linear
  time interpolation through the leader's history. There is no constant-speed
  heuristic. The leader is zero; a gap is null when leader history is
  insufficient.

The exact source status is preserved:

```text
status       = position_telemetry.status
leaderboard  = browser-derived live order (or classified-results fallback)
```

The browser `status` column is never converted into a fabricated `OUT` label.

## 5. Chunks and immutable publication

Production chunks remain 10,000 ms with a 1,000 ms handoff overlap and
half-open ownership `[startMs, endMs)`. Derived arrays are aligned to the
shared exact-union timeline. Samples before `authoritativeStartIndex` are
overlap references only; they do not become authoritative again at a handoff.
Manifest order, deterministic JSON, finite values, and immutable build-to-source
binding remain unchanged. Publication never edits canonical `current.json`.
Before staging, publication validates direct immutable contract objects with
reused, local-only `jsonschema-rs` Draft 2020-12 validators, then serializes and
hashes each artifact once. Format validation is enabled and unknown formats are
rejected at validator construction; Python `jsonschema` remains the differential
test oracle rather than a publication hot-path dependency.
Staged descriptors are verified by size and streaming SHA-256 without retaining
a second full-file byte copy.

See [Replay Data Contract](replay-data-contract.md) and
[runtime semantics](browser-replay-engine-runtime-semantics.md).

## 6. Current limitations

Calibration is currently provisional from one Bahrain race and awaits a
multi-circuit corpus, including varied pit layouts and close/grade-separated
geometry. Gap quality depends on available leader history. Terminal timing is
inferred only after the final position sample; it is not a direct canonical
retirement timestamp.
