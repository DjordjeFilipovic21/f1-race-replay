from pathlib import Path
from statistics import mean, median

import pytest

from f1_replay_pipeline.browser_delivery_reader import read_validated_canonical_generation
from f1_replay_pipeline.visual_trajectory_experiment import (
    NativePositionObservation,
    build_visual_trajectories,
    compare_visual_trajectories,
)


def test_bahrain_pchip_reduces_visual_acceleration_without_changing_coverage():
    canonical = Path(__file__).resolve().parents[2] / "artifacts" / "demo-bahrain-2024"
    if not canonical.exists():
        pytest.skip("local Bahrain canonical artifact is unavailable")

    snapshot = read_validated_canonical_generation(canonical)
    positions = snapshot.frames["position_telemetry"]
    laps = snapshot.frames["laps"]
    start_ms = min(
        laps.filter((laps["lap_number"] == 1) & laps["lap_start_time_ms"].is_not_null())
        .get_column("lap_start_time_ms").to_list()
    )
    end_ms = max(positions.get_column("session_time_ms").to_list())
    comparisons = []

    for driver_id in snapshot.frames["drivers"].get_column("driver_id").to_list():
        rows = (
            positions.filter(
                (positions["driver_id"] == driver_id)
                & positions["x"].is_not_null()
                & positions["y"].is_not_null()
                & (positions["session_time_ms"] >= start_ms)
            )
            .sort("session_time_ms")
            .select("session_time_ms", "x", "y")
            .to_dicts()
        )
        observations = tuple(
            NativePositionObservation(row["session_time_ms"], row["x"] / 10.0, row["y"] / 10.0)
            for row in rows
        )
        comparisons.append(compare_visual_trajectories(*build_visual_trajectories(
            observations, start_ms=start_ms, end_ms=end_ms,
        )))

    linear_acceleration = median(
        metric.linear.p95_acceleration_mps2 for metric in comparisons
        if metric.linear.p95_acceleration_mps2 is not None
    )
    pchip_acceleration = median(
        metric.pchip.p95_acceleration_mps2 for metric in comparisons
        if metric.pchip.p95_acceleration_mps2 is not None
    )

    assert len(comparisons) == 20
    assert mean(metric.linear.coverage for metric in comparisons) == pytest.approx(0.999978, abs=0.000001)
    assert mean(metric.pchip.coverage for metric in comparisons) == pytest.approx(0.999978, abs=0.000001)
    assert pchip_acceleration < linear_acceleration * 0.85
    assert max(metric.max_pchip_deviation_from_linear or 0.0 for metric in comparisons) < 14.0
