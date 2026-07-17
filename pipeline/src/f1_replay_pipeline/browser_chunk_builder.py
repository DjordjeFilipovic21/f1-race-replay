"""Construct immutable, native-timestamp browser delivery chunks."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TypeVar

from f1_replay_pipeline.browser_delivery_models import (
    MAX_INT64,
    BrowserDriverFields,
    deep_freeze_json,
)


FieldValue = TypeVar("FieldValue")


CHUNK_DURATION_MS = 10_000
HANDOFF_OVERLAP_MS = 1_000
CONTINUOUS_FIELD_SEMANTICS = MappingProxyType({
    "x": "linear", "y": "linear", "speed": "linear", "throttle": "linear",
    "brake": "linear", "gapToLeaderMs": "linear",
})
PREVIOUS_VALUE_FIELD_SEMANTICS = MappingProxyType({
    "lap": "previous", "position": "previous", "gear": "previous", "drs": "previous",
    "tyreCompound": "previous", "status": "previous", "isInPitLane": "previous",
    "trackStatusCode": "previous", "weatherState": "previous", "leaderboardOrder": "previous",
})


@dataclass(frozen=True)
class BrowserGlobalFields:
    """Exact-time global observations supplied to the chunk boundary."""

    time_ms: tuple[int, ...]
    leaderboard_order: tuple[tuple[str, ...] | None, ...]
    track_status_code: tuple[int | None, ...]
    weather_state: tuple[str | None, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "time_ms", tuple(self.time_ms))
        object.__setattr__(self, "leaderboard_order", tuple(
            None if value is None else tuple(value) for value in self.leaderboard_order
        ))
        object.__setattr__(self, "track_status_code", tuple(self.track_status_code))
        object.__setattr__(self, "weather_state", tuple(self.weather_state))
        _validate_timeline(self.time_ms)
        if any(not isinstance(values, tuple) for values in (
            self.leaderboard_order, self.track_status_code, self.weather_state,
        )):
            raise TypeError("global fields must be immutable tuples")
        if any(len(values) != len(self.time_ms) for values in (
            self.leaderboard_order, self.track_status_code, self.weather_state,
        )):
            raise ValueError("every global field must be aligned to time_ms")
        if any(value is not None and not isinstance(value, tuple) for value in self.leaderboard_order):
            raise TypeError("leaderboard entries must be immutable tuples or null")
        if any(value is not None and (not value or len(set(value)) != len(value) or any(not isinstance(driver_id, str) or not driver_id for driver_id in value)) for value in self.leaderboard_order):
            raise ValueError("leaderboard entries must be non-empty unique driver IDs or null")
        if any(value is not None and (type(value) is not int or value < 0) for value in self.track_status_code):
            raise TypeError("track status values must be non-negative integers or null")
        if any(value is not None and not isinstance(value, str) for value in self.weather_state):
            raise TypeError("weather values must be strings or null")


@dataclass(frozen=True)
class BrowserEvent:
    """A sparse point record; it is deliberately not part of ``time_ms``."""

    session_time_ms: int
    event_type: str
    description: str
    driver_id: str | None = None
    payload: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if type(self.session_time_ms) is not int or not 0 <= self.session_time_ms <= MAX_INT64:
            raise ValueError("session_time_ms must be a non-negative signed Int64 integer")
        if not self.event_type or not self.description:
            raise ValueError("event_type and description must be non-empty strings")
        if self.driver_id is not None and not isinstance(self.driver_id, str):
            raise TypeError("driver_id must be a string or null")
        if self.payload is not None:
            object.__setattr__(self, "payload", deep_freeze_json(self.payload))


@dataclass(frozen=True)
class BrowserOverlap:
    """The intentional reference region immediately before a chunk's authority."""

    kind: str
    previous_chunk_path: str | None
    range_start_ms: int | None
    range_end_ms: int | None
    authoritative_from_ms: int | None

    def __post_init__(self) -> None:
        if self.kind == "none":
            if any(value is not None for value in (
                self.previous_chunk_path, self.range_start_ms, self.range_end_ms,
                self.authoritative_from_ms,
            )):
                raise ValueError("a non-overlap must not contain handoff metadata")
            return
        if self.kind != "handoff" or not isinstance(self.previous_chunk_path, str):
            raise ValueError("overlap kind must be none or a complete handoff")
        values = (self.range_start_ms, self.range_end_ms, self.authoritative_from_ms)
        if any(type(value) is not int or not 0 <= value <= MAX_INT64 for value in values):
            raise ValueError("handoff times must be non-negative integers")
        start_ms, end_ms, authoritative_ms = values
        assert isinstance(start_ms, int) and isinstance(end_ms, int) and isinstance(authoritative_ms, int)
        if not start_ms < end_ms == authoritative_ms:
            raise ValueError("handoff range must end at its authoritative time")


@dataclass(frozen=True)
class BrowserChunk:
    """One immutable chunk with all arrays aligned to its shared timeline."""

    chunk_id: str
    sequence: int
    start_ms: int
    end_ms: int
    overlap: BrowserOverlap
    time_ms: tuple[int, ...]
    authoritative_start_index: int
    drivers: Mapping[str, BrowserDriverFields]
    leaderboard_order: tuple[tuple[str, ...] | None, ...]
    track_status_code: tuple[int | None, ...]
    weather_state: tuple[str | None, ...]
    events: tuple[BrowserEvent, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "time_ms", tuple(self.time_ms))
        object.__setattr__(self, "leaderboard_order", tuple(
            None if value is None else tuple(value) for value in self.leaderboard_order
        ))
        object.__setattr__(self, "track_status_code", tuple(self.track_status_code))
        object.__setattr__(self, "weather_state", tuple(self.weather_state))
        object.__setattr__(self, "events", tuple(self.events))
        if self.chunk_id != f"chunk-{self.sequence:03d}" or self.sequence < 1:
            raise ValueError("chunk identity and sequence disagree")
        if type(self.start_ms) is not int or type(self.end_ms) is not int or not 0 <= self.start_ms < self.end_ms <= MAX_INT64:
            raise ValueError("chunk bounds must be a positive non-negative interval")
        _validate_timeline(self.time_ms)
        if not self.time_ms:
            raise ValueError("a browser chunk must contain an authoritative sample")
        if not 0 <= self.authoritative_start_index < len(self.time_ms):
            raise ValueError("authoritative_start_index must identify the first owned sample")
        if any(not self.start_ms <= time_ms < self.end_ms for time_ms in self.time_ms[self.authoritative_start_index:]):
            raise ValueError("authoritative samples must be in the chunk interval")
        aligned = (self.leaderboard_order, self.track_status_code, self.weather_state)
        if any(len(values) != len(self.time_ms) for values in aligned):
            raise ValueError("global fields must be aligned to time_ms")
        if any(fields.time_ms != self.time_ms for fields in self.drivers.values()):
            raise ValueError("driver fields must be aligned to the shared time_ms")
        if any(driver_id != fields.driver_id for driver_id, fields in self.drivers.items()):
            raise ValueError("driver keys must match driver_id")
        _validate_live_leaderboard_rows(self.drivers, self.leaderboard_order)
        if any(not time_ms < self.start_ms for time_ms in self.time_ms[:self.authoritative_start_index]):
            raise ValueError("overlap samples must precede chunk authority")
        if self.sequence == 1 and self.overlap.kind != "none":
            raise ValueError("the first chunk cannot have a handoff")
        if self.sequence > 1 and (
            self.overlap.kind != "handoff" or self.overlap.authoritative_from_ms != self.start_ms
        ):
            raise ValueError("later chunks require a handoff at start_ms")
        if any(not self.start_ms <= event.session_time_ms < self.end_ms for event in self.events):
            raise ValueError("events must belong to the authoritative half-open interval")
        object.__setattr__(self, "drivers", MappingProxyType(dict(sorted(self.drivers.items()))))


def _validate_live_leaderboard_rows(
    drivers: Mapping[str, BrowserDriverFields], leaderboard_order: tuple[tuple[str, ...] | None, ...],
) -> None:
    """Reject partial live rankings while retaining legacy null-only derived rows."""
    for index, order in enumerate(leaderboard_order):
        positioned = tuple(
            (driver_id, fields.position[index], fields.gap_to_leader_ms[index])
            for driver_id, fields in drivers.items()
            if fields.position[index] is not None
        )
        if not positioned:
            continue
        if order is None or tuple(driver_id for driver_id, _, _ in sorted(positioned, key=_position_index)) != order:
            raise ValueError("live leaderboard order must exactly match positioned drivers")
        positions = tuple(position for _, position, _ in positioned)
        if set(positions) != set(range(1, len(positioned) + 1)):
            raise ValueError("live positions must be unique consecutive values")
        leader_id = order[0]
        leader = next(item for item in positioned if item[0] == leader_id)
        if leader[1] != 1 or leader[2] != 0.0:
            raise ValueError("the live leaderboard leader must have position 1 and zero gap")


def _position_index(item: tuple[str, int | None, float | None]) -> int:
    position = item[1]
    assert position is not None
    return position


def build_browser_chunks(
    driver_fields: Mapping[str, BrowserDriverFields],
    global_fields: BrowserGlobalFields,
    events: Sequence[BrowserEvent],
    *,
    start_ms: int,
    end_ms: int,
    chunk_duration_ms: int = CHUNK_DURATION_MS,
    overlap_ms: int = HANDOFF_OVERLAP_MS,
) -> tuple[BrowserChunk, ...]:
    """Partition exact observations without resampling or interpolating source rows."""
    _validate_bounds(start_ms, end_ms, chunk_duration_ms, overlap_ms)
    _validate_drivers(driver_fields)
    ordered_events = tuple(sorted(events, key=_event_sort_key))
    timeline = _shared_timeline(driver_fields, global_fields)
    aligned_drivers = {
        driver_id: _align_driver(fields, timeline) for driver_id, fields in driver_fields.items()
    }
    aligned_globals = BrowserGlobalFields(
        timeline,
        _align(global_fields.time_ms, global_fields.leaderboard_order, timeline),
        _align(global_fields.time_ms, global_fields.track_status_code, timeline),
        _align(global_fields.time_ms, global_fields.weather_state, timeline),
    )
    intervals = _chunk_intervals(timeline, start_ms, end_ms, chunk_duration_ms)
    return tuple(
        _build_chunk(sequence, chunk_start, chunk_end, timeline,
                     aligned_drivers, aligned_globals, ordered_events, overlap_ms)
        for sequence, (chunk_start, chunk_end) in enumerate(intervals, start=1)
    )


def _build_chunk(
    sequence: int, start_ms: int, end_ms: int, timeline: tuple[int, ...],
    driver_fields: Mapping[str, BrowserDriverFields], global_fields: BrowserGlobalFields,
    events: tuple[BrowserEvent, ...], overlap_ms: int,
) -> BrowserChunk:
    overlap_start = start_ms if sequence == 1 else start_ms - overlap_ms
    left = bisect_left(timeline, overlap_start)
    authority = bisect_left(timeline, start_ms)
    right = bisect_left(timeline, end_ms)
    chunk_timeline = timeline[left:right]
    authoritative_start_index = authority - left
    if authoritative_start_index == len(chunk_timeline):
        raise ValueError("every chunk interval must contain an authoritative observation")
    return BrowserChunk(
        chunk_id=_chunk_id(sequence), sequence=sequence, start_ms=start_ms, end_ms=end_ms,
        overlap=_overlap(sequence, start_ms, overlap_start), time_ms=chunk_timeline,
        authoritative_start_index=authoritative_start_index,
        drivers={driver_id: _slice_driver(fields, left, right) for driver_id, fields in driver_fields.items()},
        leaderboard_order=global_fields.leaderboard_order[left:right],
        track_status_code=global_fields.track_status_code[left:right],
        weather_state=global_fields.weather_state[left:right],
        events=tuple(event for event in events if start_ms <= event.session_time_ms < end_ms),
    )


def _align_driver(fields: BrowserDriverFields, timeline: tuple[int, ...]) -> BrowserDriverFields:
    return BrowserDriverFields(
        driver_id=fields.driver_id, time_ms=timeline,
        x=_align(fields.time_ms, fields.x, timeline), y=_align(fields.time_ms, fields.y, timeline),
        speed=_align(fields.time_ms, fields.speed, timeline), throttle=_align(fields.time_ms, fields.throttle, timeline),
        brake=_align(fields.time_ms, fields.brake, timeline), gear=_align(fields.time_ms, fields.gear, timeline),
        drs=_align(fields.time_ms, fields.drs, timeline), status=_align(fields.time_ms, fields.status, timeline),
        lap=_align(fields.time_ms, fields.lap, timeline), tyre_compound=_align(fields.time_ms, fields.tyre_compound, timeline),
        is_in_pit_lane=_align(fields.time_ms, fields.is_in_pit_lane, timeline),
        track_distance_meters=_align(fields.time_ms, fields.track_distance_meters, timeline), gap_to_leader_ms=_align(fields.time_ms, fields.gap_to_leader_ms, timeline),
        position=_align(fields.time_ms, fields.position, timeline),
    )


def _slice_driver(fields: BrowserDriverFields, left: int, right: int) -> BrowserDriverFields:
    return BrowserDriverFields(
        driver_id=fields.driver_id, time_ms=fields.time_ms[left:right],
        x=fields.x[left:right], y=fields.y[left:right], speed=fields.speed[left:right],
        throttle=fields.throttle[left:right], brake=fields.brake[left:right],
        gear=fields.gear[left:right], drs=fields.drs[left:right], status=fields.status[left:right],
        lap=fields.lap[left:right], tyre_compound=fields.tyre_compound[left:right],
        is_in_pit_lane=fields.is_in_pit_lane[left:right],
        track_distance_meters=fields.track_distance_meters[left:right],
        gap_to_leader_ms=fields.gap_to_leader_ms[left:right], position=fields.position[left:right],
    )


def _align(
    source_times: tuple[int, ...], values: tuple[FieldValue, ...], timeline: tuple[int, ...],
) -> tuple[FieldValue | None, ...]:
    by_time = dict(zip(source_times, values, strict=True))
    return tuple(by_time.get(time_ms) for time_ms in timeline)


def _shared_timeline(
    driver_fields: Mapping[str, BrowserDriverFields], global_fields: BrowserGlobalFields,
) -> tuple[int, ...]:
    return tuple(sorted({time_ms for fields in driver_fields.values() for time_ms in fields.time_ms} | set(global_fields.time_ms)))


def _chunk_intervals(timeline, start_ms: int, end_ms: int, duration_ms: int):
    intervals: list[list[int]] = []
    for chunk_start in range(start_ms, end_ms, duration_ms):
        chunk_end = min(chunk_start + duration_ms, end_ms)
        has_authority = bisect_left(timeline, chunk_start) < bisect_left(timeline, chunk_end)
        if has_authority:
            intervals.append([chunk_start, chunk_end])
        elif intervals:
            intervals[-1][1] = chunk_end
        else:
            raise ValueError("delivery begins without an authoritative observation")
    return tuple((start, end) for start, end in intervals)


def _overlap(sequence: int, start_ms: int, overlap_start: int) -> BrowserOverlap:
    if sequence == 1:
        return BrowserOverlap("none", None, None, None, None)
    return BrowserOverlap("handoff", f"chunks/{_chunk_id(sequence - 1)}.json", overlap_start, start_ms, start_ms)


def _chunk_id(sequence: int) -> str:
    return f"chunk-{sequence:03d}"


def _event_sort_key(event: BrowserEvent) -> tuple[int, str, str, str]:
    return event.session_time_ms, event.event_type, event.driver_id or "", event.description


def _validate_timeline(time_ms: tuple[int, ...]) -> None:
    if tuple(sorted(set(time_ms))) != time_ms or any(type(value) is not int or not 0 <= value <= MAX_INT64 for value in time_ms):
        raise ValueError("time_ms must be sorted unique non-negative integer milliseconds")


def _validate_bounds(start_ms: int, end_ms: int, duration_ms: int, overlap_ms: int) -> None:
    if any(type(value) is not int for value in (start_ms, end_ms, duration_ms, overlap_ms)):
        raise TypeError("chunk boundaries and durations must be integers")
    if start_ms < 0 or end_ms <= start_ms or duration_ms <= 0 or not 0 <= overlap_ms < duration_ms:
        raise ValueError("chunk bounds require a positive interval and smaller non-negative overlap")


def _validate_drivers(driver_fields: Mapping[str, BrowserDriverFields]) -> None:
    if not driver_fields:
        raise ValueError("at least one driver is required")
    if any(driver_id != fields.driver_id for driver_id, fields in driver_fields.items()):
        raise ValueError("driver field keys must match their driver_id")


__all__ = [
    "BrowserChunk", "BrowserEvent", "BrowserGlobalFields", "BrowserOverlap", "CHUNK_DURATION_MS",
    "CONTINUOUS_FIELD_SEMANTICS", "HANDOFF_OVERLAP_MS", "PREVIOUS_VALUE_FIELD_SEMANTICS",
    "build_browser_chunks",
]
