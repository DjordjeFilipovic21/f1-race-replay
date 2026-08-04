from datetime import timedelta
from itertools import permutations

import polars as pl
import pytest

from f1_replay_pipeline.domain.canonical_schema import TRACK_STATUS_INTERVALS_SCHEMA, WEATHER_SCHEMA
from f1_replay_pipeline.domain.normalizers import NormalizationError
from f1_replay_pipeline.adapters.fastf1.weather_status import adapt_track_status_intervals, adapt_weather


class FakeSession:
    def __init__(self, weather_data: object, track_status: object) -> None:
        self.weather_data = weather_data
        self.track_status = track_status


class DataNotLoadedError(Exception):
    """FastF1-shaped lazy-property failure used to test optional weather access."""


class UnreadableWeatherSession:
    @property
    def weather_data(self) -> object:
        raise DataNotLoadedError("weather data was not loaded")


class BrokenWeatherSession:
    @property
    def weather_data(self) -> object:
        raise AttributeError("weather accessor is broken")


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


def test_adapt_weather_preserves_explicit_false_and_canonical_null_rainfall():
    # Arrange — false is source-provided dry/unknown; None is canonical null.
    session = FakeSession(
        [
            {"Time": timedelta(seconds=1), "Rainfall": False},
            {"Time": timedelta(seconds=2), "Rainfall": None},
        ],
        [],
    )

    # Act
    frame = adapt_weather(session, "2026-03-race")

    # Assert — the adapter never converts canonical/adapted null into dry.
    assert frame.select("rainfall").to_series().to_list() == [False, None]


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


def test_adapt_weather_returns_typed_empty_frame_when_weather_data_is_absent():
    # Arrange — a loaded session can legitimately have no weather attribute.
    session = FakeSession([], [])
    del session.weather_data

    # Act
    frame = adapt_weather(session, "2026-03-race")

    # Assert — optional weather absence does not fabricate rows or alter schema.
    assert frame.is_empty()
    assert list(frame.schema.items()) == list(WEATHER_SCHEMA.items())


def test_adapt_weather_returns_typed_empty_frame_for_fastf1_unreadable_weather_property():
    # Arrange — FastF1's documented lazy property failure after a soft load miss.
    session = UnreadableWeatherSession()

    # Act
    frame = adapt_weather(session, "2026-03-race")

    # Assert — only the known optional access failure is converted to absence.
    assert frame.is_empty()
    assert list(frame.schema.items()) == list(WEATHER_SCHEMA.items())


def test_adapt_weather_propagates_unrelated_weather_accessor_errors():
    # Arrange — an existing weather property can fail for an unrelated reason.
    session = BrokenWeatherSession()

    # Act / Assert — only genuine absence and the known FastF1 lazy-load error are optional.
    with pytest.raises(AttributeError, match="weather accessor is broken"):
        adapt_weather(session, "2026-03-race")


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
