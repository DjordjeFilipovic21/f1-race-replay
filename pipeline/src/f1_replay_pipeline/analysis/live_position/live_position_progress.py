"""Pure state transitions for calibrated lap-local race progress."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum

from f1_replay_pipeline.analysis.live_position.live_position_projection import (
    GEOMETRIC_WRAP_POLICY_VERSION,
    PROJECTION_QUALITY_GATE_VERSION,
    CenterlineProjection,
)


STALE_PROJECTION_MS = 1_000
MAX_BACKWARD_PROGRESS_M = 200.0
FINAL_TRACK_REGION_RATIO = 0.90
INITIAL_TRACK_REGION_RATIO = 0.10
MIN_GEOMETRIC_WRAP_DECREASE_RATIO = 0.80


class ProgressMode(str, Enum):
    ACTIVE = "active"
    PIT = "pit"
    FINISHED = "finished"
    RETIRED = "retired"
    OUT = "out"


class ProgressReason(str, Enum):
    ACTIVE = "active"
    MISSING_PROJECTION = "missing_projection"
    STALE_PROJECTION = "stale_projection"
    PIT_FROZEN = "pit_frozen"
    FINISHED_FROZEN = "finished_frozen"
    TERMINAL_FROZEN = "terminal_frozen"
    LAP_REGRESSION = "lap_regression"
    INVALID_LAP_TRANSITION = "invalid_lap_transition"
    INVALID_WRAP = "invalid_wrap"
    MULTIPLE_WRAP = "multiple_wrap"
    BACKWARD_PROGRESS = "backward_progress"


@dataclass(frozen=True)
class ProgressState:
    last_session_time_ms: int | None = None
    last_lap_number: int | None = None
    last_track_distance_meters: float | None = None
    last_ranking_distance_meters: float | None = None
    last_valid_progress_meters: float | None = None
    last_valid_time_ms: int | None = None
    cut_crossing_count: int = 0
    terminal_mode: ProgressMode | None = None
    failure_reason: ProgressReason | None = None
    finished: bool = False

    def __post_init__(self) -> None:
        if self.last_session_time_ms is not None and (type(self.last_session_time_ms) is not int or self.last_session_time_ms < 0):
            raise ValueError("last_session_time_ms must be a non-negative integer or None")
        if self.last_lap_number is not None and (type(self.last_lap_number) is not int or self.last_lap_number < 1):
            raise ValueError("last_lap_number must be a positive integer or None")
        if self.last_valid_time_ms is not None and (type(self.last_valid_time_ms) is not int or self.last_valid_time_ms < 0):
            raise ValueError("last_valid_time_ms must be a non-negative integer or None")
        if (self.last_valid_progress_meters is None) != (self.last_valid_time_ms is None):
            raise ValueError("last valid progress and time must be present together")
        if any(value is not None and not _finite(value) for value in (
            self.last_track_distance_meters, self.last_ranking_distance_meters,
            self.last_valid_progress_meters,
        )):
            raise ValueError("stored progress values must be finite or None")
        if any(value is not None and value < 0 for value in (
            self.last_track_distance_meters, self.last_ranking_distance_meters,
            self.last_valid_progress_meters,
        )):
            raise ValueError("stored progress values must be non-negative or None")
        if type(self.cut_crossing_count) is not int or self.cut_crossing_count < 0:
            raise ValueError("cut_crossing_count must be a non-negative integer")
        if (self.last_track_distance_meters is None) != (self.last_ranking_distance_meters is None):
            raise ValueError("track and ranking distances must be present together")
        if self.terminal_mode is not None and self.terminal_mode not in (ProgressMode.RETIRED, ProgressMode.OUT):
            raise ValueError("terminal_mode must be retired, out, or None")
        if self.failure_reason is not None and not isinstance(self.failure_reason, ProgressReason):
            raise TypeError("failure_reason must be ProgressReason or None")
        if type(self.finished) is not bool:
            raise TypeError("finished must be a boolean")


@dataclass(frozen=True)
class ProgressUpdate:
    state: ProgressState
    mode: ProgressMode
    track_distance_meters: float | None
    race_progress_meters: float | None
    is_frozen: bool
    is_terminal: bool
    reason: ProgressReason

    def __post_init__(self) -> None:
        if not isinstance(self.state, ProgressState) or not isinstance(self.mode, ProgressMode):
            raise TypeError("state and mode must be progress values")
        if not isinstance(self.reason, ProgressReason):
            raise TypeError("reason must be a ProgressReason")
        if any(value is not None and (not _finite(value) or value < 0) for value in (self.track_distance_meters, self.race_progress_meters)):
            raise ValueError("public progress values must be finite, non-negative, or None")
        if type(self.is_frozen) is not bool or type(self.is_terminal) is not bool:
            raise TypeError("frozen and terminal flags must be booleans")


def advance_progress(
    state: ProgressState, *, session_time_ms: int, lap_number: int, circuit_length_meters: float,
    projection: CenterlineProjection | None, mode: ProgressMode,
) -> ProgressUpdate:
    """Reduce one caller-classified observation into immutable live-progress state."""
    _validate_input(state, session_time_ms, lap_number, circuit_length_meters, projection, mode)
    if state.terminal_mode is not None:
        return _terminal(state, state.terminal_mode)
    if state.finished:
        return _finished(state, ProgressMode.FINISHED)
    if mode in (ProgressMode.RETIRED, ProgressMode.OUT):
        terminal = _replace(state, last_session_time_ms=session_time_ms, terminal_mode=mode)
        return _terminal(terminal, mode)
    if mode is ProgressMode.FINISHED:
        return _finish(state, session_time_ms, lap_number, circuit_length_meters, projection)
    if mode is ProgressMode.PIT:
        return _pit(_replace(state, last_session_time_ms=session_time_ms), mode)
    if projection is None:
        return _missing(_replace(state, last_session_time_ms=session_time_ms), mode, session_time_ms)
    return _active(state, session_time_ms, lap_number, circuit_length_meters, projection, mode)


def seed_progress(
    *, session_time_ms: int, lap_number: int, circuit_length_meters: float,
    projection: CenterlineProjection, cut_crossing_count: int,
    mode: ProgressMode = ProgressMode.ACTIVE,
) -> ProgressUpdate:
    """Create one validated progress epoch without coupling it to the visual start line."""
    if not isinstance(projection, CenterlineProjection):
        raise TypeError("a progress seed requires a CenterlineProjection")
    _validate_input(
        ProgressState(), session_time_ms, lap_number, circuit_length_meters, projection, mode,
    )
    if (
        type(cut_crossing_count) is not int
        or cut_crossing_count not in (lap_number - 1, lap_number)
    ):
        raise ValueError("cut_crossing_count must be compatible with lap_number")
    if mode is not ProgressMode.ACTIVE:
        raise ValueError("a progress seed must be active")
    distance = _projection_distance(projection, circuit_length_meters)
    ranking_distance_meters = ranking_distance(distance, circuit_length_meters)
    progress = cut_crossing_count * circuit_length_meters + ranking_distance_meters
    state = ProgressState(
        last_session_time_ms=session_time_ms,
        last_lap_number=lap_number,
        last_track_distance_meters=distance,
        last_ranking_distance_meters=ranking_distance_meters,
        last_valid_progress_meters=progress,
        last_valid_time_ms=session_time_ms,
        cut_crossing_count=cut_crossing_count,
    )
    return ProgressUpdate(
        state, mode, distance, progress, False, False, ProgressReason.ACTIVE,
    )


def _active(state, time_ms, lap, length, projection, mode):
    distance = _projection_distance(projection, length)
    ranking_distance_meters = ranking_distance(distance, length)
    transition = _lap_transition(state, lap)
    if transition is not None:
        return _fail(state, mode, transition)
    track_reason = _track_transition_reason(state, distance, length)
    if track_reason is not None:
        return _fail(state, mode, track_reason)
    if state.last_ranking_distance_meters is None:
        return seed_progress(
            session_time_ms=time_ms,
            lap_number=lap,
            circuit_length_meters=length,
            projection=projection,
            cut_crossing_count=_initial_cut_crossing_count(lap, distance, length),
            mode=mode,
        )
    crossings, reason = _cut_crossings(state, lap, ranking_distance_meters, length)
    if reason is not None:
        return _fail(state, mode, reason)
    progress = crossings * length + ranking_distance_meters
    if state.last_valid_progress_meters is not None and progress < state.last_valid_progress_meters - MAX_BACKWARD_PROGRESS_M:
        return _fail(state, mode, ProgressReason.BACKWARD_PROGRESS)
    if state.last_valid_progress_meters is not None:
        progress = max(state.last_valid_progress_meters, progress)
    next_state = ProgressState(
        last_session_time_ms=time_ms,
        last_lap_number=lap,
        last_track_distance_meters=distance,
        last_ranking_distance_meters=ranking_distance_meters,
        last_valid_progress_meters=progress,
        last_valid_time_ms=time_ms,
        cut_crossing_count=crossings,
    )
    return ProgressUpdate(next_state, mode, distance, progress, False, False, ProgressReason.ACTIVE)


def _lap_transition(state, lap):
    if state.last_lap_number is None or lap == state.last_lap_number:
        return None
    if lap < state.last_lap_number:
        return ProgressReason.LAP_REGRESSION
    if lap != state.last_lap_number + 1:
        return ProgressReason.INVALID_LAP_TRANSITION
    return None


def _track_transition_reason(state, distance, length):
    previous = state.last_track_distance_meters
    if previous is None or previous - distance <= MAX_BACKWARD_PROGRESS_M:
        return None
    if _is_geometric_wrap(previous, distance, length):
        return None
    return ProgressReason.INVALID_WRAP


def _cut_crossings(state, lap, ranking_distance, length):
    previous = state.last_ranking_distance_meters
    assert previous is not None
    decrease = previous - ranking_distance
    if decrease <= MAX_BACKWARD_PROGRESS_M:
        return state.cut_crossing_count, None
    if not _is_geometric_wrap(previous, ranking_distance, length):
        return state.cut_crossing_count, ProgressReason.INVALID_WRAP
    crossings = state.cut_crossing_count + 1
    if crossings > lap:
        return state.cut_crossing_count, ProgressReason.MULTIPLE_WRAP
    return crossings, None


def _initial_cut_crossing_count(lap, distance, length):
    return lap - 1 + int(distance >= length / 2.0)


def ranking_distance(distance: float, length: float) -> float:
    """Rotate a lap-local coordinate so its cut lies opposite the visual start line."""
    if not _finite(length) or length <= 0:
        raise ValueError("circuit length must be positive and finite")
    if not _finite(distance) or not 0.0 <= distance < length:
        raise ValueError("track distance must be finite and lap-local")
    shifted = distance + length / 2.0
    return shifted - length if shifted >= length else shifted


def _is_geometric_wrap(previous, current, length):
    return (
        previous >= length * FINAL_TRACK_REGION_RATIO
        and current <= length * INITIAL_TRACK_REGION_RATIO
        and previous - current >= length * MIN_GEOMETRIC_WRAP_DECREASE_RATIO
    )


def _missing(state, mode, time_ms):
    if state.last_valid_progress_meters is None:
        return _unknown(state, mode, ProgressReason.MISSING_PROJECTION)
    age = time_ms - state.last_valid_time_ms
    if age < STALE_PROJECTION_MS:
        return ProgressUpdate(state, mode, None, state.last_valid_progress_meters, True, False, ProgressReason.MISSING_PROJECTION)
    return _unknown(state, mode, ProgressReason.STALE_PROJECTION)


def _pit(state, mode):
    if state.last_valid_progress_meters is None:
        return _unknown(state, mode, ProgressReason.MISSING_PROJECTION)
    return ProgressUpdate(state, mode, None, state.last_valid_progress_meters, True, False, ProgressReason.PIT_FROZEN)


def _finish(state, time_ms, lap, length, projection):
    if projection is None:
        return _finished(_replace(state, last_session_time_ms=time_ms, finished=True), ProgressMode.FINISHED)
    candidate = _active(state, time_ms, lap, length, projection, ProgressMode.FINISHED)
    if candidate.race_progress_meters is None:
        return _finished(_replace(state, last_session_time_ms=time_ms, finished=True), ProgressMode.FINISHED)
    if (
        state.last_valid_progress_meters is not None
        and candidate.race_progress_meters < state.last_valid_progress_meters
    ):
        return _finished(_replace(state, last_session_time_ms=time_ms, finished=True), ProgressMode.FINISHED)
    finished_state = _replace(candidate.state, finished=True)
    return ProgressUpdate(
        finished_state, ProgressMode.FINISHED, candidate.track_distance_meters,
        candidate.race_progress_meters, True, False, ProgressReason.FINISHED_FROZEN,
    )


def _finished(state, mode):
    return ProgressUpdate(
        state, mode, None, state.last_valid_progress_meters, True, False,
        ProgressReason.FINISHED_FROZEN,
    )


def _terminal(state, mode):
    return ProgressUpdate(state, mode, None, state.last_valid_progress_meters, True, True, ProgressReason.TERMINAL_FROZEN)


def _fail(state, mode, reason):
    return _unknown(_replace(state, failure_reason=reason), mode, reason)


def _unknown(state, mode, reason):
    return ProgressUpdate(state, mode, None, None, False, False, reason)


def _replace(state, **changes):
    return replace(state, **changes)


def _projection_distance(projection, length):
    distance = projection.track_distance_meters
    if not _finite(distance) or not 0.0 <= distance < length:
        raise ValueError("projection track distance must be finite and lap-local")
    return distance


def _validate_input(state, time_ms, lap, length, projection, mode):
    if not isinstance(state, ProgressState):
        raise TypeError("state must be ProgressState")
    if type(time_ms) is not int or time_ms < 0 or (state.last_session_time_ms is not None and time_ms < state.last_session_time_ms):
        raise ValueError("session_time_ms must be a non-regressing non-negative integer")
    if type(lap) is not int or lap < 1:
        raise ValueError("lap_number must be a positive integer")
    if not _finite(length) or length <= 0:
        raise ValueError("circuit_length_meters must be positive and finite")
    if projection is not None and not isinstance(projection, CenterlineProjection):
        raise TypeError("projection must be CenterlineProjection or None")
    if not isinstance(mode, ProgressMode):
        raise ValueError("mode must be a ProgressMode")


def _finite(value):
    return type(value) in (int, float) and math.isfinite(float(value))


__all__ = [
    "FINAL_TRACK_REGION_RATIO", "GEOMETRIC_WRAP_POLICY_VERSION", "INITIAL_TRACK_REGION_RATIO",
    "MAX_BACKWARD_PROGRESS_M", "MIN_GEOMETRIC_WRAP_DECREASE_RATIO", "PROJECTION_QUALITY_GATE_VERSION",
    "ProgressMode", "ProgressReason", "ProgressState", "ProgressUpdate", "STALE_PROJECTION_MS",
    "advance_progress", "ranking_distance", "seed_progress",
]
