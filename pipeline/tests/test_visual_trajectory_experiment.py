import math
from typing import cast

from f1_replay_pipeline.visual_trajectory_experiment import (
    NativePositionObservation,
    build_visual_trajectories,
    compare_visual_trajectories,
    visual_trajectory_grid,
)


def _point(time_ms, x, y):
    return NativePositionObservation(time_ms, x, y)


def test_grid_is_caller_anchored_24_hz_with_exact_integer_timestamps():
    assert visual_trajectory_grid(1_000, 1_125) == (1_000, 1_042, 1_083, 1_125)
    assert visual_trajectory_grid(1_000, 1_124) == (1_000, 1_042, 1_083)


def test_exact_native_timestamp_is_preserved_for_both_strategies():
    linear, pchip = build_visual_trajectories((_point(0, 1, 2), _point(125, 9, 10)), start_ms=0, end_ms=125)

    assert (linear.x[0], linear.y[0]) == (1.0, 2.0)
    assert (linear.x[-1], linear.y[-1]) == (9.0, 10.0)
    assert pchip.x == linear.x and pchip.y == linear.y


def test_irregular_spacing_and_1300_ms_global_gap_are_bounded_and_interpolated():
    linear, pchip = build_visual_trajectories((_point(0, 0, 0), _point(1_300, 13, 26)), start_ms=0, end_ms=1_300)

    at_1_second = linear.time_ms.index(1_000)
    x_value, y_value = linear.x[at_1_second], linear.y[at_1_second]
    assert x_value is not None and y_value is not None
    assert math.isclose(x_value, 10.0)
    assert math.isclose(y_value, 20.0)
    assert pchip.x == linear.x and pchip.y == linear.y


def test_gaps_over_1500_ms_and_extrapolation_emit_null_pairs():
    linear, pchip = build_visual_trajectories((_point(100, 1, 1), _point(1_700, 17, 17)), start_ms=0, end_ms=1_750)

    assert all(x is None and y is None for x, y in zip(linear.x, linear.y, strict=True))
    assert pchip.x == linear.x and pchip.y == linear.y


def test_pchip_is_bounded_and_uses_shape_preserving_interior_evidence():
    points = (_point(0, 0, 0), _point(200, 1, 1), _point(600, 9, 2), _point(900, 10, 3))
    linear, pchip = build_visual_trajectories(points, start_ms=0, end_ms=900)

    for index, timestamp in enumerate(pchip.time_ms):
        if 200 < timestamp < 600:
            x_value, y_value = pchip.x[index], pchip.y[index]
            assert x_value is not None and y_value is not None
            assert 1.0 <= x_value <= 9.0
            assert 1.0 <= y_value <= 2.0
    assert any(
        candidate is not None and baseline is not None and not math.isclose(candidate, baseline)
        for candidate, baseline in zip(pchip.x, linear.x, strict=True)
    )


def test_deduplication_is_deterministic_and_inputs_remain_unchanged():
    observations = (_point(0, 5, 5), _point(0, 1, 2), _point(100, 3, 4))
    before = observations

    first = build_visual_trajectories(observations, start_ms=0, end_ms=100)
    second = build_visual_trajectories(observations, start_ms=0, end_ms=100)

    assert observations == before
    assert first == second
    assert (first[0].x[0], first[0].y[0]) == (1.0, 2.0)


def test_malformed_or_unordered_observations_are_rejected():
    for observations in ((_point(100, 0, 0), _point(0, 1, 1)), ("not-observations",)):
        try:
            build_visual_trajectories(cast(tuple[NativePositionObservation, ...], observations), start_ms=0, end_ms=100)
        except (TypeError, ValueError):
            continue
        raise AssertionError("malformed observations must be rejected")


def test_comparison_metrics_report_coverage_path_acceleration_and_deviation():
    points = (_point(0, 0, 0), _point(200, 1, 1), _point(600, 9, 2), _point(900, 10, 3))
    linear, pchip = build_visual_trajectories(points, start_ms=0, end_ms=900)

    metrics = compare_visual_trajectories(linear, pchip)

    assert metrics.linear.coverage == 1.0
    assert metrics.linear.path_length > 0.0
    assert metrics.linear.p95_acceleration_mps2 is not None
    assert metrics.max_pchip_deviation_from_linear is not None
    assert metrics.max_pchip_deviation_from_linear > 0.0
