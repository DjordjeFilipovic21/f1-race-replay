# Replay Data Contract

This document defines the Phase 0 replay data contract that future offline replay fixtures, schema files, Python contract tests, and TypeScript replay tests must share. The goal is a deterministic, versioned, no-network format that preserves replay timing semantics independent of the current GUI runtime.

## Scope

- Covers replay artifact layout, timing semantics, interpolation rules, missing-data behavior, and schema evolution rules.
- Applies to committed contract artifacts only.
- Does not change current `src/` replay behavior in Phase 0.

## Core rules

### Replay time

- All replay timestamps are integer milliseconds.
- `sessionTimeMs` means elapsed replay time since the start of the fixture timeline.
- Writers must round before serialization so the stored contract never depends on floating-point comparisons.
- Readers must compare times as integers, not formatted strings.

### Chunk intervals

- Each chunk declares `startMs` and `endMs`.
- Chunk coverage uses half-open intervals: `[startMs, endMs)`.
- `startMs` is included in the chunk.
- `endMs` is excluded from the chunk and belongs to the next chunk when a contiguous next chunk starts at the same millisecond.
- Chunks may overlap only when the overlap is intentional and documented by matching samples near the handoff boundary.

### Overlap handling

- Overlap exists to let consumers test chunk handoff behavior without network or buffering assumptions.
- When two chunks contain the same `sessionTimeMs`, consumers must prefer the sample from the chunk whose interval owns that time under `[startMs, endMs)` semantics.
- Samples in a later chunk that fall before that chunk's `startMs` are treated as overlap-only reference points for interpolation and boundary assertions, not as authoritative ownership of that timestamp.
- Fixture and golden snapshot assertions must include at least one boundary case that proves this ownership rule.

### Interpolation semantics

- Continuous numeric values may use linear interpolation between the nearest surrounding authoritative samples.
- Discrete or categorical values must use previous-value / step semantics.
- Interpolation is only valid when both surrounding samples refer to the same logical entity and no explicit discontinuity says otherwise.

Use these defaults unless a field is explicitly documented differently:

| Field shape | Examples | Rule |
| --- | --- | --- |
| Continuous numeric | `x`, `y`, `speed`, `throttle`, `brake`, `gapToLeaderMs` | Linear interpolation |
| Discrete integer | `lap`, `position`, `gear`, `drs`, `trackStatusCode` | Previous value |
| Categorical/string | tyre compound, weather state, event type | Previous value |
| Boolean | flags such as pit-lane or retired state | Previous value |

### Missing-data semantics

- Missing means the producer does not know the value for that sample.
- Missing values must be omitted or set to `null` consistently with the future schema definition for that field.
- Consumers must not invent categorical or discrete values across a missing region unless a previous-value rule is explicitly allowed for that field.
- Consumers may interpolate a continuous value only when both bounding authoritative samples exist and are valid for interpolation.
- If either bound is missing, the interpolated result is missing.
- Sparse event streams are point-in-time records and are never interpolated.

### Schema evolution

- All committed artifacts are versioned under contract version `v1` for the initial Phase 0 format.
- Schema changes that break existing fixtures or consumers require a new contract version directory, not an in-place rewrite.
- Additive, backward-compatible fields may be introduced within a version only when schemas keep prior required fields stable.
- Consumers must read the manifest version first and reject unknown major contract versions.
- Golden snapshots are version-coupled to the fixture and schemas they validate.

## Planned artifact layout

Phase 0 will add the following committed artifacts. Future tasks should preserve these paths unless the contract version changes.

```text
contracts/replay-data/v1/
├── schemas/
│   ├── manifest.schema.json
│   ├── chunk.schema.json
│   └── track-assets.schema.json
└── fixtures/
    └── deterministic-race/
        ├── manifest.json
        ├── track-assets.json
        ├── chunks/
        │   ├── chunk-001.json
        │   └── chunk-002.json
        └── golden-snapshots.json
```

### Artifact roles

- `contracts/replay-data/v1/schemas/manifest.schema.json`
  - Top-level manifest for a replay fixture.
  - References chunk metadata and track-asset metadata.
- `contracts/replay-data/v1/schemas/chunk.schema.json`
  - Defines per-chunk payload structure, chunk bounds, overlap samples, and sample records.
- `contracts/replay-data/v1/schemas/track-assets.schema.json`
  - Defines track geometry and related static assets required by offline replay consumers.
- `contracts/replay-data/v1/fixtures/deterministic-race/manifest.json`
  - Fixture entry point for deterministic-race metadata, schema references, ordered chunk references, and the golden snapshot reference.
- `contracts/replay-data/v1/fixtures/deterministic-race/track-assets.json`
  - Small, human-readable, no-network track geometry and static replay asset payload shared by Python and future TypeScript tests.
- `contracts/replay-data/v1/fixtures/deterministic-race/chunks/chunk-001.json`
  - First committed replay chunk for the deterministic fixture.
- `contracts/replay-data/v1/fixtures/deterministic-race/chunks/chunk-002.json`
  - Second committed replay chunk for the deterministic fixture, including the boundary/overlap handoff coverage.
- `contracts/replay-data/v1/fixtures/deterministic-race/golden-snapshots.json`
  - Exact expected results for authoritative timestamps and interpolated timestamps, including chunk boundary and leaderboard-ordering assertions.

## Planned manifest structure

The future manifest should identify:

- contract version
- fixture id
- schema ids or paths
- track asset reference
- ordered chunk references
- driver roster metadata needed by tests
- a golden snapshot reference

The manifest is the entry point for both Python and TypeScript tests.

## Planned chunk behavior

Future chunk files must support these test cases:

1. Two contiguous chunks where chunk A ends at `T` and chunk B starts at `T`.
2. At least one overlap sample around that handoff.
3. An exact timestamp query that resolves to a stored sample.
4. An interpolated timestamp query between two continuous samples.
5. A categorical transition where previous-value semantics are required.
6. A sparse event that exists at one timestamp and is absent elsewhere.

## Consumer expectations

### Python contract tests

Python tests will:

- load the manifest as the single entry point
- validate manifest, chunk, and track-assets files against their schemas
- verify cross-file invariants such as ordered chunk coverage, overlap behavior, and consistent driver ids
- assert exact and interpolated golden snapshot expectations
- run without FastF1 session loading, network access, GUI windows, or OpenGL

### Future TypeScript replay tests

TypeScript tests will:

- load the same committed manifest and fixture files
- reuse the same timing, ownership, interpolation, and missing-data rules
- assert the same golden snapshot outputs at exact and derived timestamps
- treat the Phase 0 files as canonical offline fixtures, not regenerated runtime cache data

## Contract decisions to preserve

1. Replay time is stored as integer milliseconds only.
2. Chunk ownership uses half-open intervals `[startMs, endMs)`.
3. Continuous fields interpolate linearly; discrete, categorical, and boolean fields use previous-value semantics.
4. Missing values never authorize inferred categorical changes and only allow continuous interpolation when both bounds exist.
5. Sparse events are exact-time records and are never interpolated.
6. Contract artifacts live under `contracts/replay-data/v1/`.
7. The initial deterministic fixture is a directory rooted at `fixtures/deterministic-race/` with separate committed JSON files for the manifest, track assets, chunks, and golden snapshots.
8. The initial golden snapshot file is `fixtures/deterministic-race/golden-snapshots.json`.
9. New breaking contract changes require a new version directory rather than editing `v1` in place.

## Out of scope for Phase 0

- MessagePack or binary transport formats
- browser/runtime replay backends
- generated race datasets
- changes to current desktop replay code paths
