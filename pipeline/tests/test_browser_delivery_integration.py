"""Offline end-to-end coverage for validated canonical browser delivery."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import polars as pl
import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

from f1_replay_pipeline.browser_chunk_builder import CONTINUOUS_FIELD_SEMANTICS, PREVIOUS_VALUE_FIELD_SEMANTICS
from f1_replay_pipeline.browser_delivery_orchestration import build_browser_delivery
from f1_replay_pipeline.browser_delivery_publication import (
    BrowserDeliveryPublicationError,
    publish_browser_delivery,
)
from f1_replay_pipeline.browser_delivery_reader import read_validated_canonical_generation
from f1_replay_pipeline.canonical_schema import CANONICAL_TABLE_SCHEMAS
from f1_replay_pipeline.canonical_writer import publish_canonical_generation
from f1_replay_pipeline.parquet_io import CANONICAL_PARQUET_TABLE_NAMES


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = REPO_ROOT / "contracts" / "replay-data" / "v1"
FIXTURE_ROOT = CONTRACT_ROOT / "fixtures" / "deterministic-race"


def test_validated_canonical_generation_derives_deterministic_schema_valid_browser_artifacts(
    tmp_path: Path,
) -> None:
    # Arrange: publish a complete, validated native-cadence canonical generation.
    canonical_parent = tmp_path / "canonical"
    published_canonical = publish_canonical_generation(
        frames=_canonical_frames(), target_parent=canonical_parent, generation_id="canonical-v1"
    )
    snapshot = read_validated_canonical_generation(canonical_parent)
    track_assets = _track_assets()
    delivery = build_browser_delivery(snapshot, track_assets)

    # Act: build the identical delivery twice at independent publication targets.
    first = publish_browser_delivery(
        browser_parent=tmp_path / "browser-one",
        delivery_version="delivery-v1",
        delivery=delivery,
        schema_root=CONTRACT_ROOT / "schemas",
    )
    second = publish_browser_delivery(
        browser_parent=tmp_path / "browser-two",
        delivery_version="delivery-v1",
        delivery=delivery,
        schema_root=CONTRACT_ROOT / "schemas",
    )
    first_manifest = _load_json(first.manifest_path)
    first_chunks = tuple(_load_json(path) for path in first.chunk_paths)

    # Assert: artifacts are deterministic, v1-valid, ordered, and retain delivery semantics.
    assert _artifact_bytes(first) == _artifact_bytes(second)
    assert published_canonical.pointer_path.read_bytes() == canonical_parent.joinpath("current.json").read_bytes()
    _validate_browser_contract(first_manifest, first_chunks, track_assets)
    assert [chunk["sequence"] for chunk in first_manifest["chunks"]] == [1, 2, 3]
    assert [chunk["path"] for chunk in first_manifest["chunks"]] == [
        "chunks/chunk-001.json", "chunks/chunk-002.json", "chunks/chunk-003.json"
    ]
    assert first_manifest["sourceGenerationId"] == "canonical-v1"
    assert first_manifest["sourceManifestSha256"] == published_canonical.manifest_sha256
    assert tuple(chunk["timeMs"] for chunk in first_chunks) == (
        [0, 5_000, 6_000, 9_500], [9_500, 10_000, 11_000, 19_999], [19_999, 20_000],
    )
    assert first_chunks[0]["drivers"]["HAM"]["lap"] == [1, 1, 1, 1]
    assert first_chunks[0]["trackStatusCode"] == [1, 4, 4, 4]
    assert first_chunks[0]["weatherState"] == ["clear", "clear", "rain", "rain"]
    assert first_chunks[1]["authoritativeStartIndex"] == 1
    assert first_chunks[1]["overlap"] == {
        "kind": "handoff",
        "previousChunkPath": "chunks/chunk-001.json",
        "range": {"startMs": 9_000, "endMs": 10_000},
        "authoritativeFromMs": 10_000,
    }
    assert first_chunks[1]["events"] == [{
        "sessionTimeMs": 10_000,
        "eventType": "race_control",
        "description": "boundary event",
        "driverId": "HAM",
    }]
    assert first_chunks[1]["drivers"]["HAM"]["x"] == [None, 10.0, None, 20.0]
    assert first_chunks[1]["drivers"]["HAM"]["y"] == [9.5, 11.0, None, None]
    assert first_chunks[1]["drivers"]["HAM"]["speed"] == [None, 200.0, 210.0, None]
    assert first_chunks[1]["drivers"]["HAM"]["brake"] == [None, 1, 0, None]
    assert first_chunks[1]["drivers"]["HAM"]["trackDistanceMeters"] == [None] * 4
    assert CONTINUOUS_FIELD_SEMANTICS["speed"] == "linear"
    assert PREVIOUS_VALUE_FIELD_SEMANTICS["gear"] == "previous"


def test_browser_publication_rejects_an_unsafe_version_before_creating_output(
    tmp_path: Path,
) -> None:
    canonical_parent = tmp_path / "canonical"
    publish_canonical_generation(
        frames=_canonical_frames(), target_parent=canonical_parent, generation_id="canonical-v1"
    )
    delivery = build_browser_delivery(
        read_validated_canonical_generation(canonical_parent), _track_assets()
    )

    # Act / Assert: invalid output identity is rejected without publishing or reading.
    with pytest.raises(BrowserDeliveryPublicationError, match="safe path component"):
        publish_browser_delivery(
            browser_parent=tmp_path / "browser",
            delivery_version="../unsafe",
            delivery=delivery,
            schema_root=CONTRACT_ROOT / "schemas",
        )
    assert not (tmp_path / "browser").exists()


def test_committed_deterministic_fixture_remains_schema_valid_and_golden_compatible() -> None:
    # Arrange: load only committed compatibility artifacts.
    manifest = _load_json(FIXTURE_ROOT / "manifest.json")
    chunks = tuple(_load_json(FIXTURE_ROOT / reference["path"]) for reference in manifest["chunks"])
    track_assets = _load_json(FIXTURE_ROOT / "track-assets.json")
    golden = _load_json(FIXTURE_ROOT / "golden-snapshots.json")

    # Act: validate the immutable v1 fixture against the local schema registry.
    _validate_replay_contract(manifest, chunks, track_assets)

    # Assert: its compatibility shape and golden expectations have not been replaced.
    assert [(item["startMs"], item["endMs"], item["overlapWithPreviousMs"]) for item in manifest["chunks"]] == [
        (0, 2_000, 0), (2_000, 4_000, 500)
    ]
    assert golden["fixtureId"] == manifest["fixtureId"] == "deterministic-race"
    assert {snapshot["id"] for snapshot in golden["snapshots"]} >= {
        "overlap-ownership-at-1500", "interpolated-sparse-event-at-2600"
    }


def _canonical_frames() -> dict[str, pl.DataFrame]:
    rows = {name: [_row(name)] for name in CANONICAL_PARQUET_TABLE_NAMES}
    rows["car_telemetry"] = [
        _row("car_telemetry", session_time_ms=time, speed_kph=speed, throttle_pct=throttle,
             brake=brake, gear=gear, drs=drs)
        for time, speed, throttle, brake, gear, drs in (
            (0, 100.0, 50.0, False, 4, 0), (10_000, 200.0, 70.0, True, 6, 1),
            (11_000, 210.0, 75.0, False, 7, 1), (20_000, 220.0, 80.0, None, None, None),
        )
    ]
    rows["position_telemetry"] = [
        _row("position_telemetry", session_time_ms=time, x=x, y=y, status=status)
        for time, x, y, status in ((0, 0.0, 1.0, "OnTrack"), (9_500, None, 9.5, None), (10_000, 10.0, 11.0, "OnTrack"), (19_999, 20.0, None, "OnTrack"))
    ]
    rows["weather"] = [
        _row("weather", session_time_ms=0, rainfall=False),
        _row("weather", session_time_ms=6_000, rainfall=True),
    ]
    rows["track_status_intervals"] = [
        _row("track_status_intervals", start_time_ms=0, end_time_ms=5_000, status="1"),
        _row("track_status_intervals", start_time_ms=5_000, end_time_ms=None, status="4"),
    ]
    return {name: pl.DataFrame(value, schema=dict(CANONICAL_TABLE_SCHEMAS[name]), strict=True) for name, value in rows.items()}


def _row(table: str, **changes: object) -> dict[str, object]:
    row = {column: None for column in CANONICAL_TABLE_SCHEMAS[table]}
    row.update({"session_id": "synthetic-race", "driver_id": "HAM"})
    row.update({
        "session_metadata": {"year": 2026, "round_number": 1, "event_name": "Synthetic Grand Prix", "session_name": "Race", "session_type": "R", "session_start_time_utc": datetime(2026, 1, 1, tzinfo=timezone.utc)},
        "drivers": {"source_driver_key": "44", "driver_number": 44, "full_name": "Lewis Hamilton", "team_name": "Mercedes", "team_colour": "00D2BE"},
        "car_telemetry": {"source_driver_key": "44", "session_time_ms": 0, "source": "car"},
        "position_telemetry": {"source_driver_key": "44", "session_time_ms": 0, "source": "pos"},
        "laps": {"lap_number": 1, "lap_start_time_ms": 0, "lap_end_time_ms": 20_001, "compound": "MEDIUM"},
        "stints": {"stint_number": 1, "start_lap_number": 1},
        "weather": {"session_time_ms": 0},
        "track_status_intervals": {"start_time_ms": 0, "status": "1"},
        "race_control_messages": {"session_time_ms": 10_000, "message_index": 0, "message": "boundary event", "driver_id": "HAM"},
        "results": {"classified_position": "1"},
    }[table])
    row.update(changes)
    return row


def _track_assets() -> dict[str, object]:
    assets = _load_json(FIXTURE_ROOT / "track-assets.json")
    assets["fixtureId"] = "synthetic-race"
    return assets


def _artifact_bytes(result: object) -> tuple[bytes, ...]:
    return tuple(path.read_bytes() for path in (result.manifest_path, result.track_assets_path, *result.chunk_paths))


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _registry() -> tuple[dict[str, object], Registry]:
    schemas = {name: _load_json(CONTRACT_ROOT / "schemas" / f"{name}.schema.json") for name in ("manifest", "chunk", "track-assets")}
    registry = Registry()
    for schema in schemas.values():
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    return schemas, registry


def _validate_browser_contract(manifest: dict[str, object], chunks: tuple[dict[str, object], ...], track_assets: dict[str, object]) -> None:
    _validate_replay_contract(manifest, chunks, track_assets)


def _validate_replay_contract(manifest: dict[str, object], chunks: tuple[dict[str, object], ...], track_assets: dict[str, object]) -> None:
    schemas, registry = _registry()
    for schema, instance in ((schemas["manifest"], manifest), (schemas["track-assets"], track_assets), *((schemas["chunk"], chunk) for chunk in chunks)):
        Draft202012Validator(schema, registry=registry, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(instance)
