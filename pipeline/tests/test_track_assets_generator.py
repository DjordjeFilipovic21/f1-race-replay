import json
import math
from pathlib import Path

import polars as pl
import pytest
from jsonschema import Draft202012Validator

from f1_replay_pipeline.browser_delivery_models import CanonicalGenerationSnapshot
from f1_replay_pipeline.track_assets_generator import (
    TrackAssetsGenerationError,
    generate_track_assets,
)


SCHEMA = Path(__file__).resolve().parents[2] / "contracts/replay-data/v1/schemas/track-assets.schema.json"


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
    frames = {
        "session_metadata": pl.DataFrame([{
            "session_id": "2024-bahrain-race", "event_name": "Bahrain Grand Prix",
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
