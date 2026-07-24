"""Pure, deterministic projection of FastF1 position coordinates onto a centerline."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping, Sequence, cast


PROJECTION_QUALITY_GATE_VERSION = "projection-quality-gate-v1"
GEOMETRIC_WRAP_POLICY_VERSION = "geometric-wrap-v1"
FASTF1_DECIMETRES_PER_METER = 10.0
MAX_PROJECTION_RESIDUAL_M = 75.0
AMBIGUITY_RESIDUAL_DIFFERENCE_M = 5.0
_GRID_CELL_SIZE_M = MAX_PROJECTION_RESIDUAL_M * 2.0
_INDEX_RESIDUAL_PADDING_M = MAX_PROJECTION_RESIDUAL_M + AMBIGUITY_RESIDUAL_DIFFERENCE_M

Point = tuple[float, float]


class ProjectionGeometryError(ValueError):
    """Raised when a centerline cannot safely support deterministic projection."""


@dataclass(frozen=True)
class _Segment:
    """Immutable values needed to project onto one centerline segment."""

    index: int
    start: Point
    end: Point
    vector: Point
    denominator: float
    length: float
    cumulative_arc_meters: float


@dataclass(frozen=True)
class ProjectionGeometry:
    """Validated closed centerline in metres and its asset-declared circuit length."""

    centerline_meters: tuple[Point, ...]
    circuit_length_meters: float
    _segments: tuple[_Segment, ...] = field(init=False, repr=False, compare=False)
    _centerline_length_meters: float = field(init=False, repr=False, compare=False)
    _spatial_index: Mapping[tuple[int, int], tuple[int, ...]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        points = tuple(_finite_point(point) for point in self.centerline_meters)
        if len(points) < 4 or points[0] != points[-1]:
            raise ProjectionGeometryError("centerline must be a closed polyline with at least four points")
        if not _is_finite_positive(self.circuit_length_meters):
            raise ProjectionGeometryError("circuit_length_meters must be positive and finite")
        segments = _compile_segments(points)
        object.__setattr__(self, "centerline_meters", points)
        object.__setattr__(self, "circuit_length_meters", _as_finite_float(self.circuit_length_meters))
        object.__setattr__(self, "_segments", segments)
        object.__setattr__(self, "_centerline_length_meters", sum(segment.length for segment in segments))
        object.__setattr__(self, "_spatial_index", _build_spatial_index(segments))


@dataclass(frozen=True, order=True)
class ProjectionCandidate:
    """One segment projection, ordered deterministically by its declared fields."""

    track_distance_meters: float
    lateral_residual_meters: float
    segment_index: int


@dataclass(frozen=True)
class CenterlineProjection:
    """Resolved lap-local projection plus candidates retained for continuity inspection."""

    track_distance_meters: float
    lateral_residual_meters: float
    candidates: tuple[ProjectionCandidate, ...]
    is_ambiguous: bool


def project_fastf1_decimetres(
    x_decimetres: object, y_decimetres: object, geometry: ProjectionGeometry, *,
    previous_track_distance_meters: float | None = None,
) -> CenterlineProjection | None:
    """Project canonical FastF1 decimetre coordinates through the explicit unit boundary."""
    x, y = _as_finite_float(x_decimetres), _as_finite_float(y_decimetres)
    if x is None or y is None:
        return None
    return project_meters(
        x / FASTF1_DECIMETRES_PER_METER, y / FASTF1_DECIMETRES_PER_METER,
        geometry,
        previous_track_distance_meters=previous_track_distance_meters,
    )


def project_meters(
    x_meters: object, y_meters: object, geometry: ProjectionGeometry, *,
    previous_track_distance_meters: float | None = None,
) -> CenterlineProjection | None:
    """Project metre coordinates, returning ``None`` for invalid or unresolved observations."""
    if not isinstance(geometry, ProjectionGeometry):
        raise TypeError("geometry must be a ProjectionGeometry")
    x, y = _as_finite_float(x_meters), _as_finite_float(y_meters)
    if x is None or y is None:
        return None
    previous = _valid_previous_distance(previous_track_distance_meters, geometry.circuit_length_meters)
    candidates = _candidates((x, y), geometry)
    return _resolve_candidates(candidates, geometry, previous)


def _resolve_candidates(
    candidates: Sequence[ProjectionCandidate], geometry: ProjectionGeometry, previous: float | None,
) -> CenterlineProjection | None:
    """Apply the existing residual, topology, and continuity policies to candidates."""
    if not candidates or candidates[0].lateral_residual_meters > MAX_PROJECTION_RESIDUAL_M:
        return None
    contenders = tuple(
        candidate for candidate in candidates
        if candidate.lateral_residual_meters - candidates[0].lateral_residual_meters <= AMBIGUITY_RESIDUAL_DIFFERENCE_M
    )
    branches = _branch_representatives(contenders, len(geometry.centerline_meters) - 1)
    if len(branches) > 1 and previous is None:
        return None
    if len(branches) == 1:
        selected = branches[0]
    else:
        assert previous is not None
        selected = min(
            branches,
            key=lambda candidate: (_circular_distance(candidate.track_distance_meters, previous, geometry.circuit_length_meters), candidate.lateral_residual_meters, candidate.segment_index),
        )
    return CenterlineProjection(selected.track_distance_meters, selected.lateral_residual_meters, branches, len(branches) > 1)


def _branch_representatives(
    candidates: Sequence[ProjectionCandidate], segment_count: int,
) -> tuple[ProjectionCandidate, ...]:
    """Collapse adjoining segments into one local branch before ambiguity resolution."""
    remaining = {candidate.segment_index: candidate for candidate in candidates}
    representatives = []
    while remaining:
        pending = [next(iter(remaining))]
        branch = []
        while pending:
            index = pending.pop()
            candidate = remaining.pop(index, None)
            if candidate is None:
                continue
            branch.append(candidate)
            pending.extend(
                other_index for other_index in remaining
                if _segments_are_adjacent(index, other_index, segment_count)
            )
        representatives.append(min(branch, key=_representative_order))
    return tuple(sorted(representatives, key=_representative_order))


def _segments_are_adjacent(left: int, right: int, segment_count: int) -> bool:
    return abs(left - right) == 1 or {left, right} == {0, segment_count - 1}


def _representative_order(candidate: ProjectionCandidate) -> tuple[float, int, float]:
    return candidate.lateral_residual_meters, candidate.segment_index, candidate.track_distance_meters


def _candidates(point: Point, geometry: ProjectionGeometry) -> tuple[ProjectionCandidate, ...]:
    candidates = tuple(
        _project_segment(point, geometry._segments[index], geometry)
        for index in _candidate_segment_ids(point, geometry)
    )
    return tuple(sorted(candidates, key=lambda candidate: (candidate.lateral_residual_meters, candidate.segment_index, candidate.track_distance_meters)))


def _compile_segments(points: tuple[Point, ...]) -> tuple[_Segment, ...]:
    """Compile validated centerline geometry once for repeated pure projections."""
    segments = []
    arc_length = 0.0
    for index, (start, end) in enumerate(zip(points[:-1], points[1:], strict=True)):
        vector = (end[0] - start[0], end[1] - start[1])
        denominator = vector[0] * vector[0] + vector[1] * vector[1]
        if denominator == 0.0:
            raise ProjectionGeometryError("centerline must not contain degenerate segments")
        length = math.dist(start, end)
        segments.append(_Segment(index, start, end, vector, denominator, length, arc_length))
        arc_length += length
    return tuple(segments)


def _build_spatial_index(segments: tuple[_Segment, ...]) -> Mapping[tuple[int, int], tuple[int, ...]]:
    """Map each residual-expanded segment box to deterministic uniform-grid cells."""
    cells: dict[tuple[int, int], list[int]] = {}
    for segment in segments:
        minimum_x = min(segment.start[0], segment.end[0]) - _INDEX_RESIDUAL_PADDING_M
        maximum_x = max(segment.start[0], segment.end[0]) + _INDEX_RESIDUAL_PADDING_M
        minimum_y = min(segment.start[1], segment.end[1]) - _INDEX_RESIDUAL_PADDING_M
        maximum_y = max(segment.start[1], segment.end[1]) + _INDEX_RESIDUAL_PADDING_M
        for x_cell in range(_grid_cell(minimum_x), _grid_cell(maximum_x) + 1):
            for y_cell in range(_grid_cell(minimum_y), _grid_cell(maximum_y) + 1):
                cells.setdefault((x_cell, y_cell), []).append(segment.index)
    return MappingProxyType({cell: tuple(indices) for cell, indices in cells.items()})


def _grid_cell(coordinate: float) -> int:
    return math.floor(coordinate / _GRID_CELL_SIZE_M)


def _candidate_segment_ids(point: Point, geometry: ProjectionGeometry) -> tuple[int, ...]:
    """Return segments in the point cell's residual-and-ambiguity-expanded boxes."""
    return geometry._spatial_index.get((_grid_cell(point[0]), _grid_cell(point[1])), ())


def _project_segment(point: Point, segment: _Segment, geometry: ProjectionGeometry) -> ProjectionCandidate:
    ratio = min(1.0, max(0.0, (
        (point[0] - segment.start[0]) * segment.vector[0]
        + (point[1] - segment.start[1]) * segment.vector[1]
    ) / segment.denominator))
    projected = (
        segment.start[0] + segment.vector[0] * ratio,
        segment.start[1] + segment.vector[1] * ratio,
    )
    distance = (segment.cumulative_arc_meters + ratio * segment.length) / geometry._centerline_length_meters * geometry.circuit_length_meters
    return ProjectionCandidate(
        _lap_local_distance(distance, geometry.circuit_length_meters),
        math.dist(point, projected),
        segment.index,
    )


def _exhaustive_candidates(point: Point, geometry: ProjectionGeometry) -> tuple[ProjectionCandidate, ...]:
    """Reference implementation retained for tests of index completeness."""
    return tuple(sorted(
        (_project_segment(point, segment, geometry) for segment in geometry._segments),
        key=lambda candidate: (candidate.lateral_residual_meters, candidate.segment_index, candidate.track_distance_meters),
    ))


def _valid_previous_distance(value: float | None, circuit_length_meters: float) -> float | None:
    if value is None:
        return None
    numeric_value = _as_finite_float(value)
    if numeric_value is None or not 0.0 <= numeric_value < circuit_length_meters:
        raise ValueError("previous_track_distance_meters must be finite and lap-local")
    return numeric_value


def _circular_distance(left: float, right: float, circuit_length_meters: float) -> float:
    difference = abs(left - right)
    return min(difference, circuit_length_meters - difference)


def _lap_local_distance(distance: float, circuit_length_meters: float) -> float:
    return 0.0 if math.isclose(distance, circuit_length_meters) else distance


def _finite_point(value: object) -> Point:
    if not isinstance(value, Sequence) or len(value) != 2:
        raise ProjectionGeometryError("centerline points must be finite x/y pairs")
    x, y = _as_finite_float(value[0]), _as_finite_float(value[1])
    if x is None or y is None:
        raise ProjectionGeometryError("centerline points must be finite x/y pairs")
    return (x, y)


def _is_finite_positive(value: object) -> bool:
    numeric_value = _as_finite_float(value)
    return numeric_value is not None and numeric_value > 0.0


def _as_finite_float(value: object) -> float | None:
    if type(value) not in (int, float):
        return None
    numeric_value = float(cast(int | float, value))
    return numeric_value if math.isfinite(numeric_value) else None


__all__ = [
    "AMBIGUITY_RESIDUAL_DIFFERENCE_M", "FASTF1_DECIMETRES_PER_METER", "GEOMETRIC_WRAP_POLICY_VERSION",
    "MAX_PROJECTION_RESIDUAL_M", "PROJECTION_QUALITY_GATE_VERSION", "CenterlineProjection", "ProjectionCandidate",
    "ProjectionGeometry", "ProjectionGeometryError", "project_fastf1_decimetres", "project_meters",
]
