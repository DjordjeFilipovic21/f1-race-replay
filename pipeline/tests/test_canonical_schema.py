import pytest
import polars as pl

from f1_replay_pipeline.canonical_schema import (
    CANONICAL_TABLE_NAMES,
    CANONICAL_TABLE_SCHEMAS,
    get_canonical_schema,
)


def test_canonical_table_names_are_exact_and_ordered():
    assert CANONICAL_TABLE_NAMES == ("car_telemetry", "position_telemetry")
    assert tuple(CANONICAL_TABLE_SCHEMAS) == CANONICAL_TABLE_NAMES


def test_car_telemetry_schema_has_exact_column_order_and_dtypes():
    assert list(get_canonical_schema("car_telemetry").items()) == [
        ("session_id", pl.String),
        ("driver_id", pl.String),
        ("source_driver_key", pl.String),
        ("session_time_ms", pl.Int64),
        ("speed_kph", pl.Float64),
        ("rpm", pl.Float64),
        ("gear", pl.Int16),
        ("throttle_pct", pl.Float64),
        ("brake", pl.Boolean),
        ("drs", pl.Int16),
        ("source", pl.String),
    ]


def test_position_telemetry_schema_has_exact_column_order_and_dtypes():
    assert list(get_canonical_schema("position_telemetry").items()) == [
        ("session_id", pl.String),
        ("driver_id", pl.String),
        ("source_driver_key", pl.String),
        ("session_time_ms", pl.Int64),
        ("x", pl.Float64),
        ("y", pl.Float64),
        ("z", pl.Float64),
        ("status", pl.String),
        ("source", pl.String),
    ]


def test_schema_lookup_is_immutable():
    schema = get_canonical_schema("car_telemetry")

    with pytest.raises(TypeError):
        schema["new_column"] = pl.String  # type: ignore[index]
