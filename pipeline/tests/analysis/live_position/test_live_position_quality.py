from dataclasses import FrozenInstanceError

import polars as pl
import pytest

from f1_replay_pipeline.delivery.browser.browser_delivery_models import CanonicalGenerationSnapshot
from f1_replay_pipeline.analysis.live_position.live_position_projection import ProjectionGeometryError
from f1_replay_pipeline.analysis.live_position.live_position_quality import QUALITY_GATE_VERSION, assess_projection_quality


def _assets():
    return {
        "fixtureId": "race-1",
        "circuitLengthMeters": 400.0,
        "centerLine": [
            {"x": 0.0, "y": 0.0}, {"x": 100.0, "y": 0.0},
            {"x": 100.0, "y": 100.0}, {"x": 0.0, "y": 100.0},
            {"x": 0.0, "y": 0.0},
        ],
    }


def test_quality_gate_passes_independent_native_holdouts_and_excludes_source():
    snapshot = _snapshot()

    assessment = assess_projection_quality(snapshot, _assets())

    assert assessment.passed is True
    assert assessment.version == QUALITY_GATE_VERSION
    assert (assessment.source_driver, assessment.source_lap) == ("SRC", 1)
    assert (assessment.holdout_laps, assessment.holdout_samples) == (20, 640)
    assert (assessment.pit_samples, assessment.pit_residual_p95_m) == (32, 15.0)
    assert assessment.reasons == ()


def test_quality_gate_fails_closed_for_insufficient_laps_and_samples():
    assessment = assess_projection_quality(_snapshot(holdouts=19, points_per_lap=20), _assets())

    assert assessment.passed is False
    assert assessment.reasons == (
        "insufficient independent holdout laps",
        "insufficient independent holdout samples",
    )


def test_quality_gate_rejects_excessive_holdout_residual():
    assessment = assess_projection_quality(_snapshot(offset_holdout_x=30.0), _assets())

    assert assessment.passed is False
    assert "holdout residual p95 exceeds 25 m" in assessment.reasons


def test_quality_gate_rejects_invalid_and_multiple_geometric_wraps():
    assessment = assess_projection_quality(_snapshot(invalid_wrap=True), _assets())

    assert assessment.passed is False
    assert assessment.invalid_or_multiple_wrap_laps == 1
    assert assessment.post_unwrap_backward_jumps == 1
    assert "invalid or multiple geometric wraps" in assessment.reasons


def test_quality_gate_rejects_a_second_otherwise_valid_geometric_wrap():
    assessment = assess_projection_quality(_snapshot(multiple_wrap=True), _assets())

    assert assessment.passed is False
    assert assessment.accepted_geometric_wraps == 21
    assert assessment.invalid_or_multiple_wrap_laps == 1


@pytest.mark.parametrize("assets", [
    {**_assets(), "fixtureId": "another-race"},
    {**_assets(), "centerLine": [{"x": 0.0, "y": 0.0}] * 4},
])
def test_quality_gate_rejects_malformed_track_assets(assets):
    with pytest.raises(ProjectionGeometryError, match="track assets|invalid track assets"):
        assess_projection_quality(_snapshot(), assets)


def test_quality_assessment_is_immutable_and_deterministic_without_canonical_mutation():
    snapshot = _snapshot()
    before = snapshot.frames["position_telemetry"].clone()

    first = assess_projection_quality(snapshot, _assets())
    second = assess_projection_quality(snapshot, _assets())

    assert first == second
    assert snapshot.frames["position_telemetry"].equals(before)
    with pytest.raises(FrozenInstanceError):
        setattr(first, "passed", False)


def _snapshot(*, holdouts=20, points_per_lap=32, offset_holdout_x=0.0, invalid_wrap=False, multiple_wrap=False):
    laps = [_lap("SRC", 1, 0, 1_000, 900)]
    rows = _position_rows("SRC", 0, points_per_lap)
    for index in range(holdouts):
        start = (index + 1) * 10_000
        driver = f"D{index:02d}"
        laps.append(_lap(driver, index + 2, start, start + 1_000, 1_000 + index))
        distances = None
        if invalid_wrap and index == 0:
            distances = (300.0, 50.0, *range(60, 360, 10))
        if multiple_wrap and index == 0:
            distances = (390.0, 2.0, 390.0, 2.0, *range(10, 290, 10))
        rows.extend(_position_rows(driver, start, points_per_lap, offset_holdout_x, distances))
    laps.append(_lap("PIT", 99, 300_000, 301_000, 1_200, pit_in_time_ms=300_500))
    rows.extend(_position_rows("PIT", 300_000, points_per_lap, 15.0))
    return CanonicalGenerationSnapshot("generation", "a" * 64, {
        "session_metadata": pl.DataFrame([{"session_id": "race-1", "event_name": "Race"}]),
        "laps": pl.DataFrame(laps),
        "position_telemetry": pl.DataFrame(rows),
    })


def _lap(driver, number, start, end, duration, *, pit_in_time_ms=None):
    return {
        "driver_id": driver, "lap_number": number, "lap_start_time_ms": start,
        "lap_end_time_ms": end, "lap_duration_ms": duration,
        "pit_in_time_ms": pit_in_time_ms, "pit_out_time_ms": None,
        "deleted": False, "is_accurate": True,
    }


def _position_rows(driver, start, count, offset_x=0.0, distances=None):
    distances = distances or tuple(index * 400.0 / (count - 1) for index in range(count))
    return [
        {"driver_id": driver, "session_time_ms": start + index + 1, "x": (x + offset_x) * 10.0, "y": y * 10.0}
        for index, distance in enumerate(distances)
        for x, y in (_point_at_distance(float(distance)),)
    ]


def _point_at_distance(distance):
    distance %= 400.0
    if distance <= 100.0:
        return distance, 0.0
    if distance <= 200.0:
        return 100.0, distance - 100.0
    if distance <= 300.0:
        return 300.0 - distance, 100.0
    return 0.0, 400.0 - distance
