"""Derive deterministic visual track assets from canonical lap position data."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from f1_replay_pipeline.analysis.live_position.live_position_projection import (
    ProjectionGeometry,
    project_meters,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    FASTF1_POSITION_UNITS_PER_METER,
    CanonicalGenerationSnapshot,
)
from f1_replay_pipeline.domain.session_modes import SessionMode, normalize_session_mode


RAW_POSITION_UNITS_PER_METER = FASTF1_POSITION_UNITS_PER_METER
DEFAULT_TRACK_WIDTH_M = 20.0
DEFAULT_CENTERLINE_POINTS = 600
_CALIBRATION_CENTERLINE_POINTS = 2048
_MAX_CALIBRATION_BRACKET_INTERVAL_MS = 1000
_MIN_START_FINISH_INLIERS = 2
_MAX_START_FINISH_SPREAD_M = 75.0
_SAFE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

Point = tuple[float, float]
_DISPLAY_ROTATION_ADJUSTMENTS_DEGREES = {"2024-07-race": 180.0}


class TrackAssetsGenerationError(ValueError):
    """Raised when canonical data cannot produce trustworthy visual geometry."""


@dataclass(frozen=True)
class ReferenceLap:
    """The deterministic canonical lap and points used to build track geometry."""

    driver_id: str
    lap_number: int
    lap_start_time_ms: int
    lap_duration_ms: int
    points_meters: tuple[tuple[float, float], ...]


def generate_track_assets(
    snapshot: CanonicalGenerationSnapshot,
    *,
    track_id: str | None = None,
    visual_track_width_m: float = DEFAULT_TRACK_WIDTH_M,
    centerline_points: int = DEFAULT_CENTERLINE_POINTS,
    rotation_degrees: float | None = None,
) -> Mapping[str, object]:
    """Build a closed centerline and landscape-oriented visual metadata."""
    _validate_options(visual_track_width_m, centerline_points, rotation_degrees)
    session = snapshot.frames["session_metadata"].row(0, named=True)
    fixture_id = cast(str, session["session_id"])
    resolved_track_id = track_id or f"{fixture_id}-telemetry-layout-v2"
    if not _SAFE_ID.fullmatch(resolved_track_id):
        raise TrackAssetsGenerationError("track_id must be a lowercase kebab-case identifier")
    reference = select_reference_lap(snapshot)
    temporary_centerline = _resample_closed_polyline(
        reference.points_meters, max(centerline_points, _CALIBRATION_CENTERLINE_POINTS),
    )
    start_finish_offset = _calibrate_start_finish_offset(snapshot, temporary_centerline)
    centerline = _resample_closed_polyline(
        _rotate_closed_polyline(temporary_centerline, start_finish_offset), centerline_points,
    )
    inner, outer = _offset_boundaries(centerline, visual_track_width_m / 2.0)
    length_m = _polyline_length(centerline)
    display_rotation = _resolved_display_rotation(
        fixture_id, centerline, rotation_degrees,
    )
    return {
        "contractVersion": "v2",
        "fixtureId": fixture_id,
        "trackId": resolved_track_id,
        "trackName": cast(str, session["event_name"]),
        "coordinateSpace": {
            "units": "meters",
            "origin": "FastF1 position telemetry local coordinates",
        },
        "circuitLengthMeters": _round(length_m),
        "rotationDegrees": _round(display_rotation),
        "startFinish": {
            "center": _point(centerline[0]),
            "inner": _point(inner[0]),
            "outer": _point(outer[0]),
        },
        "centerLine": [_point(value) for value in centerline],
        "innerBoundary": [_point(value) for value in inner],
        "outerBoundary": [_point(value) for value in outer],
        "distanceMarkersMeters": list(range(1000, int(length_m), 1000)),
    }


def select_reference_lap(snapshot: CanonicalGenerationSnapshot) -> ReferenceLap:
    """Return the same deterministic usable lap consumed by asset generation."""
    laps = snapshot.frames["laps"].to_dicts()
    candidates = sorted(
        (row for row in laps if is_eligible_track_lap(row)),
        key=lambda row: (
            row["lap_duration_ms"], row["driver_id"], row["lap_number"],
            row["lap_start_time_ms"],
        ),
    )
    positions = snapshot.frames["position_telemetry"]
    for lap in candidates:
        rows = positions.filter(
            (positions["driver_id"] == lap["driver_id"])
            & (positions["session_time_ms"] >= lap["lap_start_time_ms"])
            & (positions["session_time_ms"] < lap["lap_end_time_ms"])
        ).sort(["session_time_ms", "x", "y"]).to_dicts()
        points = _clean_points(rows)
        if len(points) >= 4 and _is_spatially_valid(points):
            return ReferenceLap(
                driver_id=cast(str, lap["driver_id"]),
                lap_number=cast(int, lap["lap_number"]),
                lap_start_time_ms=cast(int, lap["lap_start_time_ms"]),
                lap_duration_ms=cast(int, lap["lap_duration_ms"]),
                points_meters=_close(points),
            )
    raise TrackAssetsGenerationError("no deterministic valid lap has usable position telemetry")


def is_eligible_track_lap(row: Mapping[str, object]) -> bool:
    return (
        row["deleted"] is False
        and row["is_accurate"] is True
        and isinstance(row["lap_duration_ms"], int)
        and cast(int, row["lap_duration_ms"]) > 0
        and isinstance(row["lap_start_time_ms"], int)
        and isinstance(row["lap_end_time_ms"], int)
        and cast(int, row["lap_end_time_ms"]) > cast(int, row["lap_start_time_ms"])
        and row["pit_in_time_ms"] is None
        and row["pit_out_time_ms"] is None
    )


def _calibrate_start_finish_offset(
    snapshot: CanonicalGenerationSnapshot, centerline: tuple[Point, ...],
) -> float:
    length = _polyline_length(centerline)
    geometry = ProjectionGeometry(centerline, length)
    positions = _index_valid_position_rows(snapshot.frames["position_telemetry"].to_dicts())
    mode = _asset_session_mode(snapshot)
    candidates: list[float] = []
    for lap in _boundary_lap_candidates(
        snapshot.frames["laps"].to_dicts(), mode,
    ):
        samples = positions.get(cast(str, lap["driver_id"]), ())
        point = _interpolate_boundary_position(samples, cast(int, lap["lap_start_time_ms"]))
        if point is None:
            continue
        projection = project_meters(point[0], point[1], geometry)
        if projection is not None and math.isfinite(projection.track_distance_meters):
            candidates.append(projection.track_distance_meters)
    if len(candidates) == 1 and mode not in {"race", "sprint"}:
        # A solo practice/qualifying run has no grid consensus, but its exact
        # lap boundary is still a deterministic visual origin.
        return candidates[0] % length
    return _estimate_circular_offset(tuple(candidates), length)


def _asset_session_mode(snapshot: CanonicalGenerationSnapshot) -> SessionMode:
    """Read mode when available; missing mode retains the historical race path."""
    value = snapshot.frames["session_metadata"].row(0, named=True).get("session_mode")
    return "race" if value is None else normalize_session_mode(value)


def _boundary_lap_candidates(
    rows: Sequence[Mapping[str, object]], mode: SessionMode = "race",
) -> tuple[Mapping[str, object], ...]:
    minimum_lap = 1 if mode not in {"race", "sprint"} else 2
    candidates = (
        row for row in rows
        if is_eligible_track_lap(row)
        and type(row.get("lap_number")) is int
        and cast(int, row["lap_number"]) >= minimum_lap
        and type(row.get("lap_start_time_ms")) is int
    )
    return tuple(sorted(
        candidates,
        key=lambda row: (
            cast(int, row["lap_start_time_ms"]), cast(str, row["driver_id"]),
            cast(int, row["lap_number"]), cast(int, row["lap_end_time_ms"]),
        ),
    ))


def _index_valid_position_rows(
    rows: Sequence[Mapping[str, object]],
) -> Mapping[str, tuple[tuple[int, Point], ...]]:
    indexed: dict[str, list[tuple[int, Point]]] = {}
    for row in rows:
        driver_id = row.get("driver_id")
        timestamp = row.get("session_time_ms")
        x, y = row.get("x"), row.get("y")
        if not isinstance(driver_id, str) or type(timestamp) is not int:
            continue
        if not _is_finite_number(x) or not _is_finite_number(y):
            continue
        point = (
            float(cast(int | float, x)) / RAW_POSITION_UNITS_PER_METER,
            float(cast(int | float, y)) / RAW_POSITION_UNITS_PER_METER,
        )
        indexed.setdefault(driver_id, []).append((timestamp, point))
    return {
        driver_id: tuple(
            sorted(samples, key=lambda sample: (sample[0], sample[1][0], sample[1][1]))
        )
        for driver_id, samples in indexed.items()
    }


def _interpolate_boundary_position(
    samples: Sequence[tuple[int, Point]], boundary_time_ms: int,
) -> Point | None:
    exact = next((point for timestamp, point in samples if timestamp == boundary_time_ms), None)
    if exact is not None:
        return exact
    before = tuple(sample for sample in samples if sample[0] < boundary_time_ms)
    after = tuple(sample for sample in samples if sample[0] > boundary_time_ms)
    if not before or not after:
        return None
    lower_time, lower = before[-1]
    upper_time, upper = after[0]
    interval = upper_time - lower_time
    if interval <= 0 or interval > _MAX_CALIBRATION_BRACKET_INTERVAL_MS:
        return None
    ratio = (boundary_time_ms - lower_time) / interval
    return (
        lower[0] + (upper[0] - lower[0]) * ratio,
        lower[1] + (upper[1] - lower[1]) * ratio,
    )


def _estimate_circular_offset(candidates: Sequence[float], length: float) -> float:
    if not math.isfinite(length) or length <= 0:
        raise TrackAssetsGenerationError("insufficient start/finish calibration evidence")
    normalized = tuple(sorted(
        candidate % length
        for candidate in candidates
        if math.isfinite(candidate)
    ))
    if len(normalized) < _MIN_START_FINISH_INLIERS:
        raise TrackAssetsGenerationError("insufficient start/finish calibration evidence")
    tolerance = max(5.0, min(_MAX_START_FINISH_SPREAD_M, length * 0.05))
    clusters = []
    for anchor in normalized:
        inliers = tuple(
            candidate for candidate in normalized
            if _circular_distance(candidate, anchor, length) <= tolerance
        )
        estimate = _circular_median(inliers, anchor, length)
        residuals = tuple(
            _circular_distance(candidate, estimate, length) for candidate in inliers
        )
        clusters.append((len(inliers), sum(residuals), max(residuals), estimate, anchor))
    best = min(
        clusters,
        key=lambda cluster: (-cluster[0], cluster[1], cluster[2], cluster[3], cluster[4]),
    )
    if best[0] < _MIN_START_FINISH_INLIERS:
        raise TrackAssetsGenerationError("insufficient start/finish calibration evidence")
    return best[3]


def _circular_median(values: Sequence[float], anchor: float, length: float) -> float:
    unwrapped = sorted(_unwrap_near(candidate, anchor, length) for candidate in values)
    middle = len(unwrapped) // 2
    if len(unwrapped) % 2:
        return unwrapped[middle] % length
    return ((unwrapped[middle - 1] + unwrapped[middle]) / 2.0) % length


def _unwrap_near(value: float, anchor: float, length: float) -> float:
    return anchor + ((value - anchor + length / 2.0) % length - length / 2.0)


def _circular_distance(left: float, right: float, length: float) -> float:
    difference = abs(left - right) % length
    return min(difference, length - difference)


def _rotate_closed_polyline(points: tuple[Point, ...], offset: float) -> tuple[Point, ...]:
    closed = _close(points)
    lengths = _cumulative_lengths(closed)
    total = lengths[-1]
    if total <= 0:
        raise TrackAssetsGenerationError("reference centerline has zero arc length")
    normalized = offset % total
    segment = next(index for index in range(1, len(lengths)) if lengths[index] >= normalized)
    point = _interpolate_at(closed, lengths, normalized)
    if math.dist(point, closed[segment]) <= 1e-9:
        return tuple(closed[segment:-1] + closed[:segment])
    return (point,) + closed[segment:-1] + closed[:segment]


def _is_finite_number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(float(cast(int | float, value)))


def _clean_points(rows: Sequence[Mapping[str, object]]) -> tuple[tuple[float, float], ...]:
    points = []
    for row in rows:
        x, y = row["x"], row["y"]
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            continue
        point = (float(x) / RAW_POSITION_UNITS_PER_METER, float(y) / RAW_POSITION_UNITS_PER_METER)
        if not all(math.isfinite(value) for value in point) or (points and point == points[-1]):
            continue
        points.append(point)
    return tuple(points)


def _resample_closed_polyline(
    points: tuple[tuple[float, float], ...], count: int,
) -> tuple[tuple[float, float], ...]:
    closed = _close(points)
    lengths = _cumulative_lengths(closed)
    total = lengths[-1]
    if total <= 0:
        raise TrackAssetsGenerationError("reference centerline has zero arc length")
    samples = [_interpolate_at(closed, lengths, total * index / count) for index in range(count)]
    return tuple(samples + [samples[0]])


def _cumulative_lengths(points: Sequence[Point]) -> tuple[float, ...]:
    lengths = [0.0]
    for previous, current in zip(points[:-1], points[1:], strict=True):
        lengths.append(lengths[-1] + math.dist(previous, current))
    return tuple(lengths)


def _is_spatially_valid(points: tuple[tuple[float, float], ...]) -> bool:
    closed = _close(points)
    if _polyline_length(closed) <= 0:
        return False
    xs, ys = zip(*points, strict=True)
    bounding_area = (max(xs) - min(xs)) * (max(ys) - min(ys))
    signed_twice_area = sum(
        current[0] * following[1] - following[0] * current[1]
        for current, following in zip(closed[:-1], closed[1:], strict=True)
    )
    return bounding_area > 0 and abs(signed_twice_area) > max(1e-6, bounding_area * 1e-6)


def _interpolate_at(points, lengths, distance):
    segment = next(index for index in range(1, len(lengths)) if lengths[index] >= distance)
    start, end = points[segment - 1], points[segment]
    span = lengths[segment] - lengths[segment - 1]
    ratio = 0.0 if span == 0 else (distance - lengths[segment - 1]) / span
    return (start[0] + (end[0] - start[0]) * ratio, start[1] + (end[1] - start[1]) * ratio)


def _offset_boundaries(centerline, half_width):
    unique = centerline[:-1]
    signed_area = sum(
        current[0] * following[1] - following[0] * current[1]
        for current, following in zip(unique, unique[1:] + unique[:1], strict=True)
    )
    interior_sign = 1.0 if signed_area > 0 else -1.0
    inner, outer = [], []
    for index, current in enumerate(unique):
        previous, following = unique[index - 1], unique[(index + 1) % len(unique)]
        dx, dy = following[0] - previous[0], following[1] - previous[1]
        norm = math.hypot(dx, dy)
        if norm == 0:
            raise TrackAssetsGenerationError("reference centerline contains a degenerate tangent")
        left = (-dy / norm, dx / norm)
        inward = (left[0] * interior_sign, left[1] * interior_sign)
        inner.append((current[0] + inward[0] * half_width, current[1] + inward[1] * half_width))
        outer.append((current[0] - inward[0] * half_width, current[1] - inward[1] * half_width))
    return tuple(inner + [inner[0]]), tuple(outer + [outer[0]])


def _close(points):
    return points if points and points[0] == points[-1] else points + (points[0],)


def _polyline_length(points):
    return sum(
        math.dist(previous, current)
        for previous, current in zip(points[:-1], points[1:], strict=True)
    )


def _landscape_rotation_degrees(points):
    """Align the principal axis horizontally without reflecting or reversing points."""
    unique = points[:-1]
    center_x = sum(point[0] for point in unique) / len(unique)
    center_y = sum(point[1] for point in unique) / len(unique)
    centered = tuple((x - center_x, y - center_y) for x, y in unique)
    covariance_xx = sum(x * x for x, _ in centered)
    covariance_yy = sum(y * y for _, y in centered)
    covariance_xy = sum(x * y for x, y in centered)
    rotation = math.degrees(
        0.5 * math.atan2(2.0 * covariance_xy, covariance_xx - covariance_yy)
    )
    width, height = _display_extents(centered, rotation)
    if height > width:
        rotation += 90.0
    return ((rotation + 180.0) % 360.0) - 180.0


def _resolved_display_rotation(
    fixture_id: str, centerline: tuple[Point, ...], explicit_rotation: float | None,
) -> float:
    if explicit_rotation is not None:
        return float(explicit_rotation)
    automatic = _landscape_rotation_degrees(centerline)
    adjusted = automatic + _DISPLAY_ROTATION_ADJUSTMENTS_DEGREES.get(fixture_id, 0.0)
    return ((adjusted + 180.0) % 360.0) - 180.0


def _display_extents(points, rotation_degrees):
    radians = math.radians(rotation_degrees)
    cosine, sine = math.cos(radians), math.sin(radians)
    rotated = tuple(
        (x * cosine + y * sine, x * sine - y * cosine)
        for x, y in points
    )
    xs, ys = zip(*rotated, strict=True)
    return max(xs) - min(xs), max(ys) - min(ys)


def _point(value):
    return {"x": _round(value[0]), "y": _round(value[1])}


def _round(value):
    return round(float(value), 6)


def _validate_options(width, points, rotation):
    if not isinstance(width, (int, float)) or not math.isfinite(width) or width <= 0:
        raise TrackAssetsGenerationError("visual_track_width_m must be positive and finite")
    if type(points) is not int or points < 4:
        raise TrackAssetsGenerationError("centerline_points must be an integer of at least four")
    if rotation is not None and (
        not isinstance(rotation, (int, float)) or not math.isfinite(rotation)
    ):
        raise TrackAssetsGenerationError("rotation_degrees must be finite")


__all__ = [
    "DEFAULT_CENTERLINE_POINTS", "DEFAULT_TRACK_WIDTH_M", "RAW_POSITION_UNITS_PER_METER",
    "ReferenceLap", "TrackAssetsGenerationError", "generate_track_assets",
    "is_eligible_track_lap", "select_reference_lap",
]
