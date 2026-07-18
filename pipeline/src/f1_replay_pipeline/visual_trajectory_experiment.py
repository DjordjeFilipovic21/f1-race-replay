"""Isolated 24 Hz visual-coordinate experiment; not browser-delivery code."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from itertools import pairwise
import math
from typing import Literal, Sequence


VISUAL_TRAJECTORY_FPS = 24
MAX_BRIDGED_GAP_MS = 1_500
TrajectoryStrategy = Literal["linear", "pchip"]


@dataclass(frozen=True)
class NativePositionObservation:
    """One finite native x/y observation at an absolute session timestamp."""

    session_time_ms: int
    x: float
    y: float

    def __post_init__(self) -> None:
        if type(self.session_time_ms) is not int or self.session_time_ms < 0:
            raise ValueError("session_time_ms must be a non-negative integer")
        object.__setattr__(self, "x", _finite_float(self.x, "x"))
        object.__setattr__(self, "y", _finite_float(self.y, "y"))


@dataclass(frozen=True)
class VisualTrajectory:
    """Immutable, null-preserving x/y samples on the experiment grid."""

    strategy: TrajectoryStrategy
    time_ms: tuple[int, ...]
    x: tuple[float | None, ...]
    y: tuple[float | None, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "time_ms", tuple(self.time_ms))
        object.__setattr__(self, "x", tuple(self.x))
        object.__setattr__(self, "y", tuple(self.y))
        if self.strategy not in ("linear", "pchip"):
            raise ValueError("strategy must be linear or pchip")
        if len(self.time_ms) != len(self.x) or len(self.x) != len(self.y):
            raise ValueError("trajectory arrays must be aligned")
        if any(type(value) is not int or value < 0 for value in self.time_ms):
            raise TypeError("trajectory timestamps must be non-negative integers")
        if tuple(sorted(set(self.time_ms))) != self.time_ms:
            raise ValueError("trajectory timestamps must be sorted and unique")
        for x_value, y_value in zip(self.x, self.y, strict=True):
            if (x_value is None) != (y_value is None):
                raise ValueError("trajectory coordinates must be paired values or null")
            if x_value is not None:
                _finite_float(x_value, "x")
                _finite_float(y_value, "y")


@dataclass(frozen=True)
class TrajectoryMetrics:
    coverage: float
    path_length: float
    p95_acceleration_mps2: float | None


@dataclass(frozen=True)
class TrajectoryComparisonMetrics:
    linear: TrajectoryMetrics
    pchip: TrajectoryMetrics
    max_pchip_deviation_from_linear: float | None


def visual_trajectory_grid(start_ms: int, end_ms: int) -> tuple[int, ...]:
    """Return the caller-anchored 24 Hz integer grid without a forced endpoint."""
    _validate_range(start_ms, end_ms)
    values: list[int] = []
    index = 0
    while True:
        timestamp = start_ms + (index * 1_000 + 12) // VISUAL_TRAJECTORY_FPS
        if timestamp > end_ms:
            return tuple(values)
        values.append(timestamp)
        index += 1


def build_visual_trajectories(
    observations: Sequence[NativePositionObservation], *, start_ms: int, end_ms: int,
) -> tuple[VisualTrajectory, VisualTrajectory]:
    """Build bounded linear and PCHIP candidates over exactly the same grid."""
    grid = visual_trajectory_grid(start_ms, end_ms)
    points = _normalise_observations(observations)
    linear = _build_trajectory("linear", grid, points, None)
    tangents = _pchip_tangents(points)
    pchip = _build_trajectory("pchip", grid, points, tangents)
    return linear, pchip


def compare_visual_trajectories(
    linear: VisualTrajectory, pchip: VisualTrajectory,
) -> TrajectoryComparisonMetrics:
    """Calculate deterministic descriptive metrics; these do not assert truth."""
    if not isinstance(linear, VisualTrajectory) or not isinstance(pchip, VisualTrajectory):
        raise TypeError("linear and pchip must be VisualTrajectory values")
    if linear.strategy != "linear" or pchip.strategy != "pchip":
        raise ValueError("comparison requires linear and pchip trajectories")
    if linear.time_ms != pchip.time_ms:
        raise ValueError("comparison trajectories must share the exact grid")
    deviations = tuple(
        math.hypot(pchip_x - linear_x, pchip_y - linear_y)
        for linear_x, linear_y, pchip_x, pchip_y in zip(linear.x, linear.y, pchip.x, pchip.y, strict=True)
        if linear_x is not None and pchip_x is not None
        and linear_y is not None and pchip_y is not None
    )
    return TrajectoryComparisonMetrics(
        linear=_trajectory_metrics(linear),
        pchip=_trajectory_metrics(pchip),
        max_pchip_deviation_from_linear=max(deviations, default=None),
    )


def _normalise_observations(observations: Sequence[NativePositionObservation]) -> tuple[NativePositionObservation, ...]:
    if isinstance(observations, (str, bytes)) or not isinstance(observations, Sequence):
        raise TypeError("observations must be a sequence of NativePositionObservation values")
    candidates: dict[int, NativePositionObservation] = {}
    previous_time: int | None = None
    for observation in observations:
        if not isinstance(observation, NativePositionObservation):
            raise TypeError("observations must contain NativePositionObservation values")
        if previous_time is not None and observation.session_time_ms < previous_time:
            raise ValueError("observations must be ordered by non-decreasing session_time_ms")
        previous_time = observation.session_time_ms
        selected = candidates.get(observation.session_time_ms)
        if selected is None or (observation.x, observation.y) < (selected.x, selected.y):
            candidates[observation.session_time_ms] = observation
    return tuple(candidates[time_ms] for time_ms in sorted(candidates))


def _build_trajectory(
    strategy: TrajectoryStrategy,
    grid: tuple[int, ...],
    points: tuple[NativePositionObservation, ...],
    tangents: tuple[tuple[float, float], ...] | None,
) -> VisualTrajectory:
    times = tuple(point.session_time_ms for point in points)
    x_values: list[float | None] = []
    y_values: list[float | None] = []
    for timestamp in grid:
        value = _sample(timestamp, points, times, strategy, tangents)
        x_values.append(None if value is None else value[0])
        y_values.append(None if value is None else value[1])
    return VisualTrajectory(strategy, grid, tuple(x_values), tuple(y_values))


def _sample(
    timestamp: int, points: tuple[NativePositionObservation, ...], times: tuple[int, ...],
    strategy: TrajectoryStrategy, tangents: tuple[tuple[float, float], ...] | None,
) -> tuple[float, float] | None:
    upper = bisect_left(times, timestamp)
    if upper < len(points) and times[upper] == timestamp:
        return points[upper].x, points[upper].y
    lower = upper - 1
    if lower < 0 or upper == len(points):
        return None
    interval_ms = times[upper] - times[lower]
    if interval_ms > MAX_BRIDGED_GAP_MS:
        return None
    ratio = (timestamp - times[lower]) / interval_ms
    linear = _linear(points[lower], points[upper], ratio)
    if strategy == "linear" or tangents is None or not _has_pchip_evidence(lower, upper, times):
        return linear
    return _pchip(points[lower], points[upper], tangents[lower], tangents[upper], ratio, interval_ms)


def _has_pchip_evidence(lower: int, upper: int, times: tuple[int, ...]) -> bool:
    return lower > 0 and upper + 1 < len(times) and all(
        times[index + 1] - times[index] <= MAX_BRIDGED_GAP_MS
        for index in (lower - 1, lower, upper)
    )


def _linear(left: NativePositionObservation, right: NativePositionObservation, ratio: float) -> tuple[float, float]:
    return left.x + (right.x - left.x) * ratio, left.y + (right.y - left.y) * ratio


def _pchip(
    left: NativePositionObservation, right: NativePositionObservation,
    left_tangent: tuple[float, float], right_tangent: tuple[float, float], ratio: float, interval_ms: int,
) -> tuple[float, float]:
    h_seconds = interval_ms / 1_000
    h00 = 2 * ratio ** 3 - 3 * ratio ** 2 + 1
    h10 = ratio ** 3 - 2 * ratio ** 2 + ratio
    h01 = -2 * ratio ** 3 + 3 * ratio ** 2
    h11 = ratio ** 3 - ratio ** 2
    return (
        _clamp(h00 * left.x + h10 * h_seconds * left_tangent[0] + h01 * right.x + h11 * h_seconds * right_tangent[0], left.x, right.x),
        _clamp(h00 * left.y + h10 * h_seconds * left_tangent[1] + h01 * right.y + h11 * h_seconds * right_tangent[1], left.y, right.y),
    )


def _pchip_tangents(points: tuple[NativePositionObservation, ...]) -> tuple[tuple[float, float], ...]:
    return tuple((_axis_tangent(points, index, "x"), _axis_tangent(points, index, "y")) for index in range(len(points)))


def _axis_tangent(points: tuple[NativePositionObservation, ...], index: int, axis: Literal["x", "y"]) -> float:
    if len(points) < 3 or index == 0 or index == len(points) - 1:
        return 0.0
    previous, current, following = points[index - 1:index + 2]
    before_ms = current.session_time_ms - previous.session_time_ms
    after_ms = following.session_time_ms - current.session_time_ms
    if before_ms > MAX_BRIDGED_GAP_MS or after_ms > MAX_BRIDGED_GAP_MS:
        return 0.0
    before_s, after_s = before_ms / 1_000, after_ms / 1_000
    before_slope = (getattr(current, axis) - getattr(previous, axis)) / before_s
    after_slope = (getattr(following, axis) - getattr(current, axis)) / after_s
    if before_slope == 0.0 or after_slope == 0.0 or before_slope * after_slope <= 0.0:
        return 0.0
    weight_before, weight_after = 2 * after_s + before_s, after_s + 2 * before_s
    return (weight_before + weight_after) / (weight_before / before_slope + weight_after / after_slope)


def _trajectory_metrics(trajectory: VisualTrajectory) -> TrajectoryMetrics:
    valid = tuple((time_ms, x, y) for time_ms, x, y in zip(trajectory.time_ms, trajectory.x, trajectory.y, strict=True) if x is not None and y is not None)
    path_length = sum(
        math.hypot(current[1] - previous[1], current[2] - previous[2])
        for previous, current in pairwise(valid)
        if current[0] - previous[0] <= 50
    )
    velocities = tuple(
        ((current[0] + previous[0]) / 2, (current[1] - previous[1]) * 1_000 / (current[0] - previous[0]), (current[2] - previous[2]) * 1_000 / (current[0] - previous[0]))
        for previous, current in pairwise(valid)
        if current[0] - previous[0] <= 50
    )
    accelerations = tuple(
        math.hypot(current[1] - previous[1], current[2] - previous[2]) * 1_000 / (current[0] - previous[0])
        for previous, current in pairwise(velocities)
        if current[0] - previous[0] <= 50
    )
    return TrajectoryMetrics(
        coverage=len(valid) / len(trajectory.time_ms) if trajectory.time_ms else 0.0,
        path_length=path_length,
        p95_acceleration_mps2=_p95(accelerations),
    )


def _p95(values: tuple[float, ...]) -> float | None:
    if not values:
        return None
    ordered = tuple(sorted(values))
    return ordered[round((len(ordered) - 1) * 0.95)]


def _validate_range(start_ms: int, end_ms: int) -> None:
    if type(start_ms) is not int or type(end_ms) is not int:
        raise TypeError("start_ms and end_ms must be integers")
    if start_ms < 0 or end_ms < start_ms:
        raise ValueError("range must be non-negative and non-empty")


def _finite_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite and numeric")
    return float(value)


def _clamp(value: float, first: float, second: float) -> float:
    return min(max(value, min(first, second)), max(first, second))


__all__ = [
    "MAX_BRIDGED_GAP_MS", "VISUAL_TRAJECTORY_FPS", "NativePositionObservation",
    "TrajectoryComparisonMetrics", "TrajectoryMetrics", "VisualTrajectory",
    "build_visual_trajectories", "compare_visual_trajectories", "visual_trajectory_grid",
]
