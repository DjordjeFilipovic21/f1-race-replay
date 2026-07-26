import json
import math
from pathlib import Path

import polars as pl
import pytest
from jsonschema import Draft202012Validator

from f1_replay_pipeline.delivery.browser.browser_delivery_models import CanonicalGenerationSnapshot
from f1_replay_pipeline.app.track_assets_generator import (
    TrackAssetsGenerationError,
    generate_track_assets,
    select_reference_lap,
)


SCHEMA = Path(__file__).resolve().parents[3] / "contracts/replay-data/v1/schemas/track-assets.schema.json"


def test_generator_selects_fastest_accurate_non_pit_lap_and_validates_against_v1_schema():
    snapshot = _snapshot()

    first = generate_track_assets(snapshot, centerline_points=8, visual_track_width_m=20.0)
    second = generate_track_assets(snapshot, centerline_points=8, visual_track_width_m=20.0)

    Draft202012Validator(json.loads(SCHEMA.read_text())).validate(first)
    assert first == second
    assert first["trackId"] == "2024-bahrain-race-telemetry-layout-v1"
    assert first["centerLine"][0] == {"x": 100.0, "y": 0.0}
    assert len(first["centerLine"]) == len(first["innerBoundary"]) == len(first["outerBoundary"]) == 9
    assert first["centerLine"][0] == first["centerLine"][-1]
    assert first["circuitLengthMeters"] == pytest.approx(40.0)


def test_generator_converts_fastf1_decimetres_to_metres_and_offsets_visual_boundaries():
    asset = generate_track_assets(_snapshot(), centerline_points=4, visual_track_width_m=20.0)
    center = asset["startFinish"]["center"]
    inner = asset["startFinish"]["inner"]
    outer = asset["startFinish"]["outer"]

    assert center == {"x": 100.0, "y": 0.0}
    assert math.dist(
        (inner["x"], inner["y"]),
        (outer["x"], outer["y"]),
    ) == pytest.approx(20.0)


@pytest.mark.parametrize("reverse", [False, True])
def test_generator_rotates_clockwise_and_anticlockwise_tracks_to_landscape_without_reflection(reverse):
    portrait = ((0.0, 0.0), (100.0, 0.0), (100.0, 400.0), (0.0, 400.0))
    points = tuple(reversed(portrait)) if reverse else portrait

    asset = generate_track_assets(_snapshot(points=points), centerline_points=8)
    centerline = tuple((point["x"], point["y"]) for point in asset["centerLine"])
    display_width, display_height = _display_extents(centerline, asset["rotationDegrees"])

    assert display_width >= display_height
    assert math.copysign(1, _signed_area(centerline)) == math.copysign(1, _signed_area(points))
    assert centerline[0] == centerline[-1]
    assert asset["circuitLengthMeters"] == pytest.approx(
        sum(math.dist(previous, current) for previous, current in zip(centerline, centerline[1:]))
    )


def test_generator_preserves_explicit_display_rotation_override():
    asset = generate_track_assets(_snapshot(), rotation_degrees=-37.5)

    assert asset["rotationDegrees"] == -37.5


def test_reference_lap_selector_matches_the_generator_source_policy():
    reference = select_reference_lap(_snapshot())

    assert (reference.driver_id, reference.lap_number) == ("BBB", 2)
    assert reference.points_meters[0] == reference.points_meters[-1]


def test_generator_rejects_degenerate_position_geometry():
    snapshot = _snapshot(points=((1000.0, 0.0),) * 4)

    with pytest.raises(TrackAssetsGenerationError, match="usable position telemetry"):
        generate_track_assets(snapshot, centerline_points=8)


@pytest.mark.parametrize("points", [
    ((0.0, 0.0), (100.0, 0.0), (200.0, 0.0), (300.0, 0.0)),
    ((0.0, 0.0), (100.0, 0.0), (200.0, 0.0), (100.0, 0.0)),
])
def test_generator_rejects_collinear_and_out_and_back_geometry(points):
    with pytest.raises(TrackAssetsGenerationError, match="usable position telemetry"):
        generate_track_assets(_snapshot(points=points), centerline_points=8)


def test_generator_calibrates_origin_from_interpolated_lap_two_boundaries():
    snapshot = _calibration_snapshot(boundary_count=3)

    asset = generate_track_assets(snapshot, centerline_points=12)

    assert asset["startFinish"]["center"] == pytest.approx({"x": 25.0, "y": 0.0})
    assert asset["centerLine"][0] == asset["startFinish"]["center"]


def test_generator_rejects_insufficient_start_finish_evidence():
    snapshot = _calibration_snapshot(boundary_count=1)

    with pytest.raises(TrackAssetsGenerationError, match="start/finish calibration evidence"):
        generate_track_assets(snapshot, centerline_points=12)


def test_generator_handles_start_finish_candidates_across_circular_zero():
    asset = generate_track_assets(_circular_calibration_snapshot(), centerline_points=12)

    assert asset["startFinish"]["center"] == pytest.approx({"x": 50.0, "y": 0.0})


def test_generator_is_invariant_to_lap_and_position_row_permutation():
    snapshot = _snapshot()
    permuted = CanonicalGenerationSnapshot(
        snapshot.generation_id,
        snapshot.manifest_sha256,
        {
            "session_metadata": snapshot.frames["session_metadata"],
            "laps": snapshot.frames["laps"].reverse(),
            "position_telemetry": snapshot.frames["position_telemetry"].reverse(),
        },
    )

    assert generate_track_assets(snapshot, centerline_points=12) == generate_track_assets(
        permuted, centerline_points=12,
    )


def test_generator_rejects_outlier_boundary_candidates_without_moving_consensus():
    snapshot = _calibration_snapshot(boundary_count=3)
    laps = snapshot.frames["laps"].vstack(pl.DataFrame([_lap("OUT", 4, 16000, 19000, 1300)]))
    positions = snapshot.frames["position_telemetry"].vstack(pl.DataFrame([
        {"driver_id": "OUT", "session_time_ms": 15900, "x": 1000.0, "y": 500.0},
        {"driver_id": "OUT", "session_time_ms": 16100, "x": 1000.0, "y": 500.0},
    ]))
    outlier_snapshot = CanonicalGenerationSnapshot(
        snapshot.generation_id,
        snapshot.manifest_sha256,
        {
            "session_metadata": snapshot.frames["session_metadata"],
            "laps": laps,
            "position_telemetry": positions,
        },
    )

    asset = generate_track_assets(outlier_snapshot, centerline_points=12)

    assert asset["startFinish"]["center"] == pytest.approx({"x": 25.0, "y": 0.0})


def _calibration_snapshot(*, boundary_count: int) -> CanonicalGenerationSnapshot:
    laps = pl.DataFrame([
        _lap("REF", 1, 1000, 5000, 900),
        _lap("AAA", 2, 7000, 10000, 1000),
        _lap("BBB", 2, 8000, 11000, 1100),
        _lap("AAA", 3, 12000, 15000, 1200),
    ])
    rows = [
        {"driver_id": "REF", "session_time_ms": 1000, "x": 500.0, "y": 0.0},
        {"driver_id": "REF", "session_time_ms": 1800, "x": 1000.0, "y": 0.0},
        {"driver_id": "REF", "session_time_ms": 2600, "x": 1000.0, "y": 500.0},
        {"driver_id": "REF", "session_time_ms": 3400, "x": 0.0, "y": 500.0},
        {"driver_id": "REF", "session_time_ms": 4200, "x": 0.0, "y": 0.0},
    ]
    boundaries = (("AAA", 7000), ("BBB", 8000), ("AAA", 12000))
    for driver_id, boundary_ms in boundaries[:boundary_count]:
        rows.extend([
            {"driver_id": driver_id, "session_time_ms": boundary_ms - 100, "x": 200.0, "y": 0.0},
            {"driver_id": driver_id, "session_time_ms": boundary_ms + 100, "x": 300.0, "y": 0.0},
        ])
    frames = {
        "session_metadata": pl.DataFrame([{
            "session_id": "synthetic-origin-race", "event_name": "Synthetic Origin Grand Prix",
        }]),
        "laps": laps,
        "position_telemetry": pl.DataFrame(rows),
    }
    return CanonicalGenerationSnapshot("canonical", "a" * 64, frames)


def _snapshot(*, points=None):
    points = points or ((1000.0, 0.0), (1100.0, 0.0), (1100.0, 100.0), (1000.0, 100.0))
    laps = pl.DataFrame([
        _lap("AAA", 1, 0, 1000, 900, pit_in_time_ms=500),
        _lap("BBB", 2, 2000, 3000, 950),
        _lap("CCC", 3, 4000, 5000, 1100),
    ])
    rows = []
    for index, (x, y) in enumerate(points):
        rows.append({"driver_id": "BBB", "session_time_ms": 2000 + index * 200, "x": x, "y": y})
    rows.extend([
        {"driver_id": "AAA", "session_time_ms": index * 200, "x": x - 1000, "y": y}
        for index, (x, y) in enumerate(points)
    ])
    rows.extend([
        {"driver_id": "CCC", "session_time_ms": 4000, "x": points[0][0], "y": points[0][1]},
        {"driver_id": "CCC", "session_time_ms": 4200, "x": points[1][0], "y": points[1][1]},
    ])
    frames = {
        "session_metadata": pl.DataFrame([{
            "session_id": "2024-bahrain-race", "event_name": "Bahrain Grand Prix",
        }]),
        "laps": laps,
        "position_telemetry": pl.DataFrame(rows),
    }
    return CanonicalGenerationSnapshot("canonical", "a" * 64, frames)


def _circular_calibration_snapshot() -> CanonicalGenerationSnapshot:
    laps = pl.DataFrame([
        _lap("REF", 1, 1000, 5000, 900),
        _lap("AAA", 2, 7000, 9000, 1000),
        _lap("BBB", 2, 8000, 10000, 1100),
        _lap("CCC", 2, 9000, 11000, 1200),
    ])
    rows = [
        {"driver_id": "REF", "session_time_ms": 1000, "x": 500.0, "y": 0.0},
        {"driver_id": "REF", "session_time_ms": 1800, "x": 1000.0, "y": 0.0},
        {"driver_id": "REF", "session_time_ms": 2600, "x": 1000.0, "y": 500.0},
        {"driver_id": "REF", "session_time_ms": 3400, "x": 0.0, "y": 500.0},
        {"driver_id": "REF", "session_time_ms": 4200, "x": 0.0, "y": 0.0},
        {"driver_id": "AAA", "session_time_ms": 7000, "x": 490.0, "y": 0.0},
        {"driver_id": "BBB", "session_time_ms": 8000, "x": 500.0, "y": 0.0},
        {"driver_id": "CCC", "session_time_ms": 9000, "x": 510.0, "y": 0.0},
    ]
    frames = {
        "session_metadata": pl.DataFrame([{
            "session_id": "synthetic-circular-race", "event_name": "Synthetic Circular Grand Prix",
        }]),
        "laps": laps,
        "position_telemetry": pl.DataFrame(rows),
    }
    return CanonicalGenerationSnapshot("canonical", "a" * 64, frames)


def _lap(driver, number, start, end, duration, *, pit_in_time_ms=None):
    return {
        "driver_id": driver,
        "lap_number": number,
        "lap_start_time_ms": start,
        "lap_end_time_ms": end,
        "lap_duration_ms": duration,
        "pit_in_time_ms": pit_in_time_ms,
        "pit_out_time_ms": None,
        "deleted": False,
        "is_accurate": True,
    }


def _display_extents(points, rotation_degrees):
    radians = math.radians(rotation_degrees)
    rotated = tuple(
        (
            x * math.cos(radians) + y * math.sin(radians),
            x * math.sin(radians) - y * math.cos(radians),
        )
        for x, y in points
    )
    xs, ys = zip(*rotated, strict=True)
    return max(xs) - min(xs), max(ys) - min(ys)


def _signed_area(points):
    closed = points if points[0] == points[-1] else points + (points[0],)
    return sum(
        current[0] * following[1] - following[0] * current[1]
        for current, following in zip(closed[:-1], closed[1:], strict=True)
    )
