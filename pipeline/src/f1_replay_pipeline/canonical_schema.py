"""Ordered, immutable schemas for Phase 1 canonical session tables."""

from collections.abc import Mapping
from types import MappingProxyType

import polars as pl

Schema = Mapping[str, pl.DataType]

CAR_TELEMETRY_SCHEMA: Schema = MappingProxyType(
    {
        "session_id": pl.String,
        "driver_id": pl.String,
        "source_driver_key": pl.String,
        "session_time_ms": pl.Int64,
        "speed_kph": pl.Float64,
        "rpm": pl.Float64,
        "gear": pl.Int16,
        "throttle_pct": pl.Float64,
        "brake": pl.Boolean,
        "drs": pl.Int16,
        "source": pl.String,
    }
)

POSITION_TELEMETRY_SCHEMA: Schema = MappingProxyType(
    {
        "session_id": pl.String,
        "driver_id": pl.String,
        "source_driver_key": pl.String,
        "session_time_ms": pl.Int64,
        "x": pl.Float64,
        "y": pl.Float64,
        "z": pl.Float64,
        "status": pl.String,
        "source": pl.String,
    }
)

SESSION_METADATA_SCHEMA: Schema = MappingProxyType(
    {
        "session_id": pl.String,
        "year": pl.Int16,
        "round_number": pl.Int16,
        "event_name": pl.String,
        "session_name": pl.String,
        "session_type": pl.String,
        "session_start_time_utc": pl.Datetime("ms", "UTC"),
    }
)

DRIVERS_SCHEMA: Schema = MappingProxyType(
    {
        "session_id": pl.String,
        "driver_id": pl.String,
        "source_driver_key": pl.String,
        "driver_number": pl.Int16,
        "full_name": pl.String,
        "team_name": pl.String,
        "team_colour": pl.String,
    }
)

LAPS_SCHEMA: Schema = MappingProxyType(
    {
        "session_id": pl.String,
        "driver_id": pl.String,
        "lap_number": pl.Int16,
        "stint_number": pl.Int16,
        "lap_start_time_ms": pl.Int64,
        "lap_end_time_ms": pl.Int64,
        "lap_duration_ms": pl.Int64,
        "pit_in_time_ms": pl.Int64,
        "pit_out_time_ms": pl.Int64,
        "compound": pl.String,
        "tyre_life": pl.Int16,
        "is_fresh_tyre": pl.Boolean,
        "track_status": pl.String,
        "is_accurate": pl.Boolean,
        "deleted": pl.Boolean,
        "deleted_reason": pl.String,
    }
)

STINTS_SCHEMA: Schema = MappingProxyType(
    {
        "session_id": pl.String,
        "driver_id": pl.String,
        "stint_number": pl.Int16,
        "start_lap_number": pl.Int16,
        "end_lap_number": pl.Int16,
        "start_time_ms": pl.Int64,
        "end_time_ms": pl.Int64,
        "compound": pl.String,
        "tyre_life_at_start": pl.Int16,
        "is_fresh_tyre": pl.Boolean,
    }
)

WEATHER_SCHEMA: Schema = MappingProxyType(
    {
        "session_id": pl.String,
        "session_time_ms": pl.Int64,
        "air_temperature_c": pl.Float64,
        "humidity_pct": pl.Float64,
        "pressure_mbar": pl.Float64,
        "rainfall": pl.Boolean,
        "track_temperature_c": pl.Float64,
        "wind_direction_deg": pl.Float64,
        "wind_speed_mps": pl.Float64,
    }
)

TRACK_STATUS_INTERVALS_SCHEMA: Schema = MappingProxyType(
    {
        "session_id": pl.String,
        "start_time_ms": pl.Int64,
        "end_time_ms": pl.Int64,
        "status": pl.String,
        "message": pl.String,
    }
)

RACE_CONTROL_MESSAGES_SCHEMA: Schema = MappingProxyType(
    {
        "session_id": pl.String,
        "session_time_ms": pl.Int64,
        "message_index": pl.Int32,
        "category": pl.String,
        "flag": pl.String,
        "scope": pl.String,
        "message": pl.String,
        "driver_id": pl.String,
        "lap_number": pl.Int16,
    }
)

RESULTS_SCHEMA: Schema = MappingProxyType(
    {
        "session_id": pl.String,
        "driver_id": pl.String,
        "classified_position": pl.String,
        "grid_position": pl.Int16,
        "status": pl.String,
        "points": pl.Float64,
        "laps_completed": pl.Int16,
        "result_time_ms": pl.Int64,
    }
)

CANONICAL_TABLE_SCHEMAS: Mapping[str, Schema] = MappingProxyType(
    {
        "car_telemetry": CAR_TELEMETRY_SCHEMA,
        "position_telemetry": POSITION_TELEMETRY_SCHEMA,
        "session_metadata": SESSION_METADATA_SCHEMA,
        "drivers": DRIVERS_SCHEMA,
        "laps": LAPS_SCHEMA,
        "stints": STINTS_SCHEMA,
        "weather": WEATHER_SCHEMA,
        "track_status_intervals": TRACK_STATUS_INTERVALS_SCHEMA,
        "race_control_messages": RACE_CONTROL_MESSAGES_SCHEMA,
        "results": RESULTS_SCHEMA,
    }
)
CANONICAL_TABLE_NAMES = tuple(CANONICAL_TABLE_SCHEMAS)


def get_canonical_schema(table_name: str) -> Schema:
    """Return the ordered immutable schema for a canonical table."""
    return CANONICAL_TABLE_SCHEMAS[table_name]


__all__ = [
    "CANONICAL_TABLE_NAMES",
    "CANONICAL_TABLE_SCHEMAS",
    "CAR_TELEMETRY_SCHEMA",
    "DRIVERS_SCHEMA",
    "LAPS_SCHEMA",
    "POSITION_TELEMETRY_SCHEMA",
    "RACE_CONTROL_MESSAGES_SCHEMA",
    "RESULTS_SCHEMA",
    "SESSION_METADATA_SCHEMA",
    "STINTS_SCHEMA",
    "TRACK_STATUS_INTERVALS_SCHEMA",
    "WEATHER_SCHEMA",
    "get_canonical_schema",
]
