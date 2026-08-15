# ADR-003: Qualifying flying-lap evidence policy

- **Status:** Accepted
- **Date:** 2026-08-04
- **Scope:** Qualifying live leaderboard, lap analysis, and browser delivery
- **Related:** [ADR-001](001-canonical-pipeline-foundation.md),
  [ADR-002](002-canonical-parquet-writer.md)

## Context

Qualifying replay requirements demand that flying laps are the only laps that
count toward best/fastest times, charts, summaries, sector history, and live
qualifying timing, and that finish is phase-local and flying-lap-local: the
last flying lap in a Q phase marks finish as soon as its completion is causal,
never the driver's ultimate session lap and never the literal last sidecar row.

FastF1 3.8.3 cannot truthfully label every lap's purpose. It has **no**
`is_out_lap`, `is_in_lap`, or cooldown column; the only authoritative in/out
signals are the `PitInTime`/`PitOutTime` columns, and a clean non-pit cooldown
lap is indistinguishable from a slow flying lap using source data alone. The
current web selector guesses: `inferLapPhase` labels a started lap with no
causal sector completion `outlap` and any other lap `flying`
(`qualifying-live-state-selectors.ts`). That heuristic contradicts the
fail-closed requirement and must be replaced by a delivery-level, aligned,
source-grounded classification.

The canonical `laps` table already carries every signal this policy needs —
`pit_in_time_ms`, `pit_out_time_ms`, `is_accurate`, `deleted`,
`lap_duration_ms`, the three sector durations, and the three sector session
timestamps (`laps_stints.py`) — but no lap-type column exists.

## Decision

1. **Publish an aligned delivery-level `lapKind` field.** Each per-driver lap
   row in the qualifying lap/sector surface carries `lapKind` with exactly one
   of `flying | outlap | inlap | unknown`. The field is aligned to the existing
   per-driver lap arrays (same length, same order) so consumers apply it
   without cross-referencing other artifacts. Canonical Parquet is unchanged.

2. **Exclude pit laps from flying using the authoritative pit signals.**
   - Non-null `PitOutTime` excludes the lap from flying and classifies it
     `outlap`.
   - Non-null `PitInTime` excludes the lap from flying and classifies it
     `inlap`.
   - A row with **both** signals present is still excluded from flying, but its
     kind is `unknown` rather than guessing between `outlap` and `inlap`
     (FastF1 allows a lap to be both in- and out-lap in one row; the policy
     refuses to pick).

3. **Require complete, consistent timing evidence for flying candidates.**
   A lap is a flying candidate only when **all** of the following hold:
   - `IsAccurate` is `true`;
   - `Deleted` is not `true`;
   - `LapTime` is non-null;
   - all three sector durations and all three causal sector session timestamps
     are present;
   - no pit-in and no pit-out; and
   - duration evidence is consistent (the sector-duration sum agrees with
     `LapTime` within the 3 ms tolerance FastF1's own accuracy check uses).

4. **Accept qualifying timing with FastF1 accurate/quicklap semantics and a
   107% per-Q-phase gate.** Timing acceptance mirrors FastF1's own
   `_calculate_qualifying_results` recipe: `pick_accurate()` partitioned per
   Q phase by `split_qualifying_sessions()`, then `pick_quicklaps()`
   (`QUICKLAP_THRESHOLD = 1.07`) applied **per phase**, keeping only rows with
   non-null `LapTime` and `Deleted == false`. Ambiguous non-pit cooldown laps
   that survive the gate cannot be distinguished authoritatively and must
   resolve to `unknown` (fail-closed); their intent is **never** inferred from
   speed or slowness beyond the deterministic 107% gate.

5. **Define finish as causal completion of the last accepted flying lap in the
   active Q phase.** A driver receives finish styling when the last
   `lapKind === 'flying'` lap in the active phase has completed causally
   (`lapEndMs <= replayTimeMs`). A later cooldown or pit-in lap never delays
   finish, never replaces the displayed qualifying time, and the driver's
   ultimate session lap is irrelevant unless it is that accepted flying lap. A
   driver progressing to Q2/Q3 still receives finish for their last flying lap
   in Q1/Q2 respectively.

6. **Keep canonical Parquet unchanged and make the v2 delivery field optional.**
   Optional delivery fields/artifacts (for example `lapKind` arrays in the
   lap/sector sidecar) preserve the existing v2 delivery shape: deliveries
   without the field remain valid and loadable, strict parsers allow its
   absence, and absent or unknown evidence is never treated as flying. The
   field is aligned so a missing value means "not flying", never a guess.

## Rationale

- FastF1's parser is the authoritative source for lap purpose, and its own
  definition is pit-based: "if `PitInTime` is not NaT the lap is an inlap;
  if `PitOutTime` is not NaT the lap is an outlap" (FastF1 3.8.3 source
  evidence). There is no cooldown concept anywhere in the library, so pit
  evidence is the only truth-preserving excluder.
- `IsAccurate` already bundles the integrity gates we need — no pit signals,
  no `FastF1Generated`, green/yellow track status, all sector times present,
  sector sum ≈ `LapTime` within 3 ms, and lap-to-lap time delta consistency
  (FastF1 3.8.3 source evidence).
  Requiring it plus non-null `LapTime` and non-deleted is exactly FastF1's own
  qualifying-results recipe.
- The parser ignores `LastLapTime` values ≥ 150 s between Q phases, so very
  slow laps may arrive with no `LapTime`; non-null `LapTime` is a hard
  precondition, and `IsAccurate` (which requires it) is applied on top.
- FastF1 applies the 107% quick-lap rule per Q phase in its own result
  computation; adopting it per phase is the library's documented heuristic for
  removing slow warm-up/cooldown laps and keeps our ranking comparable to
  official-style phase results. Any classification beyond that gate would be
  fabricated.
- `split_qualifying_sessions()` partitions by `LapStartTime` and only
  re-assigns boundary-crossing laps to the next phase when they carry a
  `PitOutTime` (FastF1 3.8.3 source evidence), which is why phase-local finish
  must be computed from laps already assigned to that phase rather than from
  session-row order.
- The existing v2 sidecar already exposes `qualifyingPhase` per lap and
  top-level `phaseBoundaries`, so phase locality is available today; the gap is
  a truthful `lapKind`, which this ADR grounds in canonical fields the adapter
  already normalizes.

## Consequences

- Slow but genuine flying laps beyond the 107% gate may be excluded. That is
  safer than publishing a false best or finish time, and matches FastF1's own
  result semantics.
- Laps with both pit signals, unknown deletion state, or missing sector
  evidence resolve to `unknown`/non-flying instead of `flying`; qualifying
  bests, charts, summaries, sector history, and live timing omit them.
- The web selector's sector-completion heuristic (`inferLapPhase`) is replaced
  by the aligned delivery field; `QualifyingLiveLapPhase` gains `inlap` and
  drops its guessing behavior.
- Finish becomes deterministic and causal: a driver whose last flying lap in a
  phase has completed shows finish even while Q-time remains, and a subsequent
  cooldown/pit-in lap cannot displace the displayed qualifying time.
- The lap/sector sidecar model, JSON Schema, guards, types, and loader need
  optional `lapKind` support; canonical Parquet and core v2 chunks remain
  untouched.
- Delivering without `lapKind` remains valid; consumers must treat absence as
  non-flying (fail-closed), never as a signal that every lap is flying.

## Rejected alternatives

- **Speed/slowness or sector-pattern heuristics for cooldown intent.** A
  cooldown lap that stays on track is indistinguishable from a slow clean lap
  using data alone; FastF1 has no cooldown concept. Any per-driver delta,
  absolute-time cutoff, or gear/telemetry classifier would fabricate intent and
  could misclassify both directions. Rejected in favor of fail-closed
  `unknown`.
- **Treating the literal last sidecar row or ultimate session lap as the
  finished lap.** Cooldown and pit-in laps follow the last flying lap; using
  them delays finish and replaces the displayed qualifying time. Rejected in
  favor of decision 5.
- **Relying on `qualifyingSummary` fallback as the primary live timing
  source.** The summary is final/phase-complete classification, not causal
  live evidence; it cannot drive finish as a lap completes. It remains only a
  fallback for completed-phase positions.
- **Adding a canonical `lap_kind` column to Parquet.** Delivery derivation
  from already-normalized canonical fields keeps the canonical contract frozen
  (ADR-001/002) and the field optional. Rejected to preserve canonical
  stability.
- **Reusing race `timelineSummary` DNF semantics for qualifying.** Race-only
  mode gating already rejects that artifact for qualifying-like modes. The
  qualifying-safe `qualifyingTimeline` artifact (yellow/red `intervals` plus
  visibility-only `race-control-car-event` `incidentMarkers`) is a separate,
  optional v2 surface specified in the [replay data
  contract](../replay-data-contract.md#optional-qualifying-timeline-and-incident-markers)
  and the [runtime semantics](../browser-replay-engine-runtime-semantics.md);
  this ADR governs flying-lap evidence only, and incident markers never change
  classification.

## Historical implementation/validation implications

> **Historical planning note (implementation complete):** The following
> bullets preserve the pre-implementation plan for provenance. Their imperative
> language and approval blocker are no longer current instructions. The
> decision above is unchanged.

- **Pipeline.** Derive `lapKind` per driver/lap in the lap/sector sidecar
  builder (`delivery/browser/browser_lap_sector_sidecar.py`) from canonical
  `laps` fields already produced by `adapters/fastf1/laps_stints.py`
  (`pit_in_time_ms`, `pit_out_time_ms`, `is_accurate`, `deleted`,
  `lap_duration_ms`, sector durations, sector session timestamps). Apply the
  107% per-Q-phase gate from FastF1's `_calculate_qualifying_results` recipe.
  Canonical Parquet and `laps_stints.py` normalization stay unchanged.
- **Schema/guards/types.** Add optional `lapKind` to
  `BrowserDriverLapSector` (`delivery/browser/browser_delivery_models.py`),
  its `as_dict`, the `browser-lap-sector-sidecar.schema.json`, and the web
  `guards.ts`/`types.ts`/`loader.ts`. The field is absent-capable; strict
  parsers allow absence and absence means non-flying. Phase locality comes from
  the existing v2 `phaseBoundaries` and `qualifyingPhase`; `lapKind` is the
  only new column.
- **Web selectors.** Replace `inferLapPhase` in
  `qualifying-live-state-selectors.ts` with the aligned `lapKind`;
  `selectFastestCausalLapDuration` and `selectFinishedLap` must consider only
  `flying` laps in the active phase with causal `lapEndMs <= replayTimeMs`.
  Update `QualifyingLiveLapPhase` to `'flying' | 'outlap' | 'inlap' |
  'unknown'`.
- **Validation.** Qualifying selector/panel tests, web typecheck, web tests,
  web build, and pipeline contract tests should cover this policy. If
  validation fails, stop and report rather than auto-fixing.
- **Approval needed to unblock implementation.** Downstream tasks adopt this
  fail-closed policy as-is: no classifier beyond the documented deterministic
  rules may be invented for non-pit cooldown laps, and any new qualifying
  timeline/incident artifact must remain optional within v2 and separate from
  race DNF semantics.

## Current implementation status

As of 2026-08-15, the policy is implemented. The pipeline derives the aligned
optional `lapKind` column for qualifying-like deliveries, the v2 model/schema
and browser guards serialize and validate it, and the web selectors use it for
causal flying-lap timing and finish state. Qualifying-like sidecars without the
column fail closed; the legacy sector-completion fallback remains only for
non-qualifying sidecars. Pipeline and web tests cover derivation, alignment,
serialization, parsing, and selector behavior. No approval blocker remains.

## References

- [Replay data contract](../replay-data-contract.md)
- [Replay data contract — Optional qualifying timeline and incident markers](../replay-data-contract.md#optional-qualifying-timeline-and-incident-markers)
- [Browser replay-engine runtime semantics](../browser-replay-engine-runtime-semantics.md)
- [Browser delivery interface freeze](../browser-delivery-interface-freeze.md)
- [ADR-001: Canonical pipeline foundation policies](001-canonical-pipeline-foundation.md)
- [ADR-002: Canonical Parquet writer and publication contract](002-canonical-parquet-writer.md)
