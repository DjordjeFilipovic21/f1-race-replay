"""Deterministic duck-typed FastF1 session inputs for adapter tests.

These fakes expose only public-shaped data consumed at the application
boundary.  They deliberately do not import FastF1 or mimic its internals.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal

import numpy as np
import pandas as pd


SESSION_TABLE_NAMES = (
    "laps",
    "results",
    "car_data",
    "pos_data",
    "weather_data",
    "track_status",
    "race_control_messages",
)

_DRIVERS = {
    "44": {
        "DriverNumber": "44",
        "Abbreviation": "HAM",
        "FullName": "Lewis Hamilton",
        "TeamName": "Ferrari",
        "TeamColor": "E8002D",
    },
    "1": {
        "DriverNumber": "1",
        "Abbreviation": "VER",
        "FullName": "Max Verstappen",
        "TeamName": "Red Bull Racing",
        "TeamColor": "3671C6",
    },
}

WeatherSessionScenario = Literal[
    "normal_sparse",
    "no_weather",
    "partial_null",
    "zero_sentinel",
    "old_delivery_no_sidecar",
]

WEATHER_SESSION_SCENARIOS: tuple[WeatherSessionScenario, ...] = (
    "normal_sparse",
    "no_weather",
    "partial_null",
    "zero_sentinel",
    "old_delivery_no_sidecar",
)


@dataclass
class FakeFastF1Session:
    """Small, in-memory session double with a public FastF1-shaped surface."""

    event: Mapping[str, object]
    name: str
    drivers: list[str]
    driver_records: Mapping[str, Mapping[str, object]]
    date: pd.Timestamp
    t0_date: pd.Timestamp
    session_start_time: timedelta | None
    is_loaded: bool = True
    load_calls: list[dict[str, bool]] = field(default_factory=list)
    laps: Any = field(init=False)
    results: Any = field(init=False)
    car_data: Any = field(init=False)
    pos_data: Any = field(init=False)
    weather_data: Any = field(init=False)
    track_status: Any = field(init=False)
    race_control_messages: Any = field(init=False)

    def load(
        self, *, laps: bool = True, telemetry: bool = True, weather: bool = True, messages: bool = True
    ) -> None:
        """Record requested load flags without contacting FastF1 or the network."""
        self.load_calls.append(
            {"laps": laps, "telemetry": telemetry, "weather": weather, "messages": messages}
        )
        self.is_loaded = True

    def get_driver(self, identifier: object) -> Mapping[str, object]:
        """Return the public driver record for a source driver key."""
        key = str(identifier)
        if key in self.driver_records:
            return self.driver_records[key]
        for record in self.driver_records.values():
            if record.get("Abbreviation") == key:
                return record
        raise ValueError(f"unknown driver: {identifier}")


class FakeQualifyingLaps(pd.DataFrame):
    """DataFrame double exposing FastF1's qualifying partition API."""

    _metadata = [
        "_cancelled_q3", "_missing_status", "_duplicate_partition",
        "_incomplete_partition", "_malformed_partition",
    ]

    @property
    def _constructor(self):  # type: ignore[no-untyped-def]
        return FakeQualifyingLaps

    def split_qualifying_sessions(self) -> list[pd.DataFrame | None]:
        if getattr(self, "_missing_status", False):
            raise DataNotLoadedError("session status data is not loaded")
        partitions: list[pd.DataFrame | None] = [
            self.loc[self["QualifyingPhase"] == phase].drop(columns=["QualifyingPhase"])
            for phase in ("Q1", "Q2", "Q3")
        ]
        if getattr(self, "_cancelled_q3", False):
            partitions[2] = None
        if getattr(self, "_duplicate_partition", False):
            partitions[1] = partitions[0]
        if getattr(self, "_incomplete_partition", False):
            if partitions[1] is not None:
                partitions[1] = partitions[1].iloc[:0]
        if getattr(self, "_malformed_partition", False):
            partitions[0] = "not-a-partition"
        return partitions


class DataNotLoadedError(RuntimeError):
    """FastF1-shaped missing session-status failure for adapter tests."""


@dataclass(frozen=True)
class FakeFastF1EventSchedule:
    """Public-shaped schedule seam for the special FastF1 testing lookup."""

    testing_event: Mapping[str, object]

    def get_event_by_round(self, round_number: int) -> Mapping[str, object]:
        """Match FastF1's explicitly unsupported round-zero testing lookup."""
        if round_number == 0:
            raise ValueError("Cannot get testing event by round number!")
        raise ValueError(f"unknown round: {round_number}")

    def get_testing_event(self, year: int, test_number: int) -> Mapping[str, object]:
        """Return the 1-based testing event through FastF1's supported shape."""
        if (year, test_number) != (self.testing_event["Year"], 1):
            raise ValueError("unknown testing event")
        return self.testing_event


@dataclass
class FakeFastF1SessionFactory:
    """Zero-argument injected factory which records deterministic calls."""

    session: FakeFastF1Session
    calls: int = 0

    def __call__(self) -> FakeFastF1Session:
        self.calls += 1
        return self.session


def build_complete_session() -> FakeFastF1Session:
    """Build an already-loaded session with native, non-aligned telemetry streams."""
    session = _new_session()
    _set_tables(session, _complete_tables())
    return session


def build_2026_session_with_default_drs() -> FakeFastF1Session:
    """Build a 2026-shaped session whose public DRS channel defaults to zero."""
    session = _new_session()
    tables = _complete_tables()
    tables["car_data"] = {
        source_key: frame.assign(DRS=0)
        for source_key, frame in tables["car_data"].items()
    }
    _set_tables(session, tables)
    return session


def build_weather_session(scenario: WeatherSessionScenario = "normal_sparse") -> FakeFastF1Session:
    """Build a complete FastF1-shaped session with a named weather scenario.

    These scenarios change only ``weather_data``; the existing complete session
    remains the default fixture so unrelated adapter tests keep their history.
    """
    if scenario not in WEATHER_SESSION_SCENARIOS:
        raise ValueError(f"unknown weather session scenario: {scenario}")
    session = _new_session()
    tables = _complete_tables()
    tables["weather_data"] = _weather_table(scenario)
    _set_tables(session, tables)
    return session


def build_normal_sparse_weather_session() -> FakeFastF1Session:
    """Build complete weather observations at deterministic one-minute steps."""
    return build_weather_session("normal_sparse")


def build_no_weather_session() -> FakeFastF1Session:
    """Build a typed empty weather table for a session without weather data."""
    return build_weather_session("no_weather")


def build_partial_weather_session() -> FakeFastF1Session:
    """Build weather rows whose nullable channels are independently incomplete."""
    return build_weather_session("partial_null")


def build_zero_sentinel_weather_session() -> FakeFastF1Session:
    """Build uncorroborated and corroborated FastF1 zero-sentinel rows."""
    return build_weather_session("zero_sentinel")


def build_empty_session() -> FakeFastF1Session:
    """Build an already-loaded session whose in-scope tables retain their types but no rows."""
    session = _new_session()
    _set_tables(session, _empty_tables())
    return session


def build_session_with_missing_table(table_name: str) -> FakeFastF1Session:
    """Build a complete session with exactly one required session table absent."""
    _validate_table_name(table_name)
    session = _new_session()
    tables = _complete_tables()
    del tables[table_name]
    _set_tables(session, tables)
    return session


def build_session_with_empty_table(table_name: str) -> FakeFastF1Session:
    """Build a complete session with exactly one typed table empty."""
    _validate_table_name(table_name)
    session = _new_session()
    tables = _complete_tables()
    tables[table_name] = _empty_tables()[table_name]
    _set_tables(session, tables)
    return session


def build_permuted_session() -> FakeFastF1Session:
    """Build the complete data with every row and driver-key order reversed."""
    session = _new_session(driver_keys=list(reversed(tuple(_DRIVERS))))
    _set_tables(session, _permuted_tables(_complete_tables()))
    return session


def build_practice_session(practice_index: int = 1) -> FakeFastF1Session:
    """Build an already-loaded FP1/FP2/FP3 session with FastF1-documented practice shapes.

    FastF1 documents per-lap ``Position`` as NaN for FP1/FP2/FP3 while valid lap
    timing and telemetry remain available; the fixture models exactly that.
    """
    _validate_practice_index(practice_index)
    session = _new_session(name=f"FP{practice_index}")
    tables = _complete_tables()
    tables["laps"] = _practice_laps()
    tables["results"] = _practice_results()
    _set_tables(session, tables)
    return session


def build_qualifying_session() -> FakeFastF1Session:
    """Build an already-loaded Qualifying session with populated and missing Q1/Q2/Q3 times."""
    session = _new_session(name="Qualifying")
    tables = _complete_tables()
    tables["laps"] = _qualifying_laps()
    tables["results"] = _qualifying_results()
    _set_tables(session, tables)
    return session


def build_qualifying_session_with_cancelled_q3() -> FakeFastF1Session:
    """Build a Qualifying session whose Q3 was cancelled, leaving Q3 null for everyone.

    FastF1 models cancelled qualifying segments as missing timedelta values
    (``None`` from ``split_qualifying_sessions`` and NaT in results), so the
    fixture keeps Q1/Q2 populated and Q3 NaT for every driver.
    """
    session = _new_session(name="Qualifying")
    tables = _complete_tables()
    tables["laps"] = _qualifying_laps(cancelled_q3=True)
    tables["results"] = _qualifying_results(cancelled_q3=True)
    _set_tables(session, tables)
    return session


def build_qualifying_session_with_missing_status() -> FakeFastF1Session:
    """Build a qualifying session whose source status prerequisite is absent."""
    session = _new_session(name="Qualifying")
    tables = _complete_tables()
    tables["laps"] = _qualifying_laps(missing_status=True)
    tables["results"] = _qualifying_results()
    _set_tables(session, tables)
    return session


def build_qualifying_session_with_duplicate_partition() -> FakeFastF1Session:
    """Build a qualifying session whose split API repeats a lap across phases."""
    session = _new_session(name="Qualifying")
    tables = _complete_tables()
    tables["laps"] = _qualifying_laps(duplicate_partition=True)
    tables["results"] = _qualifying_results()
    _set_tables(session, tables)
    return session


def build_qualifying_session_with_missing_splitter() -> FakeFastF1Session:
    """Build a qualifying session whose laps expose no authoritative splitter."""
    session = build_qualifying_session()
    session.laps = session.laps.to_dict("records")
    return session


def build_qualifying_session_with_incomplete_partition() -> FakeFastF1Session:
    """Build a qualifying session whose split API omits a source lap."""
    session = _new_session(name="Qualifying")
    tables = _complete_tables()
    tables["laps"] = _qualifying_laps(incomplete_partition=True)
    tables["results"] = _qualifying_results()
    _set_tables(session, tables)
    return session


def build_qualifying_session_with_malformed_partition() -> FakeFastF1Session:
    """Build a qualifying session whose split API returns a malformed partition."""
    session = _new_session(name="Qualifying")
    tables = _complete_tables()
    tables["laps"] = _qualifying_laps(malformed_partition=True)
    tables["results"] = _qualifying_results()
    _set_tables(session, tables)
    return session


def build_qualifying_session_with_invalid_q1() -> FakeFastF1Session:
    """Build a Qualifying session whose Q1 value cannot be normalized.

    This is a deliberate invalid fixture used by explicit failing tests: the
    value is present but not a duration, so normalization must raise rather than
    silently coerce or drop it.
    """
    session = _new_session(name="Qualifying")
    tables = _complete_tables()
    tables["laps"] = _qualifying_laps()
    tables["results"] = _invalid_q1_results()
    _set_tables(session, tables)
    return session


def build_sprint_session() -> FakeFastF1Session:
    """Build an already-loaded Sprint session with race-shaped data.

    FastF1 Sprint sessions are race-shaped: full lap timing and classified
    results alongside native telemetry, weather, status, and messages.  The
    fixture models exactly that shape under a distinct ``Sprint`` identity so
    the mode normalizer can prove sprint is not mislabeled as a race.
    """
    session = _new_session(name="Sprint")
    _set_tables(session, _complete_tables())
    return session


def build_permuted_practice_session(practice_index: int = 1) -> FakeFastF1Session:
    """Build an FP session with every row and driver-key order reversed."""
    _validate_practice_index(practice_index)
    session = _new_session(name=f"FP{practice_index}", driver_keys=list(reversed(tuple(_DRIVERS))))
    tables = _complete_tables()
    tables["laps"] = _practice_laps()
    tables["results"] = _practice_results()
    _set_tables(session, _permuted_tables(tables))
    return session


def build_permuted_qualifying_session() -> FakeFastF1Session:
    """Build a Qualifying session with every row and driver-key order reversed."""
    session = _new_session(name="Qualifying", driver_keys=list(reversed(tuple(_DRIVERS))))
    tables = _complete_tables()
    tables["laps"] = _qualifying_laps()
    tables["results"] = _qualifying_results()
    _set_tables(session, _permuted_tables(tables))
    return session


def build_permuted_sprint_session() -> FakeFastF1Session:
    """Build a Sprint session with every row and driver-key order reversed."""
    session = _new_session(name="Sprint", driver_keys=list(reversed(tuple(_DRIVERS))))
    tables = _complete_tables()
    _set_tables(session, _permuted_tables(tables))
    return session


def build_session_factory(session: FakeFastF1Session | None = None) -> FakeFastF1SessionFactory:
    """Return an injectable no-network factory for a supplied or complete fake session."""
    return FakeFastF1SessionFactory(session or build_complete_session())


def build_testing_event_schedule() -> FakeFastF1EventSchedule:
    """Build a round-zero event available only through the testing lookup."""
    return FakeFastF1EventSchedule(
        {
            "Year": 2026,
            "RoundNumber": 0,
            "EventName": "Pre-Season Testing",
            "EventFormat": "testing",
            "Session1Date": pd.Timestamp("2026-02-18T01:00:00+11:00"),
            "Session1DateUtc": pd.Timestamp("2026-02-17T14:00:00"),
        }
    )


def _new_session(driver_keys: list[str] | None = None, name: str = "Race") -> FakeFastF1Session:
    keys = driver_keys or list(_DRIVERS)
    return FakeFastF1Session(
        event={
            "Year": 2026,
            "RoundNumber": 3,
            "EventName": "Australian Grand Prix",
            "EventFormat": "conventional",
        },
        name=name,
        drivers=keys,
        driver_records={key: dict(_DRIVERS[key]) for key in keys},
        # FastF1's UTC schedule timestamps are timezone-naive pandas Timestamps.
        date=pd.Timestamp("2026-03-08T05:00:00"),
        t0_date=pd.Timestamp("2026-03-08T05:00:00"),
        # This is an offset from t0_date, never an absolute session datetime.
        session_start_time=timedelta(minutes=12),
    )


def _complete_tables() -> dict[str, Any]:
    return {
        "laps": pd.DataFrame(
            {
                "DriverNumber": ["44", "1"],
                "LapNumber": [1, 1],
                "LapStartTime": [pd.Timedelta(0, unit="s"), pd.Timedelta(0, unit="s")],
                "LapTime": [pd.Timedelta(92_500, unit="ms"), pd.NaT],
                "Compound": ["SOFT", pd.NA],
            }
        ),
        "results": pd.DataFrame(
            {"DriverNumber": ["44", "1"], "Position": [1, pd.NA], "Points": [25.0, float("nan")]}
        ),
        "car_data": {
            "44": pd.DataFrame(
                {
                    "SessionTime": [pd.Timedelta(1, unit="s"), pd.Timedelta(1_240, unit="ms"), pd.Timedelta(1_240, unit="ms")],
                    "Time": [pd.Timedelta(1, unit="s"), pd.Timedelta(1_240, unit="ms"), pd.Timedelta(1_240, unit="ms")],
                    "Date": [pd.Timestamp("2026-03-08T05:00:01"), pd.NaT, pd.Timestamp("2026-03-08T05:00:01.240")],
                    "Speed": [280.0, np.nan, 281.0],
                    "RPM": [11000, pd.NA, 11100],
                    "nGear": [7, 7, 7],
                    "Throttle": [98.0, pd.NA, 99.0],
                    "Brake": [False, pd.NA, False],
                    "DRS": [12, 12, 12],
                    "Source": ["car", "car", "car"],
                }
            ),
            "1": pd.DataFrame(
                {"SessionTime": [pd.Timedelta(1_720, unit="ms")], "Time": [pd.Timedelta(1_720, unit="ms")], "Speed": [300.0], "Source": ["car"]}
            ),
        },
        "pos_data": {
            "44": pd.DataFrame(
                {
                    "SessionTime": [pd.Timedelta(1_100, unit="ms"), pd.Timedelta(1_480, unit="ms"), pd.Timedelta(1_480, unit="ms")],
                    "Time": [pd.Timedelta(1_100, unit="ms"), pd.Timedelta(1_480, unit="ms"), pd.Timedelta(1_480, unit="ms")],
                    "Date": [pd.Timestamp("2026-03-08T05:00:01.100"), pd.NaT, pd.Timestamp("2026-03-08T05:00:01.480")],
                    "X": [10.0, np.nan, 11.0],
                    "Y": [20.0, 21.0, 21.0],
                    "Z": [0.0, pd.NA, 0.0],
                    "Status": ["OnTrack", pd.NA, "OnTrack"],
                    "Source": ["pos", "pos", "pos"],
                }
            ),
            "1": pd.DataFrame(
                {"SessionTime": [pd.Timedelta(2_030, unit="ms")], "Time": [pd.Timedelta(2_030, unit="ms")], "X": [30.0], "Source": ["pos"]}
            ),
        },
        "weather_data": pd.DataFrame(
            {"Time": [pd.Timedelta(0, unit="s"), pd.Timedelta(1, unit="min")], "AirTemp": [24.5, np.nan], "Humidity": [pd.NA, 61.0], "Rainfall": [False, pd.NA]}
        ),
        "track_status": pd.DataFrame(
            {"Time": [pd.Timedelta(0, unit="s"), pd.Timedelta(1_500, unit="ms")], "Status": ["1", "2"], "Message": ["AllClear", None]}
        ),
        "race_control_messages": pd.DataFrame(
            {
                # Race-control Time is absolute UTC, unlike the duration-shaped streams above.
                "Time": [pd.Timestamp("2026-03-08T05:00:01.250"), pd.Timestamp("2026-03-08T05:00:01.750")],
                "Category": ["Flag", pd.NA],
                "Message": ["GREEN FLAG", "TRACK CLEAR"],
                "Flag": [None, pd.NA],
                "Scope": [pd.NA, None],
                "RacingNumber": ["44", None],
                "Lap": [1, pd.NA],
            }
        ),
    }


def _weather_table(scenario: WeatherSessionScenario) -> pd.DataFrame:
    if scenario in {"no_weather", "old_delivery_no_sidecar"}:
        return pd.DataFrame({
            "Time": pd.Series(dtype="timedelta64[ns]"),
            "AirTemp": pd.Series(dtype="float64"),
            "Humidity": pd.Series(dtype="float64"),
            "Pressure": pd.Series(dtype="float64"),
            "Rainfall": pd.Series(dtype="boolean"),
            "TrackTemp": pd.Series(dtype="float64"),
            "WindDirection": pd.Series(dtype="int64"),
            "WindSpeed": pd.Series(dtype="float64"),
        })
    if scenario == "partial_null":
        rows = [
            _fastf1_weather_row(
                0, AirTemp=21.0, Rainfall=False,
            ),
            _fastf1_weather_row(
                60_000, Humidity=55.0, WindSpeed=2.5,
            ),
            _fastf1_weather_row(
                120_000, Pressure=1012.0, TrackTemp=34.0,
            ),
        ]
    elif scenario == "zero_sentinel":
        rows = [
            _fastf1_weather_row(
                0, AirTemp=0.0, Humidity=0.0, Pressure=0.0, Rainfall=False,
                TrackTemp=0.0, WindDirection=0, WindSpeed=0.0,
            ),
            _fastf1_weather_row(
                60_000, AirTemp=22.0, Humidity=0.0, Pressure=1012.0, Rainfall=False,
                TrackTemp=34.0, WindDirection=0, WindSpeed=0.0,
            ),
        ]
    else:
        rows = [
            _fastf1_weather_row(
                0, AirTemp=21.0, Humidity=50.0, Pressure=1013.0, Rainfall=False,
                TrackTemp=35.0, WindDirection=90, WindSpeed=2.5,
            ),
            _fastf1_weather_row(
                60_000, AirTemp=22.0, Humidity=51.0, Pressure=1012.0, Rainfall=True,
                TrackTemp=36.0, WindDirection=270, WindSpeed=3.0,
            ),
            _fastf1_weather_row(
                120_000, AirTemp=22.5, Humidity=52.0, Pressure=1012.0, Rainfall=False,
                TrackTemp=37.0, WindDirection=280, WindSpeed=3.2,
            ),
        ]
    return pd.DataFrame(rows, columns=[
        "Time", "AirTemp", "Humidity", "Pressure", "Rainfall", "TrackTemp",
        "WindDirection", "WindSpeed",
    ])


def _fastf1_weather_row(session_time_ms: int, **changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "Time": pd.Timedelta(session_time_ms, unit="ms"),
        "AirTemp": pd.NA,
        "Humidity": pd.NA,
        "Pressure": pd.NA,
        "Rainfall": pd.NA,
        "TrackTemp": pd.NA,
        "WindDirection": pd.NA,
        "WindSpeed": pd.NA,
    }
    row.update(changes)
    return row


def _empty_tables() -> dict[str, Any]:
    tables = _complete_tables()
    return {
        name: {key: frame.iloc[0:0].copy() for key, frame in table.items()} if isinstance(table, dict) else table.iloc[0:0].copy()
        for name, table in tables.items()
    }


def _practice_laps() -> pd.DataFrame:
    """Lap timing with valid timed laps and FastF1's documented NaN per-lap Position."""
    return pd.DataFrame(
        {
            "DriverNumber": ["44", "1"],
            "LapNumber": [1, 1],
            "LapStartTime": [pd.Timedelta(0, unit="s"), pd.Timedelta(0, unit="s")],
            "Time": [pd.Timedelta(92_500, unit="ms"), pd.Timedelta(93_200, unit="ms")],
            "LapTime": [pd.Timedelta(92_500, unit="ms"), pd.Timedelta(93_200, unit="ms")],
            "Compound": ["SOFT", "MEDIUM"],
            "Position": [np.nan, np.nan],
        }
    )


def _practice_results() -> pd.DataFrame:
    """Practice results with nullable positions where classification is unavailable.

    FastF1 derives practice results ordering where available and leaves it
    missing otherwise; ``Position``/``ClassifiedPosition`` must stay nullable.
    """
    return pd.DataFrame(
        {
            "DriverNumber": ["44", "1"],
            "Position": [1.0, np.nan],
            "ClassifiedPosition": ["1", np.nan],
            "GridPosition": [np.nan, np.nan],
            "Status": ["Finished", None],
            "Points": [0.0, np.nan],
            "Laps": [23.0, 22.0],
            "Time": [pd.NaT, pd.NaT],
        }
    )


def _qualifying_laps(
    *, cancelled_q3: bool = False, missing_status: bool = False, duplicate_partition: bool = False,
    incomplete_partition: bool = False, malformed_partition: bool = False,
) -> FakeQualifyingLaps:
    """Flying laps with valid times and FastF1's documented NaN per-lap Position.

    Each row carries the FastF1 3.8.3 evidence columns the qualifying flying-lap
    policy consumes (PitInTime/PitOutTime/IsAccurate/Deleted/TrackStatus plus
    the three sector durations and session timestamps).  All three laps are
    accurate, non-deleted, pit-free flying laps whose sector sums equal their
    LapTime, so the canonical adapter and the lap-sector sidecar can derive a
    deterministic ``flying`` classification for each phase.
    """
    laps = FakeQualifyingLaps(
        {
            "DriverNumber": ["44", "1", "44"],
            "LapNumber": [1, 1, 2],
            "LapStartTime": [
                pd.Timedelta(0, unit="s"),
                pd.Timedelta(0, unit="s"),
                pd.Timedelta(110, unit="s"),
            ],
            "Time": [
                pd.Timedelta(105_123, unit="ms"),
                pd.Timedelta(105_200, unit="ms"),
                pd.Timedelta(213_999, unit="ms"),
            ],
            "LapTime": [
                pd.Timedelta(105_123, unit="ms"),
                pd.Timedelta(105_200, unit="ms"),
                pd.Timedelta(103_999, unit="ms"),
            ],
            "Compound": ["SOFT", "SOFT", "SOFT"],
            "Position": [np.nan, np.nan, np.nan],
            "QualifyingPhase": ["Q1", "Q2", "Q3"],
            "PitInTime": [pd.NaT, pd.NaT, pd.NaT],
            "PitOutTime": [pd.NaT, pd.NaT, pd.NaT],
            "IsAccurate": [True, True, True],
            "Deleted": [False, False, False],
            "TrackStatus": ["1", "1", "1"],
            "Sector1Time": [
                pd.Timedelta(35_041, unit="ms"),
                pd.Timedelta(35_000, unit="ms"),
                pd.Timedelta(34_500, unit="ms"),
            ],
            "Sector2Time": [
                pd.Timedelta(35_041, unit="ms"),
                pd.Timedelta(35_000, unit="ms"),
                pd.Timedelta(34_500, unit="ms"),
            ],
            "Sector3Time": [
                pd.Timedelta(35_041, unit="ms"),
                pd.Timedelta(35_200, unit="ms"),
                pd.Timedelta(34_999, unit="ms"),
            ],
            "Sector1SessionTime": [
                pd.Timedelta(35_041, unit="ms"),
                pd.Timedelta(35_000, unit="ms"),
                pd.Timedelta(144_500, unit="ms"),
            ],
            "Sector2SessionTime": [
                pd.Timedelta(70_082, unit="ms"),
                pd.Timedelta(70_000, unit="ms"),
                pd.Timedelta(179_000, unit="ms"),
            ],
            "Sector3SessionTime": [
                pd.Timedelta(105_123, unit="ms"),
                pd.Timedelta(105_200, unit="ms"),
                pd.Timedelta(213_999, unit="ms"),
            ],
        }
    )
    if cancelled_q3:
        laps = laps.loc[laps["QualifyingPhase"] != "Q3"].copy()
    laps._cancelled_q3 = cancelled_q3
    laps._missing_status = missing_status
    laps._duplicate_partition = duplicate_partition
    laps._incomplete_partition = incomplete_partition
    laps._malformed_partition = malformed_partition
    return laps


def build_qualifying_session_with_incidents() -> FakeFastF1Session:
    """Build a Qualifying session whose race-control stream carries CarEvents."""
    session = _new_session(name="Qualifying")
    tables = _complete_tables()
    tables["laps"] = _qualifying_laps()
    tables["results"] = _qualifying_results()
    tables["race_control_messages"] = _incident_messages()
    _set_tables(session, tables)
    return session


def _incident_messages() -> pd.DataFrame:
    """Race-control messages mixing terminal CarEvents and non-incident rows.

    ``CAR 44 CRASH`` and ``CAR 1 STOPS`` are actionable terminal CarEvents;
    ``YELLOW FLAG`` is a session-wide Flag (never a per-driver marker) and
    ``CAR 44 OFF TRACK`` is a non-terminal CarEvent (never proof of an
    incident).  Race-control ``Time`` is absolute UTC, unlike the duration
    streams, so all timestamps anchor to ``t0_date``.
    """
    return pd.DataFrame(
        {
            "Time": [
                pd.Timestamp("2026-03-08T05:00:40.000"),
                pd.Timestamp("2026-03-08T05:00:50.000"),
                pd.Timestamp("2026-03-08T05:01:00.000"),
                pd.Timestamp("2026-03-08T05:01:10.000"),
            ],
            "Category": ["CarEvent", "CarEvent", "Flag", "CarEvent"],
            "Message": [
                "CAR 44 CRASH",
                "CAR 1 STOPS",
                "YELLOW FLAG",
                "CAR 44 OFF TRACK",
            ],
            "Flag": [None, None, "YELLOW", None],
            "Scope": [None, None, "Track", None],
            "RacingNumber": ["44", None, None, None],
            "Lap": [7, 9, None, None],
        }
    )


def _qualifying_results(*, cancelled_q3: bool = False) -> pd.DataFrame:
    """Qualifying results with populated, missing, and optionally cancelled Q values.

    ``cancelled_q3`` models a cancelled Q3 segment: FastF1 leaves every Q3 value
    as NaT for a cancelled segment, distinct from a driver simply being
    eliminated (missing Q2/Q3 for the driver).
    """
    q3 = pd.NaT if cancelled_q3 else pd.Timedelta(103_999, unit="ms")
    return pd.DataFrame(
        {
            "DriverNumber": ["44", "1"],
            "Position": [1.0, 2.0],
            "ClassifiedPosition": ["1", "2"],
            "GridPosition": [np.nan, np.nan],
            "Q1": [pd.Timedelta(105_123, unit="ms"), pd.Timedelta(105_200, unit="ms")],
            "Q2": [pd.Timedelta(104_567, unit="ms"), pd.NaT],
            "Q3": [q3, pd.NaT],
            "Status": ["Finished", "Finished"],
            "Points": [0.0, 0.0],
            "Laps": [18.0, 16.0],
        }
    )


def _invalid_q1_results() -> pd.DataFrame:
    """A single qualifying result whose Q1 value cannot be normalized."""
    return pd.DataFrame({"DriverNumber": ["44"], "Q1": ["not-a-duration"]})


def _permuted_tables(tables: Mapping[str, Any]) -> dict[str, Any]:
    """Reverse every row and driver-key order within the supplied tables."""
    permuted: dict[str, Any] = {}
    for name, table in tables.items():
        if isinstance(table, dict):
            permuted[name] = {
                key: frame.iloc[::-1].reset_index(drop=True)
                for key, frame in reversed(tuple(table.items()))
            }
        else:
            permuted[name] = table.iloc[::-1].reset_index(drop=True)
    return permuted


def _set_tables(session: FakeFastF1Session, tables: Mapping[str, Any]) -> None:
    for name, table in tables.items():
        setattr(session, name, table)


def _validate_practice_index(practice_index: int) -> None:
    if practice_index not in (1, 2, 3):
        raise ValueError(f"unknown practice session: {practice_index}; expected 1, 2, or 3")


def _validate_table_name(table_name: str) -> None:
    if table_name not in SESSION_TABLE_NAMES:
        raise ValueError(f"unknown session table: {table_name}")
