"""Deterministic, in-memory telemetry fixtures for canonical pipeline tests.

The ``*_source_frame`` builders intentionally retain unsorted duplicate source
rows.  The corresponding ``*_frame`` builders apply the documented canonical
sort-and-deduplicate policy while preserving each stream's native timestamps.
"""

from collections.abc import Mapping, Sequence

import polars as pl

from f1_replay_pipeline.canonical_schema import (
    CAR_TELEMETRY_SCHEMA,
    POSITION_TELEMETRY_SCHEMA,
)
from f1_replay_pipeline.normalizers import sort_and_deduplicate_rows


def build_car_source_frame() -> pl.DataFrame:
    """Return unsorted car observations, including a duplicate native sample."""
    return _frame(_car_source_rows(), CAR_TELEMETRY_SCHEMA)


def build_position_source_frame() -> pl.DataFrame:
    """Return unsorted position observations, including a duplicate native sample."""
    return _frame(_position_source_rows(), POSITION_TELEMETRY_SCHEMA)


def build_car_frame() -> pl.DataFrame:
    """Return the car fixture after canonical sorting and deduplication."""
    return _canonical_frame(
        _car_source_rows(),
        CAR_TELEMETRY_SCHEMA,
        ("speed_kph", "rpm", "gear", "throttle_pct", "brake", "drs"),
    )


def build_position_frame() -> pl.DataFrame:
    """Return the position fixture after canonical sorting and deduplication."""
    return _canonical_frame(_position_source_rows(), POSITION_TELEMETRY_SCHEMA, ("x", "y", "z", "status"))


def _canonical_frame(
    rows: Sequence[Mapping[str, object]],
    schema: Mapping[str, pl.DataType],
    measurement_fields: Sequence[str],
) -> pl.DataFrame:
    normalized = sort_and_deduplicate_rows(
        rows, column_order=tuple(schema), measurement_fields=measurement_fields
    )
    return _frame(normalized, schema)


def _frame(rows: Sequence[Mapping[str, object]], schema: Mapping[str, pl.DataType]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=dict(schema), strict=True)


def _car_source_rows() -> list[dict[str, object]]:
    return [
        _car_row(
            driver_id="VER", source_driver_key="1", session_time_ms=1025,
            speed_kph=302.0, rpm=11_400.0, gear=7, throttle_pct=98.0,
            brake=False, drs=12,
        ),
        _car_row(
            session_time_ms=1000, speed_kph=300.0, rpm=11_000.0, gear=None,
            throttle_pct=65.0, brake=None, drs=10,
        ),
        _car_row(
            session_time_ms=1012, speed_kph=301.0, rpm=None, gear=6,
            throttle_pct=70.0, brake=False, drs=None,
        ),
        _car_row(
            session_time_ms=1000, speed_kph=299.5, rpm=11_000.0, gear=6,
            throttle_pct=67.0, brake=False, drs=10,
        ),
    ]


def _position_source_rows() -> list[dict[str, object]]:
    return [
        _position_row(session_time_ms=1048, x=None, y=25.0, z=0.0, status="OnTrack"),
        _position_row(session_time_ms=1003, x=1.0, y=None, z=0.0, status=None),
        _position_row(session_time_ms=1023, x=12.0, y=22.0, z=0.0, status="OnTrack"),
        _position_row(session_time_ms=1023, x=11.0, y=22.0, z=0.0, status="OnTrack"),
    ]


def _car_row(**changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "session_id": "2026-example-race",
        "driver_id": "HAM",
        "source_driver_key": "44",
        "session_time_ms": 0,
        "speed_kph": None,
        "rpm": None,
        "gear": None,
        "throttle_pct": None,
        "brake": None,
        "drs": None,
        "source": "car",
    }
    row.update(changes)
    return row


def _position_row(**changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "session_id": "2026-example-race",
        "driver_id": "HAM",
        "source_driver_key": "44",
        "session_time_ms": 0,
        "x": None,
        "y": None,
        "z": None,
        "status": None,
        "source": "pos",
    }
    row.update(changes)
    return row
