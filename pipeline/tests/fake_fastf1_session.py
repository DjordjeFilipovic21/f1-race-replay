"""Deterministic duck-typed FastF1 session inputs for adapter tests.

These fakes expose only public-shaped data consumed at the application
boundary.  They deliberately do not import FastF1 or mimic its internals.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

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
    tables = _complete_tables()
    for name, table in tables.items():
        if isinstance(table, dict):
            tables[name] = {
                key: frame.iloc[::-1].reset_index(drop=True)
                for key, frame in reversed(tuple(table.items()))
            }
        else:
            tables[name] = table.iloc[::-1].reset_index(drop=True)
    _set_tables(session, tables)
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


def _new_session(driver_keys: list[str] | None = None) -> FakeFastF1Session:
    keys = driver_keys or list(_DRIVERS)
    return FakeFastF1Session(
        event={
            "Year": 2026,
            "RoundNumber": 3,
            "EventName": "Australian Grand Prix",
            "EventFormat": "conventional",
        },
        name="Race",
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
                "LapStartTime": [pd.Timedelta("0s"), pd.Timedelta("0s")],
                "LapTime": [pd.Timedelta("00:01:32.500"), pd.NaT],
                "Compound": ["SOFT", pd.NA],
            }
        ),
        "results": pd.DataFrame(
            {"DriverNumber": ["44", "1"], "Position": [1, pd.NA], "Points": [25.0, float("nan")]}
        ),
        "car_data": {
            "44": pd.DataFrame(
                {
                    "SessionTime": [pd.Timedelta("1s"), pd.Timedelta("1.240s"), pd.Timedelta("1.240s")],
                    "Time": [pd.Timedelta("1s"), pd.Timedelta("1.240s"), pd.Timedelta("1.240s")],
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
                {"SessionTime": [pd.Timedelta("1.720s")], "Time": [pd.Timedelta("1.720s")], "Speed": [300.0], "Source": ["car"]}
            ),
        },
        "pos_data": {
            "44": pd.DataFrame(
                {
                    "SessionTime": [pd.Timedelta("1.100s"), pd.Timedelta("1.480s"), pd.Timedelta("1.480s")],
                    "Time": [pd.Timedelta("1.100s"), pd.Timedelta("1.480s"), pd.Timedelta("1.480s")],
                    "Date": [pd.Timestamp("2026-03-08T05:00:01.100"), pd.NaT, pd.Timestamp("2026-03-08T05:00:01.480")],
                    "X": [10.0, np.nan, 11.0],
                    "Y": [20.0, 21.0, 21.0],
                    "Z": [0.0, pd.NA, 0.0],
                    "Status": ["OnTrack", pd.NA, "OnTrack"],
                    "Source": ["pos", "pos", "pos"],
                }
            ),
            "1": pd.DataFrame(
                {"SessionTime": [pd.Timedelta("2.030s")], "Time": [pd.Timedelta("2.030s")], "X": [30.0], "Source": ["pos"]}
            ),
        },
        "weather_data": pd.DataFrame(
            {"Time": [pd.Timedelta("0s"), pd.Timedelta("1min")], "AirTemp": [24.5, np.nan], "Humidity": [pd.NA, 61.0], "Rainfall": [False, pd.NA]}
        ),
        "track_status": pd.DataFrame(
            {"Time": [pd.Timedelta("0s"), pd.Timedelta("1.500s")], "Status": ["1", "2"], "Message": ["AllClear", None]}
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


def _empty_tables() -> dict[str, Any]:
    tables = _complete_tables()
    return {
        name: {key: frame.iloc[0:0].copy() for key, frame in table.items()} if isinstance(table, dict) else table.iloc[0:0].copy()
        for name, table in tables.items()
    }


def _set_tables(session: FakeFastF1Session, tables: Mapping[str, Any]) -> None:
    for name, table in tables.items():
        setattr(session, name, table)


def _validate_table_name(table_name: str) -> None:
    if table_name not in SESSION_TABLE_NAMES:
        raise ValueError(f"unknown session table: {table_name}")
