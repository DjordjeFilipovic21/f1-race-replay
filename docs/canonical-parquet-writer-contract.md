# Canonical Parquet writer contract

This is the normative v1 contract for writing the ten validated canonical
tables from [ADR-001](adr/001-canonical-pipeline-foundation.md). It is separate
from the Phase 0 browser manifest and browser chunks.

The key words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative.

## 1. Input boundary

The writer MUST receive a mapping of these tables, each as an already validated
Polars `DataFrame`, with the exact schema, column order, null policy, and row
order defined by the canonical schema:

```text
session_metadata, drivers, car_telemetry, position_telemetry, laps,
stints, weather, track_status_intervals, race_control_messages, results
```

It MUST validate all ten tables before serialization, including declared dtype,
column order, required fields, and row count. Empty tables retain their typed
schema. The writer MUST NOT load FastF1, resample/interpolate telemetry, infer
schema, reorder rows, create browser artifacts, or call code under legacy
`src/`.

## 2. Published layout

For a caller-supplied `generation_id`, the target layout is:

```text
<target_parent>/
├── current.json
└── generations/
    └── <generation_id>/
        ├── manifest.json
        └── tables/
            ├── session_metadata.parquet
            ├── drivers.parquet
            ├── car_telemetry.parquet
            ├── position_telemetry.parquet
            ├── laps.parquet
            ├── stints.parquet
            ├── weather.parquet
            ├── track_status_intervals.parquet
            ├── race_control_messages.parquet
            └── results.parquet
```

`generation_id` MUST be a single safe path component. Table and manifest paths
in metadata MUST be relative POSIX paths and MUST NOT escape the generation
directory. A generation MUST contain exactly one entry for each canonical table.

### Current pointer

`current.json` MUST be compact deterministic JSON with at least this object:

```json
{"format_version":"canonical-parquet-v1","generation_id":"2026-07-15T120000Z-abc","manifest_path":"generations/2026-07-15T120000Z-abc/manifest.json","manifest_sha256":"<64 lowercase hex characters>"}
```

Readers MUST reject a pointer when its JSON is invalid, fields are missing or
malformed, `format_version` is unsupported, the generation ID/path disagree,
the manifest is absent, the manifest hash disagrees, the manifest is invalid,
any table is absent, or any recorded logical/byte hash, path, schema, or row
count fails validation. Readers MUST keep using the prior valid pointer when a
new pointer fails validation; they MUST NOT select a directory merely because
its name appears under `generations`.

## 3. Parquet serialization

Each table MUST be written with native `DataFrame.write_parquet` settings:

```python
{
    "use_pyarrow": False,
    "compression": "zstd",
    "compression_level": 3,
    "statistics": "full",
    "row_group_size": 262144,
    "data_page_size": 1048576,
}
```

The writer MUST NOT pass undocumented `maintain_order` to this API and MUST NOT
require or implicitly select PyArrow. Input order is already canonical; the
writer MUST NOT use an unstable output-order option to define identity.

These settings describe the v1 writer configuration and MUST be recorded in
the manifest. They do not make Parquet bytes portable across Polars/Arrow
versions, operating systems, or storage environments.

## 4. Logical hash v1

The logical table hash is `sha256(wire_bytes)`, where `wire_bytes` is exactly:

```text
ASCII("F1RP-LOGICAL-TABLE\0v1\0")
U64BE(table-name-byte-length) + table-name UTF-8 bytes
U64BE(column-count)
repeat column-count:
    U64BE(column-name-byte-length) + column-name UTF-8 bytes
    U64BE(type-token-byte-length) + type-token UTF-8 bytes
U64BE(row-count)
repeat rows in declared canonical order:
    repeat columns in declared column order: one encoded cell
```

`U64BE` is an unsigned 64-bit big-endian integer. Every length and count MUST
fit that representation. Tokens MUST be ASCII-compatible UTF-8 and identify
the exact declared Polars dtype, for example `String`, `Int16`, `Int64`,
`Float64`, `Boolean`, and `Datetime[ms,UTC]`.

Each cell begins with one tag:

| Tag | Value encoding |
| --- | --- |
| `0x00` | null; no payload |
| `0x01` | boolean; exactly `0x00` false or `0x01` true |
| `0x02` | signed integer, two's-complement big-endian, using the declared dtype width (8/16/32/64 bits) |
| `0x03` | IEEE-754 binary64, big-endian, finite only |
| `0x04` | UTF-8 byte length as U64BE, then UTF-8 bytes |
| `0x05` | signed UTC epoch milliseconds as an Int64 big-endian payload |

The tag MUST agree with the declared type token. Missing values use `0x00`.
Unsupported dtypes, unsigned integers, decimals, lists/structs, timezone-less
datetimes, malformed UTF-8, out-of-range integers, and non-finite floats MUST
fail. Negative zero floats MUST be encoded as positive zero so equivalent
logical values have one representation. Datetimes MUST already be UTC at
millisecond precision.

This encoding hashes logical schema and ordered values, not Parquet metadata,
compression, page layout, or file bytes. A fixed input permutation MUST produce
the same hash only after the canonical normalizer has restored the declared row
order.

## 5. Deterministic manifest

The manifest MUST contain `format_version`, `manifest_version`, `generation_id`,
`tables`, and `writer_settings`. `tables` MUST be an ordered array in the
canonical table order above. Each entry MUST contain:

```json
{
  "name": "car_telemetry",
  "path": "tables/car_telemetry.parquet",
  "row_count": 0,
  "schema": [{"name":"session_id","dtype":"String"}],
  "logical_sha256": "<64 lowercase hex characters>",
  "byte_sha256": "<64 lowercase hex characters>"
}
```

`schema` MUST preserve declared column order and include every column's exact
dtype token. The manifest MUST record the complete writer settings shown in
Section 3 and the generation identity. It MUST NOT contain a self-referential
manifest hash.

Manifest bytes MUST be produced as follows, with no custom float or date
encoder needed because all values are normalized metadata:

```python
json.dumps(
    manifest,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
    allow_nan=False,
).encode("utf-8") + b"\n"
```

The exact bytes written MUST be the bytes hashed for `manifest_sha256` and MUST
end with exactly one newline. Hashes are lowercase hexadecimal SHA-256 digests.

## 6. Atomic publication and recovery

The publisher MUST:

1. Reject symlinked or non-directory publication roots and ancestors, and reject
    symlinked generation directories or files. Pointer, manifest, and table paths
    MUST be opened descriptor-relatively with no-follow checks where the platform
    provides them. Hashing and native Polars validation MUST consume one guarded
    file snapshot, and a changed device/inode identity MUST fail validation.
2. Acquire and retain an exclusive recovery lock/lease, then create a uniquely
   named staging directory under `target_parent`. Fail closed if ownership is
   active, malformed, or unverifiable.
3. Write each table to a staging file, flush, `os.fsync`, and close it before
   renaming it into the staging `tables/` directory.
4. Write and fsync the manifest after all table hashes are known.
5. Validate the staged generation with the same complete validator used by
   readers, then rename it into
   `generations/<generation_id>` on the same filesystem.
6. Retain the lease through publication and cleanup; failure to release it is
   reported as an ownership/cleanup error.
7. Create a unique pointer temp beside `current.json` with
   `O_CREAT|O_EXCL|O_NOFOLLOW`; write, flush, and fsync it, then atomically
   replace `current.json` with `os.replace`.
8. Treat replacement of `current.json` as the commit point. If the subsequent
   parent fsync fails, raise a committed-but-durability-uncertain outcome whose
   result identifies the selected generation.
9. Attempt and report directory fsync boundaries for parent creation (when
   applicable), `generations`/staging creation, the generation, the
   `generations` directory, and the target parent after commit. Each status is
   `succeeded`, `unsupported`, or `failed`.
10. Remove only stale staging directories matching the publisher's known prefix;
   cleanup errors MUST be reported without hiding the original failure.

Before pointer replacement, an existing valid `current.json` MUST remain
untouched. The publisher MUST reject a conflicting existing generation ID rather
than overwrite it. Recovery MAY remove stale staging directories and MUST
revalidate the current pointer and complete manifest before selecting a
generation. It MUST never delete or rewrite the prior published generation as
part of failed publication. Cleanup and lease-release failures MUST be
aggregated on the primary error, or reported as a cleanup error when
publication otherwise succeeded.

The complete validator is the selection rule: a directory is never current
merely because it exists under `generations/`. Pointer topology, manifest
digest, table presence, schemas, row counts, logical hashes, and byte hashes
must all validate.

## 7. Durability limits

Flush and fsync provide best-effort crash durability, not a database transaction,
replication, signature, or authorization guarantee. Directory fsync and
`O_DIRECTORY` are platform-dependent; the implementation MUST report
`unsupported` or `failed` rather than claim an unavailable boundary was
performed. Staging and destination on different filesystems can fail with
`EXDEV`. NFS can report ambiguous rename or lock outcomes, and overlay/network
filesystems may weaken persistence or visibility. A crash may leave an unused
complete generation, pointer temp, or staging residue. The contract guarantees
only one atomic `current.json` replacement where same-filesystem semantics
support it; it does not promise multi-path transactionality.

## 8. Python API boundary

The implementation SHOULD expose one orchestration entry point equivalent to:

```python
result = write_generation(
    frames=validated_canonical_frames,
    target_parent=Path("artifacts"),
    generation_id="2026-07-15T120000Z-abc",
    filesystem=filesystem_seam,
)
```

The API MUST require the ten-frame mapping, target parent, and caller-supplied
generation ID. It SHOULD return the published generation path, manifest path,
pointer path, and manifest digest. Filesystem operations, directory fsync,
rename, cleanup, and failure injection MUST be injectable seams so tests can
fail at each write/fsync/rename step deterministically. The API MUST be
side-effect free at import time and MUST NOT expose FastF1 loading, a user CLI,
browser/CDN publishing, telemetry transformation, or OpenGL behavior.

The reader boundary SHOULD expose pointer resolution and complete-generation
validation separately from raw Parquet loading. A consumer may then use native
Polars `read_parquet`/`scan_parquet` only after pointer, manifest, schema, row
count, and hash validation succeeds.

Memory-streaming optimization is explicitly deferred: v1 materializes the
validated in-memory frames and closed Parquet bytes. The writer requires native
Polars (`use_pyarrow=False`), does not load FastF1, and does not alter the Phase 0
browser manifest, browser chunks, or legacy `src/` pipeline.
