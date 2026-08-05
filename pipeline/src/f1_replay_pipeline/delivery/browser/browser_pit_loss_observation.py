"""Extract causal pit-loss observations from browser delivery arrays."""

from __future__ import annotations

from collections.abc import Mapping
import math

from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    BrowserDriverFields,
    BrowserDriverStintSummary,
    BrowserStintSummary,
)
from f1_replay_pipeline.delivery.browser.browser_pit_loss_model import (
    GapSample,
    LeaderSnapshot,
    PitLossObservation,
    PitStopCandidate,
    StopIntervalStatus,
    PitLossStatus,
    classify_stop_interval_status,
    is_eligible_observation,
)


def extract_eligible_pit_loss_observations(
    *,
    stint_summary: BrowserStintSummary,
    drivers: Mapping[str, BrowserDriverFields],
    leaderboard_order: tuple[tuple[str, ...] | None, ...],
    track_status_code: tuple[int | None, ...],
    quality_gate_passed: bool,
) -> tuple[PitLossObservation, ...]:
    """Build and filter pure observations from aligned browser delivery data."""
    time_ms = _validate_inputs(
        stint_summary=stint_summary,
        drivers=drivers,
        leaderboard_order=leaderboard_order,
        track_status_code=track_status_code,
        quality_gate_passed=quality_gate_passed,
    )
    if not quality_gate_passed:
        return ()

    observations = tuple(
        observation
        for driver_id, pit_in_time_ms, pit_out_time_ms in _pit_transitions(stint_summary)
        for observation in (
            _build_observation(
                driver_id=driver_id,
                pit_in_time_ms=pit_in_time_ms,
                pit_out_time_ms=pit_out_time_ms,
                drivers=drivers,
                time_ms=time_ms,
                leaderboard_order=leaderboard_order,
                track_status_code=track_status_code,
                quality_gate_passed=quality_gate_passed,
            ),
        )
        if observation is not None and is_eligible_observation(observation)
    )
    return tuple(sorted(observations, key=_observation_sort_key))


def _validate_inputs(
    *,
    stint_summary: BrowserStintSummary,
    drivers: Mapping[str, BrowserDriverFields],
    leaderboard_order: tuple[tuple[str, ...] | None, ...],
    track_status_code: tuple[int | None, ...],
    quality_gate_passed: bool,
) -> tuple[int, ...]:
    if not isinstance(stint_summary, BrowserStintSummary):
        raise ValueError("stint_summary must be a BrowserStintSummary")
    if not isinstance(drivers, Mapping):
        raise ValueError("drivers must be a mapping")
    if type(quality_gate_passed) is not bool:
        raise ValueError("quality_gate_passed must be a boolean")
    expected_driver_ids = set(stint_summary.drivers)
    if set(drivers) != expected_driver_ids:
        raise ValueError("stint and dynamic driver sets must match")
    if any(
        not isinstance(fields, BrowserDriverFields)
        or fields.driver_id != driver_id
        for driver_id, fields in drivers.items()
    ):
        raise ValueError("drivers must map each ID to matching BrowserDriverFields")

    driver_values = tuple(drivers.values())
    time_ms = driver_values[0].time_ms
    if any(fields.time_ms != time_ms for fields in driver_values[1:]):
        raise ValueError("all BrowserDriverFields must share the same time_ms")
    if type(leaderboard_order) is not tuple or type(track_status_code) is not tuple:
        raise ValueError("global arrays must be immutable tuples")
    if len(leaderboard_order) != len(time_ms) or len(track_status_code) != len(time_ms):
        raise ValueError("global arrays must align to the driver time_ms")
    if any(
        order is not None
        and (
            type(order) is not tuple
            or not order
            or any(type(driver_id) is not str or not driver_id for driver_id in order)
            or len(set(order)) != len(order)
            or any(driver_id not in expected_driver_ids for driver_id in order)
        )
        for order in leaderboard_order
    ):
        raise ValueError("leaderboard entries must contain known unique driver IDs")
    if any(code is not None and (type(code) is not int or code < 0) for code in track_status_code):
        raise ValueError("track status values must be non-negative integers or null")
    _pit_transitions(stint_summary)
    return time_ms


def _pit_transitions(
    stint_summary: BrowserStintSummary,
) -> tuple[tuple[str, int, int], ...]:
    """Return complete transitions and reject ambiguous mapped pit inputs."""
    seen_pit_in: set[tuple[str, int]] = set()
    seen_pit_out: set[tuple[str, int]] = set()
    transitions: list[tuple[str, int, int]] = []
    for driver_id, summary in stint_summary.drivers.items():
        if not isinstance(summary, BrowserDriverStintSummary):
            raise ValueError("stint summary values must be BrowserDriverStintSummary")
        pit_in_times: list[int] = []
        pit_out_times: list[int] = []
        for pit_in_time_ms, pit_out_time_ms in zip(
            summary.pit_in_time_ms, summary.pit_out_time_ms, strict=True,
        ):
            if pit_in_time_ms is not None:
                key = (driver_id, pit_in_time_ms)
                if key in seen_pit_in:
                    raise ValueError("duplicate pit-in transition input")
                seen_pit_in.add(key)
            if pit_out_time_ms is not None:
                key = (driver_id, pit_out_time_ms)
                if key in seen_pit_out:
                    raise ValueError("duplicate pit-out transition input")
                seen_pit_out.add(key)
                pit_out_times.append(pit_out_time_ms)
            if pit_in_time_ms is not None:
                pit_in_times.append(pit_in_time_ms)
        transitions.extend(
            (driver_id, pit_in_time_ms, pit_out_time_ms)
            for pit_in_time_ms, pit_out_time_ms in _pair_pit_times(
                tuple(sorted(pit_in_times)), tuple(sorted(pit_out_times)),
            )
        )
    return tuple(transitions)


def _pair_pit_times(
    pit_in_times: tuple[int, ...], pit_out_times: tuple[int, ...],
) -> tuple[tuple[int, int], ...]:
    """Pair each pit-in with the next unused pit-out across adjacent stints."""
    remaining = list(pit_out_times)
    pairs: list[tuple[int, int]] = []
    for pit_in_time_ms in pit_in_times:
        match_index = next(
            (index for index, pit_out_time_ms in enumerate(remaining) if pit_out_time_ms > pit_in_time_ms),
            None,
        )
        if match_index is None:
            continue
        pairs.append((pit_in_time_ms, remaining.pop(match_index)))
    return tuple(pairs)


def _build_observation(
    *,
    driver_id: str,
    pit_in_time_ms: int,
    pit_out_time_ms: int,
    drivers: Mapping[str, BrowserDriverFields],
    time_ms: tuple[int, ...],
    leaderboard_order: tuple[tuple[str, ...] | None, ...],
    track_status_code: tuple[int | None, ...],
    quality_gate_passed: bool,
) -> PitLossObservation | None:
    if pit_out_time_ms <= pit_in_time_ms:
        return None
    driver = drivers[driver_id]
    before = _latest_before_sample(driver, pit_in_time_ms)
    after = _first_after_sample(driver, pit_out_time_ms)
    if before is None or after is None:
        return None
    before_index, before_gap = before
    after_index, after_gap = after
    before_leader = _leader_at(leaderboard_order, before_index)
    after_leader = _leader_at(leaderboard_order, after_index)
    if before_leader is None or before_leader != after_leader:
        return None
    leader = drivers.get(before_leader)
    if leader is None:
        return None

    interval_indices = tuple(
        index
        for index, timestamp in enumerate(time_ms)
        if pit_in_time_ms <= timestamp <= pit_out_time_ms
    )
    leader_snapshot = LeaderSnapshot(
        before_leader_id=before_leader,
        after_leader_id=after_leader,
        before_is_in_pit_lane=leader.is_in_pit_lane[before_index],
        after_is_in_pit_lane=leader.is_in_pit_lane[after_index],
        interval_is_in_pit_lane=tuple(leader.is_in_pit_lane[index] for index in interval_indices),
    )
    status = StopIntervalStatus(tuple(track_status_code[index] for index in interval_indices))
    candidate = PitStopCandidate(
        driver_id=driver_id,
        pit_in_time_ms=pit_in_time_ms,
        pit_out_time_ms=pit_out_time_ms,
        quality_gate_passed=quality_gate_passed,
        mapping_complete_and_unique=True,
    )
    return PitLossObservation(candidate, before_gap, after_gap, leader_snapshot, status)


def _latest_before_sample(
    fields: BrowserDriverFields, pit_in_time_ms: int,
) -> tuple[int, GapSample] | None:
    earlier = tuple(
        index
        for index, timestamp in enumerate(fields.time_ms)
        if timestamp < pit_in_time_ms and _valid_gap(fields.gap_to_leader_ms[index])
    )
    index = earlier[-1] if earlier else _exact_gap_index(fields, pit_in_time_ms)
    if index is None:
        return None
    return index, _gap_sample(fields, index, is_after=False)


def _first_after_sample(
    fields: BrowserDriverFields, pit_out_time_ms: int,
) -> tuple[int, GapSample] | None:
    for index, timestamp in enumerate(fields.time_ms):
        if timestamp >= pit_out_time_ms and _valid_after_sample(fields, index):
            return index, _gap_sample(fields, index, is_after=True)
    return None


def _exact_gap_index(fields: BrowserDriverFields, pit_in_time_ms: int) -> int | None:
    for index, timestamp in enumerate(fields.time_ms):
        if timestamp == pit_in_time_ms and _valid_gap(fields.gap_to_leader_ms[index]):
            return index
    return None


def _valid_after_sample(fields: BrowserDriverFields, index: int) -> bool:
    return (
        _valid_gap(fields.gap_to_leader_ms[index])
        and fields.is_in_pit_lane[index] is False
        and fields.is_finished[index] is not True
        and _is_active_status(fields.status[index])
    )


def _gap_sample(fields: BrowserDriverFields, index: int, *, is_after: bool) -> GapSample:
    status = fields.status[index]
    return GapSample(
        driver_id=fields.driver_id,
        time_ms=fields.time_ms[index],
        gap_to_leader_ms=fields.gap_to_leader_ms[index],
        status=status,
        is_in_pit_lane=fields.is_in_pit_lane[index],
        is_on_track=True if is_after and _is_active_status(status) else None,
        is_finished=fields.is_finished[index],
    )


def _leader_at(
    leaderboard_order: tuple[tuple[str, ...] | None, ...], index: int,
) -> str | None:
    order = leaderboard_order[index]
    return None if order is None or not order else order[0]


def _is_active_status(status: str | None) -> bool:
    if not isinstance(status, str):
        return False
    normalized = status.strip().lower().replace("_", "").replace("-", "").replace(" ", "")
    return normalized in {"active", "ontrack"}


def _valid_gap(value: float | None) -> bool:
    return type(value) is float and math.isfinite(value) and value >= 0


def _observation_sort_key(observation: PitLossObservation) -> tuple[int, str, int]:
    candidate = observation.candidate
    return candidate.pit_out_time_ms, candidate.driver_id, candidate.pit_in_time_ms


__all__ = [
    "PitLossStatus",
    "classify_stop_interval_status",
    "extract_eligible_pit_loss_observations",
]
