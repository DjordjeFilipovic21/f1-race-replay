# Replay data contract

**Status:** Active · **Contract:** `v2` · **Browser format:**
`browser-delivery-v2`

This document is the semantic guide for the browser/replay delivery contract.
The JSON Schemas linked below are normative for serialized shape, field types,
and schema identities. The browser reader accepts **only v2**. Canonical Parquet
remains an unchanged, native-cadence source; delivery derives presentation data
without rewriting canonical rows.

## Source of truth

Every v2 artifact is identified by a schema under
`urn:f1-cache-replay:schema:replay-data:v2:*`:

| Artifact | Schema identity | Normative schema |
| --- | --- | --- |
| Manifest | `urn:f1-cache-replay:schema:replay-data:v2:manifest` | [manifest schema](../contracts/replay-data/v2/schemas/manifest.schema.json) |
| Chunk | `urn:f1-cache-replay:schema:replay-data:v2:chunk` | [chunk schema](../contracts/replay-data/v2/schemas/chunk.schema.json) |
| Track assets | `urn:f1-cache-replay:schema:replay-data:v2:track-assets` | [track-assets schema](../contracts/replay-data/v2/schemas/track-assets.schema.json) |
| Timeline summary | `urn:f1-cache-replay:schema:replay-data:v2:timeline-summary` | [timeline schema](../contracts/replay-data/v2/schemas/timeline-summary.schema.json) |
| Lap/sector sidecar | `urn:f1-cache-replay:schema:replay-data:v2:browser-lap-sector-sidecar` | [lap/sector schema](../contracts/replay-data/v2/schemas/browser-lap-sector-sidecar.schema.json) |
| Stint summary | `urn:f1-cache-replay:schema:replay-data:v2:stint-summary` | [stint schema](../contracts/replay-data/v2/schemas/stint-summary.schema.json) |
| Weather sidecar | `urn:f1-cache-replay:schema:replay-data:v2:weather-sidecar` | [weather schema](../contracts/replay-data/v2/schemas/weather-sidecar.schema.json) |
| Pit-loss model | `urn:f1-cache-replay:schema:replay-data:v2:pit-loss-model` | [pit-loss model schema](../contracts/replay-data/v2/schemas/pit-loss-model.schema.json) |
| Pit-loss estimate sidecar | `urn:f1-cache-replay:schema:replay-data:v2:pit-loss-estimate-sidecar` | [pit-loss estimate schema](../contracts/replay-data/v2/schemas/pit-loss-estimate-sidecar.schema.json) |
| Issued-penalty sidecar | `urn:f1-cache-replay:schema:replay-data:v2:penalty-sidecar` | [penalty schema](../contracts/replay-data/v2/schemas/penalty-sidecar.schema.json) |
| Qualifying summary | `urn:f1-cache-replay:schema:replay-data:v2:qualifying-summary` | [qualifying summary schema](../contracts/replay-data/v2/schemas/qualifying-summary.schema.json) |
| Qualifying lap status | `urn:f1-cache-replay:schema:replay-data:v2:browser-qualifying-lap-status` | [lap-status schema](../contracts/replay-data/v2/schemas/browser-qualifying-lap-status.schema.json) |
| Qualifying timeline | `urn:f1-cache-replay:schema:replay-data:v2:qualifying-timeline` | [qualifying timeline schema](../contracts/replay-data/v2/schemas/qualifying-timeline.schema.json) |

Serialized manifests must contain the complete URI. Producer and consumer implementations
are [the browser guards](../web/src/data/replay/guards.ts),
[the browser loader](../web/src/data/replay/loader.ts), and
[the publication boundary](../pipeline/src/f1_replay_pipeline/delivery/browser/browser_delivery_publication.py).

## Version policy and identities

- A pointer has `formatVersion: "browser-delivery-v2"`; a manifest has
  `contractVersion: "v2"` and the same format version.
- Manifest references and payloads must use v2 schema identities. Digests bind
  referenced artifacts to the immutable delivery.
- The browser rejects v1 pointers, manifests, sidecars, and mixed-version
  payloads. V1 material is retained only as frozen historical reference
  fixtures and is excluded from the active catalog. There are no v1 readers,
  adapters, publishers, fallbacks, or compatibility payloads.
- `track-status-median-v1` is a **method identifier inside a v2 pit-loss
  estimate sidecar**, not v1 replay-contract compatibility. The same applies
  to other method names ending in `-v1`; contract versions are expressed by
  `contractVersion`, `formatVersion`, and the v2 schema URI.

## Timeline, chunks, and nulls

- Serialized times are absolute integer milliseconds. Race delivery begins at
  the earliest Lap 1 start; practice, qualifying-like, and testing delivery
  begins at the earliest published lap start. UI elapsed time is display-only.
- `timeMs` is an exact shared-union timeline. Every driver and global array is
  aligned by index; events remain sparse point records.
- Chunk ownership is half-open, `[startMs, endMs)`. Handoff overlap is reference
  data before `authoritativeStartIndex`, never a second authoritative sample.
  Production uses 10,000 ms chunks and 1,000 ms overlap.
- `null` means unavailable evidence. Consumers must not replace it with zero,
  a previous value, a categorical guess, or a fabricated retirement. Optional
  `isFinished` is nullable and may be absent from older chunks.

Canonical observations retain source cadence and nullable source values. The
browser derives `trackDistanceMeters`, cumulative progress, `position`, dynamic
`leaderboardOrder`, and `gapToLeaderMs`. Coordinates are converted from FastF1
decimetres to metres at delivery; canonical Parquet is not changed.

## Session modes and artifact gating

`sessionMode` is one of `race`, `practice`, `qualifying`, `sprint`,
`sprint-qualifying`, `sprint-shootout`, or `testing`.

| Mode | Permitted optional artifacts | Browser capabilities |
| --- | --- | --- |
| `race`, `sprint` | `timelineSummary`, `pitLossModel`, `pitLossEstimateSidecar`; all mode-agnostic artifacts | race order; timeline, tyre strategy, and pit-loss panels when their references are present |
| `qualifying`, `sprint-qualifying`, `sprint-shootout` | `qualifyingSummary`, `qualifyingLapStatus`, `qualifyingTimeline`; all mode-agnostic artifacts | qualifying classification, lap-status filtering, and timeline when present |
| `practice`, `testing` | mode-agnostic artifacts only | no race or qualifying claims |

Race-only artifacts are rejected for non-race modes. Qualifying artifacts are
rejected outside qualifying-like modes. Absence of an optional artifact means
the related capability is unavailable, not that the underlying event did not
occur.

### Optional timeline summary

The race-like-only `timelineSummary` is a compact, optional artifact for
status intervals and conservative DNF markers. Intervals are half-open and
bounded by the replay window; markers are causal and do not come from transient
position status. Non-race modes reject this reference.

### Optional lap/sector sidecar

The optional `lapSectorSidecar` is a separate, aligned columnar artifact; it
does not duplicate data in chunks. Duration and sector-completion timestamps
remain nullable and consumers reveal a sector only after its completion time.
Qualifying-like sidecars carry phase boundaries and `qualifyingPhase`. The
optional `lapKind` column is `flying`, `outlap`, `inlap`, or `unknown`; when it
is absent, qualifying consumers fail closed and treat no lap as flying. See
[ADR-003](adr/003-qualifying-flying-lap-evidence-policy.md) for derivation
evidence.

### Optional stint summary

`stintSummary` publishes aligned per-driver stint and exact pit-transition
columns. Pit-in closes the indexed stint and pit-out begins it. Ambiguous or
out-of-range source mappings fail closed; missing source values remain null.

### Optional v2 weather sidecar

`weatherSidecar` preserves the sparse native weather cadence and absolute times.
At replay time `T`, consumers may use only the latest row with `timeMs <= T`;
they must not interpolate or inspect a future row. A row older than 90,000 ms,
an absent sidecar, or no surviving measurement renders weather unavailable.
The canonical weather table is not sanitized or rewritten.

### Optional pit-loss model

`pitLossModel` is a race-like-only, causal estimate timeline using the
uncalibrated `global-prior-weighted-mean-v1` method. It is optional, has no
per-driver predicted-gap array, and does not change chunk shape.

### Optional status-aware pit-loss estimate sidecar

`pitLossEstimateSidecar` is a race-like-only artifact bound to the fixture and
track asset. `curated-track-baseline-v1` is catalog-backed; the legacy
`track-status-median-v1` method is still a method name within the v2 schema.
Unknown tracks and unsupported statuses fail closed: no fabricated 22-second
fallback is published. The existing pit-loss model may remain the fallback when
it is present.

### Optional issued-penalty sidecar

`penaltySidecar` records race-control **issuances only**. A leaderboard marker
means an issuance is visible when `sessionTimeMs <= replayTimeMs`; it does not
assert that a penalty is active, unserved, or served.

### Optional qualifying summary

`qualifyingSummary` supplies nullable per-driver classification and best-lap
columns for qualifying-like sessions. It never represents race order or gaps.

### Optional v2 qualifying lap-status sidecar

`qualifyingLapStatus` carries final lap state plus causal deletion and
reinstatement events. An event is effective when `eventTimeMs <= replayTimeMs`;
only explicitly valid laps contribute timing when the sidecar is present.
Unknown is distinct from valid, and absence of the sidecar means no invalidation
is known.

### Optional qualifying timeline and incident markers

`qualifyingTimeline` carries only bounded yellow/red intervals and visibility-
only incident markers. Markers hide track-map markers causally; they never
change classification or imply `OUT`/DNF. `OffTrack` telemetry is not a
retirement signal.

## Derived ranking and runtime behavior

- Progress is `(lap - 1) * circuitLengthMeters + trackDistanceMeters`. The
  monotonic envelope resolves ties by prior order, then `driver_id`; null
  progress is omitted.
- Pit and terminal modes freeze progress without an artificial penalty.
  Projection is stale at the 1,000 ms boundary. Circular track interpolation
  is allowed only for the approved final-10% to initial-10% wrap; invalid jumps
  become null.
- Continuous fields interpolate only between valid same-driver bounds. Display
  coordinates may bridge up to 1,500 ms; other continuous fields use a 1,000 ms
  limit. Discrete fields use previous-value semantics.
- `gapToLeaderMs` is the leader's equivalent-progress crossing time with linear
  interpolation through leader history. The leader is zero; insufficient
  history produces null. Direct sampling, playback, and seeking must agree.
- `status` is exactly `position_telemetry.status`. Finish inference is
  conservative and `isFinished` is not `OUT`; consumers must never fabricate
  retirement from missing status or `OffTrack`.

The [browser replay-engine runtime semantics](browser-replay-engine-runtime-semantics.md)
describes the runtime interpretation. The
[browser delivery interface freeze](browser-delivery-interface-freeze.md)
describes the immutable boundary and publication invariants.

## V1 frozen baseline and catalog cutover

V1 (`browser-delivery-v1`) is historical-only. The active catalog requires
schema version 2, `canonical-parquet-v2` canonical pointers, and
`browser-delivery-v2` browser pointers. V1 and mixed-version pointers or
manifests are rejected; historical artifacts are not upgraded in place.

## Current limitations

Calibration is provisional pending a multi-circuit corpus. Gap availability
depends on leader-history coverage, and finish timing is inferred after the
final position sample from completion and completed-lap evidence. The curated
pit-loss catalog covers the 2024–2026 physical-circuit union, but low-evidence
circuits carry explicit derived or proxy provenance. Generation is fully
offline; no catalog value is fetched at build time.
