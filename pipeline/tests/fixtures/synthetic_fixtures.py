"""Deterministic, in-memory telemetry fixtures for canonical pipeline tests.

The ``*_source_frame`` builders intentionally retain unsorted duplicate source
rows.  The corresponding ``*_frame`` builders apply the documented canonical
sort-and-deduplicate policy while preserving each stream's native timestamps.
"""

from collections.abc import Mapping, Sequence
from typing import Literal

import polars as pl

from f1_replay_pipeline.domain.canonical_schema import (
    CAR_TELEMETRY_SCHEMA,
    POSITION_TELEMETRY_SCHEMA,
    WEATHER_SCHEMA,
)
from f1_replay_pipeline.domain.normalizers import sort_and_deduplicate_rows


WeatherFixtureScenario = Literal[
    "normal_sparse",
    "no_weather",
    "partial_null",
    "zero_sentinel",
    "old_delivery_no_sidecar",
]

WEATHER_FIXTURE_SCENARIOS: tuple[WeatherFixtureScenario, ...] = (
    "normal_sparse",
    "no_weather",
    "partial_null",
    "zero_sentinel",
    "old_delivery_no_sidecar",
)


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


def build_weather_rows(scenario: WeatherFixtureScenario = "normal_sparse") -> list[dict[str, object]]:
    """Return a deterministic native-cadence weather scenario.

    The old-delivery scenario intentionally has no canonical weather rows: an
    old browser manifest has no ``weatherSidecar`` reference to load.
    """
    if scenario not in WEATHER_FIXTURE_SCENARIOS:
        raise ValueError(f"unknown weather fixture scenario: {scenario}")
    if scenario in {"no_weather", "old_delivery_no_sidecar"}:
        return []
    if scenario == "partial_null":
        return [
            _weather_row(0, air_temperature_c=21.0, rainfall=False),
            _weather_row(60_000, humidity_pct=55.0, wind_speed_mps=2.5),
            _weather_row(120_000, pressure_mbar=1012.0, track_temperature_c=34.0),
        ]
    if scenario == "zero_sentinel":
        return [
            _weather_row(
                0,
                air_temperature_c=0.0,
                humidity_pct=0.0,
                pressure_mbar=0.0,
                rainfall=False,
                track_temperature_c=0.0,
                wind_direction_deg=0.0,
                wind_speed_mps=0.0,
            ),
            _weather_row(
                60_000,
                air_temperature_c=22.0,
                humidity_pct=0.0,
                pressure_mbar=1012.0,
                rainfall=False,
                track_temperature_c=34.0,
                wind_direction_deg=0.0,
                wind_speed_mps=0.0,
            ),
        ]
    return [
        _weather_row(
            0,
            air_temperature_c=21.0,
            humidity_pct=50.0,
            pressure_mbar=1013.0,
            rainfall=False,
            track_temperature_c=35.0,
            wind_direction_deg=90.0,
            wind_speed_mps=2.5,
        ),
        _weather_row(
            60_000,
            air_temperature_c=22.0,
            humidity_pct=51.0,
            pressure_mbar=1012.0,
            rainfall=True,
            track_temperature_c=36.0,
            wind_direction_deg=270.0,
            wind_speed_mps=3.0,
        ),
        _weather_row(
            120_000,
            air_temperature_c=22.5,
            humidity_pct=52.0,
            pressure_mbar=1012.0,
            rainfall=False,
            track_temperature_c=37.0,
            wind_direction_deg=280.0,
            wind_speed_mps=3.2,
        ),
    ]


def build_weather_frame(scenario: WeatherFixtureScenario = "normal_sparse") -> pl.DataFrame:
    """Return the canonical weather frame for a named deterministic scenario."""
    return pl.DataFrame(
        build_weather_rows(scenario), schema=dict(WEATHER_SCHEMA), strict=True,
    )


def build_normal_sparse_weather_frame() -> pl.DataFrame:
    """Return three complete observations at the native one-minute cadence."""
    return build_weather_frame("normal_sparse")


def build_no_weather_frame() -> pl.DataFrame:
    """Return a typed empty weather table for sessions without FastF1 weather."""
    return build_weather_frame("no_weather")


def build_partial_weather_frame() -> pl.DataFrame:
    """Return aligned rows with independent nullable weather measurements."""
    return build_weather_frame("partial_null")


def build_zero_sentinel_weather_frame() -> pl.DataFrame:
    """Return uncorroborated and corroborated FastF1 zero-sentinel rows."""
    return build_weather_frame("zero_sentinel")


def build_old_delivery_manifest() -> dict[str, object]:
    """Return a schema-shaped v1 manifest that predates the weather sidecar."""
    return {
        "contractVersion": "v1",
        "fixtureId": "synthetic-race",
        "fixtureName": "Synthetic Race",
        "formatVersion": "browser-delivery-v1",
        "deliveryVersion": "legacy-delivery",
        "schemas": {
            "manifest": "urn:f1-cache-replay:schema:replay-data:v1:manifest",
            "chunk": "urn:f1-cache-replay:schema:replay-data:v1:chunk",
            "trackAssets": "urn:f1-cache-replay:schema:replay-data:v1:track-assets",
        },
        "trackAssets": {
            "path": "track-assets.json",
            "schemaId": "urn:f1-cache-replay:schema:replay-data:v1:track-assets",
            "sha256": "a" * 64,
        },
        "chunks": [{
            "sequence": 1,
            "path": "chunks/chunk-001.json",
            "schemaId": "urn:f1-cache-replay:schema:replay-data:v1:chunk",
            "startMs": 0,
            "endMs": 2_000,
            "overlapWithPreviousMs": 0,
        }],
        "drivers": [{
            "id": "HAM",
            "displayName": "Lewis Hamilton",
            "teamName": "Mercedes",
            "colorHex": "#00D2BE",
            "carNumber": "44",
        }],
    }


def _weather_row(session_time_ms: int, **changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "session_id": "synthetic-race",
        "session_time_ms": session_time_ms,
        "air_temperature_c": None,
        "humidity_pct": None,
        "pressure_mbar": None,
        "rainfall": None,
        "track_temperature_c": None,
        "wind_direction_deg": None,
        "wind_speed_mps": None,
    }
    row.update(changes)
    return row


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
