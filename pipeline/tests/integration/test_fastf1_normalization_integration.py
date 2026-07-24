"""End-to-end, offline checks for the Phase 1 FastF1 normalization boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
import socket
import urllib.request

import numpy as np
import pandas as pd
import polars as pl
from polars.testing import assert_frame_equal
import pytest

from fixtures.fake_fastf1_session import (
    SESSION_TABLE_NAMES,
    FakeFastF1Session,
    build_complete_session,
    build_empty_session,
    build_permuted_session,
    build_session_factory,
    build_session_with_missing_table,
    build_testing_event_schedule,
)
from f1_replay_pipeline.domain.canonical_schema import CANONICAL_TABLE_SCHEMAS
from f1_replay_pipeline.adapters.fastf1.car_telemetry import adapt_car_telemetry
from f1_replay_pipeline.adapters.fastf1.laps_stints import adapt_laps, adapt_stints
from f1_replay_pipeline.adapters.fastf1.messages_results import adapt_race_control_messages, adapt_results
from f1_replay_pipeline.adapters.fastf1.position_telemetry import adapt_position_telemetry
from f1_replay_pipeline.adapters.fastf1.session_loader import SessionLoaderError, load_session
from f1_replay_pipeline.adapters.fastf1.session_metadata import adapt_drivers, adapt_session_metadata
from f1_replay_pipeline.adapters.fastf1.weather_status import adapt_track_status_intervals, adapt_weather


@pytest.fixture(autouse=True)
def reject_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail deterministically if an adapter bypasses the injected session boundary."""
    def fail_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network access is forbidden in normalization tests")

    # Arrange: replace common raw socket and urllib connection entry points.
    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(socket.socket, "connect", fail_network)
    monkeypatch.setattr(urllib.request, "urlopen", fail_network)


def test_complete_session_normalizes_every_table_with_exact_schemas_and_native_samples():
    # Arrange: a complete fake session is supplied only through the injected loader factory.
    session = build_complete_session()
    factory = build_session_factory(session)

    # Act: load once, then run every Phase 1 adapter against the loaded fake.
    tables = _normalize_all(factory)

    # Assert: every canonical table has its exact declared ordered schema and expected rows.
    assert factory.calls == 1
    assert session.load_calls == [{"laps": True, "telemetry": True, "weather": True, "messages": True}]
    _assert_schemas(tables)
    assert tables["session_metadata"].to_dicts() == [{
        "session_id": "2026-03-race", "year": 2026, "round_number": 3,
        "event_name": "Australian Grand Prix", "session_name": "Race", "session_type": "race",
        "session_start_time_utc": datetime(2026, 3, 8, 5, tzinfo=timezone.utc),
    }]
    assert tables["drivers"].select("driver_id", "source_driver_key").to_dicts() == [
        {"driver_id": "HAM", "source_driver_key": "44"},
        {"driver_id": "VER", "source_driver_key": "1"},
    ]
    assert tables["drivers"].get_column("driver_id").n_unique() == tables["drivers"].height
    assert tables["drivers"].get_column("source_driver_key").n_unique() == tables["drivers"].height
    assert tables["laps"].select("driver_id", "lap_number", "lap_start_time_ms", "lap_duration_ms", "compound").to_dicts() == [
        {"driver_id": "HAM", "lap_number": 1, "lap_start_time_ms": 0, "lap_duration_ms": 92_500, "compound": "SOFT"},
        {"driver_id": "VER", "lap_number": 1, "lap_start_time_ms": 0, "lap_duration_ms": None, "compound": None},
    ]
    assert tables["stints"].is_empty()
    assert tables["car_telemetry"].select("driver_id", "session_time_ms", "speed_kph", "rpm").to_dicts() == [
        {"driver_id": "HAM", "session_time_ms": 1_000, "speed_kph": 280.0, "rpm": 11_000.0},
        {"driver_id": "HAM", "session_time_ms": 1_240, "speed_kph": 281.0, "rpm": 11_100.0},
        {"driver_id": "VER", "session_time_ms": 1_720, "speed_kph": 300.0, "rpm": None},
    ]
    assert tables["position_telemetry"].select("driver_id", "session_time_ms", "x", "y").to_dicts() == [
        {"driver_id": "HAM", "session_time_ms": 1_100, "x": 10.0, "y": 20.0},
        {"driver_id": "HAM", "session_time_ms": 1_480, "x": 11.0, "y": 21.0},
        {"driver_id": "VER", "session_time_ms": 2_030, "x": 30.0, "y": None},
    ]
    assert tables["weather"].select("session_time_ms", "air_temperature_c", "rainfall").to_dicts() == [
        {"session_time_ms": 0, "air_temperature_c": 24.5, "rainfall": False},
        {"session_time_ms": 60_000, "air_temperature_c": None, "rainfall": None},
    ]
    assert tables["track_status_intervals"].to_dicts() == [
        {"session_id": "2026-03-race", "start_time_ms": 0, "end_time_ms": 1_500, "status": "1", "message": "AllClear"},
        {"session_id": "2026-03-race", "start_time_ms": 1_500, "end_time_ms": None, "status": "2", "message": None},
    ]
    assert tables["race_control_messages"].select("session_time_ms", "message_index", "category", "message", "driver_id", "lap_number").to_dicts() == [
        {"session_time_ms": 1_250, "message_index": 0, "category": "Flag", "message": "GREEN FLAG", "driver_id": "HAM", "lap_number": 1},
        {"session_time_ms": 1_750, "message_index": 1, "category": None, "message": "TRACK CLEAR", "driver_id": None, "lap_number": None},
    ]
    assert tables["results"].select("driver_id", "classified_position", "points").to_dicts() == [
            {"driver_id": "HAM", "classified_position": "1", "points": 25.0},
        {"driver_id": "VER", "classified_position": None, "points": None},
    ]


def test_permuted_complete_session_has_identical_deterministic_canonical_tables():
    # Arrange: equivalent source records and source keys are presented in reverse order.
    complete_factory = build_session_factory(build_complete_session())
    permuted_factory = build_session_factory(build_permuted_session())

    # Act: normalize both sessions through the same injected loading boundary.
    complete = _normalize_all(complete_factory)
    permuted = _normalize_all(permuted_factory)

    # Assert: ordering and duplicate winner retention are independent of source ordering.
    for name in CANONICAL_TABLE_SCHEMAS:
        assert_frame_equal(permuted[name], complete[name])


def test_empty_session_emits_typed_empty_observation_tables_and_preserves_roster():
    # Arrange: every source table is an empty, typed FastF1-shaped table.
    factory = build_session_factory(build_empty_session())

    # Act: normalize every table via the injected loader.
    tables = _normalize_all(factory)

    # Assert: null-capable schemas survive empty inputs without inferred Null dtypes.
    _assert_schemas(tables)
    assert tables["session_metadata"].height == 1
    assert tables["drivers"].height == 2
    for name in set(CANONICAL_TABLE_SCHEMAS) - {"session_metadata", "drivers"}:
        assert tables[name].is_empty()
        assert tables[name].schema == CANONICAL_TABLE_SCHEMAS[name]


@pytest.mark.parametrize("table_name", SESSION_TABLE_NAMES)
def test_loader_rejects_each_missing_required_source_table_before_any_adapter(table_name: str):
    # Arrange: remove exactly one table from an otherwise complete source session.
    factory = build_session_factory(build_session_with_missing_table(table_name))

    # Act / Assert: the injected boundary fails locally rather than fetching missing data.
    with pytest.raises(SessionLoaderError, match=table_name):
        load_session(session_factory=factory)


def test_native_car_and_position_streams_remain_separate_sparse_noninterpolated_cadences():
    # Arrange: the fake has duplicate native samples and non-aligned, irregular streams.
    factory = build_session_factory(build_complete_session())

    # Act: normalize all tables without any telemetry-merge adapter.
    tables = _normalize_all(factory)

    # Assert: duplicate timestamp retention chooses the complete native record, while streams stay distinct.
    car_times = tables["car_telemetry"].get_column("session_time_ms").to_list()
    position_times = tables["position_telemetry"].get_column("session_time_ms").to_list()
    assert car_times == [1_000, 1_240, 1_720]
    assert position_times == [1_100, 1_480, 2_030]
    assert set(car_times).isdisjoint(position_times)
    assert [right - left for left, right in zip(car_times, car_times[1:])] == [240, 480]
    assert [right - left for left, right in zip(position_times, position_times[1:])] == [380, 550]


def test_offline_fixture_models_fastf1_timestamp_duration_missing_and_native_mapping_shapes():
    # Arrange: build the reusable public-shaped fake without importing FastF1 or touching a network.
    session = build_complete_session()

    # Act: inspect the source values that adapters receive at the public boundary.
    race_time = session.race_control_messages.loc[0, "Time"]
    weather_time = session.weather_data.loc[0, "Time"]
    track_time = session.track_status.loc[0, "Time"]

    # Assert: absolute and duration timestamps, missing variants, and telemetry mappings stay distinct.
    assert isinstance(session.date, pd.Timestamp) and session.date.tz is None
    assert isinstance(session.t0_date, pd.Timestamp) and session.t0_date.tz is None
    assert isinstance(session.session_start_time, timedelta)
    assert isinstance(race_time, pd.Timestamp) and race_time.tz is None
    assert isinstance(weather_time, pd.Timedelta) and isinstance(track_time, pd.Timedelta)
    assert pd.isna(session.car_data["44"].loc[1, "Date"])
    assert np.isnan(session.car_data["44"].loc[1, "Speed"])
    assert session.laps.loc[1, "Compound"] is pd.NA
    assert session.track_status.loc[1, "Message"] is None
    assert session.car_data["44"] is not session.pos_data["44"]
    assert session.car_data["44"]["SessionTime"].tolist() != session.pos_data["44"]["SessionTime"].tolist()


def test_offline_fixture_rejects_prohibited_round_zero_testing_event_lookup():
    # Arrange: obtain the deterministic schedule fake for a testing event.
    schedule = build_testing_event_schedule()

    # Act: use the FastF1 lookup that explicitly excludes testing events.
    with pytest.raises(ValueError) as error:
        schedule.get_event_by_round(0)

    # Assert: the round-zero path remains prohibited.
    assert "Cannot get testing event" in str(error.value)


def test_offline_fixture_supports_testing_event_lookup_for_round_zero_event():
    # Arrange: obtain the deterministic schedule fake for a testing event.
    schedule = build_testing_event_schedule()

    # Act: use FastF1's supported 1-based testing-event lookup.
    event = schedule.get_testing_event(2026, 1)

    # Assert: testing identity is preserved, including its round-zero value.
    assert event["RoundNumber"] == 0


def _normalize_all(factory: Callable[[], FakeFastF1Session]) -> Mapping[str, pl.DataFrame]:
    """Exercise the public loader seam followed by every Phase 1 table adapter."""
    loaded = load_session(session_factory=factory)
    metadata = adapt_session_metadata(loaded)
    session_id = metadata.item(0, "session_id")
    drivers = adapt_drivers(loaded, session_id)
    driver_ids = {row["source_driver_key"]: row["driver_id"] for row in drivers.to_dicts()}
    return {
        "session_metadata": metadata,
        "drivers": drivers,
        "laps": adapt_laps(loaded, session_id, driver_ids),
        "stints": adapt_stints(loaded, session_id, driver_ids),
        "car_telemetry": adapt_car_telemetry(loaded, session_id),
        "position_telemetry": adapt_position_telemetry(loaded, session_id, driver_ids),
        "weather": adapt_weather(loaded, session_id),
        "track_status_intervals": adapt_track_status_intervals(loaded, session_id),
        "race_control_messages": adapt_race_control_messages(loaded, drivers, session_id),
        "results": adapt_results(loaded, drivers, session_id),
    }


def _assert_schemas(tables: Mapping[str, pl.DataFrame]) -> None:
    assert set(tables) == set(CANONICAL_TABLE_SCHEMAS)
    for name, schema in CANONICAL_TABLE_SCHEMAS.items():
        assert tables[name].schema == schema
