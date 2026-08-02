"""End-to-end, offline checks for the Phase 1 FastF1 normalization boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
import socket
import sys
import urllib.request
from typing import cast

import numpy as np
import pandas as pd
import polars as pl
from polars.testing import assert_frame_equal
import pytest

from fixtures.fake_fastf1_session import (
    SESSION_TABLE_NAMES,
    FakeFastF1Session,
    build_complete_session,
    build_2026_session_with_default_drs,
    build_empty_session,
    build_permuted_practice_session,
    build_permuted_qualifying_session,
    build_permuted_session,
    build_permuted_sprint_session,
    build_practice_session,
    build_qualifying_session,
    build_qualifying_session_with_cancelled_q3,
    build_qualifying_session_with_invalid_q1,
    build_session_factory,
    build_session_with_empty_table,
    build_session_with_missing_table,
    build_sprint_session,
    build_testing_event_schedule,
)
from f1_replay_pipeline.domain.canonical_schema import CANONICAL_TABLE_SCHEMAS_V2
from f1_replay_pipeline.domain.normalizers import NormalizationError
from f1_replay_pipeline.app.orchestration import RaceSelection, normalize_session
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
        "session_mode": "race",
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
    for name in CANONICAL_TABLE_SCHEMAS_V2:
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
    for name in set(CANONICAL_TABLE_SCHEMAS_V2) - {"session_metadata", "drivers"}:
        assert tables[name].is_empty()
        assert tables[name].schema == CANONICAL_TABLE_SCHEMAS_V2[name]


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


def test_2026_public_drs_default_normalizes_to_zero_without_synthesizing_new_telemetry():
    # Arrange: FastF1's 2026-compatible public DRS column is present but zero-filled.
    factory = build_session_factory(build_2026_session_with_default_drs())

    # Act: normalize the deterministic session through the normal loader boundary.
    tables = _normalize_all(factory)

    # Assert: retain canonical DRS only; no factual Overtake Mode, aero, or ERS data is added.
    assert tables["car_telemetry"].get_column("drs").to_list() == [0, 0, 0]
    assert "overtake_mode" not in tables["car_telemetry"].columns
    assert "active_aero" not in tables["car_telemetry"].columns
    assert "ers" not in tables["car_telemetry"].columns


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


@pytest.mark.parametrize(
    ("practice_index", "session_id", "session_name"),
    [
        (1, "2026-03-practice-1", "FP1"),
        (2, "2026-03-practice-2", "FP2"),
        (3, "2026-03-practice-3", "FP3"),
    ],
)
def test_practice_fixtures_normalize_deterministic_canonical_frames(
    practice_index: int, session_id: str, session_name: str
):
    # Arrange: each FP fixture is supplied only through the injected loader factory.
    factory = build_session_factory(build_practice_session(practice_index))

    # Act: load once and normalize every canonical table.
    tables = _normalize_all(factory)

    # Assert: metadata identity, valid laps, and nullable practice classification.
    assert factory.calls == 1
    _assert_schemas(tables)
    assert tables["session_metadata"].item(0, "session_id") == session_id
    assert tables["session_metadata"].item(0, "session_name") == session_name
    assert tables["session_metadata"].item(0, "session_type") == "practice"
    assert tables["session_metadata"].item(0, "session_mode") == "practice"
    assert tables["laps"].select("driver_id", "lap_duration_ms", "compound").to_dicts() == [
        {"driver_id": "HAM", "lap_duration_ms": 92_500, "compound": "SOFT"},
        {"driver_id": "VER", "lap_duration_ms": 93_200, "compound": "MEDIUM"},
    ]
    assert tables["results"].select("driver_id", "classified_position").to_dicts() == [
        {"driver_id": "HAM", "classified_position": "1"},
        {"driver_id": "VER", "classified_position": None},
    ]


def test_qualifying_fixture_normalizes_populated_and_missing_q_results():
    # Arrange: supply the deterministic qualifying fixture through the injected loader.
    factory = build_session_factory(build_qualifying_session())

    # Act: load once and normalize every canonical table.
    tables = _normalize_all(factory)

    # Assert: Q1/Q2/Q3 populate truthfully and missing segments stay null.
    assert tables["session_metadata"].item(0, "session_id") == "2026-03-qualifying"
    assert tables["session_metadata"].item(0, "session_type") == "qualifying"
    assert tables["session_metadata"].item(0, "session_mode") == "qualifying"
    assert tables["results"].select("driver_id", "q1_time_ms", "q2_time_ms", "q3_time_ms").to_dicts() == [
        {"driver_id": "HAM", "q1_time_ms": 105_123, "q2_time_ms": 104_567, "q3_time_ms": 103_999},
        {"driver_id": "VER", "q1_time_ms": 105_200, "q2_time_ms": None, "q3_time_ms": None},
    ]


def test_sprint_fixture_normalizes_race_shaped_canonical_frames():
    # Arrange: supply the deterministic sprint fixture through the injected loader.
    factory = build_session_factory(build_sprint_session())

    # Act: load once and normalize every canonical table.
    tables = _normalize_all(factory)

    # Assert: sprint keeps race-shaped laps/results but stays mode-distinct metadata.
    assert factory.calls == 1
    _assert_schemas(tables)
    assert tables["session_metadata"].item(0, "session_id") == "2026-03-sprint"
    assert tables["session_metadata"].item(0, "session_name") == "Sprint"
    assert tables["session_metadata"].item(0, "session_type") == "sprint"
    assert tables["session_metadata"].item(0, "session_mode") == "sprint"
    assert tables["results"].select("driver_id", "classified_position", "points").to_dicts() == [
        {"driver_id": "HAM", "classified_position": "1", "points": 25.0},
        {"driver_id": "VER", "classified_position": None, "points": None},
    ]


@pytest.mark.parametrize(
    ("alias", "expected_mode"),
    [
        ("FP1", "practice"),
        ("FP2", "practice"),
        ("FP3", "practice"),
        ("Q", "qualifying"),
        ("R", "race"),
        ("S", "sprint"),
        ("SQ", "sprint-qualifying"),
        ("SS", "sprint-shootout"),
    ],
)
def test_normalize_session_emits_explicit_v2_mode_for_supported_aliases(
    alias: str, expected_mode: str,
):
    # Arrange: the injected session exposes the same alias as FastF1 metadata.
    session = build_complete_session()
    session.name = alias
    selection = RaceSelection(year=2026, round_number=3, session=alias)

    # Act: normalize through the application orchestration boundary.
    frames = normalize_session(session, selection)

    # Assert: v2 metadata carries one stable mode without changing race-shaped tables.
    metadata = cast(pl.DataFrame, frames["session_metadata"])
    results = cast(pl.DataFrame, frames["results"])
    assert list(metadata.schema) == list(CANONICAL_TABLE_SCHEMAS_V2["session_metadata"])
    assert metadata.item(0, "session_mode") == expected_mode
    assert results.item(0, "classified_position") == "1"


def test_cancelled_q3_qualifying_fixture_normalizes_null_q3_for_all():
    # Arrange: a cancelled Q3 segment is modeled as NaT for every remaining driver.
    factory = build_session_factory(build_qualifying_session_with_cancelled_q3())

    # Act / Assert: end-to-end normalization keeps Q3 null without fabricating a time.
    tables = _normalize_all(factory)
    assert tables["results"].select("driver_id", "q3_time_ms").to_dicts() == [
        {"driver_id": "HAM", "q3_time_ms": None},
        {"driver_id": "VER", "q3_time_ms": None},
    ]


def test_qualifying_fixture_with_invalid_q1_fails_normalization_end_to_end():
    # Arrange: an invalid Q1 value must not silently become null anywhere in the pipeline.
    factory = build_session_factory(build_qualifying_session_with_invalid_q1())

    # Act / Assert: the normalization stage fails explicitly with an actionable label.
    with pytest.raises(NormalizationError, match="q1 time"):
        _normalize_all(factory)


@pytest.mark.parametrize("practice_index", [1, 2, 3])
def test_permuted_practice_fixtures_are_deterministic(practice_index: int):
    # Arrange: the same FP source data is presented in reversed row and key order.
    complete = _normalize_all(build_session_factory(build_practice_session(practice_index)))
    permuted = _normalize_all(build_session_factory(build_permuted_practice_session(practice_index)))

    # Assert: every canonical table is identical regardless of source ordering.
    for name in CANONICAL_TABLE_SCHEMAS_V2:
        assert_frame_equal(permuted[name], complete[name])


def test_permuted_qualifying_fixture_is_deterministic():
    # Arrange: the same qualifying source data is presented in reversed order.
    complete = _normalize_all(build_session_factory(build_qualifying_session()))
    permuted = _normalize_all(build_session_factory(build_permuted_qualifying_session()))

    # Assert: Q1/Q2/Q3 normalization is independent of source row and key order.
    for name in CANONICAL_TABLE_SCHEMAS_V2:
        assert_frame_equal(permuted[name], complete[name])


def test_permuted_sprint_fixture_is_deterministic():
    # Arrange: the same sprint source data is presented in reversed order.
    complete = _normalize_all(build_session_factory(build_sprint_session()))
    permuted = _normalize_all(build_session_factory(build_permuted_sprint_session()))

    # Assert: sprint canonical frames are identical regardless of source ordering.
    for name in CANONICAL_TABLE_SCHEMAS_V2:
        assert_frame_equal(permuted[name], complete[name])


def test_practice_fixture_models_unavailable_position_semantics_with_available_telemetry():
    # Arrange: build the deterministic FP fixture without FastF1 or a network.
    session = build_practice_session(1)

    # Assert: per-lap live order is documented-unavailable NaN while lap timing
    # and native telemetry streams remain fully available.
    assert np.isnan(session.laps.loc[0, "Position"])
    assert np.isnan(session.laps.loc[1, "Position"])
    assert pd.isna(session.results.loc[1, "Position"])
    assert pd.isna(session.results.loc[1, "ClassifiedPosition"])
    assert session.car_data["44"].loc[0, "Speed"] == 280.0
    assert session.pos_data["44"].loc[0, "X"] == 10.0


def test_qualifying_fixture_models_q_shapes_without_importing_fastf1_or_using_network():
    # Arrange: capture the module state before building the qualifying fixture.
    before_modules = set(sys.modules)

    # Act: build the fixture (network is rejected by the autouse fixture).
    session = build_qualifying_session()

    # Assert: the fixture models FastF1 Q columns as timedeltas and never imports FastF1.
    assert "fastf1" not in set(sys.modules).difference(before_modules)
    assert isinstance(session.results.loc[0, "Q1"], pd.Timedelta)
    assert isinstance(session.results.loc[0, "Q2"], pd.Timedelta)
    assert isinstance(session.results.loc[0, "Q3"], pd.Timedelta)
    assert pd.isna(session.results.loc[1, "Q2"])
    assert pd.isna(session.results.loc[1, "Q3"])


def test_qualifying_fixture_models_documented_nan_position_and_nullable_results():
    # Arrange / Act: build the deterministic qualifying fixture without FastF1.
    session = build_qualifying_session()

    # Assert: per-lap live order is documented-unavailable NaN while Q segments
    # stay nullable timedeltas, mirroring FastF1's qualifying data model.
    assert np.isnan(session.laps.loc[0, "Position"])
    assert np.isnan(session.laps.loc[1, "Position"])
    assert isinstance(session.results.loc[0, "Q3"], pd.Timedelta)
    assert pd.isna(session.results.loc[1, "Q2"])
    assert pd.isna(session.results.loc[1, "Q3"])


def test_practice_session_with_incomplete_lap_timing_preserves_null_duration():
    # Arrange: an FP session where one driver has no recorded timed lap.
    session = build_practice_session(1)
    session.laps = pd.DataFrame({
        "DriverNumber": ["44", "1"],
        "LapNumber": [1, 1],
        "LapStartTime": [pd.Timedelta(0, unit="s"), pd.Timedelta(0, unit="s")],
        "Time": [pd.Timedelta(92_500, unit="ms"), pd.NaT],
        "LapTime": [pd.Timedelta(92_500, unit="ms"), pd.NaT],
        "Compound": ["SOFT", "MEDIUM"],
        "Position": [np.nan, np.nan],
    })
    factory = build_session_factory(session)

    # Act: load once and normalize every canonical table.
    tables = _normalize_all(factory)

    # Assert: the timed lap survives while the incomplete lap keeps a null duration.
    assert tables["laps"].select("driver_id", "lap_duration_ms").to_dicts() == [
        {"driver_id": "HAM", "lap_duration_ms": 92_500},
        {"driver_id": "VER", "lap_duration_ms": None},
    ]
    assert tables["stints"].is_empty()


def test_normalize_session_rejects_unsupported_session_mode_end_to_end():
    # Arrange: a loaded session whose label cannot be normalized to any mode.
    session = build_complete_session()
    session.name = "Warmup"
    selection = RaceSelection(year=2026, round_number=3, session="R")

    # Act / Assert: the orchestration boundary fails the metadata stage explicitly.
    with pytest.raises(RuntimeError, match="normalization failed during session_metadata"):
        normalize_session(session, selection)


def test_qualifying_fixture_with_empty_results_table_emits_typed_empty_results():
    # Arrange: a qualifying session whose timing/classification table is empty.
    session = build_session_with_empty_table("results")
    session.name = "Qualifying"
    factory = build_session_factory(session)

    # Act: load once and normalize every canonical table.
    tables = _normalize_all(factory)

    # Assert: qualifying identity is preserved and results stay a typed empty table.
    _assert_schemas(tables)
    assert tables["session_metadata"].item(0, "session_id") == "2026-03-qualifying"
    assert tables["session_metadata"].item(0, "session_mode") == "qualifying"
    assert tables["results"].is_empty()
    assert tables["results"].schema == CANONICAL_TABLE_SCHEMAS_V2["results"]


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
    assert set(tables) == set(CANONICAL_TABLE_SCHEMAS_V2)
    for name, schema in CANONICAL_TABLE_SCHEMAS_V2.items():
        assert tables[name].schema == schema
