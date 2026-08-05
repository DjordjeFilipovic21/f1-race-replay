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

### Optional season and telemetry capability metadata

The manifest may include `seasonMetadata` and `telemetryCapabilities` to describe
season-specific source availability without changing driver or chunk columns:

```json
{
  "seasonMetadata": {"year": 2026},
  "telemetryCapabilities": {
    "drs": "not-published",
    "overtakeMode": "not-published",
    "activeAero": "not-published",
    "ersReplacement": "not-published"
  }
}
```

Both fields are optional. `seasonMetadata` contains only a positive `year`; each
capability is either `available` or `not-published`. FastF1 3.8.1+ retains the
legacy `drs` column for 2026 compatibility, but the source DRS channel is absent
and FastF1 fills it with zero. That zero is not a measured DRS-Off state, and
the contract deliberately does not add a factual `overtakeMode`, active-aero,
or ERS telemetry column. Consumers should render a not-published state when
the metadata says the capability is unavailable. Manifests without either
optional field remain valid and preserve legacy DRS behavior.

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

### Optional lap/sector sidecar

The manifest may include a compact, optional `lapSectorSidecar` reference to
a dedicated columnar artifact that exposes completed lap and sector timing
records for every driver:

```json
"lapSectorSidecar": {
  "path": "lap-sector-sidecar.json",
  "schemaId": "urn:f1-cache-replay:schema:replay-data:v1:browser-lap-sector-sidecar",
  "sha256": "<64 lowercase hexadecimal characters>"
}
```

The referenced `lap-sector-sidecar.json` is a `v1` object whose drivers map
to equal-length column arrays sorted by ascending lap number:

```json
{
  "contractVersion": "v1",
  "fixtureId": "bahrain-2024",
  "drivers": {
    "HAM": {
      "lapNumber": [1, 2],
      "lapStartMs": [1000, 78000],
      "lapEndMs": [77000, 155000],
      "lapDurationMs": [76000, 77000],
      "sector1DurationMs": [25000, 26000],
      "sector2DurationMs": [27000, 28000],
      "sector3DurationMs": [24000, 23000],
      "sector1SessionTimeMs": [1000, 78000],
      "sector2SessionTimeMs": [26000, 104000],
      "sector3SessionTimeMs": [53000, 132000]
    }
  }
}
```

- Every driver key is a canonical driver code (`^[A-Z0-9]{2,4}$`). Lap numbers
  are positive integers; `lapStartMs` and `lapEndMs` are non-nullable
  non-negative integers (absolute session time).
- `lapDurationMs`, sector durations (`sector1DurationMs`–`sector3DurationMs`),
  and sector completion timestamps (`sector1SessionTimeMs`–
  `sector3SessionTimeMs`) are nullable. Nulls propagate from missing canonical
  data; the pipeline never invents values.
- Sector durations are paired with their completion timestamps so consumers
  can reveal only sector records completed by the current replay time (causal
  completion). All timing values are integer milliseconds; no float seconds
  are used.
- The sidecar duplicates no values present in telemetry chunks. It is a
  dedicated artifact produced alongside chunks without embedding lap or sector
  data into them.
- Publication serializes the sidecar deterministically, verifies its SHA-256
  digest against the manifest reference, and validates both the JSON Schema
  (`browser-lap-sector-sidecar.schema.json`) and semantic contract rules
  (fixture agreement, driver set identity).
- The reference is optional: older deliveries and manifests without
  `lapSectorSidecar` remain valid and loadable. Strict parsers must explicitly
  allow its absence.

### Optional stint summary

The manifest may include a compact, optional `stintSummary` reference to
a dedicated columnar artifact that publishes per-driver tyre stint and exact
pit-transition data:

```json
"stintSummary": {
  "path": "stint-summary.json",
  "schemaId": "urn:f1-cache-replay:schema:replay-data:v1:stint-summary",
  "sha256": "<64 lowercase hexadecimal characters>"
}
```

The referenced `stint-summary.json` is a `v1` object mapping every canonical
driver to equal-length column arrays sorted by ascending stint number:

```json
{
  "contractVersion": "v1",
  "fixtureId": "bahrain-2024",
  "drivers": {
    "VER": {
      "stintNumber": [1, 2, 3],
      "compound": ["SOFT", "HARD", "SOFT"],
      "startLap": [1, 14, 33],
      "endLap": [13, 32, null],
      "startTimeMs": [1000, 137000, 320000],
      "endTimeMs": [135000, 318000, null],
      "tyreLifeAtStart": [0, 12, 18],
      "isFreshTyre": [true, false, false],
      "pitInTimeMs": [null, 134500, null],
      "pitOutTimeMs": [null, 139000, 321000]
    },
    "NOR": {
      "stintNumber": [1],
      "compound": ["SOFT"],
      "startLap": [1],
      "endLap": [57],
      "startTimeMs": [2000],
      "endTimeMs": [570000],
      "tyreLifeAtStart": [0],
      "isFreshTyre": [true],
      "pitInTimeMs": [null],
      "pitOutTimeMs": [null]
    }
  }
}
```

- Every driver key is a canonical driver code (`^[A-Z0-9]{2,4}$`). All column
  arrays within a driver entry are equal length. Drivers without stints produce
  empty aligned arrays.
- `stintNumber` and `startLap` are non-nullable positive integers.
  `endLap`, `endTimeMs`, `tyreLifeAtStart`, and `startTimeMs` are nullable.
  Null in `startTimeMs` means the canonical stint start time is unavailable.
- `pitInTimeMs` and `pitOutTimeMs` are nullable integer milliseconds; nulls
  propagate from missing source data — the pipeline never invents or forward-fills
  pit transition values.
- `isFreshTyre` is `true` when the stint began on new tyres, `false` for used
  tyres, and `null` when data is absent.

**Pit-in and pit-out to stint mapping**

Exact pit transitions are derived deterministically from canonical lap rows.
Each canonical lap carries a `stint_number` identifying the stint active during
that lap:

- **Pit-in** (`pitInTimeMs`) belongs to the stint whose number matches the
  lap's canonical `stint_number` and **ends** that indexed stint.
- **Pit-out** (`pitOutTimeMs`) belongs to the stint whose number matches the
  lap's canonical `stint_number` and **begins** (or resumes) that indexed stint.
- The mapping is validated: the lap number must fall within the canonical
  stint's lap range [`startLap`, `endLap`].
- If a canonical stint has a non-null `pitInTimeMs`, the driver entered the
  pits during that stint and the stint is closed. A non-null `pitOutTimeMs`
  means the driver exited the pits to begin that stint.

**Null and edge-case semantics**

- **Stint 1 pit-out** is normally `null` (no pit exit before the first stint
  begins), but a real source event is preserved when present — for example a
  pit-lane start where the driver leaves the pits before the first racing lap.
- **Final-stint pit-in** is `null` when no pit entry is observed during that
  stint (race ended or driver retired without pitting again).
- **No-stop races**: every stint has both `pitInTimeMs` and `pitOutTimeMs`
  `null` when no source pit events exist.
- **Ongoing stints** have nullable `endLap`, `endTimeMs`, and `pitInTimeMs`.
  `pitOutTimeMs` is non-null for any stint that began from a pit exit (stint 2+
  with a pit stop); it is null only for stint 1 without a pit-lane start.

**Fail-closed rules**

The mapping fails closed on ambiguity or inconsistency:

- A lap-level pit event whose `stint_number` does not match any canonical stint
  → error.
- A pit event lap outside the stint's lap range → error.
- Multiple pit-in candidates for the same stint → error.
- Multiple pit-out candidates for the same stint → error.

These rules ensure deterministic derivation: every pit transition maps to
exactly one stint or the pipeline refuses to publish.

**Backward compatibility**

The stint summary is an independent artifact produced alongside chunks and the
lap/sector sidecar. Core replay chunks are unchanged. The manifest reference is
optional: consumers must accept manifests without `stintSummary`. All existing
v1 chunks, timeline summaries, sidecars, and navigation markers remain valid
and replayable.

### Optional pit-loss model

The manifest may include a compact, optional `pitLossModel` reference to a
sparse causal pit-loss estimate artifact:

```json
"pitLossModel": {
  "path": "pit-loss-model.json",
  "schemaId": "urn:f1-cache-replay:schema:replay-data:v1:pit-loss-model",
  "sha256": "<64 lowercase hexadecimal characters>"
}
```

The referenced `pit-loss-model.json` is a `v1` object using
`"global-prior-weighted-mean-v1"` — an uncalibrated deterministic global
heuristic, not a track-calibrated model. It emits three aligned arrays,
strictly increasing by `timeMs`:

```json
{
  "contractVersion": "v1",
  "fixtureId": "2024-sao-paulo-race",
  "method": "global-prior-weighted-mean-v1",
  "baselineMs": 22000,
  "priorWeight": 2,
  "timeMs": [1000, 1523000, 1678000],
  "estimatedLossMs": [22000, 21867, 22150],
  "observedSampleCount": [0, 1, 3]
}
```

**Placeholder and refinement.** A placeholder exists from replay start
before any observed stop. Default model: prior-weighted mean with
`baselineMs = 22_000`, `priorWeight = 2`. At zero observations the
estimate is 22,000 ms and `observedSampleCount = 0`. After valid observed
losses `L`:

`round_half_up((baselineMs * priorWeight + sum(L)) / (priorWeight + len(L)))`

Refinement is causal: a new sample is emitted at the distinct
`pitOutTimeMs` where all eligible observations completed at that timestamp
are incorporated. A consumer picks the latest sample with
`timeMs <= replayTimeMs`.

**Observed loss from one completed eligible stop:**

`observedPitLossMs = gapAfterPitOutMs - gapBeforePitInMs`

Gaps use the latest available sample strictly before or at pit-in and the
first valid on-track sample at or after pit-out. Integer milliseconds,
deterministic rounding.

**Eligibility.** A stop refines the estimate only at its `pitOutTimeMs`,
never earlier, and only when:
- canonical pit-in and pit-out timestamps form a complete positive interval;
- before/after gap values are non-null and finite;
- observed loss is finite and positive;
- projection/ranking quality gate passed (dynamic gaps trustworthy);
- track status is all-clear for the full stop interval;
- the same leader remains leader before and after;
- the leader is not in the pit lane at the before/after observations or
  during the stop interval where known;
- the stopped driver returns on track and is not retired/out/finished at
  the after sample;
- mapping from stint-summary pit-in/pit-out data is unique and deterministic.

Lapped cars are allowed when their derived gap is valid and leader identity
is stable. Exclusion is not a build failure.

**Scope.** The artifact is a sparse global estimate; no per-driver
predicted-gap array is shipped. Frontend code will later compute
`predictedGapToLeaderMs = currentGapToLeaderMs + currentPitLossEstimateMs`
and compare against live gaps for nearest-car-ahead/behind animation. No
panel, payload loader, or runtime snapshot is included.

**Backward compatibility.** The `pitLossModel` manifest reference is
optional. Existing deliveries, chunks, and manifests without it remain
valid and loadable.

### Optional status-aware pit-loss estimate sidecar

The manifest may additionally include a compact, per-delivery/per-track
`pitLossEstimateSidecar` reference. It is additive to the legacy
`pitLossModel` and does not change canonical Parquet or browser chunk shape:

```json
"pitLossEstimateSidecar": {
  "path": "pit-loss-estimate-sidecar.json",
  "schemaId": "urn:f1-cache-replay:schema:replay-data:v1:pit-loss-estimate-sidecar",
  "sha256": "<64 lowercase hexadecimal characters>"
}
```

The referenced `pit-loss-estimate-sidecar.json` binds both the replay fixture
and the selected track asset.

**Versioned repository-local catalog.** Production values are resolved from
the versioned, repository-local pit-loss baseline catalog
(`pit-loss-baseline-catalog.schema.json`, schema ID
`urn:f1-cache-replay:schema:replay-data:v1:pit-loss-baseline-catalog`). The
catalog exists so generation is deterministic and fully offline: it is checked
into the repository, never fetched from a circuit statistic at runtime, and is
the source of truth for curated values. Generation performs no live network
access. Current-race gap-difference estimates are **diagnostic-only**; they
never change a resolved production value.

**2024-2026 physical-circuit union.** The catalog ships one fixture-less entry
per physical circuit used in the 2024, 2025, and 2026 calendars — 26 circuits
total. The same stable `trackId` is reused for a circuit that appears in
multiple seasons, so one per-circuit statistic resolves for every calendar.
Genuinely different venues or layouts are distinct entries: `bahrain` (Sakhir)
and `sepang` are separate circuits, and `barcelona-catalunya` (2024/2025) and
`madring` (Madrid, 2026) are separate circuits. Bare names shared by two
circuits (for example `spanish-grand-prix`) are ambiguous, so only
season-qualified forms such as `2024-spanish-grand-prix` versus
`2026-spanish-grand-prix` resolve. A deterministic repository-local identity
registry (`browser_pit_loss_track_identity.py`, schema ID
`urn:f1-cache-replay:schema:replay-data:v1:pit-loss-track-identity`) maps
fixture, track-asset, and alias names onto physical circuits and rejects
unknown or ambiguous bindings instead of guessing. Australia is the only entry
bound to a specific fixture (`2026-round-01-australian-grand-prix` and its
telemetry track asset); every other entry is fixture-less.

The curated sidecar uses the `curated-track-baseline-v1` method and carries only
the public fixture/track identity and replay-start timelines. Catalog metadata —
`catalogVersion`, `sourceStatus`, `provenance`, `evidenceCount`, `confidence`,
and derivation details — remains in the repository-local catalog and is not
serialized into the public sidecar:

```json
{
  "contractVersion": "v1",
  "fixtureId": "2026-round-01-australian-grand-prix",
  "trackId": "australia",
  "method": "curated-track-baseline-v1",
  "race": {"timeMs": [0], "estimatedLossMs": [19300]},
  "safetyCar": {"timeMs": [0], "estimatedLossMs": [9300]},
  "virtualSafetyCar": {"timeMs": [0], "estimatedLossMs": [12300]}
}
```

The Australia entry (bound to the 2026 season opener) is:

```text
Green 19300 ms  (All Clear, code 1)
VSC   12300 ms  (Virtual Safety Car, codes 6/7)
SC     9300 ms  (Safety Car, code 4)
```

- **Metric semantics.** Green is the official Formula1.com "pit stop time
  loss" figure (pit-lane transit plus approximately 2.5 s of stationary
  service). VSC and SC are the time lost versus staying out during a
  neutralised period. Each status names its metric family —
  `f1-com-lane-plus-stationary`, `measured-total-cost`, or
  `fia-stop-duration` — and the catalog never mixes families silently. FIA
  pit-stop-summary durations are measured stop times, not loss baselines;
  Sepang's FIA summary is used only as a magnitude reference.
- **Per-status source status.** Every status value is `direct` (published by
  an official or measured source), `derived` (produced by a documented policy
  from a baseline), or `proxy` (an explicit estimate for a low-evidence
  track). Derived and proxy values must carry an explicit `derivation` record
  (`method`, `baseStatus`, optional `discountMs`, `notes`); direct values
  must not. `sourceStatus` is **catalog-only** metadata: it is never
  serialized into the sidecar payload or exposed to the frontend, and the
  sidecar schema's `additionalProperties: false` rejects any attempt to
  inject it.
- **Non-universal discount policy.** The fixed `Green − 7 s` / `Green − 10 s`
  rule is not mandatory. Australia's VSC and SC keep the legacy default
  discounts (7 s / 10 s), but every other circuit resolves its own values:
  direct track-specific status values override the policy (Canada, Monaco,
  Monza, and Baku), and derived values use the bounded proportional policy
  (VSC ≈ 0.55–0.70 × Green, SC ≈ 0.45–0.55 × Green). Discounts may fall
  outside the legacy 5–10 s window when provenance supports them — for
  example Baku's 8.5 s SC implies an 11.2 s discount. The monotonic invariant
  `SC <= VSC <= Green` is validated at the catalog, resolver, and sidecar
  boundaries; malformed entries are rejected.
- **Provenance, evidence, and confidence.** Each catalog status carries its
  own `provenance` (`sourceUrl`, `capturedDate`, `evidence`, `method`),
  `evidenceCount` (the curated source-evidence or calibration count, not
  current-race observations), and `confidence`
  (`high`/`medium`/`low`). These fields remain catalog-only and are not part of
  the public sidecar. Low-evidence circuits (Bahrain, Jeddah, Imola, Madring,
  Sepang, Zandvoort, Marina Bay) carry explicit derived/proxy estimates with
  provenance and low or medium confidence; a missing entry is explicit and
  never becomes a hidden 22-second baseline. F1 Manager game-balance values
  are rejected as production evidence.
- **Availability at replay start.** A curated sidecar always resolves Green,
  Safety Car, and Virtual Safety Car values from the catalog, one replay-start
  point per timeline, even when Safety Car or Virtual Safety Car never occurred
  in the current race. Curated timelines never carry `observedSampleCount`:
  catalog evidence remains in the catalog-only `evidenceCount`, and current-race
  observation counts are never fabricated for catalog values.
- **Status selection.** Code `1` selects the `race` (Green) value, code `4`
  the `safetyCar` value, and codes `6`/`7` the `virtualSafetyCar` value.
  Unknown, yellow, red, missing, or mixed/ambiguous stop intervals fail
  closed: a curated sidecar never reuses the Green value for an unsupported
  status, and only a legacy `pitLossModel` remains a fallback when the
  delivery still carries one.
- **Fail-closed unknown tracks.** Unknown tracks have no catalog entry and the
  pipeline fails closed: no curated sidecar is emitted and no 22,000 ms
  fallback is silently applied. A track without a curated entry remains
  publishable through the legacy delivery path; the browser treats pit-loss as
  unavailable unless a legacy `pitLossModel` is present.
- **Publication.** The sidecar is scoped to the current canonical fixture and
  track asset; it does not aggregate multiple races for one circuit.
  Publication validates the schema, catalog binding, fixture/track identity,
  deterministic bytes, and SHA-256 digest. Delivery never touches
  `templates/`, and R2 publication behavior is unchanged.

**Legacy compatibility.** The sidecar schema accepts both
`curated-track-baseline-v1` and the legacy race-derived
`track-status-median-v1` method. A legacy sidecar aggregates eligible
current-race All Clear observations into its replay-start `race` median,
emits `safetyCar`/`virtualSafetyCar` only when those statuses occurred, and
marks an occurred-but-unobserved status as `{ "status": "unavailable" }`; it
carries no catalog metadata. Older `track-status-median-v1` sidecars,
`pitLossModel`-only deliveries, and deliveries with neither artifact remain
valid and loadable. The sidecar itself is optional: consumers must accept
manifests without `pitLossEstimateSidecar`.

### Optional issued-penalty sidecar

The manifest may include a compact, optional `penaltySidecar` reference for
definitive race-control penalty issuances:

```json
"penaltySidecar": {
  "path": "penalty-sidecar.json",
  "schemaId": "urn:f1-cache-replay:schema:replay-data:v1:penalty-sidecar",
  "sha256": "<64 lowercase hexadecimal characters>"
}
```

The referenced `penalty-sidecar.json` is a `v1` object containing
`penaltyIssuances`. Each issuance preserves the canonical driver ID, absolute
`sessionTimeMs`, parsed `penaltyType`, normalized `reason`, raw race-control
`rawMessage`, and optional `lapNumber`. Canonical rows with a null
`driver_id` are resolved through the car number and driver metadata before an
issuance is published; unresolved identities fail closed rather than creating
a false marker.

This is intentionally an **issued-only** contract. A leaderboard `!` marker
means that a penalty has been issued at or before the replay cursor. It does
not mean that the penalty is currently active, remains unserved, or has been
served. FastF1's race-control feed supplies issuance messages only and has no
authoritative served-state field or separate penalty-served stream. Consumers
must not infer served state from pit duration, telemetry, or final results.

The sidecar is optional: existing deliveries and manifests without
`penaltySidecar` remain valid and loadable. When present, the browser applies
the same causal rule for playback and arbitrary seeks: an issuance is visible
when `sessionTimeMs <= replayTimeMs`, remains visible through replay end, and
is hidden again when seeking before its issuance time. Publication validates
the sidecar schema, fixture identity, driver identity, and digest before the
browser pointer is replaced.

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
optional `isFinished` field. The curated pit-loss catalog covers the full
2024-2026 physical-circuit union (26 circuits); low-evidence circuits carry
explicit derived/proxy estimates with provenance and low or medium confidence,
and values are never invented at generation time without catalog metadata.
Generation is fully offline: no catalog value is fetched from the network at
build time.
