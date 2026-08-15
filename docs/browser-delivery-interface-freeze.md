# Browser delivery interface freeze

**Status:** Accepted · **Version:** `browser-delivery-v2` · **Date:** 2026-07-17

This document freezes the boundary between validated canonical Parquet and
browser replay artifacts. The [Replay Data Contract](replay-data-contract.md)
is the single normative source for payload semantics and optional-artifact
behavior; this document records boundary, publication, and compatibility rules.

## 1. Contract identity

The active browser interface is v2 only:

- the current pointer uses `formatVersion: "browser-delivery-v2"`;
- the manifest uses `contractVersion: "v2"` and
  `formatVersion: "browser-delivery-v2"`; and
- every manifest schema identity starts with
  `urn:f1-cache-replay:schema:replay-data:v2:`.

The web reader rejects v1 pointers, manifests, sidecars, and mixed-version
payloads. V1 (`browser-delivery-v1`) remains only as a frozen historical
baseline and is excluded from the active catalog. This project does not add v1
readers, adapters, publishers, fallbacks, fixtures, or compatibility payloads.

Names such as `track-status-median-v1` and `curated-track-baseline-v1` are
algorithm method identifiers **inside v2 artifacts**, not v1 contract support.

Normative wire shapes are the [v2 manifest schema](../contracts/replay-data/v2/schemas/manifest.schema.json),
[v2 chunk schema](../contracts/replay-data/v2/schemas/chunk.schema.json), and
[v2 track-assets schema](../contracts/replay-data/v2/schemas/track-assets.schema.json).

## 2. Canonical-to-browser boundary

Canonical Parquet is the loss-minimizing, native-cadence source. Publication
does not rewrite, resample, interpolate, forward-fill, or republish canonical
rows. Browser delivery may derive presentation fields, including:

- metre coordinates from FastF1 decimetres;
- track distance and cumulative progress;
- live position and dynamic leaderboard order; and
- gap to the current leader.

Source nullable values remain nullable. Derived values are aligned to the
shared exact-union timeline, and unavailable evidence remains `null` rather
than becoming zero or a fabricated retirement. Canonical pipeline details are
documented in the [canonical pipeline schema](canonical-pipeline-schema.md) and
[canonical Parquet writer contract](canonical-parquet-writer-contract.md).

## 3. Delivery contents and optional artifacts

An immutable generation contains a manifest, track assets, ordered chunks, and
only the optional artifacts referenced by that manifest. Each reference carries
its path, complete v2 schema identity, and SHA-256 digest. The contract document
owns the detailed rules; use these links when implementing or reviewing an
artifact:

| Delivery item | Contract section | Schema |
| --- | --- | --- |
| Timeline summary | [timeline summary](replay-data-contract.md#optional-timeline-summary) | [schema](../contracts/replay-data/v2/schemas/timeline-summary.schema.json) |
| Lap/sector sidecar | [lap/sector sidecar](replay-data-contract.md#optional-lapsector-sidecar) | [schema](../contracts/replay-data/v2/schemas/browser-lap-sector-sidecar.schema.json) |
| Stint summary | [stint summary](replay-data-contract.md#optional-stint-summary) | [schema](../contracts/replay-data/v2/schemas/stint-summary.schema.json) |
| Weather sidecar | [v2 weather sidecar](replay-data-contract.md#optional-v2-weather-sidecar) | [schema](../contracts/replay-data/v2/schemas/weather-sidecar.schema.json) |
| Pit-loss model | [pit-loss model](replay-data-contract.md#optional-pit-loss-model) | [schema](../contracts/replay-data/v2/schemas/pit-loss-model.schema.json) |
| Status-aware pit-loss | [status-aware pit-loss](replay-data-contract.md#optional-status-aware-pit-loss-estimate-sidecar) | [schema](../contracts/replay-data/v2/schemas/pit-loss-estimate-sidecar.schema.json) |
| Issued penalties | [issued penalties](replay-data-contract.md#optional-issued-penalty-sidecar) | [schema](../contracts/replay-data/v2/schemas/penalty-sidecar.schema.json) |
| Qualifying summary | [qualifying summary](replay-data-contract.md#optional-qualifying-summary) | [schema](../contracts/replay-data/v2/schemas/qualifying-summary.schema.json) |
| Qualifying lap status | [lap status](replay-data-contract.md#optional-v2-qualifying-lap-status-sidecar) | [schema](../contracts/replay-data/v2/schemas/browser-qualifying-lap-status.schema.json) |
| Qualifying timeline | [qualifying timeline](replay-data-contract.md#optional-qualifying-timeline-and-incident-markers) | [schema](../contracts/replay-data/v2/schemas/qualifying-timeline.schema.json) |

Optional artifacts are additive: core chunks and canonical Parquet remain
unchanged, and consumers must accept a manifest without any optional reference.
Mode gating is strict: race-only artifacts are not valid for non-race sessions,
and qualifying artifacts are not valid outside qualifying-like sessions.

## 4. Publication and immutability invariants

Publication validates v2 schema instances and semantic identity before replacing
the browser pointer. It must:

1. bind the manifest to one source generation and canonical manifest digest;
2. use deterministic JSON bytes and one SHA-256 digest per referenced artifact;
3. keep chunk paths contiguous and ownership half-open, `[startMs, endMs)`;
4. preserve overlap samples only as pre-authority handoff references;
5. reject unsafe or unreferenced files and references with mismatched digests;
6. replace the pointer atomically only after validation; and
7. never modify `current.json`, canonical Parquet, or `templates/` as part of
   browser publication.

The implementation enforcing this boundary is the
[browser publication module](../pipeline/src/f1_replay_pipeline/delivery/browser/browser_delivery_publication.py).

## 5. Consumer boundary

Consumers should use the [browser loader and guards](../web/src/data/replay/loader.ts)
and [replay types](../web/src/data/replay/types.ts), not duplicate contract
validation. At runtime:

- timestamps remain absolute integer milliseconds; controls may display elapsed
  time relative to delivery start;
- arrays are index-aligned and chunks use half-open ownership;
- continuous interpolation and discrete previous-value sampling follow the
  [runtime semantics](browser-replay-engine-runtime-semantics.md);
- non-race sessions never expose race order, race gaps, finish, DNF, or pit-loss
  claims; and
- absent optional evidence renders the related capability unavailable rather
  than inventing a value.

## 6. V1 baseline and catalog cutover

The active catalog is v2-only: schema version 2, `canonical-parquet-v2`
canonical pointers, and `browser-delivery-v2` browser pointers. The catalog
cannot be activated with v1 or mixed-version entries. Historical v1 fixtures
remain unchanged and are never loaded by the v2 reader.

## 7. Current limitations

Projection and gap quality depend on available native telemetry and leader
history. Finish timing is inferred from completion and completed-lap evidence
after the final position sample, not read as a canonical retirement timestamp.
Calibration remains provisional pending a multi-circuit corpus. The curated
pit-loss catalog is generated offline and unknown tracks fail closed.

For implementation-level runtime behavior, see [Browser replay-engine runtime
semantics](browser-replay-engine-runtime-semantics.md). For canonical source
semantics, see the [Replay Data Contract](replay-data-contract.md).
