# Browser delivery interface freeze

**Status:** Accepted · **Version:** browser-delivery-v2 · **Date:** 2026-07-17

This is the boundary between validated canonical Parquet and browser replay
artifacts. Canonical Parquet is unchanged and remains the native-cadence,
loss-minimizing source. Browser delivery derives presentation fields without
mutating, resampling, or republishing canonical rows. The v2 interface below
is the active contract, and the web reader is v2-only. The v1 interface is a
frozen historical baseline that is excluded from the active catalog and never
loaded by the web application; see [V1 baseline and catalog
cutover](#16-v1-baseline-and-catalog-cutover).

## 1. Timeline and provenance

The delivery window begins at the earliest non-null canonical Lap 1
`lap_start_time_ms`. Serialized `sessionTimeMs` values remain absolute integer
milliseconds; the UI displays time relative to that delivery start. A missing
Lap 1 start fails closed.

Non-race sessions (`practice`, qualifying-like, and `testing`) have no race
start: delivery begins at the earliest published lap start (the first chunk
boundary), elapsed time is session-relative, and race-order controls are
disabled. No race gap, finish, DNF, or pit-loss claim may be derived from a
non-race session.

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

The nullable columnar shape established by the frozen v1 baseline remains
backward compatible in v2. Null-only v2 generations are valid and replay
normally; frozen v1 generations remain valid only as historical fixtures and
are not loaded by the v2-only reader. Derived columns are aligned arrays:

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
`BrowserDeliveryBuild` provenance and is not added to the serialized browser
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
retirement timestamp. The curated pit-loss catalog covers the full 2024-2026
physical-circuit union (26 circuits); low-evidence circuits carry explicit
derived/proxy estimates with provenance and low or medium confidence, and
generation is fully offline.

## 7. Optional lap/sector sidecar

Canonical laps now include nullable sector 1/2/3 duration and completion
session-time columns. A compact optional sidecar artifact exposes these for
every driver without duplicating values into replay chunks:

- The manifest may carry a `lapSectorSidecar` reference (`path`,
  `schemaId`, `sha256`) pointing to `lap-sector-sidecar.json`.
- The sidecar is columnar per driver: equal-length arrays of
  `lapNumber`, `lapStartMs`, `lapEndMs`, `lapDurationMs`,
  `sector1DurationMs`–`sector3DurationMs`,
  `sector1SessionTimeMs`–`sector3SessionTimeMs`, and `qualifyingPhase` (v2
  sidecars only), all integer milliseconds.
- Sector durations and their completion timestamps are nullable; nulls
  propagate from the canonical source. Start and end times are never null.
- V2 sidecars are phase-aware: top-level `phaseBoundaries` (`{phase, startMs}`
  entries ordered by `startMs`; empty for non-qualifying sessions) plus the
  aligned per-driver `qualifyingPhase` column (`Q1`, `Q2`, `Q3`, or `null`).
- The optional `lapKind` column classifies each lap `flying | outlap | inlap |
  unknown`, aligned with the other per-driver columns and derived per
  [ADR-003](adr/003-qualifying-flying-lap-evidence-policy.md): authoritative
  pit signals for outlap/inlap, the deterministic 107% per-Q-phase quick-lap
  gate as a heuristic, and fail-closed `unknown` for ambiguous non-pit cooldown
  laps. An absent column means the capability is unavailable: qualifying-like
  sidecars then treat no lap as flying (fail-closed), while non-qualifying
  sidecars keep the legacy all-laps behavior. `unknown` never contributes
  timing and is distinct from `false` or `OUT`.
- The sidecar is produced alongside chunks by `build_browser_delivery` and
  validated against
  `urn:f1-cache-replay:schema:replay-data:v2:browser-lap-sector-sidecar`.
- **Core chunks are unchanged**: no lap, sector, or driver-completion data is
  embedded in chunk payloads. The sidecar is an independent artifact.
- **Backward compatibility**: consumers must accept manifests without
  `lapSectorSidecar`. Strict parsers must explicitly allow its absence.
  All existing v2 chunks, timeline summaries, and navigation markers remain
  valid and replayable.

See [Replay Data Contract — Optional lap/sector sidecar](replay-data-contract.md#optional-lapsector-sidecar)
for the full contract including serialization rules, causal completion
semantics, and semantic validation.

## 8. Optional stint summary

Canonical stints and laps now support exact pit-transition mapping. A compact
optional stint-summary artifact exposes per-driver tyre stint and pit-transition
data without duplicating values into replay chunks:

- The manifest may carry a `stintSummary` reference (`path`, `schemaId`,
  `sha256`) pointing to `stint-summary.json`.
- The summary is columnar per driver: equal-length arrays of `stintNumber`,
  `compound`, `startLap`/`endLap`, `startTimeMs`/`endTimeMs`,
  `tyreLifeAtStart`, `isFreshTyre`, `pitInTimeMs`, and `pitOutTimeMs`.
- Pit-in ends the indexed stint; pit-out begins the indexed stint. The mapping
  is deterministic: ambiguous or out-of-range candidates fail closed.
- The summary is produced alongside chunks by `build_browser_delivery` and
  validated against
  `urn:f1-cache-replay:schema:replay-data:v2:stint-summary`.
- **Core chunks are unchanged**: no stint or pit-transition data is embedded
  in chunk payloads. The summary is an independent artifact.
- **Backward compatibility**: consumers must accept manifests without
  `stintSummary`. Strict parsers must explicitly allow its absence.
  All existing v2 chunks, timeline summaries, sidecars, and navigation
  markers remain valid and replayable.

See [Replay Data Contract — Optional stint summary](replay-data-contract.md#optional-stint-summary)
for the full contract including pit-mapping semantics, null rules, and
fail-closed guarantees.

## 9. Optional weather sidecar

Weather is delivered as an independent optional artifact. The manifest may
carry a `weatherSidecar` reference (`path`, `schemaId`, `sha256`) pointing to
`weather-sidecar.json` with schema ID
`urn:f1-cache-replay:schema:replay-data:v1:weather-sidecar`. The payload uses
the exact v1 names `contractVersion`, `fixtureId`, `timeMs`, `airTempC`,
`humidityPct`, `pressureMbar`, `rainfall`, `trackTempC`, `windDirectionDeg`,
and `windSpeedMps`; all measurement arrays are nullable and aligned to native
canonical weather rows.

The sidecar retains native sparse cadence and absolute integer milliseconds.
It is never resampled, interpolated, forward-filled, or populated by a future
sample. A weather panel uses the latest observation at or before the replay
cursor; observations more than 90,000 ms old, rows with no surviving
measurement, pre-first-sample time, and a missing manifest reference all
render the same fail-closed unavailable state. Wind direction is the
meteorological from-direction in degrees; the v1 UI may show a cautious
from-direction arrow, but the payload does not encode a flow-toward transform.

The producer applies ADR-003's FastF1 zero-sentinel audit before publication.
Sentinel-prone zeros become null, while genuine-zero-capable values require
row corroboration. Explicit FastF1 rainfall `false` remains its dry/unknown
source limitation; canonical or adapted rainfall `null` remains null and
renders unavailable. This does not alter canonical Parquet. Deterministic JSON, aligned/strictly ordered
arrays, schema validation, fixture identity, and SHA-256 verification remain
publication-boundary checks. See [Replay Data Contract — Optional weather
sidecar](replay-data-contract.md#optional-weather-sidecar) and
[ADR-003](adr/003-weather-sidecar-and-replay-panel.md) for the complete policy.

**Backward compatibility:** core chunks and `browser-delivery-v1` are
unchanged. Strict consumers must allow manifests without `weatherSidecar`;
old generations remain valid and replayable and simply show weather as
unavailable.

## 10. Optional pit-loss model

A compact optional pit-loss model artifact enables future frontend
after-pit position prediction without embedding arrays into replay chunks:

- The manifest may carry a `pitLossModel` reference (`path`, `schemaId`,
  `sha256`) pointing to `pit-loss-model.json`.
- The artifact supplies a single causal pit-loss estimate timeline using
  the deterministic `global-prior-weighted-mean-v1` method with a 22 s
  baseline, prior weight of 2, and `round_half_up` aggregation. At zero
  observations the estimate is exactly 22 s; refinement is causal at each
  distinct eligible pit-out timestamp.
- The estimate is an uncalibrated global heuristic, not track-calibrated.
  No per-driver predicted-gap array is shipped.
- The future frontend will compute
  `predictedGapToLeaderMs = currentGapToLeaderMs + currentPitLossEstimateMs`
  for nearest-car-ahead/behind animation.
- **Core chunks are unchanged**: no chunk fields, no version bump.
- **Backward compatibility**: consumers must accept manifests without
  `pitLossModel`. All existing v2 chunks, manifests, and optional artifacts
  remain valid and replayable.

See [Replay Data Contract — Optional pit-loss model](replay-data-contract.md#optional-pit-loss-model)
for the full contract including placeholder semantics, refinement rules,
eligibility criteria, and causal availability.

## 11. Optional status-aware pit-loss estimate sidecar

The browser delivery may publish an additive `pit-loss-estimate-sidecar.json`
artifact for the selected fixture and track:

- The manifest reference is `pitLossEstimateSidecar` with the fixed path
  `pit-loss-estimate-sidecar.json`, the schema ID
  `urn:f1-cache-replay:schema:replay-data:v2:pit-loss-estimate-sidecar`, and a
  SHA-256 digest.
- The artifact binds `fixtureId` to the manifest and `trackId` to
  `track-assets.json`; cross-race circuit aggregation is outside this
  delivery boundary. The public sidecar carries exactly `contractVersion`
  (`v2`), `fixtureId`, `trackId`, `method`, and replay-start
  `race`/`safetyCar`/`virtualSafetyCar` timelines.
- **Mode gating.** The reference is race-like-only (`race`, `sprint`); the
  schema and browser guard reject it for `practice`, `testing`, and
  qualifying-like modes. It is additive to the existing v2 `pitLossModel` and
  does not change canonical Parquet or browser chunk shape.
- **Catalog-backed values.** Production values come from the versioned,
  repository-local pit-loss baseline catalog
  (`pit-loss-baseline-catalog.schema.json`, schema ID
  `urn:f1-cache-replay:schema:replay-data:v2:pit-loss-baseline-catalog`), so
  generation is deterministic and performs no live network access. The curated
  method is `curated-track-baseline-v1`; each public sidecar carries only its
  identity, method, and replay-start timelines. Catalog audit metadata
  (`catalogVersion`, `sourceStatus`, provenance, evidence count, confidence,
  and derivation details) remains repository-internal, and the catalog's
  `catalogVersion` value is catalog metadata, not a contract version.
  Current-race gap-difference estimates are diagnostic-only and never alter
  these values.
- **2024-2026 physical-circuit union.** The catalog ships one fixture-less
  entry per physical circuit used in the 2024, 2025, and 2026 calendars (26
  circuits), reusing one stable `trackId` per circuit across seasons. Distinct
  venues are separate entries: `bahrain` (Sakhir) and `sepang`, and
  `barcelona-catalunya` and `madring` (Madrid). A deterministic
  repository-local identity registry (`browser_pit_loss_track_identity.py`,
  schema ID `urn:f1-cache-replay:schema:replay-data:v2:pit-loss-track-identity`)
  maps fixture and track-asset names onto circuits; bare names shared by two
  circuits resolve only with a season qualifier. Australia is bound to its 2026
  season opener; all other entries are fixture-less.
- **Per-status source status.** Every Green/VSC/SC value is `direct`,
  `derived`, or `proxy` and carries its own metric definition, provenance,
  evidence count, and confidence; derived/proxy values include an explicit
  derivation record. `sourceStatus` is catalog-only: it is never serialized
  into the sidecar payload or exposed to the frontend, and the sidecar
  schema's `additionalProperties: false` rejects it.
- **Non-universal discounts.** The fixed `Green − 7 s` / `Green − 10 s` rule
  is not mandatory. Australia keeps those legacy defaults, but other circuits
  use direct track-specific status values (Canada, Monaco, Monza, Baku) or the
  bounded proportional policy (VSC ≈ 0.55–0.70 × Green, SC ≈ 0.45–0.55 ×
  Green), and discounts may exceed the legacy 5–10 s window with provenance.
  The invariant `SC <= VSC <= Green` is validated and malformed entries are
  rejected.
- **Australia entry.** Green 19300 ms, VSC 12300 ms, SC 9300 ms.
- **Replay-start availability.** The curated sidecar always resolves Green,
  Safety Car, and Virtual Safety Car values from the catalog — one point at
  replay start per timeline — even when Safety Car or Virtual Safety Car never
  occurred in the current race. Status code `1` selects Green, code `4` Safety
  Car, and codes `6`/`7` Virtual Safety Car.
- **Fail-closed unknown tracks.** Unknown tracks have no catalog entry: no
  curated sidecar is emitted and no 22 s fallback is silently applied. A track
  without a curated entry is delivered without `pitLossEstimateSidecar`;
  pit-loss data is then unavailable unless the manifest carries the existing v2
  `pitLossModel`.
- Publication validates the direct sidecar contract, catalog binding,
  fixture/track identity, deterministic serialization, and digest before the
  immutable browser pointer is replaced.
- **Core chunks are unchanged**: no new chunk fields, no canonical Parquet
  rewrite, and no format-version bump. Delivery never touches `templates/`, and
  R2 publication behavior is unchanged.
- **Method suffixes and optionality.** `curated-track-baseline-v1` and
  `track-status-median-v1` are method identifiers — the `-v1` suffix is not a
  contract version and does not imply v1 compatibility. There is no v1
  pit-loss compatibility or read path: the v2-only reader rejects v1 pit-loss
  sidecars, catalogs, and identity registries. Manifests without this sidecar,
  including `pitLossModel`-only deliveries, remain valid and replayable.

See [Replay Data Contract — Optional status-aware pit-loss estimate sidecar](replay-data-contract.md#optional-status-aware-pit-loss-estimate-sidecar)
for the full schema, catalog provenance, availability, fallback, and
generation-time rules.

## 12. Optional issued-penalty sidecar

The manifest may carry a `penaltySidecar` reference (`path`, `schemaId`, and
`sha256`) pointing to `penalty-sidecar.json`. The sidecar contains definitive
race-control issuance records with canonical driver identity, issuance time,
penalty type, reason, raw message text, and optional `lapNumber`. A canonical
race-control row may have a null `driver_id`; the pipeline resolves its car
number or abbreviation against driver metadata before publishing the record.

The leaderboard marker is an **issued-penalty marker**, not an active- or
unserved-penalty indicator. It is shown for a driver when an issuance has
`sessionTimeMs <= replayTimeMs`, remains shown through the end of replay, and
disappears only when the cursor is moved before that issuance. Playback and
arbitrary seeks use this same causal comparison.

FastF1 and the underlying F1 live-timing race-control stream expose penalty
issuance messages only. They provide no authoritative served-state field and
no separate penalty-served stream. The browser and pipeline therefore do not
infer served state from pit duration, telemetry, or results. An issuance must
never be described as proof that a penalty remains active, is unserved, or has
been served.

The sidecar and manifest reference are optional. Core chunks and older
manifests remain valid without them. See [Replay Data Contract — Optional
issued-penalty sidecar](replay-data-contract.md#optional-issued-penalty-sidecar)
for the schema and publication guarantees.

## 13. Optional v2 qualifying lap-status sidecar

V2 qualifying-like deliveries (`sessionMode` `qualifying`,
`sprint-qualifying`, or `sprint-shootout`) may carry a `qualifyingLapStatus`
manifest reference (`path`, `schemaId`, `sha256`) pointing to
`qualifying-lap-status.json`:

- The sidecar publishes the final lap status per driver plus the causal
  deletion/reinstatement events that produced it, validated against
  `urn:f1-cache-replay:schema:replay-data:v2:browser-qualifying-lap-status`.
- Raw race-control text is parsed because FastF1 3.8.3 does not populate
  deletion through the structured `RacingNumber`/`Lap` columns. Both `TIME ...
  DELETED` and `TIME ... REINSTATED` messages are recognized; reinstatement
  exists because FastF1 can unmark laps through a look-ahead pass. The 2026
  Chinese GP `CAR ... LAP DELETED ... LAP <n> ...` form is a non-timed advisory,
  so it creates no causal event and does not trigger sidecar publication.
- The final canonical `laps.deleted` column is authoritative and is never
  rewritten. Without a replay boundary the reconciled event state must exactly
  equal it; contradiction, ambiguous identity, contradictory fields, or an
  unresolvable canonical lap fails closed instead of inventing a lap or
  timestamp.
- Consumers reveal events causally: an event is effective when
  `eventTimeMs <= replayTimeMs`, and reinstatement restores the lap to `valid`
  at its event time. Duplicate messages are idempotent and event order is
  deterministic.
- `deletedReason` is null for valid laps and for deleted laps without a source
  reason; valid laps never carry a reason. Drivers without status rows publish
  empty aligned arrays.
- Publication validates schema, digest, fixture/driver identity, aligned
  columns, deterministic ordering, and agreement between events and final
  status before the manifest reference is published.
- **Frozen surface**: v1 chunks, v1 contracts, and core v2 chunk payloads are
  unchanged; the sidecar embeds nothing into chunks. The reference is
  v2-only and qualifying-like-only, and is absent when no actionable timed
  status messages exist. Consumers must accept manifests without
  `qualifyingLapStatus`.
- **Unknown handling**: `selectQualifyingLapStatus` returns `null` (unknown)
  for a driver or lap with no published record, which is distinct from
  `valid`. With the sidecar present, only laps whose causal status is `valid`
  contribute timing, sectors, and bests; deleted laps are hidden until a
  `reinstated` event restores them, and unknown laps are omitted from
  candidate filtering. With the sidecar absent, the browser applies no
  invalidation filtering — absence means no invalidation is known, not that
  every lap is valid.
- **Scope**: v2-only, qualifying-like-only, and web-consumed. The browser
  loads the sidecar through `browser-delivery-v2` pointers and applies it
  causally to lap/sector timing, session and personal bests, and candidate
  filtering; it is not a Python-only artifact.

See [Replay Data Contract — Optional v2 qualifying lap-status
sidecar](replay-data-contract.md#optional-v2-qualifying-lap-status-sidecar)
for the full contract including parsing, matching, and fail-closed rules.

## 14. Optional qualifying summary

V2 qualifying-like deliveries (`sessionMode` `qualifying`,
`sprint-qualifying`, or `sprint-shootout`) may carry a `qualifyingSummary`
manifest reference (`path`, `schemaId`, `sha256`) pointing to
`qualifying-summary.json`:

- The summary publishes per-driver `qualifyingPosition`, `q1TimeMs`,
  `q2TimeMs`, `q3TimeMs`, `bestLapNumber`, and `bestLapTimeMs`, validated
  against `urn:f1-cache-replay:schema:replay-data:v2:qualifying-summary`.
- Q1/Q2/Q3 timings come from canonical qualifying results; best-lap timing is
  derived from valid (non-deleted) canonical laps. All columns are nullable
  and nulls propagate from the source — a driver that did not take part in Q2
  or Q3 keeps the corresponding column null, and the pipeline never invents a
  position or time.
- The reference is v2-only and qualifying-like-only, and is optional. When
  absent, the browser disables the qualifying classification panel
  (`canShowQualifyingClassification` is false); manifests without it remain
  valid and loadable. Strict parsers must explicitly allow its absence.
- **Core chunks are unchanged** and race-only artifacts are not involved: the
  summary expresses qualifying competition, never race order or gaps.

See [Replay Data Contract — Optional qualifying
summary](replay-data-contract.md#optional-qualifying-summary) for the full
contract including field semantics and null rules.

## 15. Optional qualifying timeline and incident markers

V2 qualifying-like deliveries (`sessionMode` `qualifying`,
`sprint-qualifying`, or `sprint-shootout`) may carry a `qualifyingTimeline`
manifest reference (`path`, `schemaId`, `sha256`) pointing to
`qualifying-timeline.json`:

- The artifact publishes bounded half-open `[startMs, endMs)` yellow/red
  track-status `intervals` plus separate `incidentMarkers`, validated against
  `urn:f1-cache-replay:schema:replay-data:v2:qualifying-timeline`.
- `intervals` and `incidentMarkers` are both required keys and each may be
  empty; the artifact is omitted entirely when there is nothing to publish.
- Incident markers are visibility-only records for track-map marker hiding:
  `driverId`, `timeMs`, `source` (`race-control-car-event`), `rawMessage`, and
  optional `lapNumber`. They are not race `OUT`/DNF markers and never change
  classification; they are distinct from `PositionData` `OffTrack`, which
  FastF1 backfills for missing samples and is not a retirement signal.
- Intervals are deterministically ordered by `startMs`, `endMs`, `kind`;
  markers by `timeMs`, `driverId`, `rawMessage`. The loader verifies the
  SHA-256 digest against the manifest reference, and every marker `driverId`
  must be a manifest driver.
- Consumers apply markers causally: effective when `timeMs <= replayTimeMs`.
  Playback and arbitrary seeks use the same comparisons.
- The reference is v2-only and qualifying-like-only. When absent, no intervals
  render and no markers hide (fail-closed); older deliveries remain valid and
  loadable. Core chunks and race-only artifacts are not involved.

See [Replay Data Contract — Optional qualifying timeline and incident
markers](replay-data-contract.md#optional-qualifying-timeline-and-incident-markers)
for the full contract including interval bounds, marker fields, ordering, and
causal semantics.

## 16. V1 baseline and catalog cutover

The v1 (`browser-delivery-v1`) interface is frozen and historical. Its
committed fixtures remain in the repository as reference fixtures, are never
upgraded in place, and are excluded from the active catalog.

The active catalog is v2-only:

- The catalog must be `schemaVersion 2`, every canonical pointer must be
  `canonical-parquet-v2`, and every browser pointer must be
  `browser-delivery-v2`; v1 or mixed-version pointers and manifests are
  rejected.
- **Republish all before cutover**: before the active-catalog cutover, every
  catalog race must be republished as v2 (canonical and browser). The
  migration refuses to activate a v1 catalog — canonical and browser v1
  artifacts are deprecated, and the catalog is switched only after all catalog
  races exist as v2.
- The web reader enforces the same rule: `parsePointer` and `parseManifest`
  require `formatVersion` `browser-delivery-v2` and `contractVersion` `v2`,
  and v2 manifests must reference v2 artifact schemas only (never `:v1:`).
