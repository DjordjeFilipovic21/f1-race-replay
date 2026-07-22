# ADR-002: Canonical Parquet writer and publication contract

- **Status:** Accepted
- **Date:** 2026-07-15
- **Scope:** Phase 1 canonical-data pipeline
- **Related:** [ADR-001](001-canonical-pipeline-foundation.md)

## Context

ADR-001 defines the ten native-cadence canonical tables and deliberately leaves
artifact serialization, logical hashes, checksums, and publication to the
writer. Multiple Parquet files must become readable as one generation without
making readers observe a partially written set.

## Decision

1. **Use versioned generation directories.** A generation is published below
   `target_parent/generations/<generation-id>/`. Its table files are at
   `tables/<canonical-table>.parquet`; its manifest is `manifest.json`.
2. **Make `current.json` the sole reader visibility boundary.** The pointer is
   replaced atomically only after the complete generation is staged, flushed,
   and validated. It contains `format_version`, `generation_id`,
   `manifest_path`, and `manifest_sha256`.
3. **Use native Polars only.** Every table is validated against ADR-001 before
   writing with `use_pyarrow=False`, `compression="zstd"`,
   `compression_level=3`, `statistics="full"`, `row_group_size=262144`, and
   `data_page_size=1048576`. `maintain_order` is not a `DataFrame.write_parquet`
   contract option and is excluded.
4. **Separate logical identity from byte integrity.** The SHA-256 logical hash
   is computed from the versioned wire encoding in the writer contract, not
   from Parquet bytes. A second SHA-256 covers the exact published Parquet
   bytes.
5. **Serialize one deterministic manifest.** Manifest bytes are UTF-8 JSON
   using sorted keys, compact separators, `ensure_ascii=False`,
   `allow_nan=False`, and one trailing newline. The manifest does not contain
   its own hash.
6. **Refuse symlink traversal.** Publication and resolution reject symlinked
    roots, ancestors, generation directories, and files. Pointer, manifest, and
    table reads use descriptor-relative no-follow checks where available; the
    byte hash and Polars reads consume the same guarded snapshot and reject an
    inode/device replacement.
7. **Publish with same-parent staging and an exclusive pointer temp.** Stage
   under the target parent, flush and `fsync` every file, validate the complete
   generation with the shared reader validator, rename it into `generations`,
   then create a unique pointer temp beside `current.json` using
   `O_CREAT|O_EXCL|O_NOFOLLOW`. Flush and fsync it before atomically replacing
   `current.json`.
8. **Make recovery ownership explicit.** Publication and stale cleanup acquire
   an exclusive lock/lease and fail closed when ownership is active, malformed,
   unverifiable, or cannot be released. Cleanup removes only known staging
   prefixes and aggregates cleanup errors without hiding the primary error.
9. **Record durability boundaries and status.** Attempted directory fsyncs are
   recorded individually for parent creation when needed, after
   `generations`/staging creation, the staged or selected generation, the
   `generations` directory, and the target parent after commit. Each is
   recorded as `succeeded`, `unsupported`, or `failed`.
10. **Expose orchestration, not policy leakage.** The Python API accepts
   validated canonical Polars frames and explicit filesystem seams. It does not
   load FastF1, create browser artifacts, modify `legacy/src/`, or provide a CLI.

## Consequences

- Readers can reject an invalid pointer or incomplete generation before using
  any table.
- `current.json` replacement is the commit point. If it succeeds but the final
  parent fsync fails, the outcome is committed but durability is uncertain; the
  selected generation remains the reported result.
- Directory-fsync support is explicit. NFS, overlay, and network filesystems can
  make rename, locking, visibility, or persistence ambiguous; this is not a
  multi-path transaction and crashes can leave staging residue.
- Logical equality remains comparable across Parquet writer environments, while
  byte hashes detect corruption or unexpected artifact changes.
- Parquet bytes are not promised to be identical across Polars, Arrow, OS, or
  filesystem versions.
- `current.json` publication is one atomic pathname replacement, not a general
  multi-path transaction. A crash can leave an unused complete generation or
  staging residue, but it must not be selected unless the pointer and shared
  complete validator pass.

## Reconciliation with ADR-001

This ADR implements the serialization and publication policies explicitly
deferred by ADR-001. It preserves ADR-001's schema, column order, normalized
nulls, native cadence, row order, and separation from Phase 0 browser chunks.
It does not alter ADR-001 or reuse the Phase 0 browser manifest schema.

## References

- [Canonical pipeline schema](../canonical-pipeline-schema.md)
- [Canonical Parquet writer contract](../canonical-parquet-writer-contract.md)
- [Phase 0 replay data contract](../replay-data-contract.md)
