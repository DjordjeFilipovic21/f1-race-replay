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

from f1_replay_pipeline.domain.canonical_schema import CANONICAL_TABLE_SCHEMAS_V2
from f1_replay_pipeline.domain.canonical_contract import CANONICAL_PARQUET_V2
from f1_replay_pipeline.storage.canonical_writer import (
    PublishedCanonicalGeneration,
    publish_canonical_generation,
    resolve_published_canonical_generation,
)
from f1_replay_pipeline.storage.canonical_generation_validation import validate_complete_canonical_generation
from f1_replay_pipeline.domain.dataset_manifest import (
    CurrentPointer, DatasetManifest, ManifestValidationError, TableManifestEntry, manifest_sha256,
    parse_manifest, serialize_current_pointer, serialize_deterministic_json, serialize_manifest,
)
from f1_replay_pipeline.storage.generation_publication import GenerationPublicationError, GenerationPublicationResult
from f1_replay_pipeline.storage.parquet_io import CANONICAL_PARQUET_TABLE_NAMES
from f1_replay_pipeline.domain.validators import CanonicalValidationError
from f1_replay_pipeline.domain.logical_hashes import logical_table_sha256


def test_publish_all_ten_tables_returns_immutable_current_metadata(tmp_path: Path) -> None:
    result = publish_canonical_generation(
        frames=_canonical_frames(), target_parent=tmp_path, generation_id="first",
    )

    manifest = parse_manifest(result.manifest_path.read_bytes())

    assert isinstance(result, PublishedCanonicalGeneration)
    entries = cast(tuple[TableManifestEntry, ...], manifest.tables)
    assert manifest.format_version == CANONICAL_PARQUET_V2
    assert manifest.manifest_version == 2
    assert manifest.schema_token == "canonical-parquet-v2:manifest"
    assert all(entry.schema_token.startswith("canonical-parquet-v2:") for entry in entries)
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


def test_active_generation_validation_rejects_the_frozen_v1_golden_manifest(tmp_path: Path) -> None:
    golden = Path(__file__).parents[1] / "golden" / "canonical_manifest_v1.json"
    generation = tmp_path / "2026-07-15T120000Z-abc"
    generation.mkdir()
    (generation / "manifest.json").write_bytes(golden.read_bytes())

    with pytest.raises(ValueError, match="manifest format_version and manifest_version must identify canonical-parquet-v2"):
        validate_complete_canonical_generation(
            generation,
            expected_generation_id=generation.name,
        )


def test_v2_golden_manifest_is_a_separate_versioned_fixture() -> None:
    fixture = Path(__file__).parents[1] / "golden" / "canonical_manifest_v2.json"

    manifest = parse_manifest(fixture.read_bytes())

    assert manifest.format_version == CANONICAL_PARQUET_V2
    assert all(entry.schema_version == "v2" for entry in cast(tuple[TableManifestEntry, ...], manifest.tables))


def test_malformed_v2_fixture_rejects_mixed_schema_tokens() -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "canonical-v2" / "malformed-mixed-version-manifest.json"

    with pytest.raises(ManifestValidationError):
        parse_manifest(fixture.read_bytes())


def test_v2_writer_preserves_nullable_qualifying_result_fields(tmp_path: Path) -> None:
    result = publish_canonical_generation(
        frames=_canonical_frames(), target_parent=tmp_path, generation_id="qualifying",
    )

    restored = pl.read_parquet(result.generation_path / "tables" / "results.parquet", use_pyarrow=False)

    assert restored.select("q1_time_ms", "q2_time_ms", "q3_time_ms").null_count().row(0) == (1, 1, 1)


def test_v2_manifest_logical_hash_includes_session_mode_and_qualifying_values(tmp_path: Path) -> None:
    # Arrange: publish otherwise identical v2 data with qualifying-specific values.
    qualifying = _canonical_frames()
    qualifying["session_metadata"] = qualifying["session_metadata"].with_columns(
        pl.lit("qualifying").alias("session_mode"),
        pl.lit("qualifying").alias("session_type"),
    )
    qualifying["results"] = qualifying["results"].with_columns(
        pl.lit(105_123, dtype=pl.Int64).alias("q1_time_ms"),
    )
    published = publish_canonical_generation(
        frames=qualifying, target_parent=tmp_path / "qualifying", generation_id="qualifying",
    )
    manifest = parse_manifest(published.manifest_path.read_bytes())
    entries = {entry.name: entry for entry in cast(tuple[TableManifestEntry, ...], manifest.tables)}

    # Assert: complete v2 logical identity is present in the manifest.
    assert entries["session_metadata"].logical_sha256 == logical_table_sha256(
        "session_metadata", qualifying["session_metadata"], version="v2",
    )
    assert entries["results"].logical_sha256 == logical_table_sha256(
        "results", qualifying["results"], version="v2",
    )
    changed_mode = qualifying["session_metadata"].with_columns(pl.lit("race").alias("session_mode"))
    assert logical_table_sha256("session_metadata", changed_mode, version="v2") != entries["session_metadata"].logical_sha256


def test_v2_manifest_schema_tokens_include_session_mode_and_qualifying_columns(tmp_path: Path) -> None:
    # Arrange / Act: publish one deterministic v2 generation.
    result = publish_canonical_generation(
        frames=_canonical_frames(), target_parent=tmp_path, generation_id="schema-tokens",
    )
    manifest = parse_manifest(result.manifest_path.read_bytes())
    entries = {entry.name: entry for entry in cast(tuple[TableManifestEntry, ...], manifest.tables)}

    # Assert: v2 metadata and results schema tokens carry the qualifying boundary.
    metadata_tokens = {(token.name, token.dtype) for token in entries["session_metadata"].schema}
    results_tokens = {(token.name, token.dtype) for token in entries["results"].schema}
    assert ("session_mode", "canonical-parquet-v2:String") in metadata_tokens
    assert ("q1_time_ms", "canonical-parquet-v2:Int64") in results_tokens
    assert ("q2_time_ms", "canonical-parquet-v2:Int64") in results_tokens
    assert ("q3_time_ms", "canonical-parquet-v2:Int64") in results_tokens


def test_v2_round_trip_preserves_qualifying_values_and_session_mode(tmp_path: Path) -> None:
    # Arrange: a qualifying-shaped generation with populated Q1/Q2 and null Q3.
    frames = _canonical_frames()
    frames["session_metadata"] = frames["session_metadata"].with_columns(
        pl.lit("qualifying").alias("session_type"),
        pl.lit("qualifying").alias("session_mode"),
    )
    frames["results"] = frames["results"].with_columns(
        pl.lit(105_123, dtype=pl.Int64).alias("q1_time_ms"),
        pl.lit(104_567, dtype=pl.Int64).alias("q2_time_ms"),
    )

    # Act: publish and read the actual Parquet artifacts back.
    published = publish_canonical_generation(
        frames=frames, target_parent=tmp_path, generation_id="qualifying-roundtrip",
    )
    manifest = parse_manifest(published.manifest_path.read_bytes())
    entries = {entry.name: entry for entry in cast(tuple[TableManifestEntry, ...], manifest.tables)}
    restored_results = pl.read_parquet(published.generation_path / "tables" / "results.parquet", use_pyarrow=False)
    restored_metadata = pl.read_parquet(published.generation_path / "tables" / "session_metadata.parquet", use_pyarrow=False)

    # Assert: values, nulls, and the published logical hashes all round trip.
    assert restored_results.item(0, "q1_time_ms") == 105_123
    assert restored_results.item(0, "q2_time_ms") == 104_567
    assert restored_results.item(0, "q3_time_ms") is None
    assert restored_metadata.item(0, "session_mode") == "qualifying"
    assert entries["results"].logical_sha256 == logical_table_sha256("results", frames["results"], version="v2")
    assert entries["session_metadata"].logical_sha256 == logical_table_sha256(
        "session_metadata", frames["session_metadata"], version="v2",
    )


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
    disagreeing = DatasetManifest(
        "different", manifest.tables, manifest.writer_settings,
        format_version=manifest.format_version, manifest_version=manifest.manifest_version,
    )
    result.manifest_path.write_bytes(serialize_manifest(disagreeing))
    result.pointer_path.write_bytes(
        serialize_current_pointer(
            CurrentPointer("published", manifest_sha256(disagreeing), format_version=CANONICAL_PARQUET_V2),
        )
    )

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
        format_version=manifest.format_version,
        manifest_version=manifest.manifest_version,
    )
    result.manifest_path.write_bytes(serialize_manifest(logically_invalid))
    result.pointer_path.write_bytes(
        serialize_current_pointer(
            CurrentPointer("published", manifest_sha256(logically_invalid), format_version=CANONICAL_PARQUET_V2),
        )
    )

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
        serialize_current_pointer(
            CurrentPointer(
                "published", hashlib.sha256(invalid_payload).hexdigest(),
                format_version=CANONICAL_PARQUET_V2,
            )
        )
    )

    with pytest.raises(GenerationPublicationError, match="canonical generation validation failed"):
        resolve_published_canonical_generation(tmp_path)


def test_complete_validator_independently_rejects_table_byte_integrity_failure(tmp_path: Path) -> None:
    result = publish_canonical_generation(
        frames=_canonical_frames(), target_parent=tmp_path, generation_id="published",
    )
    table = result.generation_path / "tables" / "drivers.parquet"
    table.write_bytes(table.read_bytes() + b"tampered")

    with pytest.raises(GenerationPublicationError, match="canonical generation validation failed"):
        resolve_published_canonical_generation(tmp_path)


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

    with pytest.raises(GenerationPublicationError, match="canonical generation validation failed"):
        resolve_published_canonical_generation(tmp_path / "first")


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
    return pl.DataFrame(rows, schema=dict(CANONICAL_TABLE_SCHEMAS_V2[table_name]), strict=True)


def _row(table_name: str) -> dict[str, object]:
    row: dict[str, object] = {column: None for column in CANONICAL_TABLE_SCHEMAS_V2[table_name]}
    row.update({"session_id": "2026-example-race", "driver_id": "HAM"})
    values = {
        "session_metadata": {"year": 2026, "round_number": 1, "event_name": "Example Grand Prix", "session_name": "Race", "session_type": "R", "session_mode": "race", "session_start_time_utc": datetime(2026, 1, 1, tzinfo=timezone.utc)},
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
