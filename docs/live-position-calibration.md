# Live-position projection calibration and generation quality gate

**Implementation status (2026-08-15):** The browser-delivery pipeline contains
the `projection-quality-gate-v1` and `geometric-wrap-v1` policies. They are
versioned algorithm identifiers, not replay-contract versions. Canonical rows
remain unchanged; projection, quality assessment, ranking, and gaps are
browser-delivery fields.

**Evidence status (2026-08-15):** This checkout does not contain the historical
Bahrain input or browser track-asset files referenced by the offline calibration
spike. Consequently, the numeric Bahrain measurements and delivery statistics
previously recorded in this document are not treated as verified evidence or as
production calibration facts. The thresholds below describe the implemented
policy; they are still provisional until independently measured on a
representative multi-circuit corpus.

## Per-generation process

Every generated race uses the same quality-gate process:

1. Select one deterministic source lap: the shortest accurate, non-deleted,
   non-pit lap with at least four finite native position points, ordered by
   duration, driver, lap number, and start time. Generate the track centerline
   from that lap.
2. Exclude that source lap from all quality metrics because it is
   self-referential rather than independent evidence.
3. Project a bounded sample from every other accurate, non-deleted, non-pit lap
   onto the centerline. Convert FastF1 decimetres to metres once, retain native
   session order, discard null/non-finite coordinates, and remove later duplicate
   timestamps. Retain every valid point for laps with 32 or fewer points;
   otherwise retain exactly 32 endpoint-inclusive stratified points using
   `floor(i * (n - 1) / 31)` for `i = 0..31`. There is no telemetry merge,
   resampling, or interpolation.
4. Require at least 20 independent laps and 500 independent samples. Calculate
   nearest-segment residual p95 and maximum, then apply the versioned
   `geometric-wrap-v1` continuity analysis per timing lap. Pit laps are measured
   separately and do not enter clean-track thresholds.
5. Publish derived fields only when every evidence gate passes. Insufficient or
   poor evidence fails closed: track distance, race progress, leaderboard order,
   and gaps remain null or unpublished rather than guessed.

This is a per-generation gate; it does not require a circuit-specific source
edit. A changed layout receives a new `trackId` and a fresh result. The global
thresholds remain provisional until a multi-circuit corpus covers wet
conditions, pit layouts, close parallel geometry, and grade-separated crossings.

## Evidence and reproduction

The repository test at
`pipeline/tests/analysis/live_position/test_live_position_calibration.py` is an
offline calibration spike, not production projection code. In the current
checkout its registered test covers the synthetic wrap, ambiguity, stale
fallback, and sampling contract. Its artifact-backed measurement helper expects
these local inputs, which are absent here:

```text
artifacts/demo-bahrain-2024
artifacts/browser-bahrain-cli/generations/2024-bahrain-race-cli-v2/track-assets.json
```

Run the registered synthetic contract with:

```bash
.venv/bin/python -m pytest -s pipeline/tests/analysis/live_position/test_live_position_calibration.py
```

This command does not establish Bahrain measurements or production readiness
without the missing inputs and an explicitly executed artifact-backed
measurement. Do not copy historical metric values into a production claim
without recording the input paths, run date, and output.

## Provisional global policy (`projection-quality-gate-v1`)

| Gate | Limit | Failure behaviour |
| --- | ---: | --- |
| Independent eligible holdout laps | >= 20 | Fail closed. |
| Independent native holdout samples | >= 500 | Fail closed. |
| Holdout residual p95 | <= 25 m | Fail closed. |
| Holdout residual maximum | <= 75 m | Fail closed. |
| Laps with invalid/multiple geometric wraps | 0 | Fail closed. |
| Implausible backward jump after geometric unwrap | 0 over 200 m | Fail closed. |
| Ambiguous candidate residual difference | <= 5 m | Require continuity; otherwise unknown. |
| Accepted-coordinate freshness | < 1,000 ms | Freeze last valid progress; at 1,000 ms return null. |

Residual limits are policy values, not Bahrain constants. They must be confirmed
or revised from independent holdouts across the multi-circuit corpus. A
generation with too little evidence does not pass merely because it stays below
a residual limit.

### Geometric wrap policy (`geometric-wrap-v1`)

Read `circuitLengthMeters` from the validated track-assets payload; do not
recompute a conflicting length from the centerline. A raw projected decrease is
an accepted geometric wrap only when the preceding projection is in the final
10% of that length, the following projection is in the initial 10%, and the
decrease is at least 80% of the length. Add exactly one asset circuit length to
following samples in that timing lap. Ratios make this portable across layouts.

At most one accepted geometric wrap is allowed per timing lap. A backward jump
over 200 m outside those regions, a decrease that fails a ratio check, or a
second otherwise-valid wrap invalidates that lap. The gate fails closed if any
such lap exists or if any backward jump over 200 m remains after unwrapping.
Timing-lap boundaries and the geometric centerline origin may differ by several
native samples, so official lap-number advancement is not itself the unwrap
trigger.

At a self-intersection or nearby parallel segment, candidates within 5 m
residual require continuity with the prior accepted progress; without it the
result is unknown. Invalid coordinates freeze the last derived progress only
inside the freshness limit; stale coordinates become null. Retired or out state
remains explicit status logic and cannot turn stale geometry into a live
observation.

## Live ranking semantics

Browser ranking does not add a circuit length when the official lap counter
changes. Lap and position streams can reach the same browser timestamp in either
order. Live progress instead unwraps projected distance at a deterministic
ranking cut half a circuit from the visual start/finish, independent of the
official timing line and valid for either direction of travel.

All grid starters share one cut-crossing epoch at the first synchronized frame.
A driver first observed from the pit lane receives the lap-compatible cut epoch
nearest the established field's median progress; no circuit is fabricated.
Official lap changes remain validation evidence, while the projected geometric
cut supplies live spatial unwrapping. The cut affects only derived progress and
leaderboard order; published track distance, geometry, and start/finish
coordinates retain their visual coordinate system.

Ranking progress is monotonic. Finished and pit modes freeze progress rather
than receiving an artificial penalty, and null progress is omitted. Ties resolve
by prior order and then driver ID. Gap values use the leader's equivalent-
progress history, not a constant-speed heuristic; insufficient history yields
null.

## Limitations

The calibration test helper is intentionally scoped to this offline spike and
is not a production projection module. Nearest-segment residual measures
geometry consistency, not surveyed position truth. The currently available
repository evidence cannot validate all layouts or racing conditions, and a
passing gate alone does not establish ranking or gap correctness.
