"""Offline end-to-end coverage for validated canonical browser delivery."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import cast

import polars as pl
import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

import f1_replay_pipeline.delivery.browser.browser_delivery_orchestration as delivery_orchestration
from f1_replay_pipeline.delivery.browser.browser_chunk_builder import CONTINUOUS_FIELD_SEMANTICS, PREVIOUS_VALUE_FIELD_SEMANTICS
from f1_replay_pipeline.delivery.browser.browser_delivery_models import BrowserDriverFields, CanonicalGenerationSnapshot
from f1_replay_pipeline.delivery.browser.browser_delivery_orchestration import (
    BrowserDeliveryBuildError,
    _delivery_timeline,
    _leader_lap_starts,
    build_browser_delivery,
    build_timeline_summary,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_publication import (
    BrowserDeliveryPublicationError,
    publish_browser_delivery,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_reader import BrowserReadProgress, read_validated_canonical_generation
from f1_replay_pipeline.domain.canonical_schema import CANONICAL_TABLE_SCHEMAS
from f1_replay_pipeline.storage.canonical_writer import publish_canonical_generation
from f1_replay_pipeline.storage.parquet_io import CANONICAL_PARQUET_TABLE_NAMES
from f1_replay_pipeline.domain.validators import CanonicalValidationError
from f1_replay_pipeline.analysis.live_position.live_position_quality import ProjectionQualityAssessment, QUALITY_GATE_VERSION


REPO_ROOT = Path(__file__).resolve().parents[4]
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
    reader_progress: list[BrowserReadProgress] = []
    snapshot = read_validated_canonical_generation(canonical_parent, progress=reader_progress.append)
    table_names = list(snapshot.frames)
    assert reader_progress[0] == BrowserReadProgress("canonical_snapshot_reading", 0, len(table_names), table_names[0])
    assert reader_progress[-1] == BrowserReadProgress("canonical_snapshot_reading", len(table_names), len(table_names), table_names[-1])
    assert [progress.detail for progress in reader_progress[1::2]] == table_names
    track_assets = _track_assets()
    delivery = build_browser_delivery(snapshot, track_assets)
    assert delivery.timeline_summary is not None
    assert delivery.timeline_summary.as_dict()["intervals"] == [
        {"kind": "sc", "startMs": 5_000, "endMs": 20_001},
    ]

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
    assert first_chunks[1]["drivers"]["HAM"]["x"] == [None, 1.0, None, 2.0]
    assert first_chunks[1]["drivers"]["HAM"]["y"] == [0.95, 1.1, None, None]
    assert first_chunks[1]["drivers"]["HAM"]["speed"] == [None, 200.0, 210.0, None]
    assert first_chunks[1]["drivers"]["HAM"]["brake"] == [None, 1, 0, None]
    assert first_chunks[1]["drivers"]["HAM"]["trackDistanceMeters"] == [None] * 4
    assert CONTINUOUS_FIELD_SEMANTICS["speed"] == "linear"
    assert PREVIOUS_VALUE_FIELD_SEMANTICS["gear"] == "previous"


def test_penalty_sidecar_is_published_only_when_issuances_exist_and_validates(
    tmp_path: Path,
) -> None:
    frames = _canonical_frames()
    frames["race_control_messages"] = pl.DataFrame([
        _row(
            "race_control_messages",
            session_time_ms=9_000,
            message_index=0,
            message=(
                "FIA STEWARDS: 10 SECOND TIME PENALTY FOR CAR 44 (HAM) - "
                "CAUSING A COLLISION"
            ),
            driver_id=None,
            lap_number=9,
        ),
    ], schema=dict(CANONICAL_TABLE_SCHEMAS["race_control_messages"]), strict=True)
    canonical_parent = tmp_path / "canonical-with-penalty"
    publish_canonical_generation(
        frames=frames, target_parent=canonical_parent, generation_id="canonical-v1",
    )

    delivery = build_browser_delivery(
        read_validated_canonical_generation(canonical_parent), _track_assets(),
    )
    assert delivery.penalty_sidecar is not None
    published = publish_browser_delivery(
        browser_parent=tmp_path / "browser-with-penalty",
        delivery_version="delivery-v1",
        delivery=delivery,
        schema_root=CONTRACT_ROOT / "schemas",
    )
    manifest = _load_json(published.manifest_path)
    assert manifest["penaltySidecar"]["path"] == "penalty-sidecar.json"
    assert published.penalty_sidecar_path is not None
    penalty = _load_json(published.penalty_sidecar_path)
    schemas, registry = _registry()
    Draft202012Validator(schemas["penalty-sidecar"], registry=registry).validate(penalty)
    assert penalty["penaltyIssuances"][0]["driverId"] == "HAM"

    no_penalty_frames = _canonical_frames()
    no_penalty_parent = tmp_path / "canonical-without-penalty"
    publish_canonical_generation(
        frames=no_penalty_frames, target_parent=no_penalty_parent, generation_id="canonical-v2",
    )
    no_penalty_delivery = build_browser_delivery(
        read_validated_canonical_generation(no_penalty_parent), _track_assets(),
    )
    no_penalty_published = publish_browser_delivery(
        browser_parent=tmp_path / "browser-without-penalty",
        delivery_version="delivery-v1",
        delivery=no_penalty_delivery,
        schema_root=CONTRACT_ROOT / "schemas",
    )
    no_penalty_manifest = _load_json(no_penalty_published.manifest_path)
    assert no_penalty_delivery.penalty_sidecar is None
    assert "penaltySidecar" not in no_penalty_manifest
    assert no_penalty_published.penalty_sidecar_path is None


def test_browser_delivery_serializes_native_nullable_rpm_without_zero_filling(tmp_path: Path) -> None:
    canonical_parent = tmp_path / "canonical"
    publish_canonical_generation(
        frames=_canonical_frames(include_rpm=True), target_parent=canonical_parent, generation_id="canonical-v1"
    )
    delivery = build_browser_delivery(
        read_validated_canonical_generation(canonical_parent), _track_assets()
    )
    published = publish_browser_delivery(
        browser_parent=tmp_path / "browser", delivery_version="delivery-v1", delivery=delivery,
        schema_root=CONTRACT_ROOT / "schemas",
    )
    chunks = tuple(_load_json(path) for path in published.chunk_paths)
    _validate_browser_contract(_load_json(published.manifest_path), chunks, _track_assets())

    assert chunks[0]["drivers"]["HAM"]["rpm"] == [11_000.0, None, None, None]
    assert chunks[1]["drivers"]["HAM"]["rpm"] == [None, 12_000.0, 12_500.0, None]


def test_browser_delivery_serializes_canonical_tyre_life_as_aligned_tyre_age(tmp_path: Path) -> None:
    canonical_parent = tmp_path / "canonical"
    frames = _canonical_frames()
    frames["laps"] = frames["laps"].with_columns(pl.lit(7, dtype=pl.Int16).alias("tyre_life"))
    publish_canonical_generation(
        frames=frames, target_parent=canonical_parent, generation_id="canonical-v1",
    )

    delivery = build_browser_delivery(
        read_validated_canonical_generation(canonical_parent), _track_assets(),
    )
    published = publish_browser_delivery(
        browser_parent=tmp_path / "browser", delivery_version="delivery-v1", delivery=delivery,
        schema_root=CONTRACT_ROOT / "schemas",
    )
    serialized = _load_json(published.chunk_paths[0])
    times = cast(list[int], serialized["timeMs"])
    serialized_drivers = cast(dict[str, dict[str, object]], serialized["drivers"])

    assert delivery.chunks[0].drivers["HAM"].tyre_age == (7,) * len(delivery.chunks[0].time_ms)
    assert serialized_drivers["HAM"]["tyreAge"] == [7] * len(times)


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


def test_browser_delivery_starts_at_lap_one_and_preserves_pre_race_global_state(
    tmp_path: Path,
) -> None:
    # Arrange: canonical data retains pre-race observations and a later Lap 1 start.
    canonical_parent = tmp_path / "canonical"
    publish_canonical_generation(
        frames=_canonical_frames(lap_one_start_ms=5_000, include_pre_race=True),
        target_parent=canonical_parent,
        generation_id="canonical-v1",
    )

    # Act: derive browser-only artifacts from the unmodified canonical generation.
    delivery = build_browser_delivery(
        read_validated_canonical_generation(canonical_parent), _track_assets()
    )

    # Assert: absolute race-time delivery excludes the pre-race event but owns the boundary event.
    first_chunk = delivery.chunks[0]
    boundary_chunk = next(chunk for chunk in delivery.chunks if chunk.start_ms <= 10_000 < chunk.end_ms)
    assert first_chunk.start_ms == first_chunk.time_ms[0] == 5_000
    assert all(time_ms >= 5_000 for chunk in delivery.chunks for time_ms in chunk.time_ms)
    assert not any(
        event.session_time_ms == 1_000 and event.description == "pre-race event"
        for chunk in delivery.chunks
        for event in chunk.events
    )
    assert [(event.session_time_ms, event.description) for event in boundary_chunk.events] == [
        (10_000, "boundary event"),
    ]
    assert first_chunk.weather_state[0] == "clear"
    assert first_chunk.track_status_code[0] == 2


def test_browser_delivery_selects_the_earliest_lap_one_start_across_drivers(
    tmp_path: Path,
) -> None:
    # Arrange: multiple drivers report different valid Lap 1 starts.
    canonical_parent = tmp_path / "canonical"
    publish_canonical_generation(
        frames=_canonical_frames(lap_one_rows=(("HAM", 7_000), ("VER", 6_000), ("LEC", 5_000))),
        target_parent=canonical_parent,
        generation_id="canonical-v1",
    )

    # Act: derive the browser delivery from the validated multi-driver snapshot.
    delivery = build_browser_delivery(
        read_validated_canonical_generation(canonical_parent), _track_assets()
    )

    # Assert: the earliest canonical Lap 1 start owns delivery regardless of driver order.
    assert delivery.chunks[0].start_ms == delivery.chunks[0].time_ms[0] == 5_000


def test_canonical_boundary_rejects_null_lap_one_starts(tmp_path: Path) -> None:
    # Canonical validation guarantees browser snapshots cannot contain null Lap 1 starts.
    canonical_parent = tmp_path / "canonical"
    with pytest.raises(CanonicalValidationError, match="lap_start_time_ms"):
        publish_canonical_generation(
            frames=_canonical_frames(lap_one_rows=(("HAM", None), ("VER", None))),
            target_parent=canonical_parent,
            generation_id="canonical-v1",
        )


def test_browser_delivery_fails_closed_without_lap_one_rows(tmp_path: Path) -> None:
    canonical_parent = tmp_path / "canonical"
    publish_canonical_generation(
        frames=_canonical_frames(lap_number=2), target_parent=canonical_parent,
        generation_id="canonical-v1",
    )
    with pytest.raises(BrowserDeliveryBuildError, match="non-null Lap 1 start time"):
        build_browser_delivery(read_validated_canonical_generation(canonical_parent), _track_assets())


def test_delivery_timeline_unions_canonical_timestamps_and_filters_pre_race_values() -> None:
    frames = _canonical_frames(lap_one_start_ms=5_501, include_pre_race=True)
    frames["car_telemetry"] = pl.DataFrame([
        _row("car_telemetry", session_time_ms=4_999),
        _row("car_telemetry", session_time_ms=5_001),
    ], schema=dict(CANONICAL_TABLE_SCHEMAS["car_telemetry"]), strict=True)
    frames["position_telemetry"] = pl.DataFrame([
        _row("position_telemetry", session_time_ms=5_101),
    ], schema=dict(CANONICAL_TABLE_SCHEMAS["position_telemetry"]), strict=True)
    frames["weather"] = pl.DataFrame([
        _row("weather", session_time_ms=5_201),
    ], schema=dict(CANONICAL_TABLE_SCHEMAS["weather"]), strict=True)
    frames["track_status_intervals"] = pl.DataFrame([
        _row("track_status_intervals", start_time_ms=5_301, status="2"),
    ], schema=dict(CANONICAL_TABLE_SCHEMAS["track_status_intervals"]), strict=True)
    frames["race_control_messages"] = pl.DataFrame([
        _row("race_control_messages", session_time_ms=5_401, message_index=0, message="yellow"),
    ], schema=dict(CANONICAL_TABLE_SCHEMAS["race_control_messages"]), strict=True)
    frames["laps"] = pl.DataFrame([
        _row("laps", lap_start_time_ms=5_501, pit_in_time_ms=5_601, pit_out_time_ms=5_701),
    ], schema=dict(CANONICAL_TABLE_SCHEMAS["laps"]), strict=True)
    snapshot = CanonicalGenerationSnapshot("generation", "a" * 64, frames)

    timeline = _delivery_timeline(snapshot, 5_000)

    assert timeline == (5_001, 5_101, 5_201, 5_301, 5_401, 5_501, 5_601, 5_701)


def test_timeline_summary_maps_clips_merges_and_sorts_status_intervals() -> None:
    frames = _canonical_frames()
    status_rows = [
        _row("track_status_intervals", start_time_ms=0, end_time_ms=1_500, status="2"),
        _row("track_status_intervals", start_time_ms=1_500, end_time_ms=2_000, status="2"),
        _row("track_status_intervals", start_time_ms=2_000, end_time_ms=2_500, status="4"),
        _row("track_status_intervals", start_time_ms=2_500, end_time_ms=3_500, status="5"),
        _row("track_status_intervals", start_time_ms=3_500, end_time_ms=4_000, status="6"),
        _row("track_status_intervals", start_time_ms=4_000, end_time_ms=None, status="7"),
    ]
    frames["track_status_intervals"] = pl.DataFrame(
        status_rows, schema=dict(CANONICAL_TABLE_SCHEMAS["track_status_intervals"]), strict=True,
    )
    snapshot = CanonicalGenerationSnapshot("generation", "a" * 64, frames)

    summary = build_timeline_summary(snapshot, 1_000, 5_000)
    reversed_summary = build_timeline_summary(
        CanonicalGenerationSnapshot(
            "generation", "a" * 64,
            {**frames, "track_status_intervals": pl.DataFrame(
                list(reversed(status_rows)),
                schema=dict(CANONICAL_TABLE_SCHEMAS["track_status_intervals"]), strict=True,
            )},
        ),
        1_000,
        5_000,
    )

    assert summary == reversed_summary
    assert summary.as_dict()["intervals"] == [
        {"kind": "yellow", "startMs": 1_000, "endMs": 2_000},
        {"kind": "sc", "startMs": 2_000, "endMs": 2_500},
        {"kind": "red", "startMs": 2_500, "endMs": 3_500},
        {"kind": "vsc", "startMs": 3_500, "endMs": 5_000},
    ]


def test_browser_delivery_derives_each_driver_once_on_the_final_timeline(tmp_path: Path, monkeypatch) -> None:
    canonical_parent = tmp_path / "canonical"
    publish_canonical_generation(
        frames=_canonical_frames(lap_one_rows=(("HAM", 0), ("VER", 0))),
        target_parent=canonical_parent,
        generation_id="canonical-v1",
    )
    snapshot = read_validated_canonical_generation(canonical_parent)
    calls: list[tuple[str, tuple[int, ...] | None]] = []
    original = delivery_orchestration.derive_browser_driver_fields

    def counted_derivation(snapshot, driver_id, *, timeline=None):
        calls.append((driver_id, timeline))
        return original(snapshot, driver_id, timeline=timeline)

    monkeypatch.setattr(delivery_orchestration, "derive_browser_driver_fields", counted_derivation)

    delivery = build_browser_delivery(snapshot, _track_assets())

    final_timeline = _delivery_timeline(snapshot, 0)
    assert calls == [("HAM", final_timeline), ("VER", final_timeline)]


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


def test_passing_quality_assessment_derives_dynamic_positions_gaps_and_overlap_without_new_timestamps(tmp_path: Path, monkeypatch) -> None:
    canonical_parent = tmp_path / "canonical"
    frames = _live_frames()
    publish_canonical_generation(frames=frames, target_parent=canonical_parent, generation_id="canonical-v1")
    snapshot = read_validated_canonical_generation(canonical_parent)
    calls = 0
    original_rank_timeline = delivery_orchestration.rank_timeline

    def counted_rank_timeline(ranking_frames):
        nonlocal calls
        calls += 1
        return original_rank_timeline(ranking_frames)

    monkeypatch.setattr(delivery_orchestration, "rank_timeline", counted_rank_timeline)

    delivery = build_browser_delivery(
        snapshot, _square_track_assets(), chunk_duration_ms=1_000, overlap_ms=500,
        quality_assessor=lambda *_: _assessment(True),
    )

    first, second = delivery.chunks[:2]
    assert first.leaderboard_order[:2] == (("HAM", "RUS"), ("RUS", "HAM"))
    assert first.drivers["HAM"].position[:2] == (1, 2)
    assert first.drivers["RUS"].position[:2] == (2, 1)
    assert first.drivers["HAM"].gap_to_leader_ms[0] == 0.0
    assert first.drivers["RUS"].gap_to_leader_ms[1] == 0.0
    assert all(value is None or value >= 0 for value in first.drivers["HAM"].gap_to_leader_ms)
    assert calls == 1
    overlap_index = second.time_ms.index(500)
    assert second.drivers["HAM"].track_distance_meters[overlap_index] == first.drivers["HAM"].track_distance_meters[1]
    assert second.leaderboard_order[overlap_index] == first.leaderboard_order[1]
    assert {time_ms for chunk in delivery.chunks for time_ms in chunk.time_ms} == _delivery_source_times(snapshot)
    assert snapshot.frames["position_telemetry"].equals(frames["position_telemetry"])


def test_startup_guard_does_not_mix_asynchronous_per_driver_samples(tmp_path: Path) -> None:
    canonical_parent = tmp_path / "canonical"
    frames = _asynchronous_startup_grid_frames()
    publish_canonical_generation(frames=frames, target_parent=canonical_parent, generation_id="canonical-v1")

    delivery = build_browser_delivery(
        read_validated_canonical_generation(canonical_parent), _square_track_assets(),
        quality_assessor=lambda *_: _assessment(True),
    )

    assert _leaderboard_value(delivery, 4_000) == ("NOR", "RUS", "TSU", "OCO")


def test_origin_split_seeds_dynamic_leaderboard_in_grid_order(tmp_path: Path) -> None:
    canonical_parent = tmp_path / "canonical"
    frames = _startup_grid_frames({"NOR": 390.0, "RUS": 380.0, "TSU": 370.0, "OCO": 10.0})
    publish_canonical_generation(frames=frames, target_parent=canonical_parent, generation_id="canonical-v1")

    delivery = build_browser_delivery(
        read_validated_canonical_generation(canonical_parent), _square_track_assets(),
        quality_assessor=lambda *_: _assessment(True),
    )

    assert _leaderboard_value(delivery, 0) == ("NOR", "RUS", "TSU", "OCO")
    assert _leaderboard_value(delivery, 5_000) == ("NOR", "RUS", "TSU", "OCO")


def test_origin_split_excludes_a_pit_lane_starter_from_grid_calibration(tmp_path: Path) -> None:
    canonical_parent = tmp_path / "canonical"
    frames = _startup_grid_frames({
        "NOR": 30.0, "RUS": 20.0, "TSU": 10.0, "OCO": 390.0, "SAI": 299.0,
    })
    _add_pit_lane_start(frames, "SAI", 15_000)
    publish_canonical_generation(
        frames=frames, target_parent=canonical_parent, generation_id="canonical-v1",
    )

    delivery = build_browser_delivery(
        read_validated_canonical_generation(canonical_parent), _square_track_assets(),
        quality_assessor=lambda *_: _assessment(True),
    )

    assert _leaderboard_value(delivery, 0)[:4] == ("NOR", "RUS", "TSU", "OCO")


def test_pit_lane_starter_joins_live_progress_after_pit_out(tmp_path: Path) -> None:
    canonical_parent = tmp_path / "canonical"
    frames = _startup_grid_frames(
        {"NOR": 30.0, "RUS": 20.0, "TSU": 10.0, "OCO": 390.0, "PER": 380.0, "SAI": 299.0},
        position_samples={
            "NOR": ((0, 30.0), (600, 100.0)),
            "RUS": ((0, 20.0), (600, 90.0)),
            "TSU": ((0, 10.0), (600, 80.0)),
            "OCO": ((0, 390.0), (600, 35.0)),
            "PER": ((0, 380.0), (600, 20.0)),
            "SAI": ((0, 299.0), (600, 25.0)),
        },
    )
    _add_pit_lane_start(frames, "SAI", 500)
    publish_canonical_generation(
        frames=frames, target_parent=canonical_parent, generation_id="canonical-v1",
    )

    delivery = build_browser_delivery(
        read_validated_canonical_generation(canonical_parent), _square_track_assets(),
        quality_assessor=lambda *_: _assessment(True),
    )

    order = _leaderboard_value(delivery, 600)
    assert order.index("SAI") < order.index("PER")
    assert _driver_value(delivery, "SAI", "position", 600) == 5


def test_origin_split_seeds_progress_state_once_at_the_shared_startup_frame(
    tmp_path: Path, monkeypatch,
) -> None:
    canonical_parent = tmp_path / "canonical"
    frames = _startup_grid_frames({"NOR": 30.0, "RUS": 20.0, "TSU": 10.0, "OCO": 390.0})
    publish_canonical_generation(frames=frames, target_parent=canonical_parent, generation_id="canonical-v1")
    captured = {}
    original = delivery_orchestration._derive_live_fields

    def capture(*args, startup_plan=None, **kwargs):
        captured["plan"] = startup_plan
        return original(*args, startup_plan=startup_plan, **kwargs)

    monkeypatch.setattr(delivery_orchestration, "_derive_live_fields", capture)

    build_browser_delivery(
        read_validated_canonical_generation(canonical_parent), _square_track_assets(),
        quality_assessor=lambda *_: _assessment(True),
    )

    plan = captured["plan"]
    assert plan.timestamp_ms == 0
    assert plan.seeds["NOR"].state.last_valid_progress_meters == 430.0
    assert plan.seeds["NOR"].state.last_valid_time_ms == 0
    assert plan.seeds["NOR"].state.within_lap_wrap_count == 1
    assert plan.seeds["NOR"].state.within_lap_offset_meters == 400.0
    assert plan.seeds["OCO"].state.last_valid_progress_meters == 390.0
    assert plan.seeds["OCO"].state.within_lap_wrap_count == 0
    assert plan.seeds["OCO"].state.within_lap_offset_meters == 0.0


def test_three_starter_origin_split_seeds_dynamic_leaderboard_in_grid_order(tmp_path: Path) -> None:
    canonical_parent = tmp_path / "canonical"
    frames = _startup_grid_frames({"NOR": 390.0, "RUS": 380.0, "TSU": 10.0})
    publish_canonical_generation(frames=frames, target_parent=canonical_parent, generation_id="canonical-v1")

    delivery = build_browser_delivery(
        read_validated_canonical_generation(canonical_parent), _square_track_assets(),
        quality_assessor=lambda *_: _assessment(True),
    )

    assert _leaderboard_value(delivery, 0) == ("NOR", "RUS", "TSU")


def test_origin_split_rejects_inconsistent_projected_grid_order(tmp_path: Path) -> None:
    canonical_parent = tmp_path / "canonical"
    frames = _startup_grid_frames({"NOR": 390.0, "RUS": 10.0, "TSU": 380.0, "OCO": 370.0})
    publish_canonical_generation(frames=frames, target_parent=canonical_parent, generation_id="canonical-v1")

    with pytest.raises(BrowserDeliveryBuildError, match="projected ordering"):
        build_browser_delivery(
            read_validated_canonical_generation(canonical_parent), _square_track_assets(),
            quality_assessor=lambda *_: _assessment(True),
        )


def test_origin_split_rejects_duplicate_grid_positions(tmp_path: Path) -> None:
    canonical_parent = tmp_path / "canonical"
    frames = _startup_grid_frames(
        {"NOR": 390.0, "RUS": 380.0, "TSU": 370.0, "OCO": 10.0},
        grid_positions={"NOR": 1, "RUS": 1, "TSU": 3, "OCO": 4},
    )
    publish_canonical_generation(frames=frames, target_parent=canonical_parent, generation_id="canonical-v1")

    with pytest.raises(BrowserDeliveryBuildError, match="not unique"):
        build_browser_delivery(
            read_validated_canonical_generation(canonical_parent), _square_track_assets(),
            quality_assessor=lambda *_: _assessment(True),
        )


def test_origin_split_rejects_a_non_compact_projected_pack(tmp_path: Path) -> None:
    canonical_parent = tmp_path / "canonical"
    frames = _startup_grid_frames({"NOR": 390.0, "RUS": 250.0, "TSU": 100.0, "OCO": 10.0})
    publish_canonical_generation(frames=frames, target_parent=canonical_parent, generation_id="canonical-v1")

    with pytest.raises(BrowserDeliveryBuildError, match="not a compact circular grid"):
        build_browser_delivery(
            read_validated_canonical_generation(canonical_parent), _square_track_assets(),
            quality_assessor=lambda *_: _assessment(True),
        )


def test_dns_starter_is_excluded_before_origin_split_reconciliation(tmp_path: Path) -> None:
    canonical_parent = tmp_path / "canonical"
    frames = _startup_grid_frames(
        {"NOR": 390.0, "RUS": 380.0, "TSU": 370.0, "OCO": 10.0},
        result_statuses={"OCO": "Did not start"},
    )
    publish_canonical_generation(frames=frames, target_parent=canonical_parent, generation_id="canonical-v1")

    delivery = build_browser_delivery(
        read_validated_canonical_generation(canonical_parent), _square_track_assets(),
        quality_assessor=lambda *_: _assessment(True),
    )

    assert _leaderboard_value(delivery, 0) == ("NOR", "RUS", "TSU")
    assert _driver_value(delivery, "OCO", "status", 0) == "OUT"


def test_origin_split_handles_an_immediate_rear_side_wrap_without_a_ranking_jump(tmp_path: Path) -> None:
    canonical_parent = tmp_path / "canonical"
    frames = _startup_grid_frames(
        {"NOR": 30.0, "RUS": 20.0, "TSU": 10.0, "OCO": 390.0},
        position_samples={
            "NOR": ((0, 30.0), (100, 31.0)),
            "RUS": ((0, 20.0), (100, 21.0)),
            "TSU": ((0, 10.0), (100, 11.0)),
            "OCO": ((0, 390.0), (100, 10.0)),
        },
    )
    publish_canonical_generation(frames=frames, target_parent=canonical_parent, generation_id="canonical-v1")

    delivery = build_browser_delivery(
        read_validated_canonical_generation(canonical_parent), _square_track_assets(),
        quality_assessor=lambda *_: _assessment(True),
    )

    assert _leaderboard_value(delivery, 0) == ("NOR", "RUS", "TSU", "OCO")
    assert _leaderboard_value(delivery, 100) == ("NOR", "RUS", "TSU", "OCO")
    assert _driver_value(delivery, "OCO", "track_distance_meters", 100) == 10.0


def test_compact_startup_grid_with_trustworthy_origin_publishes_p1_to_p4_order(tmp_path: Path) -> None:
    canonical_parent = tmp_path / "canonical"
    frames = _startup_grid_frames({"NOR": 130.0, "RUS": 120.0, "TSU": 110.0, "OCO": 95.0})
    publish_canonical_generation(frames=frames, target_parent=canonical_parent, generation_id="canonical-v1")

    delivery = build_browser_delivery(
        read_validated_canonical_generation(canonical_parent), _square_track_assets(),
        quality_assessor=lambda *_: _assessment(True),
    )

    assert delivery.chunks[0].leaderboard_order[0] == ("NOR", "RUS", "TSU", "OCO")


def test_leader_lap_starts_follow_leader_changes_without_duplicate_or_regressing_markers() -> None:
    fields = {
        "HAM": _browser_fields("HAM", (1, 2, 2, 1)),
        "RUS": _browser_fields("RUS", (1, 1, 4, 4)),
    }

    markers = _leader_lap_starts(
        (0, 1_000, 2_000, 3_000), fields,
        (("HAM", "RUS"), ("HAM", "RUS"), ("RUS", "HAM"), ("HAM", "RUS")),
    )

    assert [(marker.lap, marker.start_ms) for marker in markers] == [(1, 0), (2, 1_000), (4, 2_000)]


def test_failed_quality_assessment_preserves_null_derived_fields_and_classified_fallback(tmp_path: Path) -> None:
    canonical_parent = tmp_path / "canonical"
    publish_canonical_generation(frames=_live_frames(), target_parent=canonical_parent, generation_id="canonical-v1")

    delivery = build_browser_delivery(
        read_validated_canonical_generation(canonical_parent), _square_track_assets(),
        quality_assessor=lambda *_: _assessment(False),
    )

    assert all(value is None for fields in delivery.chunks[0].drivers.values() for value in fields.track_distance_meters + fields.gap_to_leader_ms + fields.position)
    assert delivery.chunks[0].leaderboard_order[0] == ("HAM", "RUS")
    assert delivery.projection_quality_assessment == _assessment(False)


def test_retired_and_disqualified_drivers_become_out_after_their_last_genuine_activity(tmp_path: Path) -> None:
    canonical_parent = tmp_path / "canonical"
    frames = _terminal_live_frames({"HAM": "Retired", "RUS": "Disqualified"})
    publish_canonical_generation(frames=frames, target_parent=canonical_parent, generation_id="canonical-v1")

    delivery = build_browser_delivery(
        read_validated_canonical_generation(canonical_parent), _square_track_assets(),
        quality_assessor=lambda *_: _assessment(True),
    )

    assert _driver_value(delivery, "HAM", "status", 850) == "OffTrack"
    assert _driver_value(delivery, "HAM", "status", 900) == "OffTrack"
    assert _driver_value(delivery, "RUS", "status", 900) == "OffTrack"
    assert _driver_value(delivery, "HAM", "status", 1_000) == "OUT"
    assert _driver_value(delivery, "RUS", "status", 1_000) == "OUT"
    assert _driver_value(delivery, "HAM", "track_distance_meters", 1_000) == _driver_value(delivery, "HAM", "track_distance_meters", 500)


def test_dns_is_out_from_race_start_while_finished_offtrack_driver_remains_active(tmp_path: Path) -> None:
    canonical_parent = tmp_path / "canonical"
    frames = _terminal_live_frames({"HAM": "Did not start", "RUS": "Finished"})
    publish_canonical_generation(frames=frames, target_parent=canonical_parent, generation_id="canonical-v1")

    delivery = build_browser_delivery(
        read_validated_canonical_generation(canonical_parent), _square_track_assets(),
        quality_assessor=lambda *_: _assessment(True),
    )

    assert _driver_value(delivery, "HAM", "status", 0) == "OUT"
    assert _driver_value(delivery, "RUS", "status", 1_000) == "OffTrack"


def test_finished_state_uses_completed_final_lap_and_remains_nullable_for_noncompletion(
    tmp_path: Path,
) -> None:
    canonical_parent = tmp_path / "canonical"
    publish_canonical_generation(
        frames=_terminal_live_frames({"HAM": "Finished", "RUS": "Retired"}),
        target_parent=canonical_parent,
        generation_id="canonical-v1",
    )

    delivery = build_browser_delivery(
        read_validated_canonical_generation(canonical_parent), _square_track_assets(),
        quality_assessor=lambda *_: _assessment(True),
    )

    assert _driver_value(delivery, "HAM", "is_finished", 500) is False
    assert _driver_value(delivery, "HAM", "is_finished", 850) is True
    assert all(
        _driver_value(delivery, "RUS", "is_finished", time_ms) is None
        for time_ms in (0, 500, 850, 900, 1_000)
    )
    assert all(
        len(fields.is_finished) == len(chunk.time_ms)
        for chunk in delivery.chunks
        for fields in chunk.drivers.values()
    )

    published = publish_browser_delivery(
        browser_parent=tmp_path / "browser", delivery_version="delivery-v1", delivery=delivery,
        schema_root=CONTRACT_ROOT / "schemas",
    )
    serialized_chunk = _load_json(published.chunk_paths[0])
    serialized_drivers = cast(dict[str, dict[str, object]], serialized_chunk["drivers"])
    serialized_times = cast(list[int], serialized_chunk["timeMs"])
    assert cast(list[bool], serialized_drivers["HAM"]["isFinished"]) == [
        time_ms >= 800 for time_ms in serialized_times
    ]


def test_finished_leader_keeps_frozen_progress_and_p1_after_offtrack_data(
    tmp_path: Path,
) -> None:
    canonical_parent = tmp_path / "canonical"
    publish_canonical_generation(
        frames=_terminal_live_frames(
            {"HAM": "Finished", "RUS": "Retired"},
            finished_leader_with_later_competitor=True,
        ),
        target_parent=canonical_parent,
        generation_id="canonical-v1",
    )

    delivery = build_browser_delivery(
        read_validated_canonical_generation(canonical_parent), _square_track_assets(),
        quality_assessor=lambda *_: _assessment(True),
    )

    assert all(_driver_value(delivery, "HAM", "is_finished", time_ms) is True for time_ms in (850, 900, 1_000))
    assert _driver_value(delivery, "HAM", "status", 900) == "OffTrack"
    assert all(_driver_value(delivery, "HAM", "position", time_ms) == 1 for time_ms in (850, 900, 1_000))
    orders = tuple(_leaderboard_value(delivery, time_ms) for time_ms in (850, 900, 1_000))
    assert all(order is not None and order[0] == "HAM" for order in orders)
    assert _driver_value(delivery, "HAM", "track_distance_meters", 900) == _driver_value(
        delivery, "HAM", "track_distance_meters", 850,
    )


def _canonical_frames(
    *, lap_number: int = 1, lap_one_start_ms: int | None = 0,
    lap_one_rows: tuple[tuple[str, int | None], ...] | None = None,
    include_pre_race: bool = False, include_rpm: bool = False,
) -> dict[str, pl.DataFrame]:
    rows = {name: [_row(name)] for name in CANONICAL_PARQUET_TABLE_NAMES}
    rows["car_telemetry"] = [
        _row("car_telemetry", session_time_ms=time, speed_kph=speed, throttle_pct=throttle,
             brake=brake, gear=gear, drs=drs, **({"rpm": rpm} if include_rpm else {}))
        for time, speed, rpm, throttle, brake, gear, drs in (
            (0, 100.0, 11_000.0, 50.0, False, 4, 0), (10_000, 200.0, 12_000.0, 70.0, True, 6, 1),
            (11_000, 210.0, 12_500.0, 75.0, False, 7, 1), (20_000, 220.0, None, 80.0, None, None, None),
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
    if include_pre_race:
        rows["track_status_intervals"] = [
            _row("track_status_intervals", start_time_ms=0, end_time_ms=None, status="2"),
        ]
        rows["race_control_messages"] = [
            _row(
                "race_control_messages", session_time_ms=1_000,
                message_index=0, message="pre-race event", driver_id="HAM",
            ),
            _row(
                "race_control_messages", session_time_ms=10_000,
                message_index=1, message="boundary event", driver_id="HAM",
            ),
    ]
    lap_rows = lap_one_rows or (("HAM", lap_one_start_ms),)
    extra_driver_ids = tuple(driver_id for driver_id, _ in lap_rows if driver_id != "HAM")
    source_car_telemetry = tuple(rows["car_telemetry"])
    source_position_telemetry = tuple(rows["position_telemetry"])
    for driver_id in extra_driver_ids:
        rows["drivers"].append(_row(
            "drivers", driver_id=driver_id, source_driver_key=driver_id,
            driver_number=1, full_name=driver_id, team_name=f"{driver_id} Racing", team_colour="112233",
        ))
        rows["results"].append(_row("results", driver_id=driver_id, classified_position="2"))
        rows["car_telemetry"].extend(
            {**row, "driver_id": driver_id, "source_driver_key": driver_id}
            for row in source_car_telemetry
        )
        rows["position_telemetry"].extend(
            {**row, "driver_id": driver_id, "source_driver_key": driver_id}
            for row in source_position_telemetry
        )
    rows["laps"] = [
        _row(
            "laps", driver_id=driver_id, lap_number=lap_number, lap_start_time_ms=lap_start_ms,
            lap_end_time_ms=20_001, compound="MEDIUM",
        )
        for driver_id, lap_start_ms in lap_rows
    ]
    rows["drivers"].sort(key=lambda row: row["driver_id"])
    rows["results"].sort(key=lambda row: row["driver_id"])
    rows["car_telemetry"].sort(key=lambda row: (row["driver_id"], row["session_time_ms"]))
    rows["position_telemetry"].sort(key=lambda row: (row["driver_id"], row["session_time_ms"]))
    rows["laps"].sort(key=lambda row: (row["driver_id"], row["lap_number"]))
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


def _square_track_assets() -> dict[str, object]:
    assets = _track_assets()
    line = [
        {"x": 0.0, "y": 0.0}, {"x": 100.0, "y": 0.0}, {"x": 100.0, "y": 100.0},
        {"x": 0.0, "y": 100.0}, {"x": 0.0, "y": 0.0},
    ]
    assets.update({"circuitLengthMeters": 400.0, "centerLine": line, "innerBoundary": line, "outerBoundary": line})
    return assets


def _assessment(passed: bool) -> ProjectionQualityAssessment:
    return ProjectionQualityAssessment(
        QUALITY_GATE_VERSION, passed, () if passed else ("insufficient independent holdout laps",), "HAM", 1,
        20 if passed else 0, 500 if passed else 0, 0.0 if passed else None, 0.0 if passed else None,
        0, 0, 0, 0, None, None,
    )


def _startup_grid_frames(
    distances: dict[str, float], *, result_statuses: dict[str, str] | None = None,
    position_samples: dict[str, tuple[tuple[int, float], ...]] | None = None,
    grid_positions: dict[str, int] | None = None,
) -> dict[str, pl.DataFrame]:
    drivers = tuple(distances)
    frames = _canonical_frames(lap_one_rows=tuple((driver, 0) for driver in ("HAM", *drivers[1:])))
    for table, frame in frames.items():
        expressions = []
        if "driver_id" in frame.columns:
            expressions.append(
                pl.when(pl.col("driver_id") == "HAM").then(pl.lit(drivers[0])).otherwise(pl.col("driver_id")).alias("driver_id")
            )
        if "source_driver_key" in frame.columns:
            expressions.append(
                pl.when(pl.col("source_driver_key") == "HAM").then(pl.lit(drivers[0])).otherwise(pl.col("source_driver_key")).alias("source_driver_key")
            )
        if expressions:
            frames[table] = frame.with_columns(expressions)

    grid_position = pl.col("grid_position")
    positions = grid_positions or {driver: index for index, driver in enumerate(drivers, start=1)}
    for driver, position in positions.items():
        grid_position = pl.when(pl.col("driver_id") == driver).then(pl.lit(position).cast(pl.Int16)).otherwise(grid_position)
    frames["results"] = frames["results"].with_columns(grid_position.alias("grid_position"))
    if result_statuses:
        status = pl.col("status")
        for driver, value in result_statuses.items():
            status = pl.when(pl.col("driver_id") == driver).then(pl.lit(value)).otherwise(status)
        frames["results"] = frames["results"].with_columns(status.alias("status"))
    position_samples = position_samples or {
        driver: tuple((time_ms, distance) for time_ms in (0, 5_000, 10_000, 20_000))
        for driver, distance in distances.items()
    }
    frames["position_telemetry"] = pl.DataFrame([
        _row(
            "position_telemetry", driver_id=driver, source_driver_key=driver, session_time_ms=time_ms,
            x=x, y=y, status="OnTrack",
        )
        for driver in sorted(distances)
        for time_ms, distance in position_samples[driver]
        for x, y in (_square_position(distance),)
    ], schema=dict(CANONICAL_TABLE_SCHEMAS["position_telemetry"]), strict=True)
    return frames


def _add_pit_lane_start(
    frames: dict[str, pl.DataFrame], driver_id: str, pit_out_time_ms: int,
) -> None:
    is_start_lap = (pl.col("driver_id") == driver_id) & (pl.col("lap_number") == 1)
    frames["laps"] = frames["laps"].with_columns(
        pl.when(is_start_lap)
        .then(pl.lit(1).cast(pl.Int16))
        .otherwise(pl.col("stint_number"))
        .alias("stint_number"),
        pl.when(is_start_lap)
        .then(pl.lit(pit_out_time_ms).cast(pl.Int64))
        .otherwise(pl.col("pit_out_time_ms"))
        .alias("pit_out_time_ms"),
    )
    pit_lane_stint = pl.DataFrame(
        [_row("stints", driver_id=driver_id, stint_number=1, start_lap_number=1)],
        schema=dict(CANONICAL_TABLE_SCHEMAS["stints"]),
        strict=True,
    )
    frames["stints"] = pl.concat([frames["stints"], pit_lane_stint]).sort(
        "session_id", "driver_id", "stint_number",
    )


def _asynchronous_startup_grid_frames() -> dict[str, pl.DataFrame]:
    frames = _startup_grid_frames({"NOR": 130.0, "RUS": 120.0, "TSU": 110.0, "OCO": 95.0})
    startup_samples = {
        "NOR": ((0, 390.0), (4_000, 130.0)),
        "RUS": ((1_000, 380.0), (4_000, 120.0)),
        "TSU": ((2_000, 370.0), (4_000, 110.0)),
        "OCO": ((3_000, 10.0), (4_000, 95.0)),
    }
    rows = [
        _row(
            "position_telemetry", driver_id=driver, source_driver_key=driver,
            session_time_ms=time_ms, x=x, y=y, status="OnTrack",
        )
        for driver, samples in startup_samples.items()
        for time_ms, distance in samples
        for x, y in (_square_position(distance),)
    ]
    rows.sort(key=lambda row: (row["session_id"], row["driver_id"], row["session_time_ms"]))
    frames["position_telemetry"] = pl.DataFrame(
        rows, schema=dict(CANONICAL_TABLE_SCHEMAS["position_telemetry"]), strict=True,
    )
    return frames


def _square_position(distance: float) -> tuple[float, float]:
    if distance <= 100.0:
        point = (distance, 0.0)
    elif distance <= 200.0:
        point = (100.0, distance - 100.0)
    elif distance <= 300.0:
        point = (300.0 - distance, 100.0)
    else:
        point = (0.0, 400.0 - distance)
    return point[0] * 10.0, point[1] * 10.0


def _live_frames(*, result_statuses: dict[str, str] | None = None) -> dict[str, pl.DataFrame]:
    frames = _canonical_frames(lap_one_rows=(("HAM", 0), ("RUS", 0)))
    positions = []
    for driver, samples in {
        "HAM": ((0, 100.0, 0.0), (500, 150.0, 0.0), (1_500, None, None)),
        "RUS": ((0, 80.0, 0.0), (500, 200.0, 0.0), (1_500, None, None)),
    }.items():
        positions.extend(_row("position_telemetry", driver_id=driver, source_driver_key=driver, session_time_ms=time, x=x, y=y, status="OffTrack") for time, x, y in samples)
    frames["position_telemetry"] = pl.DataFrame(sorted(positions, key=lambda row: (row["driver_id"], row["session_time_ms"])), schema=dict(CANONICAL_TABLE_SCHEMAS["position_telemetry"]), strict=True)
    laps = [
        _row("laps", driver_id=driver, lap_number=1, lap_start_time_ms=0, lap_end_time_ms=2_000, compound="MEDIUM")
        for driver in ("HAM", "RUS")
    ]
    frames["laps"] = pl.DataFrame(laps, schema=dict(CANONICAL_TABLE_SCHEMAS["laps"]), strict=True)
    if result_statuses:
        results = [
            _row(
                "results", driver_id=driver, classified_position=str(index + 1),
                status=result_statuses.get(driver), laps_completed=1,
            )
            for index, driver in enumerate(("HAM", "RUS"))
        ]
        frames["results"] = pl.DataFrame(results, schema=dict(CANONICAL_TABLE_SCHEMAS["results"]), strict=True)
    return frames


def _terminal_live_frames(
    result_statuses: dict[str, str], *, finished_leader_with_later_competitor: bool = False,
) -> dict[str, pl.DataFrame]:
    frames = _live_frames(result_statuses=result_statuses)
    frames["car_telemetry"] = pl.DataFrame([
        _row("car_telemetry", driver_id=driver, source_driver_key=driver, session_time_ms=time, speed_kph=speed)
        for driver in ("HAM", "RUS")
        for time, speed in ((0, 100.0), (900, 10.0), (1_000, 0.0))
    ], schema=dict(CANONICAL_TABLE_SCHEMAS["car_telemetry"]), strict=True)
    positions = {
        "HAM": ((0, 0.0), (500, 50.0), (850, 50.0), (900, None), (1_000, None)),
        "RUS": ((0, 0.0), (500, 50.0), (850, 75.0), (900, 75.0), (1_000, 75.0)),
    } if finished_leader_with_later_competitor else {
        driver: ((0, 0.0), (500, 50.0), (850, 50.0), (900, 50.0), (1_000, 50.0))
        for driver in ("HAM", "RUS")
    }
    frames["position_telemetry"] = pl.DataFrame([
        _row("position_telemetry", driver_id=driver, source_driver_key=driver, session_time_ms=time, x=x, y=0.0, status="OffTrack")
        for driver, samples in positions.items()
        for time, x in samples
    ], schema=dict(CANONICAL_TABLE_SCHEMAS["position_telemetry"]), strict=True)
    frames["laps"] = pl.DataFrame([
        _row("laps", driver_id=driver, lap_number=1, lap_start_time_ms=0, lap_end_time_ms=800, compound="MEDIUM")
        for driver in ("HAM", "RUS")
    ], schema=dict(CANONICAL_TABLE_SCHEMAS["laps"]), strict=True)
    return frames


def _driver_value(delivery, driver_id, field, time_ms):
    chunk = next(chunk for chunk in delivery.chunks if chunk.start_ms <= time_ms < chunk.end_ms)
    return getattr(chunk.drivers[driver_id], field)[chunk.time_ms.index(time_ms)]


def _leaderboard_value(delivery, time_ms):
    chunk = next(chunk for chunk in delivery.chunks if chunk.start_ms <= time_ms < chunk.end_ms)
    return chunk.leaderboard_order[chunk.time_ms.index(time_ms)]


def _browser_fields(driver_id: str, laps: tuple[int, ...]):
    times = tuple(index * 1_000 for index in range(len(laps)))
    return BrowserDriverFields(
        driver_id, times, (None,) * len(laps), (None,) * len(laps), (None,) * len(laps),
        (None,) * len(laps), (None,) * len(laps), (None,) * len(laps), (None,) * len(laps),
        (None,) * len(laps), laps, (None,) * len(laps), (None,) * len(laps), (None,) * len(laps),
        (None,) * len(laps), (None,) * len(laps),
    )


def _delivery_source_times(snapshot) -> set[int]:
    values = set()
    for frame, column in (
        (snapshot.frames["car_telemetry"], "session_time_ms"),
        (snapshot.frames["position_telemetry"], "session_time_ms"),
        (snapshot.frames["weather"], "session_time_ms"),
        (snapshot.frames["track_status_intervals"], "start_time_ms"),
        (snapshot.frames["race_control_messages"], "session_time_ms"),
        (snapshot.frames["laps"], "lap_start_time_ms"),
        (snapshot.frames["laps"], "pit_in_time_ms"),
        (snapshot.frames["laps"], "pit_out_time_ms"),
    ):
        values.update(value for value in frame.get_column(column).drop_nulls().to_list() if value >= 0)
    return values


def _artifact_bytes(result: object) -> tuple[bytes, ...]:
    paths = (result.manifest_path, result.track_assets_path, *result.chunk_paths)
    if result.timeline_summary_path is not None:
        paths = (*paths, result.timeline_summary_path)
    return tuple(path.read_bytes() for path in paths)


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _registry() -> tuple[dict[str, object], Registry]:
    schemas = {
        name: _load_json(CONTRACT_ROOT / "schemas" / f"{name}.schema.json")
        for name in (
            "manifest", "chunk", "track-assets", "timeline-summary",
            "browser-lap-sector-sidecar", "penalty-sidecar", "stint-summary", "pit-loss-model",
        )
    }
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
