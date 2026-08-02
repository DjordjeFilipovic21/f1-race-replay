"""Complete on-disk validation for a canonical Parquet generation."""

from __future__ import annotations

from pathlib import Path
import hashlib
from io import BytesIO
from typing import cast

import polars as pl

from f1_replay_pipeline.domain.canonical_contract import CANONICAL_PARQUET_V2
from f1_replay_pipeline.domain.dataset_manifest import DatasetManifest, TableManifestEntry, parse_manifest
from f1_replay_pipeline.storage.generation_publication import (
    read_regular_file_no_follow,
    verify_regular_file_identity,
)
from f1_replay_pipeline.domain.logical_hashes import logical_table_sha256
from f1_replay_pipeline.storage.parquet_io import verify_canonical_parquet_round_trip
from f1_replay_pipeline.domain.validators import validate_canonical_frames


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
    _require_active_v2_manifest(manifest)
    if manifest.generation_id != expected_generation_id:
        raise ValueError("manifest generation_id disagrees with its path")
    entries = cast(tuple[TableManifestEntry, ...], manifest.tables)
    table_snapshots = []
    frames: dict[str, pl.DataFrame] = {}
    for entry in entries:
        table_path = generation_path / entry.path
        table_file = read_regular_file_no_follow(table_path, f"manifest table {entry.path}")
        if hashlib.sha256(table_file.data).hexdigest() != entry.byte_sha256:
            raise ValueError(f"manifest table checksum disagrees for {entry.name}")
        frame = pl.read_parquet(BytesIO(table_file.data), use_pyarrow=False)
        verify_canonical_parquet_round_trip(entry.name, frame, table_file.data, version="v2")
        frames[entry.name] = frame
        if (
            frame.height != entry.row_count
            or logical_table_sha256(entry.name, frame, version="v2") != entry.logical_sha256
        ):
            raise ValueError(f"manifest logical metadata disagrees for {entry.name}")
        table_snapshots.append((table_path, table_file, entry.path))
    validate_canonical_frames(frames, version="v2")
    for table_path, table_file, table_path_text in table_snapshots:
        verify_regular_file_identity(table_path, table_file, f"manifest table {table_path_text}")
    verify_regular_file_identity(manifest_path, manifest_file, "manifest")
    return manifest


def _require_active_v2_manifest(manifest: DatasetManifest) -> None:
    """Keep historical v1 parsing separate from the active read boundary."""
    if manifest.format_version != CANONICAL_PARQUET_V2 or manifest.manifest_version != 2:
        raise ValueError(
            "active canonical validation requires canonical-parquet-v2; "
            "canonical-parquet-v1 is a deprecated historical reference"
        )
    entries = cast(tuple[TableManifestEntry, ...], manifest.tables)
    if any(entry.schema_version != "v2" for entry in entries):
        raise ValueError("active canonical validation rejects mixed-version table schema tokens")


__all__ = ["validate_complete_canonical_generation"]
