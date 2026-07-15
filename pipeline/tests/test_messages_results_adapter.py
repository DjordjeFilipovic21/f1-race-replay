from datetime import datetime, timedelta, timezone
from itertools import permutations
from types import SimpleNamespace

import pandas as pd
import polars as pl
import pytest
import numpy as np

from f1_replay_pipeline.canonical_schema import RACE_CONTROL_MESSAGES_SCHEMA, RESULTS_SCHEMA
from f1_replay_pipeline.messages_results_adapter import adapt_race_control_messages, adapt_results
from f1_replay_pipeline.normalizers import NormalizationError


DRIVERS = {"44": "HAM", "1": "VER"}


def test_adapt_race_control_messages_preserves_sparse_records_and_typed_nulls():
    messages = [
        {"Time": timedelta(seconds=2), "Message": "Track clear"},
        {"Time": timedelta(seconds=1), "Message": "Yellow", "RacingNumber": "44", "Flag": "YELLOW"},
    ]

    frame = adapt_race_control_messages(messages, DRIVERS, "2026-03-race")

    assert list(frame.schema.items()) == list(RACE_CONTROL_MESSAGES_SCHEMA.items())
    assert frame.to_dicts() == [
        {"session_id": "2026-03-race", "session_time_ms": 1000, "message_index": 0,
         "category": None, "flag": "YELLOW", "scope": None, "message": "Yellow", "driver_id": "HAM", "lap_number": None},
        {"session_id": "2026-03-race", "session_time_ms": 2000, "message_index": 1,
         "category": None, "flag": None, "scope": None, "message": "Track clear", "driver_id": None, "lap_number": None},
    ]


def test_adapt_race_control_messages_is_deterministic_under_input_permutation():
    messages = [
        {"Time": timedelta(seconds=1), "Message": "Zulu"},
        {"Time": timedelta(seconds=1), "Message": "Alpha"},
    ]

    frames = [adapt_race_control_messages(order, DRIVERS, "2026-03-race") for order in permutations(messages)]

    assert frames[0].equals(frames[1])


def test_adapt_race_control_messages_converts_absolute_fastf1_times_relative_to_t0():
    session = SimpleNamespace(
        t0_date=pd.Timestamp("2026-03-08T05:00:00"),
        race_control_messages=[
            {"Time": pd.Timestamp("2026-03-08T05:00:12.499499999"), "Message": "Below boundary"},
            {"Time": pd.Timestamp("2026-03-08T05:00:12.499500000"), "Message": "Half-up boundary"},
            {"Time": datetime(2026, 3, 8, 7, 0, 2, tzinfo=timezone(timedelta(hours=2))), "Message": "Aware UTC"},
        ],
    )

    frame = adapt_race_control_messages(session, DRIVERS, "2026-03-race")

    assert frame.select("session_time_ms", "message").to_dicts() == [
        {"session_time_ms": 2000, "message": "Aware UTC"},
        {"session_time_ms": 12499, "message": "Below boundary"},
        {"session_time_ms": 12500, "message": "Half-up boundary"},
    ]


def test_adapt_race_control_messages_keeps_duration_compatibility_explicitly_without_t0():
    frame = adapt_race_control_messages(
        [{"Time": timedelta(seconds=1.4995), "Message": "Duration compatibility"}], DRIVERS, "2026-03-race"
    )

    assert frame.item(0, "session_time_ms") == 1500


@pytest.mark.parametrize(
    "time, expected_message",
    [
        (None, "missing required timestamp"),
        (pd.NaT, "missing required timestamp"),
        (pd.Timestamp("2026-03-08T04:59:59"), "precedes session.t0_date"),
    ],
)
def test_adapt_race_control_messages_rejects_invalid_absolute_timestamps(time, expected_message):
    session = SimpleNamespace(
        t0_date=pd.Timestamp("2026-03-08T05:00:00"),
        race_control_messages=[{"Time": time, "Message": "Invalid time"}],
    )

    with pytest.raises(NormalizationError, match=expected_message):
        adapt_race_control_messages(session, DRIVERS, "2026-03-race")


def test_adapt_race_control_messages_requires_t0_for_absolute_timestamp():
    with pytest.raises(NormalizationError, match="requires session.t0_date"):
        adapt_race_control_messages(
            [{"Time": pd.Timestamp("2026-03-08T05:00:01"), "Message": "Absolute"}], DRIVERS, "2026-03-race"
        )


def test_adapt_race_control_messages_converts_pandas_timestamp_extremes_without_timedelta_overflow():
    session = SimpleNamespace(
        t0_date=pd.Timestamp.min,
        race_control_messages=[{"Time": pd.Timestamp.max, "Message": "Maximum elapsed time"}],
    )

    frame = adapt_race_control_messages(session, DRIVERS, "2026-03-race")

    assert frame.item(0, "session_time_ms") == 18_446_744_073_710


@pytest.mark.parametrize("time", [1, SimpleNamespace(value=1)])
def test_adapt_race_control_messages_rejects_non_duration_compatibility_values(time):
    with pytest.raises(NormalizationError, match="absolute datetime or duration"):
        adapt_race_control_messages(
            [{"Time": time, "Message": "Invalid compatibility"}], DRIVERS, "2026-03-race"
        )


@pytest.mark.parametrize("messages", [None, []])
def test_adapt_race_control_messages_returns_typed_empty_frame(messages):
    frame = adapt_race_control_messages(messages, DRIVERS, "2026-03-race")

    assert frame.height == 0
    assert list(frame.schema.items()) == list(RACE_CONTROL_MESSAGES_SCHEMA.items())


def test_adapt_race_control_messages_rejects_unknown_driver_with_actionable_context():
    with pytest.raises(NormalizationError, match="unknown driver '99'.*RacingNumber.*44"):
        adapt_race_control_messages(
            [{"Time": timedelta(seconds=1), "Message": "Investigation", "RacingNumber": "99"}],
            DRIVERS,
            "2026-03-race",
        )


def test_adapt_results_maps_drivers_converts_nullable_fields_and_sorts():
    results = [
        {"DriverNumber": "44", "ClassifiedPosition": "R", "GridPosition": float("nan"), "Status": None,
         "Points": float("nan"), "Laps": None, "Time": None},
        {"DriverNumber": "1", "ClassifiedPosition": "1", "GridPosition": 2.0, "Status": "Finished",
         "Points": 25.0, "Laps": 58.0, "Time": timedelta(seconds=5400)},
    ]

    frame = adapt_results(results, DRIVERS, "2026-03-race")

    assert list(frame.schema.items()) == list(RESULTS_SCHEMA.items())
    assert frame.to_dicts() == [
        {"session_id": "2026-03-race", "driver_id": "HAM", "classified_position": "R", "grid_position": None,
         "status": None, "points": None, "laps_completed": None, "result_time_ms": None},
        {"session_id": "2026-03-race", "driver_id": "VER", "classified_position": "1", "grid_position": 2,
         "status": "Finished", "points": 25.0, "laps_completed": 58, "result_time_ms": 5_400_000},
    ]


@pytest.mark.parametrize("results", [None, []])
def test_adapt_results_returns_typed_empty_frame(results):
    frame = adapt_results(results, DRIVERS, "2026-03-race")

    assert frame.height == 0
    assert list(frame.schema.items()) == list(RESULTS_SCHEMA.items())


def test_adapt_results_rejects_unknown_driver_with_actionable_context():
    with pytest.raises(NormalizationError, match="unknown driver '99'.*DriverNumber.*44"):
        adapt_results([{"DriverNumber": "99"}], DRIVERS, "2026-03-race")


def test_adapt_results_accepts_canonical_driver_metadata_frame():
    drivers = pl.DataFrame(
        {"session_id": ["2026-03-race"], "source_driver_key": ["044"], "driver_number": [44], "driver_id": ["HAM"]}
    )

    frame = adapt_results([{"DriverNumber": "44"}], drivers, "2026-03-race")

    assert frame.item(0, "driver_id") == "HAM"


def test_adapt_results_only_resolves_driver_metadata_for_the_requested_session():
    drivers = pl.DataFrame(
        {"session_id": ["2026-03-race", "2026-04-race"], "source_driver_key": ["44", "1"], "driver_number": [44, 1], "driver_id": ["HAM", "VER"]}
    )

    with pytest.raises(NormalizationError, match="unknown driver '1'.*available source keys: 44"):
        adapt_results([{"DriverNumber": "1"}], drivers, "2026-03-race")


@pytest.mark.parametrize(
    "drivers, expected_message",
    [
        ({"44": "ham"}, "invalid canonical"),
        (pl.DataFrame({"session_id": ["2026-03-race", "2026-03-race"], "source_driver_key": ["44", "44"], "driver_number": [44, 44],
                       "driver_id": ["HAM", "HAM"]}), "duplicate source"),
        (pl.DataFrame({"session_id": ["2026-03-race", "2026-03-race"], "source_driver_key": ["44", "1"], "driver_number": [44, 1],
                       "driver_id": ["HAM", "HAM"]}), "multiple source"),
    ],
)
def test_adapt_results_rejects_ambiguous_or_invalid_canonical_driver_metadata(drivers, expected_message):
    with pytest.raises(NormalizationError, match=expected_message):
        adapt_results([], drivers, "2026-03-race")


def test_adapters_resolve_source_driver_keys_before_optional_driver_number_aliases():
    drivers = pl.DataFrame(
        {"session_id": ["2026-03-race"], "source_driver_key": ["044"], "driver_number": [44], "driver_id": ["HAM"]}
    )

    messages = adapt_race_control_messages(
        [{"Time": timedelta(seconds=1), "Message": "Source key", "RacingNumber": "044"}], drivers, "2026-03-race"
    )
    results = adapt_results([{"DriverNumber": "44"}], drivers, "2026-03-race")

    assert messages.item(0, "driver_id") == results.item(0, "driver_id") == "HAM"


def test_adapt_results_rejects_duplicate_canonical_driver_results():
    with pytest.raises(NormalizationError, match="duplicate result for canonical driver: HAM"):
        adapt_results([{"DriverNumber": "44"}, {"DriverNumber": "44"}], DRIVERS, "2026-03-race")


def test_adapt_results_falls_back_from_blank_classification_to_position():
    frame = adapt_results([{"DriverNumber": "44", "ClassifiedPosition": " ", "Position": 1}], DRIVERS, "2026-03-race")

    assert frame.item(0, "classified_position") == "1"


def test_driver_alias_conflicts_are_rejected_regardless_of_roster_order():
    roster = [
        {"session_id": "2026-03-race", "source_driver_key": "44", "driver_number": 1, "driver_id": "HAM"},
        {"session_id": "2026-03-race", "source_driver_key": "1", "driver_number": 44, "driver_id": "VER"},
    ]

    for rows in permutations(roster):
        with pytest.raises(NormalizationError, match="conflicts"):
            adapt_results([], pl.DataFrame(rows), "2026-03-race")


def test_adapt_results_rejects_nonfinite_numpy_source_driver_key_before_string_conversion():
    with pytest.raises(NormalizationError, match="missing required DriverNumber for canonical driver mapping"):
        adapt_results([{"DriverNumber": np.float32("nan")}], DRIVERS, "2026-03-race")
