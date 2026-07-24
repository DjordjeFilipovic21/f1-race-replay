import math
from dataclasses import FrozenInstanceError

import pytest

from f1_replay_pipeline.analysis.live_position.live_position_projection import (
    AMBIGUITY_RESIDUAL_DIFFERENCE_M,
    MAX_PROJECTION_RESIDUAL_M,
    CenterlineProjection,
    ProjectionCandidate,
    ProjectionGeometry,
    ProjectionGeometryError,
    project_fastf1_decimetres,
    project_meters,
)
from f1_replay_pipeline.analysis.live_position.live_position_projection import (
    _candidate_segment_ids,
    _candidates,
    _exhaustive_candidates,
    _resolve_candidates,
)


SQUARE = ProjectionGeometry(((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)), 400.0)


def test_projects_to_a_segment_interior_with_lap_local_distance_and_residual():
    result = project_meters(25.0, 3.0, SQUARE)

    assert result == CenterlineProjection(25.0, 3.0, (ProjectionCandidate(25.0, 3.0, 0),), False)


def test_converts_fastf1_decimetres_at_the_explicit_input_boundary():
    result = project_fastf1_decimetres(250.0, 30.0, SQUARE)

    assert result is not None and result.track_distance_meters == pytest.approx(25.0)


def test_orders_equal_distance_candidates_deterministically_by_segment_index():
    crossing = ProjectionGeometry(((0.0, 0.0), (100.0, 100.0), (0.0, 100.0), (100.0, 0.0), (0.0, 0.0)), 482.842712)

    result = project_meters(50.0, 50.0, crossing, previous_track_distance_meters=0.0)

    assert result is not None and tuple(candidate.segment_index for candidate in result.candidates) == (0, 2)


def test_resolves_parallel_ambiguity_with_circular_continuity_across_origin():
    parallel = ProjectionGeometry(((0.0, 0.0), (100.0, 0.0), (100.0, 10.0), (0.0, 10.0), (0.0, 0.0)), 220.0)

    result = project_meters(0.0, 1.0, parallel, previous_track_distance_meters=219.0)

    assert result is not None and result.track_distance_meters == pytest.approx(219.0)


def test_resolves_an_ordinary_vertex_without_previous_continuity():
    result = project_meters(100.0, 0.0, SQUARE)

    assert result == CenterlineProjection(100.0, 0.0, (ProjectionCandidate(100.0, 0.0, 0),), False)


def test_resolves_the_closed_centerline_origin_without_previous_continuity():
    result = project_meters(0.0, 0.0, SQUARE)

    assert result == CenterlineProjection(0.0, 0.0, (ProjectionCandidate(0.0, 0.0, 0),), False)


def test_returns_none_for_self_intersection_ambiguity_without_continuity():
    crossing = ProjectionGeometry(((0.0, 0.0), (100.0, 100.0), (0.0, 100.0), (100.0, 0.0), (0.0, 0.0)), 482.842712)

    assert project_meters(50.0, 50.0, crossing) is None


def test_rejects_coordinate_outside_calibrated_maximum_residual():
    assert project_meters(50.0, -(MAX_PROJECTION_RESIDUAL_M + 0.1), SQUARE) is None


def test_accepts_coordinate_at_calibrated_maximum_residual_boundary():
    result = project_meters(50.0, -MAX_PROJECTION_RESIDUAL_M, SQUARE)

    assert result is not None and result.lateral_residual_meters == MAX_PROJECTION_RESIDUAL_M


@pytest.mark.parametrize("x, y", [(None, 0.0), (math.nan, 0.0), (0.0, math.inf)])
def test_returns_none_for_null_or_non_finite_coordinates(x, y):
    assert project_meters(x, y, SQUARE) is None


@pytest.mark.parametrize("centerline, length", [
    (((0.0, 0.0), (1.0, 0.0), (1.0, 1.0)), 3.0),
    (((0.0, 0.0), (1.0, 0.0), (1.0, 0.0), (0.0, 0.0)), 3.0),
    (((0.0, 0.0), (math.nan, 0.0), (1.0, 1.0), (0.0, 0.0)), 3.0),
    (((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)), 0.0),
])
def test_rejects_malformed_or_degenerate_geometry(centerline, length):
    with pytest.raises(ProjectionGeometryError):
        ProjectionGeometry(centerline, length)


def test_projection_result_and_geometry_are_immutable_and_deterministic():
    first = project_meters(25.0, 3.0, SQUARE)
    second = project_meters(25.0, 3.0, SQUARE)

    assert first == second
    with pytest.raises(FrozenInstanceError):
        setattr(SQUARE, "circuit_length_meters", 1.0)


def test_ambiguity_band_matches_the_calibration_policy():
    assert AMBIGUITY_RESIDUAL_DIFFERENCE_M == 5.0


@pytest.mark.parametrize("point", [
    (-50.0, -75.0), (-75.0, 50.0), (0.0, 0.0), (50.0, 50.0),
    (100.0, 175.0), (175.0, 100.0), (150.0, 150.0),
])
def test_spatial_index_retains_every_exhaustive_candidate_within_residual_limit(point):
    indexed = _candidates(point, SQUARE)
    exhaustive = _exhaustive_candidates(point, SQUARE)

    assert set(indexed) >= set(
        candidate for candidate in exhaustive
        if candidate.lateral_residual_meters <= MAX_PROJECTION_RESIDUAL_M
    )
    assert project_meters(point[0], point[1], SQUARE) == _resolve_candidates(exhaustive, SQUARE, None)


def test_spatial_index_preserves_crossing_and_parallel_projection_results():
    geometry = ProjectionGeometry(((-100.0, -100.0), (100.0, 100.0), (-100.0, 100.0), (100.0, -100.0), (-100.0, -100.0)), 965.685424)

    for point in ((0.0, 0.0), (-75.0, -75.0), (75.0, -75.0), (150.0, 0.0)):
        assert set(_candidates(point, geometry)) >= set(
            candidate for candidate in _exhaustive_candidates(point, geometry)
            if candidate.lateral_residual_meters <= MAX_PROJECTION_RESIDUAL_M
        )
        assert project_meters(point[0], point[1], geometry) == _resolve_candidates(
            _exhaustive_candidates(point, geometry), geometry, None,
        )
    assert project_meters(0.0, 0.0, geometry, previous_track_distance_meters=0.0) == _resolve_candidates(
        _exhaustive_candidates((0.0, 0.0), geometry), geometry, 0.0,
    )


def test_compiled_geometry_internals_are_immutable_and_deterministic():
    first = ProjectionGeometry(SQUARE.centerline_meters, SQUARE.circuit_length_meters)
    second = ProjectionGeometry(SQUARE.centerline_meters, SQUARE.circuit_length_meters)

    assert first == second
    assert first._segments == second._segments
    assert isinstance(first._segments, tuple)
    assert all(isinstance(indices, tuple) for indices in first._spatial_index.values())
    with pytest.raises(AttributeError):
        getattr(first._spatial_index, "__setitem__")


def test_spatial_index_evaluates_a_bounded_local_subset_of_a_600_segment_geometry():
    segment_count = 600
    unique_points = tuple(
        (1000.0 * math.cos(2.0 * math.pi * index / segment_count), 1000.0 * math.sin(2.0 * math.pi * index / segment_count))
        for index in range(segment_count)
    )
    points = unique_points + (unique_points[0],)
    geometry = ProjectionGeometry(points, 2.0 * math.pi * 1000.0)

    assert len(_candidate_segment_ids((1000.0, 0.0), geometry)) < segment_count // 6
