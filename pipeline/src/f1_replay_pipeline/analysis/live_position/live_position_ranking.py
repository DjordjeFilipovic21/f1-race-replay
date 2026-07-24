"""Pure deterministic ranking and equivalent-progress leader-gap derivation."""

from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass
from itertools import pairwise

from f1_replay_pipeline.analysis.live_position.live_position_progress import ProgressMode


@dataclass(frozen=True)
class DriverProgressInput:
    driver_id: str
    race_progress_meters: float | None
    mode: ProgressMode

    def __post_init__(self) -> None:
        if not isinstance(self.driver_id, str) or not self.driver_id:
            raise ValueError("driver_id must be non-empty")
        if self.race_progress_meters is not None and (not _finite(self.race_progress_meters) or self.race_progress_meters < 0):
            raise ValueError("race_progress_meters must be finite, non-negative, or None")
        if not isinstance(self.mode, ProgressMode):
            raise ValueError("mode must be a ProgressMode")


@dataclass(frozen=True)
class DriverHistory:
    driver_id: str
    points: tuple[tuple[int, float], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "points", tuple((time_ms, float(progress)) for time_ms, progress in self.points))
        _validate_history(self)


@dataclass(frozen=True)
class RankingState:
    last_session_time_ms: int | None = None
    previous_order: tuple[str, ...] = ()
    histories: tuple[DriverHistory, ...] = ()

    def __post_init__(self) -> None:
        if self.last_session_time_ms is not None and (type(self.last_session_time_ms) is not int or self.last_session_time_ms < 0):
            raise ValueError("last_session_time_ms must be a non-negative integer or None")
        object.__setattr__(self, "previous_order", tuple(self.previous_order))
        object.__setattr__(self, "histories", tuple(self.histories))
        if len(set(self.previous_order)) != len(self.previous_order) or any(not isinstance(driver_id, str) or not driver_id for driver_id in self.previous_order):
            raise ValueError("previous_order must contain unique non-empty driver IDs")
        ordered = tuple(sorted(self.histories, key=lambda history: history.driver_id))
        if len({history.driver_id for history in ordered}) != len(ordered):
            raise ValueError("history driver IDs must be unique")
        for history in ordered:
            _validate_history(history)
        object.__setattr__(self, "histories", ordered)


@dataclass(frozen=True)
class DriverRanking:
    driver_id: str
    mode: ProgressMode
    effective_progress_meters: float | None
    position: int | None
    gap_to_leader_ms: float | None

    def __post_init__(self) -> None:
        if not isinstance(self.driver_id, str) or not self.driver_id or not isinstance(self.mode, ProgressMode):
            raise TypeError("driver ranking requires an ID and ProgressMode")
        if any(value is not None and (not _finite(value) or value < 0) for value in (self.effective_progress_meters, self.gap_to_leader_ms)):
            raise ValueError("ranking values must be finite, non-negative, or None")
        if self.position is not None and (type(self.position) is not int or self.position < 1):
            raise ValueError("position must be a positive integer or None")


@dataclass(frozen=True)
class RankingResult:
    state: RankingState
    drivers: tuple[DriverRanking, ...]
    leaderboard_order: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "drivers", tuple(self.drivers))
        object.__setattr__(self, "leaderboard_order", tuple(self.leaderboard_order))
        known = tuple(entry for entry in self.drivers if entry.effective_progress_meters is not None)
        ordered = tuple(sorted(known, key=_position_key))
        ordered_known = tuple(entry.driver_id for entry in ordered)
        if (
            not isinstance(self.state, RankingState)
            or len(set(self.leaderboard_order)) != len(self.leaderboard_order)
            or len({entry.driver_id for entry in self.drivers}) != len(self.drivers)
            or tuple(entry.position for entry in ordered) != tuple(range(1, len(known) + 1))
            or ordered_known != self.leaderboard_order
        ):
            raise ValueError("result requires immutable state and unique leaderboard order")


@dataclass(frozen=True)
class RankingTimelineFrame:
    """One immutable shared-time input frame for batch ranking."""

    session_time_ms: int
    inputs: tuple[DriverProgressInput, ...]

    def __post_init__(self) -> None:
        if type(self.session_time_ms) is not int or self.session_time_ms < 0:
            raise ValueError("session_time_ms must be a non-negative integer")
        object.__setattr__(self, "inputs", tuple(self.inputs))
        if not all(isinstance(item, DriverProgressInput) for item in self.inputs):
            raise TypeError("inputs must contain DriverProgressInput values")
        if len({item.driver_id for item in self.inputs}) != len(self.inputs):
            raise ValueError("driver IDs must be unique")


@dataclass(frozen=True)
class BatchRankingFrame:
    """A lightweight immutable ranking snapshot with no reducer history."""

    session_time_ms: int
    drivers: tuple[DriverRanking, ...]
    leaderboard_order: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.session_time_ms) is not int or self.session_time_ms < 0:
            raise ValueError("session_time_ms must be a non-negative integer")
        object.__setattr__(self, "drivers", tuple(self.drivers))
        object.__setattr__(self, "leaderboard_order", tuple(self.leaderboard_order))
        known = tuple(entry for entry in self.drivers if entry.effective_progress_meters is not None)
        ordered = tuple(sorted(known, key=_position_key))
        if (
            len({entry.driver_id for entry in self.drivers}) != len(self.drivers)
            or len(set(self.leaderboard_order)) != len(self.leaderboard_order)
            or tuple(entry.driver_id for entry in ordered) != self.leaderboard_order
            or tuple(entry.position for entry in ordered) != tuple(range(1, len(known) + 1))
        ):
            raise ValueError("batch frame requires unique, consecutive rankings")


def rank_drivers(
    state: RankingState, *, session_time_ms: int, inputs: tuple[DriverProgressInput, ...],
) -> RankingResult:
    """Reduce one shared-time driver batch into immutable rank and leader-gap values."""
    normalized = _validate_batch(state, session_time_ms, inputs)
    histories = {history.driver_id: history for history in state.histories}
    effective = tuple(_effective_input(item, histories.get(item.driver_id), session_time_ms) for item in normalized)
    next_histories = _next_histories(histories, effective)
    order = _leaderboard_order(effective, state.previous_order)
    rankings = _rankings(effective, order, next_histories, session_time_ms)
    next_state = RankingState(session_time_ms, order, tuple(next_histories.values()))
    return RankingResult(next_state, rankings, order)


def rank_timeline(frames: tuple[RankingTimelineFrame, ...]) -> tuple[BatchRankingFrame, ...]:
    """Rank an ordered immutable timeline in one pass without retaining snapshots' histories.

    Local mutable histories make this a pure linear-history transformation: only
    frozen ranking values leave the function.
    """
    _validate_timeline(frames)
    histories: dict[str, tuple[list[int], list[float]]] = {}
    previous_order: tuple[str, ...] = ()
    results: list[BatchRankingFrame] = []
    for frame in frames:
        effective = _batch_effective(frame, histories)
        order = _batch_order(effective, previous_order)
        results.append(_batch_result(frame.session_time_ms, effective, order, histories))
        previous_order = order
    return tuple(results)


def _validate_timeline(frames: tuple[RankingTimelineFrame, ...]) -> None:
    if not isinstance(frames, tuple) or not all(isinstance(frame, RankingTimelineFrame) for frame in frames):
        raise TypeError("frames must contain RankingTimelineFrame values")
    if any(current.session_time_ms < previous.session_time_ms for previous, current in pairwise(frames)):
        raise ValueError("frame session times must be non-regressing")


def _batch_effective(frame, histories):
    effective = []
    for item in sorted(frame.inputs, key=lambda value: value.driver_id):
        if item.race_progress_meters is None:
            effective.append((item, None))
            continue
        times, progress_values = histories.setdefault(item.driver_id, ([], []))
        progress = item.race_progress_meters if not progress_values else max(progress_values[-1], item.race_progress_meters)
        if not progress_values or progress > progress_values[-1]:
            if times and times[-1] == frame.session_time_ms:
                progress_values[-1] = progress
            else:
                times.append(frame.session_time_ms)
                progress_values.append(progress)
        effective.append((item, progress))
    return effective


def _batch_order(effective, previous_order):
    previous_index = {driver_id: index for index, driver_id in enumerate(previous_order)}
    return tuple(item.driver_id for item, progress in sorted(
        ((item, progress) for item, progress in effective if progress is not None),
        key=lambda value: (-value[1], previous_index.get(value[0].driver_id, len(previous_index)), value[0].driver_id),
    ))


def _batch_result(time_ms, effective, order, histories):
    by_id = {item.driver_id: (item, progress) for item, progress in effective}
    positions = {driver_id: index + 1 for index, driver_id in enumerate(order)}
    leader_id = order[0] if order else None
    leader_progress = None if leader_id is None else by_id[leader_id][1]
    drivers = tuple(
        DriverRanking(
            driver_id,
            item.mode,
            progress,
            positions.get(driver_id),
            _batch_gap(driver_id, progress, leader_id, leader_progress, histories, time_ms),
        )
        for driver_id, (item, progress) in sorted(by_id.items())
    )
    return BatchRankingFrame(time_ms, drivers, order)


def _batch_gap(driver_id, progress, leader_id, leader_progress, histories, time_ms):
    if progress is None or leader_id is None:
        return None
    if driver_id == leader_id or progress == leader_progress:
        return 0.0
    times, progress_values = histories[leader_id]
    index = bisect_left(progress_values, progress)
    if index == len(progress_values) or index == 0:
        return None
    if progress_values[index] == progress:
        crossing = float(times[index])
    else:
        crossing = times[index - 1] + (progress - progress_values[index - 1]) * (times[index] - times[index - 1]) / (progress_values[index] - progress_values[index - 1])
    return max(0.0, float(time_ms) - crossing)


def _validate_batch(state, time_ms, inputs):
    if not isinstance(state, RankingState):
        raise TypeError("state must be a RankingState")
    if type(time_ms) is not int or time_ms < 0 or (state.last_session_time_ms is not None and time_ms < state.last_session_time_ms):
        raise ValueError("session_time_ms must be a non-regressing non-negative integer")
    if not isinstance(inputs, tuple) or not all(isinstance(item, DriverProgressInput) for item in inputs):
        raise TypeError("inputs must contain DriverProgressInput values")
    normalized = tuple(sorted(inputs, key=lambda item: item.driver_id))
    if len({item.driver_id for item in normalized}) != len(normalized):
        raise ValueError("driver IDs must be unique")
    return normalized


def _effective_input(item, history, time_ms):
    if item.race_progress_meters is None:
        return item, None
    prior = None if history is None or not history.points else history.points[-1][1]
    progress = item.race_progress_meters if prior is None else max(prior, item.race_progress_meters)
    return item, (time_ms, progress)


def _next_histories(histories, effective):
    next_histories = dict(histories)
    for item, point in effective:
        if point is None:
            continue
        prior = next_histories.get(item.driver_id)
        points = () if prior is None else prior.points
        if not points or point[1] > points[-1][1]:
            updated = points[:-1] + (point,) if points and point[0] == points[-1][0] else points + (point,)
            next_histories[item.driver_id] = DriverHistory(item.driver_id, updated)
    return next_histories


def _leaderboard_order(effective, previous_order):
    previous_index = {driver_id: index for index, driver_id in enumerate(previous_order)}
    known = [(item, point) for item, point in effective if point is not None]
    return tuple(item.driver_id for item, point in sorted(
        known, key=lambda value: (-value[1][1], previous_index.get(value[0].driver_id, len(previous_index)), value[0].driver_id),
    ))


def _rankings(effective, order, histories, time_ms):
    by_id = {item.driver_id: (item, point) for item, point in effective}
    leader_id = order[0] if order else None
    leader_progress = None if leader_id is None else by_id[leader_id][1][1]
    entries = []
    for driver_id in sorted(by_id):
        item, point = by_id[driver_id]
        progress = None if point is None else point[1]
        position = None if progress is None else order.index(driver_id) + 1
        gap = _gap(driver_id, progress, leader_id, leader_progress, histories, time_ms)
        entries.append(DriverRanking(driver_id, item.mode, progress, position, gap))
    return tuple(entries)


def _gap(driver_id, progress, leader_id, leader_progress, histories, time_ms):
    if progress is None or leader_id is None:
        return None
    if driver_id == leader_id or progress == leader_progress:
        return 0.0
    crossing = _crossing_time(histories[leader_id].points, progress)
    return None if crossing is None else max(0.0, float(time_ms) - crossing)


def _crossing_time(points, target):
    for index, (time_ms, progress) in enumerate(points):
        if progress < target:
            continue
        if progress == target or index == 0:
            return float(time_ms) if progress == target else None
        prior_time, prior_progress = points[index - 1]
        return prior_time + (target - prior_progress) * (time_ms - prior_time) / (progress - prior_progress)
    return None


def _validate_history(history):
    if not isinstance(history.driver_id, str) or not history.driver_id:
        raise ValueError("history driver_id must be non-empty")
    if any(type(time_ms) is not int or time_ms < 0 or not _finite(progress) or progress < 0 for time_ms, progress in history.points):
        raise ValueError("history points must contain non-negative finite progress and time")
    if any(current[0] <= previous[0] or current[1] <= previous[1] for previous, current in pairwise(history.points)):
        raise ValueError("history points must increase in time and progress")


def _finite(value):
    return type(value) in (int, float) and math.isfinite(float(value))


def _position_key(entry):
    assert entry.position is not None
    return entry.position


__all__ = [
    "BatchRankingFrame", "DriverHistory", "DriverProgressInput", "DriverRanking", "RankingResult",
    "RankingState", "RankingTimelineFrame", "rank_drivers", "rank_timeline",
]
