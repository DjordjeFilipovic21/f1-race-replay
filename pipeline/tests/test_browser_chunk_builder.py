"""Focused behavior tests for native-timestamp browser chunk construction."""

from __future__ import annotations

import pytest

from f1_replay_pipeline.browser_chunk_builder import (
    CONTINUOUS_FIELD_SEMANTICS,
    PREVIOUS_VALUE_FIELD_SEMANTICS,
    BrowserEvent,
    BrowserGlobalFields,
    build_browser_chunks,
)
from f1_replay_pipeline.browser_delivery_models import MAX_INT64, BrowserDriverFields


def test_builder_preserves_null_observations_and_aligns_every_column() -> None:
    chunks = build_browser_chunks(_drivers(), _globals(), (), start_ms=0, end_ms=2_000,
                                  chunk_duration_ms=2_000, overlap_ms=500)

    chunk = chunks[0]

    assert chunk.time_ms == (0, 500, 1_000, 1_500)
    assert chunk.drivers["HAM"].x == (1.0, None, 3.0, None)
    assert chunk.drivers["RUS"].x == (None, 2.0, None, None)
    assert all(len(values) == len(chunk.time_ms) for values in (
        chunk.leaderboard_order, chunk.track_status_code, chunk.weather_state,
        *(
            column
            for fields in chunk.drivers.values()
            for column in _columns(fields)
        ),
    ))


def test_later_chunk_has_only_pre_start_overlap_and_half_open_ownership() -> None:
    chunks = build_browser_chunks(_drivers(), _globals(), (), start_ms=0, end_ms=4_000,
                                  chunk_duration_ms=2_000, overlap_ms=500)

    later = chunks[1]

    assert (later.time_ms, later.authoritative_start_index) == ((1_500, 2_000, 2_500, 3_000), 1)
    assert later.overlap.previous_chunk_path == "chunks/chunk-001.json"
    assert (later.overlap.range_start_ms, later.overlap.range_end_ms, later.overlap.authoritative_from_ms) == (1_500, 2_000, 2_000)


def test_events_are_sparse_and_owned_by_exactly_one_half_open_interval() -> None:
    events = (BrowserEvent(1_999, "notice", "before handoff"), BrowserEvent(2_000, "notice", "at handoff"))
    chunks = build_browser_chunks(_drivers(), _globals(), events, start_ms=0, end_ms=4_000,
                                  chunk_duration_ms=2_000, overlap_ms=500)

    assert tuple(event.session_time_ms for event in chunks[0].events) == (1_999,)
    assert tuple(event.session_time_ms for event in chunks[1].events) == (2_000,)


def test_ids_and_field_semantics_follow_the_frozen_delivery_policy() -> None:
    chunks = build_browser_chunks(_drivers(), _globals(), (), start_ms=0, end_ms=4_000,
                                  chunk_duration_ms=2_000, overlap_ms=500)

    assert tuple((chunk.chunk_id, chunk.sequence) for chunk in chunks) == (("chunk-001", 1), ("chunk-002", 2))
    assert CONTINUOUS_FIELD_SEMANTICS == {"x": "linear", "y": "linear", "speed": "linear", "throttle": "linear", "brake": "linear", "gapToLeaderMs": "linear"}
    assert set(PREVIOUS_VALUE_FIELD_SEMANTICS.values()) == {"previous"}


def test_builder_rejects_a_chunk_without_an_authoritative_observation() -> None:
    empty_driver = _driver("HAM", (), ())
    empty_globals = BrowserGlobalFields((), (), (), ())

    with pytest.raises(ValueError, match="authoritative observation"):
        build_browser_chunks({"HAM": empty_driver}, empty_globals, (), start_ms=0, end_ms=1_000)


def test_public_time_models_reject_values_above_signed_int64() -> None:
    with pytest.raises(ValueError, match="Int64"):
        BrowserEvent(MAX_INT64 + 1, "notice", "invalid time")
    with pytest.raises(ValueError, match="time_ms"):
        BrowserGlobalFields((MAX_INT64 + 1,), (("HAM",),), (1,), ("clear",))


def _drivers() -> dict[str, BrowserDriverFields]:
    return {
        "HAM": _driver("HAM", (0, 1_000, 1_500), (1.0, 3.0, None)),
        "RUS": _driver("RUS", (500, 2_000, 2_500, 3_000), (2.0, None, 4.0, 5.0)),
    }


def _driver(driver_id: str, time_ms: tuple[int, ...], x: tuple[float | None, ...]) -> BrowserDriverFields:
    count = len(time_ms)
    return BrowserDriverFields(
        driver_id=driver_id, time_ms=time_ms, x=x, y=(None,) * count,
        speed=(None,) * count, throttle=(None,) * count, brake=(None,) * count,
        gear=(None,) * count, drs=(None,) * count, status=(None,) * count,
        lap=(None,) * count, tyre_compound=(None,) * count,
        is_in_pit_lane=(None,) * count, track_distance_meters=(None,) * count,
        gap_to_leader_ms=(None,) * count, position=(None,) * count,
    )


def _globals() -> BrowserGlobalFields:
    return BrowserGlobalFields((0, 1_500, 2_000), (("HAM", "RUS"), None, ("RUS", "HAM")), (1, None, 4), ("clear", None, "rain"))


def _columns(fields: BrowserDriverFields) -> tuple[tuple[object, ...], ...]:
    return (
        fields.x, fields.y, fields.speed, fields.throttle, fields.brake, fields.gear, fields.drs,
        fields.status, fields.lap, fields.tyre_compound, fields.is_in_pit_lane,
        fields.track_distance_meters, fields.gap_to_leader_ms, fields.position,
    )
