from datetime import timedelta
from itertools import permutations

import polars as pl
import pytest

from f1_replay_pipeline.canonical_schema import TRACK_STATUS_INTERVALS_SCHEMA, WEATHER_SCHEMA
from f1_replay_pipeline.normalizers import NormalizationError
from f1_replay_pipeline.weather_status_adapter import adapt_track_status_intervals, adapt_weather


class FakeSession:
    def __init__(self, weather_data: object, track_status: object) -> None:
        self.weather_data = weather_data
        self.track_status = track_status


def test_adapt_weather_preserves_sparse_observations_orders_rows_and_deduplicates_deterministically():
    session = FakeSession(
        [
            {"Time": timedelta(minutes=2), "AirTemp": 25.0, "Rainfall": False},
            {"Time": timedelta(seconds=10), "AirTemp": 20.0, "Humidity": 70.0, "Rainfall": True},
            {"Time": timedelta(minutes=2), "AirTemp": 25.0, "Humidity": 60.0, "Rainfall": False},
        ],
        [],
    )

    frame = adapt_weather(session, "2026-03-race")

    assert list(frame.schema.items()) == list(WEATHER_SCHEMA.items())
    assert frame.to_dicts() == [
        {"session_id": "2026-03-race", "session_time_ms": 10_000, "air_temperature_c": 20.0,
         "humidity_pct": 70.0, "pressure_mbar": None, "rainfall": True, "track_temperature_c": None,
         "wind_direction_deg": None, "wind_speed_mps": None},
        {"session_id": "2026-03-race", "session_time_ms": 120_000, "air_temperature_c": 25.0,
         "humidity_pct": 60.0, "pressure_mbar": None, "rainfall": False, "track_temperature_c": None,
         "wind_direction_deg": None, "wind_speed_mps": None},
    ]


def test_adapt_track_status_intervals_uses_only_the_next_observed_start_as_end():
    session = FakeSession([], pl.DataFrame({"Time": [timedelta(minutes=5), timedelta(seconds=40), timedelta(minutes=2)],
                                            "Status": ["1", "4", "5"], "Message": ["Clear", "Safety Car", None]}))

    frame = adapt_track_status_intervals(session, "2026-03-race")

    assert list(frame.schema.items()) == list(TRACK_STATUS_INTERVALS_SCHEMA.items())
    assert frame.to_dicts() == [
        {"session_id": "2026-03-race", "start_time_ms": 40_000, "end_time_ms": 120_000, "status": "4", "message": "Safety Car"},
        {"session_id": "2026-03-race", "start_time_ms": 120_000, "end_time_ms": 300_000, "status": "5", "message": None},
        {"session_id": "2026-03-race", "start_time_ms": 300_000, "end_time_ms": None, "status": "1", "message": "Clear"},
    ]


@pytest.mark.parametrize("adapter, field, schema", [
    (adapt_weather, "weather_data", WEATHER_SCHEMA),
    (adapt_track_status_intervals, "track_status", TRACK_STATUS_INTERVALS_SCHEMA),
])
def test_adapters_return_typed_empty_frames(adapter, field, schema):
    session = FakeSession([], [])
    setattr(session, field, [])

    frame = adapter(session, "2026-03-race")

    assert frame.is_empty()
    assert list(frame.schema.items()) == list(schema.items())


def test_adapt_weather_rejects_missing_required_timestamp():
    with pytest.raises(NormalizationError, match="weather timestamp is required"):
        adapt_weather(FakeSession([{"AirTemp": 20.0}], []), "2026-03-race")


def test_adapt_weather_uses_typed_scalar_tie_breaking_independent_of_input_order():
    higher = {"Time": timedelta(seconds=1), "AirTemp": 1.0}
    lower = {"Time": timedelta(seconds=1), "AirTemp": 0.5}
    frames = [
        adapt_weather(FakeSession(list(rows), []), "2026-03-race")
        for rows in permutations((higher, lower))
    ]

    assert all(frame.equals(frames[0]) for frame in frames)
    assert frames[0].item(0, "air_temperature_c") == 0.5


def test_adapt_track_status_intervals_rejects_duplicate_transition_times():
    session = FakeSession([], [{"Time": timedelta(seconds=1), "Status": "1"}, {"Time": timedelta(seconds=1), "Status": "4"}])

    with pytest.raises(NormalizationError, match="duplicate"):
        adapt_track_status_intervals(session, "2026-03-race")
