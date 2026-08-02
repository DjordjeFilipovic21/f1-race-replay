"""Ordered, immutable schemas for versioned canonical session tables."""

from collections.abc import Mapping
from types import MappingProxyType

import polars as pl

from .canonical_contract import ContractVersion, get_canonical_contract

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

SESSION_METADATA_SCHEMA_V1: Schema = MappingProxyType(
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
        "sector_1_duration_ms": pl.Int64,
        "sector_2_duration_ms": pl.Int64,
        "sector_3_duration_ms": pl.Int64,
        "sector_1_session_time_ms": pl.Int64,
        "sector_2_session_time_ms": pl.Int64,
        "sector_3_session_time_ms": pl.Int64,
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

RESULTS_SCHEMA_V1: Schema = MappingProxyType(
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

CANONICAL_TABLE_SCHEMAS_V1: Mapping[str, Schema] = MappingProxyType(
    {
        "car_telemetry": CAR_TELEMETRY_SCHEMA,
        "position_telemetry": POSITION_TELEMETRY_SCHEMA,
        "session_metadata": SESSION_METADATA_SCHEMA_V1,
        "drivers": DRIVERS_SCHEMA,
        "laps": LAPS_SCHEMA,
        "stints": STINTS_SCHEMA,
        "weather": WEATHER_SCHEMA,
        "track_status_intervals": TRACK_STATUS_INTERVALS_SCHEMA,
        "race_control_messages": RACE_CONTROL_MESSAGES_SCHEMA,
        "results": RESULTS_SCHEMA_V1,
    }
)
CANONICAL_TABLE_NAMES_V1 = tuple(CANONICAL_TABLE_SCHEMAS_V1)

RESULTS_SCHEMA_V2: Schema = MappingProxyType({
    **RESULTS_SCHEMA_V1,
    "q1_time_ms": pl.Int64,
    "q2_time_ms": pl.Int64,
    "q3_time_ms": pl.Int64,
})

SESSION_METADATA_SCHEMA_V2: Schema = MappingProxyType({
    "session_id": pl.String,
    "year": pl.Int16,
    "round_number": pl.Int16,
    "event_name": pl.String,
    "session_name": pl.String,
    "session_type": pl.String,
    "session_mode": pl.String,
    "session_start_time_utc": pl.Datetime("ms", "UTC"),
})

CANONICAL_TABLE_SCHEMAS_V2: Mapping[str, Schema] = MappingProxyType({
    **CANONICAL_TABLE_SCHEMAS_V1,
    "session_metadata": SESSION_METADATA_SCHEMA_V2,
    "results": RESULTS_SCHEMA_V2,
})
CANONICAL_TABLE_NAMES_V2 = tuple(CANONICAL_TABLE_SCHEMAS_V2)

CAR_TELEMETRY_SCHEMA_V1 = CAR_TELEMETRY_SCHEMA
POSITION_TELEMETRY_SCHEMA_V1 = POSITION_TELEMETRY_SCHEMA
DRIVERS_SCHEMA_V1 = DRIVERS_SCHEMA
LAPS_SCHEMA_V1 = LAPS_SCHEMA
STINTS_SCHEMA_V1 = STINTS_SCHEMA
WEATHER_SCHEMA_V1 = WEATHER_SCHEMA
TRACK_STATUS_INTERVALS_SCHEMA_V1 = TRACK_STATUS_INTERVALS_SCHEMA
RACE_CONTROL_MESSAGES_SCHEMA_V1 = RACE_CONTROL_MESSAGES_SCHEMA
CAR_TELEMETRY_SCHEMA_V2 = CAR_TELEMETRY_SCHEMA
POSITION_TELEMETRY_SCHEMA_V2 = POSITION_TELEMETRY_SCHEMA
DRIVERS_SCHEMA_V2 = DRIVERS_SCHEMA
LAPS_SCHEMA_V2 = LAPS_SCHEMA
STINTS_SCHEMA_V2 = STINTS_SCHEMA
WEATHER_SCHEMA_V2 = WEATHER_SCHEMA
TRACK_STATUS_INTERVALS_SCHEMA_V2 = TRACK_STATUS_INTERVALS_SCHEMA
RACE_CONTROL_MESSAGES_SCHEMA_V2 = RACE_CONTROL_MESSAGES_SCHEMA

# The unversioned names are deliberately the v1 read contract.  Existing
# adapters, readers, and committed fixtures therefore keep their behavior.
SESSION_METADATA_SCHEMA = SESSION_METADATA_SCHEMA_V1
RESULTS_SCHEMA = RESULTS_SCHEMA_V1
CANONICAL_TABLE_SCHEMAS = CANONICAL_TABLE_SCHEMAS_V1
CANONICAL_TABLE_NAMES = CANONICAL_TABLE_NAMES_V1


def get_canonical_schema(table_name: str, version: ContractVersion | str = "v1") -> Schema:
    """Return the ordered immutable schema for a table and contract version."""
    schemas = CANONICAL_TABLE_SCHEMAS_V1 if get_canonical_contract(version).version == "v1" else CANONICAL_TABLE_SCHEMAS_V2
    return schemas[table_name]


def get_canonical_schema_v1(table_name: str) -> Schema:
    """Return a v1 schema for compatibility readers."""
    return get_canonical_schema(table_name, "v1")


def get_canonical_schema_v2(table_name: str) -> Schema:
    """Return a v2 schema for new writers and strict v2 readers."""
    return get_canonical_schema(table_name, "v2")


__all__ = [
    "CANONICAL_TABLE_NAMES",
    "CANONICAL_TABLE_SCHEMAS",
    "CANONICAL_TABLE_NAMES_V1",
    "CANONICAL_TABLE_NAMES_V2",
    "CANONICAL_TABLE_SCHEMAS_V1",
    "CANONICAL_TABLE_SCHEMAS_V2",
    "CAR_TELEMETRY_SCHEMA",
    "CAR_TELEMETRY_SCHEMA_V1",
    "CAR_TELEMETRY_SCHEMA_V2",
    "DRIVERS_SCHEMA",
    "DRIVERS_SCHEMA_V1",
    "DRIVERS_SCHEMA_V2",
    "LAPS_SCHEMA",
    "LAPS_SCHEMA_V1",
    "LAPS_SCHEMA_V2",
    "POSITION_TELEMETRY_SCHEMA",
    "POSITION_TELEMETRY_SCHEMA_V1",
    "POSITION_TELEMETRY_SCHEMA_V2",
    "RACE_CONTROL_MESSAGES_SCHEMA",
    "RACE_CONTROL_MESSAGES_SCHEMA_V1",
    "RACE_CONTROL_MESSAGES_SCHEMA_V2",
    "RESULTS_SCHEMA",
    "RESULTS_SCHEMA_V1",
    "RESULTS_SCHEMA_V2",
    "SESSION_METADATA_SCHEMA",
    "SESSION_METADATA_SCHEMA_V1",
    "SESSION_METADATA_SCHEMA_V2",
    "STINTS_SCHEMA",
    "STINTS_SCHEMA_V1",
    "STINTS_SCHEMA_V2",
    "TRACK_STATUS_INTERVALS_SCHEMA",
    "TRACK_STATUS_INTERVALS_SCHEMA_V1",
    "TRACK_STATUS_INTERVALS_SCHEMA_V2",
    "WEATHER_SCHEMA",
    "WEATHER_SCHEMA_V1",
    "WEATHER_SCHEMA_V2",
    "get_canonical_schema",
    "get_canonical_schema_v1",
    "get_canonical_schema_v2",
]
