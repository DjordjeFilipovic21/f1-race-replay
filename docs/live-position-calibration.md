# Live-position projection calibration and generation quality gate

**Status:** implemented in the production browser-delivery pipeline and selected
for the Bahrain v4 derived generation. The calibration thresholds remain
provisional pending a representative multi-circuit corpus. Canonical rows remain
unchanged; projection, quality assessment, ranking, and gaps are browser-derived.

## Per-generation process

Every generated race, regardless of circuit, uses the same
`projection-quality-gate-v1` process:

1. Select one deterministic source lap: the shortest accurate, non-deleted,
   non-pit lap with at least four finite native position points, ordered by
   duration, driver, lap number, and start time. Generate the existing track
   asset centerline from that lap.
2. Exclude that source lap from all quality metrics. It is necessarily
   self-referential, not independent evidence.
3. Project a deterministic bounded sample from every other accurate,
   non-deleted, non-pit lap onto that centerline. Convert FastF1 decimetres to
   metres once, order by native session timestamp, and exclude null/non-finite
   coordinates and later duplicate timestamps. Retain every valid point when a
   lap has 32 or fewer; otherwise retain exactly 32 evenly stratified points at
   indices `floor(i * (n - 1) / 31)`, for `i = 0..31`. This includes both valid
   endpoints and preserves timestamp order. No telemetry merge, resampling, or
   interpolation is used.
4. Require at least 20 independent laps and 500 independent samples. Calculate
   nearest-segment residual p95 and maximum, then apply the versioned
   `geometric-wrap-v1` per-timing-lap continuity analysis. Pit laps are measured
   in a separate population and never enter clean-track thresholds.
5. Publish derived fields only when all evidence gates pass. Insufficient or
   poor evidence fails closed: `trackDistanceMeters`, race progress, position,
   leaderboard order, and gap remain `null`/unpublished rather than guessed.

This runs automatically for every generation; no circuit-specific source edit
or manual code change is required. A changed layout receives a new `trackId`
and a fresh gate result. Thresholds are versioned global algorithm policy, not
Bahrain constants. They remain provisional until a representative multi-circuit
corpus covers wet conditions, pit layouts, close parallel geometry, and
grade-separated crossings.

## Bahrain 2024 evidence and reproduction

Inputs are the validated local canonical generation
`artifacts/demo-bahrain-2024` and the existing immutable browser track asset
`artifacts/browser-bahrain-cli/generations/2024-bahrain-race-cli-v2/track-assets.json`.
The deterministic source is VER lap 39, whose official duration is 92,608 ms.
Its 32-point sampled self-fit yields p95 residual **0.301 m** and maximum
**0.502 m**. Those values prove only that the generated centerline reproduces
its own input; they are explicitly excluded from the gate.

The pre-unwrap discovery run took about 48 seconds and measured 1,004 holdout
laps / 32,128 samples, holdout p95 **0.445 m**, maximum **5.291 m**, and 851
raw projected backward transitions over 200 m. It separately measured 2,752
pit-affected samples (p95 **15.386 m**, maximum **16.582 m**). Those raw
backward transitions are approximately one centerline-origin crossing per
timing lap: timing-lap boundaries and geometric centerline zero differ by a few
native samples. The corrected unwrap accepted all **851** geometric wraps,
found **0** laps with invalid or multiple wraps, and left **0** backward jumps
over 200 m after unwrapping.

The checked-in offline test indexes position rows by driver once, then uses
timestamp binary-search bounds for each lap. It computes and asserts the
independent Bahrain holdout selection count, bounded sample count, residual
p95/max, backward-jump count, and separate pit-affected-lap population. Run it
with output enabled to record the exact values from these repository inputs:

```bash
.venv/bin/python -m pytest -s pipeline/tests/test_live_position_calibration.py
```

It skips only the artifact-backed case, with an explicit reason, if these local
artifacts are not checked out; its synthetic wrap/ambiguity/staleness contract
test always runs and needs no network. The test validates the canonical pointer
and manifest before reading tables. Clean and pit-affected laps use the same
32-point cap and endpoint-inclusive stratification after per-lap timestamp
de-duplication. Clean sampled points are pooled for p95, which is the sorted
value at `round((n - 1) * 0.95)`. Bounding each lap keeps analysis runtime
predictable for future races while retaining coverage across every included lap.

Pit-lap rows are deliberately reported separately. Their larger residuals are
expected because the centerline represents the racing circuit rather than pit
road. A pit residual must not itself demote a driver; later ranking logic must
retain explicit pit/status semantics independently of this geometry gate.

## Timing and distance provenance

Official lap timing is timing truth for selecting and checking laps, not spatial
ground truth. FastF1 `Distance` is speed/time integration and is also not
measured ground truth. This spike does not use an integrated-distance comparator
for fitting, validation, or X/Y projection.

## Published Bahrain derived-delivery evidence

The selected local browser pointer now resolves to the immutable generation
`2024-bahrain-race-cli-v4-derived`, built from canonical generation
`2024-bahrain-race`:

```text
artifacts/browser-bahrain-cli/
├── browser-current.json
└── generations/2024-bahrain-race-cli-v4-derived/
```

Reproduction uses the project virtual environment explicitly:

```bash
.venv/bin/python -m f1_replay_pipeline browser \
  --canonical artifacts/demo-bahrain-2024 \
  --output artifacts/browser-bahrain-cli \
  --delivery-version 2024-bahrain-race-cli-v4-derived \
  --schema-root contracts/replay-data/v1/schemas
```

The production quality gate passed the same 1,004 independent laps and 32,128
samples recorded above. The delivery contains 575 chunks and 44,747
authoritative timestamps over `[3,599,911, 9,374,320)`. Across 894,940
authoritative driver cells, position and track distance are available for
865,370 cells (96.70%); a dynamic leaderboard is available at 43,709 timestamps
(97.68%). Validation found 361 order changes, zero position/order disagreements,
and zero non-zero leader gaps. Remaining nulls are the intended fail-closed
result of unavailable, stale, pit, or terminal source state.

The selected manifest SHA-256 is
`3a89b32849b1361bbb52f5ae86a86677b4e9eba1d3d3f7af69972361f00b2c96`.
Chunk plus track-asset payloads total 100.53 MB as raw JSON and 18.18 MB with
per-file gzip. A pre-publication profile reduced the first 2,000-timestamp
derived pass from 76.4 seconds to 6.1 seconds by indexing centerline segments
once and using a batch ranking history; this is a performance implementation
change only and does not alter the approved projection, ranking, or gap rules.

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
| Accepted-coordinate freshness | < 1,000 ms | Freeze last valid progress; at 1,000 ms return `null`. |

The residual limits are conservative relative to the observed self-fit only and
must be confirmed or revised from independent holdouts across the future
multi-circuit corpus. They are separate from the per-generation pass/fail
result: a generation with too little evidence never passes merely because it
does not exceed a residual limit.

### Geometric wrap policy (`geometric-wrap-v1`)

The test reads `circuitLengthMeters` from the validated track-assets payload;
it does not recompute a conflicting length from the centerline. A raw projected
decrease is an accepted geometric wrap only when all three ratio-based checks
pass: the preceding projection is in the final **10%** of that asset length
(`>= 0.90 * length`), the following projection is in the initial **10%**
(`<= 0.10 * length`), and the decrease is at least **80%** of the length
(`>= 0.80 * length`). It then adds exactly one asset circuit length to all
following samples in that timing lap. Ratios, rather than Bahrain metre values,
make this policy portable across layouts.

At most one accepted geometric wrap is allowed per timing lap. A backward jump
over 200 m outside those regions, a decrease that fails any ratio check, or a
second otherwise-valid wrap marks that timing lap invalid. The gate fails
closed if any such lap exists or if any backward jump over 200 m remains after
unwrapping. This continuity rule does not require simultaneous official
lap-number advancement because a timing-lap boundary and centerline origin can
be several native samples apart. Future circuits therefore receive the same
asset-length-based gate and fail closed rather than silently accepting an
unfamiliar discontinuity.

At a self-intersection or nearby parallel segment, candidates within 5 m
residual require continuity with the prior accepted progress; without it the
result is unknown. Invalid means null/non-finite coordinates, no acceptable
candidate, or unresolved ambiguity. Invalid coordinates freeze the last derived
progress only inside the freshness limit; stale coordinates become `null`.
Retired/out state remains explicit future status logic and cannot turn stale
geometry into a live observation.

## Limitations

The test helper is intentionally scoped to this calibration spike and is not a
production projection module. Nearest-segment residual is geometry consistency,
not surveyed position truth. The current one-race evidence cannot validate all
layouts or racing conditions, and passing the gate does not establish ranking or
gap correctness.
