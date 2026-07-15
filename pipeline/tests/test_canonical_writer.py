"""Integration coverage for the public canonical Parquet publication API."""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import FrozenInstanceError, replace
import hashlib
import os
from pathlib import Path
from typing import cast

import polars as pl
import pytest

from f1_replay_pipeline.canonical_schema import CANONICAL_TABLE_SCHEMAS
from f1_replay_pipeline.canonical_generation_validation import validate_complete_canonical_generation
from f1_replay_pipeline.canonical_writer import (
    PublishedCanonicalGeneration,
    publish_canonical_generation,
    resolve_published_canonical_generation,
)
from f1_replay_pipeline.dataset_manifest import (
    CurrentPointer, DatasetManifest, TableManifestEntry, manifest_sha256,
    parse_manifest, serialize_current_pointer, serialize_deterministic_json, serialize_manifest,
)
from f1_replay_pipeline.generation_publication import GenerationPublicationError, GenerationPublicationResult
from f1_replay_pipeline.parquet_io import CANONICAL_PARQUET_TABLE_NAMES
from f1_replay_pipeline.validators import CanonicalValidationError


def test_publish_all_ten_tables_returns_immutable_current_metadata(tmp_path: Path) -> None:
    result = publish_canonical_generation(
        frames=_canonical_frames(), target_parent=tmp_path, generation_id="first",
    )

    manifest = parse_manifest(result.manifest_path.read_bytes())

    assert isinstance(result, PublishedCanonicalGeneration)
    entries = cast(tuple[TableManifestEntry, ...], manifest.tables)
    assert tuple(entry.name for entry in entries) == CANONICAL_PARQUET_TABLE_NAMES
    assert all((result.generation_path / entry.path).is_file() for entry in entries)
    assert result.committed
    assert result.directory_fsyncs
    assert result.durability_confirmed == (
        result.directory_fsyncs[-1].outcome == "succeeded"
    )
    assert resolve_published_canonical_generation(tmp_path) == result
    with pytest.raises(FrozenInstanceError):
        result.generation_id = "changed"  # type: ignore[misc]


def test_fixed_inputs_and_settings_produce_identical_manifest_bytes(tmp_path: Path) -> None:
    first = publish_canonical_generation(frames=_canonical_frames(), target_parent=tmp_path / "one", generation_id="fixed")
    second = publish_canonical_generation(frames=_canonical_frames(), target_parent=tmp_path / "two", generation_id="fixed")

    assert first.manifest_path.read_bytes() == second.manifest_path.read_bytes()
    assert first.manifest_sha256 == second.manifest_sha256


def test_new_current_generation_preserves_prior_generation(tmp_path: Path) -> None:
    previous = publish_canonical_generation(frames=_canonical_frames(), target_parent=tmp_path, generation_id="previous")
    current = publish_canonical_generation(frames=_canonical_frames(), target_parent=tmp_path, generation_id="current")

    assert previous.manifest_path.is_file()
    assert resolve_published_canonical_generation(tmp_path) == current


def test_invalid_frame_is_rejected_before_target_directory_is_created(tmp_path: Path) -> None:
    frames = _canonical_frames()
    frames["results"] = frames["results"].select(list(reversed(frames["results"].columns)))
    target = tmp_path / "not-created"

    with pytest.raises(CanonicalValidationError, match="schema mismatch"):
        publish_canonical_generation(frames=frames, target_parent=target, generation_id="invalid")

    assert not target.exists()


def test_checkpoint_and_publisher_seams_are_available_to_callers(tmp_path: Path) -> None:
    publisher_calls: list[dict[str, object]] = []

    def recording_publisher(**kwargs: object) -> GenerationPublicationResult:
        publisher_calls.append(kwargs)
        return GenerationPublicationResult(
            tmp_path / "generation", tmp_path / "manifest.json", tmp_path / "current.json", "a" * 64,
        )

    result = publish_canonical_generation(
        frames=_canonical_frames(), target_parent=tmp_path, generation_id="seams", publisher=recording_publisher,
    )

    assert result.manifest_sha256 == "a" * 64
    assert publisher_calls[0]["filesystem"] is None
    assert not result.durability_confirmed


def test_resolution_rejects_pointer_and_manifest_generation_id_disagreement(tmp_path: Path) -> None:
    result = publish_canonical_generation(frames=_canonical_frames(), target_parent=tmp_path, generation_id="published")
    manifest = parse_manifest(result.manifest_path.read_bytes())
    disagreeing = DatasetManifest("different", manifest.tables, manifest.writer_settings)
    result.manifest_path.write_bytes(serialize_manifest(disagreeing))
    result.pointer_path.write_bytes(serialize_current_pointer(CurrentPointer("published", manifest_sha256(disagreeing))))

    with pytest.raises(GenerationPublicationError, match="canonical generation validation failed"):
        resolve_published_canonical_generation(tmp_path)


@pytest.mark.parametrize(
    "pointer_bytes",
    [
        b'{"format_version":"canonical-parquet-v1","generation_id":"published","generation_id":"other","manifest_path":"generations/published/manifest.json","manifest_sha256":"' + b"0" * 64 + b'"}\n',
        b'{ "format_version":"canonical-parquet-v1","generation_id":"published","manifest_path":"generations/published/manifest.json","manifest_sha256":"' + b"0" * 64 + b'"}\n',
    ],
)
def test_resolution_rejects_duplicate_or_noncanonical_current_pointer_bytes(
    tmp_path: Path, pointer_bytes: bytes,
) -> None:
    result = publish_canonical_generation(
        frames=_canonical_frames(), target_parent=tmp_path, generation_id="published",
    )
    result.pointer_path.write_bytes(pointer_bytes)

    with pytest.raises(GenerationPublicationError, match="invalid current pointer"):
        resolve_published_canonical_generation(tmp_path)


def test_resolution_rejects_schema_valid_generation_with_invalid_logical_metadata(tmp_path: Path) -> None:
    result = publish_canonical_generation(frames=_canonical_frames(), target_parent=tmp_path, generation_id="published")
    manifest = parse_manifest(result.manifest_path.read_bytes())
    entries = cast(tuple[TableManifestEntry, ...], manifest.tables)
    logically_invalid = DatasetManifest(
        manifest.generation_id,
        (replace(entries[0], logical_sha256="0" * 64), *entries[1:]),
        manifest.writer_settings,
    )
    result.manifest_path.write_bytes(serialize_manifest(logically_invalid))
    result.pointer_path.write_bytes(serialize_current_pointer(CurrentPointer("published", manifest_sha256(logically_invalid))))

    with pytest.raises(GenerationPublicationError, match="canonical generation validation failed"):
        resolve_published_canonical_generation(tmp_path)


def test_resolution_rejects_generation_with_schema_invalid_manifest_metadata(tmp_path: Path) -> None:
    result = publish_canonical_generation(frames=_canonical_frames(), target_parent=tmp_path, generation_id="published")
    payload = parse_manifest(result.manifest_path.read_bytes()).to_dict()
    tables = cast(list[dict[str, object]], payload["tables"])
    tables[0]["schema"] = []
    invalid_payload = serialize_deterministic_json(payload)
    result.manifest_path.write_bytes(invalid_payload)
    result.pointer_path.write_bytes(
        serialize_current_pointer(CurrentPointer("published", hashlib.sha256(invalid_payload).hexdigest()))
    )

    with pytest.raises(GenerationPublicationError, match="canonical generation validation failed"):
        resolve_published_canonical_generation(tmp_path)


def test_complete_validator_independently_rejects_table_byte_integrity_failure(tmp_path: Path) -> None:
    result = publish_canonical_generation(
        frames=_canonical_frames(), target_parent=tmp_path, generation_id="published",
    )
    table = result.generation_path / "tables" / "drivers.parquet"
    table.write_bytes(table.read_bytes() + b"tampered")

    with pytest.raises(ValueError, match="checksum"):
        validate_complete_canonical_generation(result.generation_path, expected_generation_id="published")


def test_complete_validator_fails_closed_when_a_table_inode_changes_during_polars_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = publish_canonical_generation(
        frames=_canonical_frames(), target_parent=tmp_path / "first", generation_id="first",
    )
    alternate_frames = _canonical_frames()
    alternate_frames["drivers"] = alternate_frames["drivers"].with_columns(
        pl.lit("Changed Driver").alias("full_name")
    )
    second = publish_canonical_generation(
        frames=alternate_frames, target_parent=tmp_path / "second", generation_id="second",
    )
    original_read = pl.read_parquet
    table = first.generation_path / "tables" / "drivers.parquet"
    replacement = second.generation_path / "tables" / "drivers.parquet"
    swapped = False
    reads = 0

    def swap_after_guarded_read(source: object, *args: object, **kwargs: object) -> pl.DataFrame:
        nonlocal reads, swapped
        frame = original_read(source, *args, **kwargs)
        reads += 1
        if reads == 3:
            replacement_copy = table.with_suffix(".replacement")
            replacement_copy.write_bytes(replacement.read_bytes())
            os.replace(replacement_copy, table)
            swapped = True
        return frame

    monkeypatch.setattr(pl, "read_parquet", swap_after_guarded_read)

    with pytest.raises(GenerationPublicationError, match="changed during guarded validation"):
        validate_complete_canonical_generation(first.generation_path, expected_generation_id="first")


def test_public_api_rejects_a_symlinked_publication_root_without_touching_external_data(
    tmp_path: Path,
) -> None:
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "sentinel"
    sentinel.write_bytes(b"unchanged")
    target = tmp_path / "target"
    target.symlink_to(external, target_is_directory=True)

    with pytest.raises(GenerationPublicationError, match="publication root"):
        publish_canonical_generation(
            frames=_canonical_frames(), target_parent=target, generation_id="rejected",
        )
    with pytest.raises(GenerationPublicationError, match="publication root"):
        resolve_published_canonical_generation(target)

    assert sentinel.read_bytes() == b"unchanged"
    assert not (external / "generations").exists()


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
