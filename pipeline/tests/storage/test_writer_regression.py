"""Focused offline regression coverage for the public canonical writer API."""

from __future__ import annotations

import builtins
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import cast

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from f1_replay_pipeline.domain.canonical_schema import CANONICAL_TABLE_SCHEMAS
from f1_replay_pipeline.storage.canonical_writer import (
    publish_canonical_generation,
    resolve_published_canonical_generation,
)
from f1_replay_pipeline.domain.dataset_manifest import TableManifestEntry, parse_manifest
from f1_replay_pipeline.storage.generation_publication import (
    GenerationPublicationError,
    LocalFilesystem,
    PublicationDurabilityUncertainError,
)
from f1_replay_pipeline.domain.logical_hashes import logical_table_sha256
from f1_replay_pipeline.storage.parquet_io import CANONICAL_PARQUET_TABLE_NAMES


def test_public_writer_publishes_and_verifies_all_ten_tables_deterministically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public API preserves logical identity while recording closed byte artifacts."""
    # Arrange
    _reject_pyarrow_imports(monkeypatch)
    frames = _canonical_frames()

    # Act
    first = publish_canonical_generation(
        frames=frames, target_parent=tmp_path / "first", generation_id="fixed",
    )
    second = publish_canonical_generation(
        frames=_canonical_frames(), target_parent=tmp_path / "second", generation_id="fixed",
    )
    resolved = resolve_published_canonical_generation(tmp_path / "first")
    manifest_bytes = first.manifest_path.read_bytes()
    manifest = parse_manifest(manifest_bytes)
    pointer = json.loads(first.pointer_path.read_bytes())

    # Assert
    assert resolved == first
    assert first.committed
    assert first.durability_confirmed == (
        first.directory_fsyncs[-1].outcome == "succeeded"
    )
    assert manifest_bytes == second.manifest_path.read_bytes()
    assert first.manifest_sha256 == hashlib.sha256(manifest_bytes).hexdigest()
    assert manifest_bytes.endswith(b"\n") and manifest_bytes.count(b"\n") == 1
    entries = cast(tuple[TableManifestEntry, ...], manifest.tables)
    assert tuple(entry.name for entry in entries) == CANONICAL_PARQUET_TABLE_NAMES
    assert len(entries) == len(CANONICAL_PARQUET_TABLE_NAMES) == 10
    assert pointer == {
        "format_version": "canonical-parquet-v1",
        "generation_id": "fixed",
        "manifest_path": "generations/fixed/manifest.json",
        "manifest_sha256": first.manifest_sha256,
    }
    for entry in entries:
        table_path = first.generation_path / entry.path
        restored = pl.read_parquet(table_path, use_pyarrow=False)
        assert entry.path == f"tables/{entry.name}.parquet"
        assert entry.row_count == frames[entry.name].height
        assert entry.logical_sha256 == logical_table_sha256(entry.name, frames[entry.name])
        assert entry.byte_sha256 == hashlib.sha256(table_path.read_bytes()).hexdigest()
        assert_frame_equal(restored, frames[entry.name], check_exact=True)


def test_public_writer_failure_keeps_prior_current_generation_recoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure before pointer replacement leaves the previous public generation usable."""
    # Arrange
    _reject_pyarrow_imports(monkeypatch)
    previous = publish_canonical_generation(
        frames=_canonical_frames(), target_parent=tmp_path, generation_id="previous",
    )

    def fail_before_pointer_replace(event: str) -> None:
        if event == "before_pointer_replace":
            raise OSError("injected pointer failure")

    # Act
    with pytest.raises(OSError, match="injected pointer failure"):
        publish_canonical_generation(
            frames=_canonical_frames(),
            target_parent=tmp_path,
            generation_id="failed",
            checkpoint=fail_before_pointer_replace,
        )
    recovered = resolve_published_canonical_generation(tmp_path)

    # Assert
    assert recovered == previous
    assert (tmp_path / "generations" / "failed").is_dir()
    assert json.loads((tmp_path / "current.json").read_bytes())["generation_id"] == "previous"


def test_public_writer_does_not_mask_a_committed_but_durability_uncertain_publication(
    tmp_path: Path,
) -> None:
    """A caller receives the hardened error and its committed result unchanged."""
    # Arrange
    class PostCommitFsyncFailure(LocalFilesystem):
        def __init__(self) -> None:
            self.target_parent_calls = 0

        def fsync_directory(self, path: Path) -> bool:
            if path == tmp_path:
                self.target_parent_calls += 1
                if self.target_parent_calls == 3:
                    raise OSError("injected post-commit fsync")
            return super().fsync_directory(path)

    # Act
    with pytest.raises(PublicationDurabilityUncertainError) as raised:
        publish_canonical_generation(
            frames=_canonical_frames(),
            target_parent=tmp_path,
            generation_id="uncertain",
            filesystem=PostCommitFsyncFailure(),
        )

    # Assert
    assert raised.value.committed
    assert raised.value.result.committed
    assert raised.value.result.directory_fsyncs[-1].outcome == "failed"
    assert json.loads((tmp_path / "current.json").read_bytes())["generation_id"] == "uncertain"


def test_public_writer_keeps_logical_identity_separate_from_tampered_parquet_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Logical metadata does not make a changed physical artifact acceptable."""
    # Arrange
    _reject_pyarrow_imports(monkeypatch)
    frames = _canonical_frames()
    published = publish_canonical_generation(
        frames=frames, target_parent=tmp_path, generation_id="separate-integrity",
    )
    manifest = parse_manifest(published.manifest_path.read_bytes())
    entry = cast(tuple[TableManifestEntry, ...], manifest.tables)[0]
    table_path = published.generation_path / entry.path
    logical_before = logical_table_sha256(entry.name, frames[entry.name])
    byte_before = hashlib.sha256(table_path.read_bytes()).hexdigest()
    table_path.write_bytes(b"tampered physical artifact")

    # Act
    with pytest.raises(GenerationPublicationError, match="canonical generation validation failed"):
        resolve_published_canonical_generation(tmp_path)

    # Assert
    assert logical_table_sha256(entry.name, frames[entry.name]) == logical_before == entry.logical_sha256
    assert hashlib.sha256(table_path.read_bytes()).hexdigest() != byte_before == entry.byte_sha256


def _reject_pyarrow_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def reject_pyarrow(name, *args, **kwargs):
        if name == "pyarrow" or name.startswith("pyarrow."):
            raise AssertionError("public writer regression must not import PyArrow")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_pyarrow)


def _canonical_frames() -> dict[str, pl.DataFrame]:
    return {name: _frame(name, [_row(name)]) for name in CANONICAL_PARQUET_TABLE_NAMES}


def _frame(table_name: str, rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=dict(CANONICAL_TABLE_SCHEMAS[table_name]), strict=True)


def _row(table_name: str) -> dict[str, object]:
    row: dict[str, object] = {column: None for column in CANONICAL_TABLE_SCHEMAS[table_name]}
    row.update({"session_id": "2026-example-race", "driver_id": "HAM"})
    values = {
        "session_metadata": {"year": 2026, "round_number": 1, "event_name": "Example Grand Prix", "session_name": "Race", "session_type": "R", "session_start_time_utc": datetime(2026, 1, 1, tzinfo=timezone.utc)},
        "drivers": {"source_driver_key": "44", "driver_number": 44, "full_name": "Lewis Hamilton"},
        "car_telemetry": {"source_driver_key": "44", "session_time_ms": 0, "source": "car"},
        "position_telemetry": {"source_driver_key": "44", "session_time_ms": 0, "source": "position"},
        "laps": {"lap_number": 1, "lap_start_time_ms": 0},
        "stints": {"stint_number": 1, "start_lap_number": 1},
        "weather": {"session_time_ms": 0},
        "track_status_intervals": {"start_time_ms": 0, "status": "1"},
        "race_control_messages": {"session_time_ms": 0, "message_index": 0, "message": "Race start"},
        "results": {"classified_position": "1"},
    }
    row.update(values[table_name])
    return row
