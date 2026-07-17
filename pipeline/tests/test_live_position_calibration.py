"""Offline, spike-scoped calibration checks; this is not production projection code."""

from __future__ import annotations

import json
import math
from bisect import bisect_left
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Iterable, Sequence, TypeVar

import pytest

from f1_replay_pipeline.browser_delivery_reader import read_validated_canonical_generation


CALIBRATION_VERSION = "projection-quality-gate-v1"
GEOMETRIC_WRAP_POLICY_VERSION = "geometric-wrap-v1"
MIN_HOLDOUT_LAPS = 20
MIN_HOLDOUT_SAMPLES = 500
P95_RESIDUAL_LIMIT_M = 25.0
MAX_RESIDUAL_LIMIT_M = 75.0
MAX_IMPLAUSIBLE_BACKWARD_JUMP_M = 200.0
FINAL_TRACK_REGION_RATIO = 0.90
INITIAL_TRACK_REGION_RATIO = 0.10
MIN_GEOMETRIC_WRAP_DECREASE_RATIO = 0.80
STALE_COORDINATE_MS = 1_000
MAX_SAMPLES_PER_LAP = 32

Point = tuple[float, float]
T = TypeVar("T")
ROOT = Path(__file__).resolve().parents[2]
CANONICAL_PARENT = ROOT / "artifacts/demo-bahrain-2024"
TRACK_ASSETS = (
    ROOT / "artifacts/browser-bahrain-cli/generations/2024-bahrain-race-cli-v2/track-assets.json"
)


@dataclass(frozen=True)
class CalibrationEvidence:
    source_driver: str
    source_lap: int
    source_duration_ms: int
    source_p95_residual_m: float
    source_max_residual_m: float
    holdout_laps: int
    holdout_samples: int
    p95_residual_m: float
    max_residual_m: float
    accepted_geometric_wraps: int
    laps_with_invalid_or_multiple_wraps: int
    implausible_backward_jumps_after_unwrap: int
    pit_samples: int
    pit_p95_residual_m: float | None
    pit_max_residual_m: float | None

    def report(self) -> str:
        return (
            f"{CALIBRATION_VERSION}: source={self.source_driver} lap={self.source_lap} "
            f"duration={self.source_duration_ms} ms "
            f"selfFit p95={self.source_p95_residual_m:.3f} m max={self.source_max_residual_m:.3f} m; "
            f"holdouts={self.holdout_laps} laps/{self.holdout_samples} samples; "
            f"residual p95={self.p95_residual_m:.3f} m max={self.max_residual_m:.3f} m; "
            f"wrapPolicy={GEOMETRIC_WRAP_POLICY_VERSION}; "
            f"acceptedGeometricWraps={self.accepted_geometric_wraps}; "
            f"invalidOrMultipleWrapLaps={self.laps_with_invalid_or_multiple_wraps}; "
            f"backwardJumpsAfterUnwrap={self.implausible_backward_jumps_after_unwrap}; "
            f"pit={self.pit_samples} samples p95={_format_metric(self.pit_p95_residual_m)} "
            f"max={_format_metric(self.pit_max_residual_m)}"
        )


@dataclass(frozen=True)
class DriverPositions:
    timestamps: tuple[int, ...]
    rows: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class LapUnwrap:
    accepted_wraps: int
    has_invalid_or_multiple_wrap: bool
    unwrapped_distances: tuple[float, ...]


def test_synthetic_contract_covers_wrap_ambiguity_and_stale_fallback() -> None:
    valid_wrap = _unwrap_lap_distances((390.0, 2.0), 400.0)
    assert valid_wrap == LapUnwrap(1, False, (390.0, 402.0))
    invalid_backward = _unwrap_lap_distances((300.0, 50.0), 400.0)
    assert invalid_backward.has_invalid_or_multiple_wrap is True
    assert _count_implausible_backward_jumps(invalid_backward.unwrapped_distances) == 1
    multiple_wraps = _unwrap_lap_distances((390.0, 2.0, 390.0, 2.0), 400.0)
    assert multiple_wraps.has_invalid_or_multiple_wrap is True
    assert _select_continuous_candidate(((40.0, 0.5), (240.0, 0.5)), 238.0) == 240.0
    assert _fallback_progress((10_000, 320.0), 10_999) == 320.0
    assert _fallback_progress((10_000, 320.0), 11_000) is None
    assert _stratified_samples(tuple(range(100)), 4) == (0, 33, 66, 99)


def test_bahrain_holdout_quality_gate_when_local_artifacts_are_available() -> None:
    if not CANONICAL_PARENT.is_dir() or not TRACK_ASSETS.is_file():
        pytest.skip("Bahrain canonical generation and track-assets fixture are not checked out")

    evidence = _measure_bahrain_holdouts(CANONICAL_PARENT, TRACK_ASSETS)
    print(evidence.report())

    assert evidence.source_driver == "VER"
    assert evidence.source_lap == 39
    assert evidence.source_duration_ms == 92_608
    assert evidence.holdout_laps >= MIN_HOLDOUT_LAPS, evidence.report()
    assert evidence.holdout_samples >= MIN_HOLDOUT_SAMPLES, evidence.report()
    assert evidence.p95_residual_m <= P95_RESIDUAL_LIMIT_M, evidence.report()
    assert evidence.max_residual_m <= MAX_RESIDUAL_LIMIT_M, evidence.report()
    assert evidence.laps_with_invalid_or_multiple_wraps == 0, evidence.report()
    assert evidence.implausible_backward_jumps_after_unwrap == 0, evidence.report()


def _measure_bahrain_holdouts(canonical_parent: Path, track_assets_path: Path) -> CalibrationEvidence:
    snapshot = read_validated_canonical_generation(canonical_parent)
    centerline, circuit_length_m = _read_track_assets(track_assets_path)
    laps = snapshot.frames["laps"].to_dicts()
    positions_by_driver = _index_positions(snapshot.frames["position_telemetry"].to_dicts())
    source = _select_source_lap(laps, positions_by_driver)
    source_residuals = [
        _project(point, centerline)[1] for _, point in _native_lap_points(source, positions_by_driver)
    ]
    residuals: list[float] = []
    accepted_wraps = 0
    invalid_or_multiple_wrap_laps = 0
    backward_jumps_after_unwrap = 0
    holdout_laps = 0
    for lap in laps:
        if not _eligible_lap(lap) or _same_lap(lap, source):
            continue
        samples = _native_lap_points(lap, positions_by_driver)
        if not samples:
            continue
        holdout_laps += 1
        projections = [_project(point, centerline) for _, point in samples]
        unwrap = _unwrap_lap_distances(
            [arc_distance for arc_distance, _ in projections], circuit_length_m,
        )
        accepted_wraps += unwrap.accepted_wraps
        invalid_or_multiple_wrap_laps += int(unwrap.has_invalid_or_multiple_wrap)
        backward_jumps_after_unwrap += _count_implausible_backward_jumps(unwrap.unwrapped_distances)
        residuals.extend(residual_m for _, residual_m in projections)

    pit_residuals = [
        _project(point, centerline)[1]
        for lap in laps if _is_pit_lap(lap)
        for _, point in _native_lap_points(lap, positions_by_driver)
    ]
    if not residuals:
        raise AssertionError("Bahrain generation has no independent eligible holdout position samples")
    return CalibrationEvidence(
        source_driver=str(source["driver_id"]),
        source_lap=_integer(source["lap_number"], "source lap number"),
        source_duration_ms=_integer(source["lap_duration_ms"], "source lap duration"),
        source_p95_residual_m=_percentile(source_residuals, 0.95),
        source_max_residual_m=max(source_residuals),
        holdout_laps=holdout_laps,
        holdout_samples=len(residuals),
        p95_residual_m=_percentile(residuals, 0.95),
        max_residual_m=max(residuals),
        accepted_geometric_wraps=accepted_wraps,
        laps_with_invalid_or_multiple_wraps=invalid_or_multiple_wrap_laps,
        implausible_backward_jumps_after_unwrap=backward_jumps_after_unwrap,
        pit_samples=len(pit_residuals),
        pit_p95_residual_m=_percentile(pit_residuals, 0.95) if pit_residuals else None,
        pit_max_residual_m=max(pit_residuals) if pit_residuals else None,
    )


def _select_source_lap(
    laps: Iterable[dict[str, object]], positions_by_driver: dict[str, DriverPositions],
) -> dict[str, object]:
    candidates = sorted(
        (lap for lap in laps if _eligible_lap(lap)),
        key=lambda lap: (
            _integer(lap["lap_duration_ms"], "lap duration"),
            str(lap["driver_id"]),
            _integer(lap["lap_number"], "lap number"),
            _integer(lap["lap_start_time_ms"], "lap start"),
        ),
    )
    for lap in candidates:
        if len(_native_lap_points(lap, positions_by_driver)) >= 4:
            return lap
    raise AssertionError("Bahrain generation has no deterministic centerline source lap")


def _eligible_lap(lap: dict[str, object]) -> bool:
    return (
        lap["deleted"] is False
        and lap["is_accurate"] is True
        and isinstance(lap["lap_duration_ms"], int)
        and lap["lap_duration_ms"] > 0
        and isinstance(lap["lap_start_time_ms"], int)
        and isinstance(lap["lap_end_time_ms"], int)
        and lap["lap_end_time_ms"] > lap["lap_start_time_ms"]
        and lap["pit_in_time_ms"] is None
        and lap["pit_out_time_ms"] is None
    )


def _is_pit_lap(lap: dict[str, object]) -> bool:
    return lap["pit_in_time_ms"] is not None or lap["pit_out_time_ms"] is not None


def _same_lap(left: dict[str, object], right: dict[str, object]) -> bool:
    return left["driver_id"] == right["driver_id"] and left["lap_number"] == right["lap_number"]


def _index_positions(rows: Iterable[dict[str, object]]) -> dict[str, DriverPositions]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        driver_id = row.get("driver_id")
        if isinstance(driver_id, str) and isinstance(row.get("session_time_ms"), int):
            grouped.setdefault(driver_id, []).append(row)
    return {
        driver_id: DriverPositions(
            timestamps=tuple(_integer(row["session_time_ms"], "position timestamp") for row in ordered),
            rows=tuple(ordered),
        )
        for driver_id, driver_rows in grouped.items()
        for ordered in (sorted(driver_rows, key=lambda row: _integer(row["session_time_ms"], "position timestamp")),)
    }


def _native_lap_points(
    lap: dict[str, object], positions_by_driver: dict[str, DriverPositions],
) -> tuple[tuple[int, Point], ...]:
    lap_start = _integer(lap["lap_start_time_ms"], "lap start")
    lap_end = _integer(lap["lap_end_time_ms"], "lap end")
    driver_id = lap["driver_id"]
    indexed = positions_by_driver.get(driver_id) if isinstance(driver_id, str) else None
    if indexed is None:
        return ()
    start_index = bisect_left(indexed.timestamps, lap_start)
    end_index = bisect_left(indexed.timestamps, lap_end)
    points: list[tuple[int, Point]] = []
    seen_times: set[int] = set()
    for row in indexed.rows[start_index:end_index]:
        timestamp = row["session_time_ms"]
        x, y = row["x"], row["y"]
        if not isinstance(timestamp, int) or timestamp in seen_times:
            continue
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            continue
        point = (float(x) / 10.0, float(y) / 10.0)
        if not all(math.isfinite(value) for value in point):
            continue
        seen_times.add(timestamp)
        points.append((timestamp, point))
    return _stratified_samples(tuple(points), MAX_SAMPLES_PER_LAP)


def _stratified_samples(values: Sequence[T], cap: int) -> tuple[T, ...]:
    if len(values) <= cap:
        return tuple(values)
    return tuple(values[index * (len(values) - 1) // (cap - 1)] for index in range(cap))


def _read_track_assets(path: Path) -> tuple[tuple[Point, ...], float]:
    payload = json.loads(path.read_text())
    circuit_length_m = payload.get("circuitLengthMeters")
    if not isinstance(circuit_length_m, (int, float)) or not math.isfinite(circuit_length_m) or circuit_length_m <= 0:
        raise AssertionError("track assets require a positive finite circuitLengthMeters")
    centerline = tuple((float(point["x"]), float(point["y"])) for point in payload["centerLine"])
    return centerline, float(circuit_length_m)


def _project(point: Point, centerline: Sequence[Point]) -> tuple[float, float]:
    best = (0.0, float("inf"))
    arc_distance_m = 0.0
    for start, end in zip(centerline[:-1], centerline[1:], strict=True):
        segment = (end[0] - start[0], end[1] - start[1])
        segment_length_m = math.dist(start, end)
        denominator = segment[0] ** 2 + segment[1] ** 2
        ratio = 0.0 if denominator == 0 else ((point[0] - start[0]) * segment[0] + (point[1] - start[1]) * segment[1]) / denominator
        ratio = min(1.0, max(0.0, ratio))
        projected = (start[0] + ratio * segment[0], start[1] + ratio * segment[1])
        residual_m = math.dist(point, projected)
        if residual_m < best[1]:
            best = (arc_distance_m + ratio * segment_length_m, residual_m)
        arc_distance_m += segment_length_m
    return best


def _count_implausible_backward_jumps(projected: Sequence[float]) -> int:
    return sum(
        previous - current > MAX_IMPLAUSIBLE_BACKWARD_JUMP_M
        for previous, current in pairwise(projected)
    )


def _percentile(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * percentile)]


def _unwrap_lap_distances(projected: Sequence[float], circuit_length_m: float) -> LapUnwrap:
    if not projected:
        return LapUnwrap(0, False, ())
    accepted_wraps = 0
    has_invalid_or_multiple_wrap = False
    offset_m = 0.0
    unwrapped = [projected[0]]
    for previous, current in pairwise(projected):
        if current < previous and previous - current > MAX_IMPLAUSIBLE_BACKWARD_JUMP_M:
            if _is_valid_geometric_wrap(previous, current, circuit_length_m):
                accepted_wraps += 1
                offset_m += circuit_length_m
                has_invalid_or_multiple_wrap |= accepted_wraps > 1
            else:
                has_invalid_or_multiple_wrap = True
        unwrapped.append(offset_m + current)
    return LapUnwrap(accepted_wraps, has_invalid_or_multiple_wrap, tuple(unwrapped))


def _is_valid_geometric_wrap(previous: float, current: float, circuit_length_m: float) -> bool:
    return (
        previous >= circuit_length_m * FINAL_TRACK_REGION_RATIO
        and current <= circuit_length_m * INITIAL_TRACK_REGION_RATIO
        and previous - current >= circuit_length_m * MIN_GEOMETRIC_WRAP_DECREASE_RATIO
    )


def _select_continuous_candidate(candidates: Sequence[tuple[float, float]], previous_progress_m: float) -> float:
    return min(candidates, key=lambda candidate: (abs(candidate[0] - previous_progress_m), candidate[1]))[0]


def _fallback_progress(last_valid: tuple[int, float] | None, sample_time_ms: int) -> float | None:
    if last_valid is None or sample_time_ms - last_valid[0] >= STALE_COORDINATE_MS:
        return None
    return last_valid[1]


def _format_metric(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f} m"


def _integer(value: object, label: str) -> int:
    if type(value) is not int:
        raise AssertionError(f"{label} must be an integer")
    return value
