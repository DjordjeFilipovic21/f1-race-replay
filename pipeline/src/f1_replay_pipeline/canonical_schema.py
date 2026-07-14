"""Ordered, immutable schemas for Phase 1 canonical telemetry tables."""

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

CANONICAL_TABLE_SCHEMAS: Mapping[str, Schema] = MappingProxyType(
    {
        "car_telemetry": CAR_TELEMETRY_SCHEMA,
        "position_telemetry": POSITION_TELEMETRY_SCHEMA,
    }
)
CANONICAL_TABLE_NAMES = tuple(CANONICAL_TABLE_SCHEMAS)


def get_canonical_schema(table_name: str) -> Schema:
    """Return the ordered immutable schema for a canonical telemetry table."""
    return CANONICAL_TABLE_SCHEMAS[table_name]


__all__ = [
    "CANONICAL_TABLE_NAMES",
    "CANONICAL_TABLE_SCHEMAS",
    "CAR_TELEMETRY_SCHEMA",
    "POSITION_TELEMETRY_SCHEMA",
    "get_canonical_schema",
]
