"""Offline evidence gate for publishing centerline-derived live positions."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
import math
from typing import TypeVar, cast

from f1_replay_pipeline.delivery.browser.browser_delivery_models import CanonicalGenerationSnapshot
from f1_replay_pipeline.analysis.live_position.live_position_projection import CenterlineProjection, ProjectionGeometry, ProjectionGeometryError, project_meters
from f1_replay_pipeline.app.track_assets_generator import (
    RAW_POSITION_UNITS_PER_METER,
    ReferenceLap,
    is_eligible_track_lap,
    select_reference_lap,
)


QUALITY_GATE_VERSION = "projection-quality-gate-v1"
GEOMETRIC_WRAP_VERSION = "geometric-wrap-v1"
MIN_HOLDOUT_LAPS = 20
MIN_HOLDOUT_SAMPLES = 500
MAX_SAMPLES_PER_LAP = 32
P95_RESIDUAL_LIMIT_M = 25.0
MAX_RESIDUAL_LIMIT_M = 75.0
MAX_BACKWARD_JUMP_M = 200.0
FINAL_TRACK_REGION_RATIO = 0.90
INITIAL_TRACK_REGION_RATIO = 0.10
MIN_GEOMETRIC_WRAP_DECREASE_RATIO = 0.80

Point = tuple[float, float]
T = TypeVar("T")


@dataclass(frozen=True)
class ProjectionQualityAssessment:
    """Immutable, generation-local evidence for the versioned quality policy."""

    version: str
    passed: bool
    reasons: tuple[str, ...]
    source_driver: str
    source_lap: int
    holdout_laps: int
    holdout_samples: int
    residual_p95_m: float | None
    residual_max_m: float | None
    accepted_geometric_wraps: int
    invalid_or_multiple_wrap_laps: int
    post_unwrap_backward_jumps: int
    pit_samples: int
    pit_residual_p95_m: float | None
    pit_residual_max_m: float | None


@dataclass(frozen=True)
class _DriverPositions:
    timestamps: tuple[int, ...]
    rows: tuple[Mapping[str, object], ...]


@dataclass(frozen=True)
class _UnwrapResult:
    accepted_wraps: int
    invalid_or_multiple: bool
    distances: tuple[float, ...]


def assess_projection_quality(
    snapshot: CanonicalGenerationSnapshot, track_assets: Mapping[str, object],
) -> ProjectionQualityAssessment:
    """Assess independent native position evidence without mutating the snapshot.

    The source lap is selected by the track-assets selector so a source cannot
    drift between geometry generation and this self-fit-excluding assessment.
    """
    if not isinstance(snapshot, CanonicalGenerationSnapshot):
        raise TypeError("snapshot must be a CanonicalGenerationSnapshot")
    geometry = _geometry_from_assets(snapshot, track_assets)
    source = select_reference_lap(snapshot)
    positions = _index_positions(snapshot.frames["position_telemetry"].to_dicts())
    clean = _measure_clean_laps(snapshot.frames["laps"].to_dicts(), positions, source, geometry)
    pit_samples, pit_residuals = _measure_pit_laps(snapshot.frames["laps"].to_dicts(), positions, geometry)
    reasons = _failure_reasons(clean)
    return ProjectionQualityAssessment(
        version=QUALITY_GATE_VERSION,
        passed=not reasons,
        reasons=reasons,
        source_driver=source.driver_id,
        source_lap=source.lap_number,
        holdout_laps=clean.holdout_laps,
        holdout_samples=len(clean.residuals),
        residual_p95_m=_percentile(clean.residuals),
        residual_max_m=max(clean.residuals) if clean.residuals else None,
        accepted_geometric_wraps=clean.accepted_wraps,
        invalid_or_multiple_wrap_laps=clean.invalid_wrap_laps,
        post_unwrap_backward_jumps=clean.backward_jumps,
        pit_samples=pit_samples,
        pit_residual_p95_m=_percentile(pit_residuals),
        pit_residual_max_m=max(pit_residuals) if pit_residuals else None,
    )


@dataclass(frozen=True)
class _CleanMeasurements:
    holdout_laps: int
    residuals: tuple[float, ...]
    accepted_wraps: int
    invalid_wrap_laps: int
    backward_jumps: int
    invalid_projections: int


def _measure_clean_laps(
    laps: Sequence[Mapping[str, object]],
    positions: Mapping[str, _DriverPositions],
    source: ReferenceLap,
    geometry: ProjectionGeometry,
) -> _CleanMeasurements:
    residuals: list[float] = []
    holdout_laps = accepted_wraps = invalid_wrap_laps = backward_jumps = invalid_projections = 0
    for lap in laps:
        if not is_eligible_track_lap(lap) or _is_source(lap, source):
            continue
        samples = _lap_samples(lap, positions)
        if not samples:
            continue
        holdout_laps += 1
        resolved = _project_lap_samples(samples, geometry)
        if resolved is None:
            invalid_projections += 1
            continue
        residuals.extend(projection.lateral_residual_meters for projection in resolved)
        unwrap = _unwrap(tuple(projection.track_distance_meters for projection in resolved), geometry.circuit_length_meters)
        accepted_wraps += unwrap.accepted_wraps
        invalid_wrap_laps += int(unwrap.invalid_or_multiple)
        backward_jumps += _backward_jumps(unwrap.distances)
    return _CleanMeasurements(holdout_laps, tuple(residuals), accepted_wraps, invalid_wrap_laps, backward_jumps, invalid_projections)


def _measure_pit_laps(
    laps: Sequence[Mapping[str, object]], positions: Mapping[str, _DriverPositions], geometry: ProjectionGeometry,
) -> tuple[int, tuple[float, ...]]:
    samples = 0
    values: list[float] = []
    for lap in laps:
        if lap.get("pit_in_time_ms") is None and lap.get("pit_out_time_ms") is None:
            continue
        lap_points = _lap_samples(lap, positions)
        samples += len(lap_points)
        resolved = _project_lap_samples(lap_points, geometry)
        if resolved is not None:
            values.extend(projection.lateral_residual_meters for projection in resolved)
    return samples, tuple(values)


def _failure_reasons(measurements: _CleanMeasurements) -> tuple[str, ...]:
    reasons = []
    if measurements.holdout_laps < MIN_HOLDOUT_LAPS:
        reasons.append("insufficient independent holdout laps")
    if len(measurements.residuals) < MIN_HOLDOUT_SAMPLES:
        reasons.append("insufficient independent holdout samples")
    if measurements.invalid_projections:
        reasons.append("invalid holdout projections")
    p95 = _percentile(measurements.residuals)
    if p95 is not None and p95 > P95_RESIDUAL_LIMIT_M:
        reasons.append("holdout residual p95 exceeds 25 m")
    if measurements.residuals and max(measurements.residuals) > MAX_RESIDUAL_LIMIT_M:
        reasons.append("holdout residual maximum exceeds 75 m")
    if measurements.invalid_wrap_laps:
        reasons.append("invalid or multiple geometric wraps")
    if measurements.backward_jumps:
        reasons.append("post-unwrap backward jumps exceed 200 m")
    return tuple(reasons)


def _geometry_from_assets(snapshot: CanonicalGenerationSnapshot, assets: Mapping[str, object]) -> ProjectionGeometry:
    if not isinstance(assets, Mapping):
        raise ProjectionGeometryError("track assets must be a mapping")
    fixture = assets.get("fixtureId")
    session = snapshot.frames["session_metadata"].row(0, named=True)
    if fixture != session["session_id"]:
        raise ProjectionGeometryError("track assets fixtureId must match the canonical session")
    centerline = assets.get("centerLine")
    if not isinstance(centerline, Sequence) or isinstance(centerline, (str, bytes)):
        raise ProjectionGeometryError("track assets centerLine must be a sequence of x/y points")
    points = []
    for point in centerline:
        if not isinstance(point, Mapping):
            raise ProjectionGeometryError("track assets centerLine must contain x/y mappings")
        points.append((point.get("x"), point.get("y")))
    try:
        return ProjectionGeometry(tuple(points), cast(float, assets.get("circuitLengthMeters")))
    except ProjectionGeometryError as error:
        raise ProjectionGeometryError(f"invalid track assets geometry: {error}") from error


def _index_positions(rows: Sequence[Mapping[str, object]]) -> Mapping[str, _DriverPositions]:
    grouped: dict[str, list[Mapping[str, object]]] = {}
    for row in rows:
        driver, timestamp = row.get("driver_id"), row.get("session_time_ms")
        if isinstance(driver, str) and type(timestamp) is int:
            grouped.setdefault(driver, []).append(row)
    return {
        driver: _DriverPositions(tuple(cast(int, row["session_time_ms"]) for row in ordered), tuple(ordered))
        for driver, rows_for_driver in grouped.items()
        for ordered in (sorted(rows_for_driver, key=lambda row: cast(int, row["session_time_ms"])),)
    }


def _lap_samples(lap: Mapping[str, object], positions: Mapping[str, _DriverPositions]) -> tuple[tuple[int, Point], ...]:
    driver, start, end = lap.get("driver_id"), lap.get("lap_start_time_ms"), lap.get("lap_end_time_ms")
    if not isinstance(driver, str) or type(start) is not int or type(end) is not int:
        return ()
    indexed = positions.get(driver)
    if indexed is None:
        return ()
    samples, seen = [], set()
    for row in indexed.rows[bisect_left(indexed.timestamps, start):bisect_left(indexed.timestamps, end)]:
        timestamp, x, y = row.get("session_time_ms"), row.get("x"), row.get("y")
        if type(timestamp) is not int or timestamp in seen or type(x) not in (int, float) or type(y) not in (int, float):
            continue
        point = (
            float(cast(int | float, x)) / RAW_POSITION_UNITS_PER_METER,
            float(cast(int | float, y)) / RAW_POSITION_UNITS_PER_METER,
        )
        if all(math.isfinite(value) for value in point):
            seen.add(timestamp)
            samples.append((timestamp, point))
    return _stratified_samples(tuple(samples), MAX_SAMPLES_PER_LAP)


def _stratified_samples(values: Sequence[T], cap: int) -> tuple[T, ...]:
    return tuple(values) if len(values) <= cap else tuple(values[index * (len(values) - 1) // (cap - 1)] for index in range(cap))


def _project_lap_samples(
    samples: Sequence[tuple[int, Point]], geometry: ProjectionGeometry,
) -> tuple[CenterlineProjection, ...] | None:
    """Resolve true branch ambiguity from prior accepted native observations only."""
    previous_distance = None
    projections = []
    for _, (x, y) in samples:
        projection = project_meters(
            x, y, geometry, previous_track_distance_meters=previous_distance,
        )
        if projection is None:
            return None
        projections.append(projection)
        previous_distance = projection.track_distance_meters
    return tuple(projections)


def _unwrap(distances: Sequence[float], length: float) -> _UnwrapResult:
    wraps = offset = 0
    invalid = False
    unwrapped = [distances[0]] if distances else []
    for previous, current in pairwise(distances):
        if current < previous and previous - current > MAX_BACKWARD_JUMP_M:
            if _is_geometric_wrap(previous, current, length):
                wraps += 1
                offset += length
                invalid |= wraps > 1
            else:
                invalid = True
        unwrapped.append(offset + current)
    return _UnwrapResult(wraps, invalid, tuple(unwrapped))


def _is_geometric_wrap(previous: float, current: float, length: float) -> bool:
    return previous >= length * FINAL_TRACK_REGION_RATIO and current <= length * INITIAL_TRACK_REGION_RATIO and previous - current >= length * MIN_GEOMETRIC_WRAP_DECREASE_RATIO


def _backward_jumps(distances: Sequence[float]) -> int:
    return sum(previous - current > MAX_BACKWARD_JUMP_M for previous, current in pairwise(distances))


def _percentile(values: Sequence[float]) -> float | None:
    return sorted(values)[round((len(values) - 1) * 0.95)] if values else None


def _is_source(lap: Mapping[str, object], source: ReferenceLap) -> bool:
    return lap.get("driver_id") == source.driver_id and lap.get("lap_number") == source.lap_number and lap.get("lap_start_time_ms") == source.lap_start_time_ms


__all__ = ["GEOMETRIC_WRAP_VERSION", "ProjectionQualityAssessment", "QUALITY_GATE_VERSION", "assess_projection_quality"]
