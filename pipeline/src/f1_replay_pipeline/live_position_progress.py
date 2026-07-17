"""Pure state transitions for calibrated lap-local race progress."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum

from f1_replay_pipeline.live_position_projection import (
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
    RETIRED = "retired"
    OUT = "out"


class ProgressReason(str, Enum):
    ACTIVE = "active"
    MISSING_PROJECTION = "missing_projection"
    STALE_PROJECTION = "stale_projection"
    PIT_FROZEN = "pit_frozen"
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
    last_valid_progress_meters: float | None = None
    last_valid_time_ms: int | None = None
    within_lap_wrap_count: int = 0
    within_lap_offset_meters: float = 0.0
    terminal_mode: ProgressMode | None = None
    failure_reason: ProgressReason | None = None

    def __post_init__(self) -> None:
        if self.last_session_time_ms is not None and (type(self.last_session_time_ms) is not int or self.last_session_time_ms < 0):
            raise ValueError("last_session_time_ms must be a non-negative integer or None")
        if self.last_lap_number is not None and (type(self.last_lap_number) is not int or self.last_lap_number < 1):
            raise ValueError("last_lap_number must be a positive integer or None")
        if self.last_valid_time_ms is not None and (type(self.last_valid_time_ms) is not int or self.last_valid_time_ms < 0):
            raise ValueError("last_valid_time_ms must be a non-negative integer or None")
        if (self.last_valid_progress_meters is None) != (self.last_valid_time_ms is None):
            raise ValueError("last valid progress and time must be present together")
        if any(value is not None and not _finite(value) for value in (self.last_track_distance_meters, self.last_valid_progress_meters)):
            raise ValueError("stored progress values must be finite or None")
        if any(value is not None and value < 0 for value in (self.last_track_distance_meters, self.last_valid_progress_meters)):
            raise ValueError("stored progress values must be non-negative or None")
        if type(self.within_lap_wrap_count) is not int or self.within_lap_wrap_count not in (0, 1):
            raise ValueError("within_lap_wrap_count must be zero or one")
        if not _finite(self.within_lap_offset_meters) or self.within_lap_offset_meters < 0:
            raise ValueError("within_lap_offset_meters must be finite and non-negative")
        if (self.within_lap_wrap_count == 0) != (self.within_lap_offset_meters == 0.0):
            raise ValueError("within-lap wrap count and offset must agree")
        if self.terminal_mode is not None and self.terminal_mode not in (ProgressMode.RETIRED, ProgressMode.OUT):
            raise ValueError("terminal_mode must be retired, out, or None")
        if self.failure_reason is not None and not isinstance(self.failure_reason, ProgressReason):
            raise TypeError("failure_reason must be ProgressReason or None")


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
    if mode in (ProgressMode.RETIRED, ProgressMode.OUT):
        terminal = _replace(state, last_session_time_ms=session_time_ms, terminal_mode=mode)
        return _terminal(terminal, mode)
    if mode is ProgressMode.PIT:
        return _pit(_replace(state, last_session_time_ms=session_time_ms), mode)
    if projection is None:
        return _missing(_replace(state, last_session_time_ms=session_time_ms), mode, session_time_ms)
    return _active(state, session_time_ms, lap_number, circuit_length_meters, projection, mode)


def _active(state, time_ms, lap, length, projection, mode):
    distance = _projection_distance(projection, length)
    transition = _lap_transition(state, lap)
    if transition is not None:
        return _fail(state, mode, transition)
    offset, wraps, reason = _within_lap_offset(state, lap, distance, length)
    if reason is not None:
        return _fail(state, mode, reason)
    progress = (lap - 1) * length + distance + offset
    if _is_official_lap_increment(state, lap) and _regresses_beyond_tolerance(state, progress):
        if _has_one_circuit_within_lap_wrap(state, length):
            offset, wraps = length, 1
            progress += length
        else:
            return _fail(state, mode, ProgressReason.INVALID_LAP_TRANSITION)
    if state.last_valid_progress_meters is not None and progress < state.last_valid_progress_meters - MAX_BACKWARD_PROGRESS_M:
        return _fail(state, mode, ProgressReason.BACKWARD_PROGRESS)
    next_state = ProgressState(time_ms, lap, distance, progress, time_ms, wraps, offset)
    return ProgressUpdate(next_state, mode, distance, progress, False, False, ProgressReason.ACTIVE)


def _is_official_lap_increment(state, lap):
    return state.last_lap_number is not None and lap == state.last_lap_number + 1


def _regresses_beyond_tolerance(state, progress):
    return (
        state.last_valid_progress_meters is not None
        and progress < state.last_valid_progress_meters - MAX_BACKWARD_PROGRESS_M
    )


def _has_one_circuit_within_lap_wrap(state, length):
    return (
        state.within_lap_wrap_count == 1
        and state.within_lap_offset_meters == length
    )


def _lap_transition(state, lap):
    if state.last_lap_number is None or lap == state.last_lap_number:
        return None
    if lap < state.last_lap_number:
        return ProgressReason.LAP_REGRESSION
    if lap != state.last_lap_number + 1:
        return ProgressReason.INVALID_LAP_TRANSITION
    return None


def _within_lap_offset(state, lap, distance, length):
    if state.last_lap_number != lap or state.last_track_distance_meters is None:
        return 0.0, 0, None
    previous = state.last_track_distance_meters
    decrease = previous - distance
    if decrease <= MAX_BACKWARD_PROGRESS_M:
        return state.within_lap_offset_meters, state.within_lap_wrap_count, None
    if not _is_geometric_wrap(previous, distance, length):
        return 0.0, 0, ProgressReason.INVALID_WRAP
    if state.within_lap_wrap_count:
        return 0.0, 0, ProgressReason.MULTIPLE_WRAP
    return length, 1, None


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
    "ProgressMode", "ProgressReason", "ProgressState", "ProgressUpdate", "STALE_PROJECTION_MS", "advance_progress",
]
