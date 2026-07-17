"""Derive deterministic visual track assets from canonical lap position data."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from f1_replay_pipeline.browser_delivery_models import (
    FASTF1_POSITION_UNITS_PER_METER,
    CanonicalGenerationSnapshot,
)


RAW_POSITION_UNITS_PER_METER = FASTF1_POSITION_UNITS_PER_METER
DEFAULT_TRACK_WIDTH_M = 20.0
DEFAULT_CENTERLINE_POINTS = 600
_SAFE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


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
    rotation_degrees: float = 0.0,
) -> Mapping[str, object]:
    """Build a closed centerline and synthetic fixed-width visual boundaries."""
    _validate_options(visual_track_width_m, centerline_points, rotation_degrees)
    session = snapshot.frames["session_metadata"].row(0, named=True)
    fixture_id = cast(str, session["session_id"])
    resolved_track_id = track_id or f"{fixture_id}-telemetry-layout-v1"
    if not _SAFE_ID.fullmatch(resolved_track_id):
        raise TrackAssetsGenerationError("track_id must be a lowercase kebab-case identifier")
    reference = select_reference_lap(snapshot)
    centerline = _resample_closed_polyline(reference.points_meters, centerline_points)
    inner, outer = _offset_boundaries(centerline, visual_track_width_m / 2.0)
    length_m = _polyline_length(centerline)
    return {
        "contractVersion": "v1",
        "fixtureId": fixture_id,
        "trackId": resolved_track_id,
        "trackName": cast(str, session["event_name"]),
        "coordinateSpace": {
            "units": "meters",
            "origin": "FastF1 position telemetry local coordinates",
        },
        "circuitLengthMeters": _round(length_m),
        "rotationDegrees": _round(rotation_degrees),
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
        ).sort("session_time_ms").to_dicts()
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
    lengths = [0.0]
    for previous, current in zip(closed[:-1], closed[1:], strict=True):
        lengths.append(lengths[-1] + math.dist(previous, current))
    total = lengths[-1]
    if total <= 0:
        raise TrackAssetsGenerationError("reference centerline has zero arc length")
    samples = [_interpolate_at(closed, lengths, total * index / count) for index in range(count)]
    return tuple(samples + [samples[0]])


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


def _point(value):
    return {"x": _round(value[0]), "y": _round(value[1])}


def _round(value):
    return round(float(value), 6)


def _validate_options(width, points, rotation):
    if not isinstance(width, (int, float)) or not math.isfinite(width) or width <= 0:
        raise TrackAssetsGenerationError("visual_track_width_m must be positive and finite")
    if type(points) is not int or points < 4:
        raise TrackAssetsGenerationError("centerline_points must be an integer of at least four")
    if not isinstance(rotation, (int, float)) or not math.isfinite(rotation):
        raise TrackAssetsGenerationError("rotation_degrees must be finite")


__all__ = [
    "DEFAULT_CENTERLINE_POINTS", "DEFAULT_TRACK_WIDTH_M", "RAW_POSITION_UNITS_PER_METER",
    "ReferenceLap", "TrackAssetsGenerationError", "generate_track_assets",
    "is_eligible_track_lap", "select_reference_lap",
]
