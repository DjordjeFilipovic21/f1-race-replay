"""Complete on-disk validation for a canonical Parquet generation."""

from __future__ import annotations

from pathlib import Path
import hashlib
from io import BytesIO
from typing import cast

import polars as pl

from f1_replay_pipeline.domain.dataset_manifest import DatasetManifest, TableManifestEntry, parse_manifest
from f1_replay_pipeline.storage.generation_publication import (
    read_regular_file_no_follow,
    verify_regular_file_identity,
)
from f1_replay_pipeline.domain.logical_hashes import logical_table_sha256
from f1_replay_pipeline.storage.parquet_io import verify_canonical_parquet_round_trip


def validate_complete_canonical_generation(
    generation_path: Path,
    *,
    expected_generation_id: str,
    expected_manifest_sha256: str | None = None,
    require_path_name_match: bool = True,
) -> DatasetManifest:
    """Reject every manifest/table integrity disagreement from stable snapshots.

    Each table's digest, native-Polars schema/data read, row count, and logical
    hash derive from the same guarded bytes.  The directory entry is checked
    again before accepting it so a concurrent pathname replacement fails closed.
    """
    if require_path_name_match and generation_path.name != expected_generation_id:
        raise ValueError("generation path disagrees with expected generation_id")
    manifest_path = generation_path / "manifest.json"
    manifest_file = read_regular_file_no_follow(manifest_path, "manifest")
    if expected_manifest_sha256 is not None and hashlib.sha256(manifest_file.data).hexdigest() != expected_manifest_sha256:
        raise ValueError("current pointer manifest checksum disagrees")
    manifest = parse_manifest(manifest_file.data)
    if manifest.generation_id != expected_generation_id:
        raise ValueError("manifest generation_id disagrees with its path")
    entries = cast(tuple[TableManifestEntry, ...], manifest.tables)
    table_snapshots = []
    for entry in entries:
        table_path = generation_path / entry.path
        table_file = read_regular_file_no_follow(table_path, f"manifest table {entry.path}")
        if hashlib.sha256(table_file.data).hexdigest() != entry.byte_sha256:
            raise ValueError(f"manifest table checksum disagrees for {entry.name}")
        frame = pl.read_parquet(BytesIO(table_file.data), use_pyarrow=False)
        verify_canonical_parquet_round_trip(entry.name, frame, table_file.data)
        if frame.height != entry.row_count or logical_table_sha256(entry.name, frame) != entry.logical_sha256:
            raise ValueError(f"manifest logical metadata disagrees for {entry.name}")
        table_snapshots.append((table_path, table_file, entry.path))
    for table_path, table_file, table_path_text in table_snapshots:
        verify_regular_file_identity(table_path, table_file, f"manifest table {table_path_text}")
    verify_regular_file_identity(manifest_path, manifest_file, "manifest")
    return manifest


__all__ = ["validate_complete_canonical_generation"]
