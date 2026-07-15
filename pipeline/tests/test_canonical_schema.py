import pytest
import polars as pl

from f1_replay_pipeline.canonical_schema import (
    CANONICAL_TABLE_NAMES,
    CANONICAL_TABLE_SCHEMAS,
    get_canonical_schema,
)


def test_canonical_table_names_are_exact_and_ordered():
    assert CANONICAL_TABLE_NAMES == (
        "car_telemetry",
        "position_telemetry",
        "session_metadata",
        "drivers",
        "laps",
        "stints",
        "weather",
        "track_status_intervals",
        "race_control_messages",
        "results",
    )
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


@pytest.mark.parametrize(
    ("table_name", "expected_schema"),
    [
        ("session_metadata", [
            ("session_id", pl.String), ("year", pl.Int16), ("round_number", pl.Int16),
            ("event_name", pl.String), ("session_name", pl.String),
            ("session_type", pl.String), ("session_start_time_utc", pl.Datetime("ms", "UTC")),
        ]),
        ("drivers", [
            ("session_id", pl.String), ("driver_id", pl.String), ("source_driver_key", pl.String),
            ("driver_number", pl.Int16), ("full_name", pl.String), ("team_name", pl.String),
            ("team_colour", pl.String),
        ]),
        ("laps", [
            ("session_id", pl.String), ("driver_id", pl.String), ("lap_number", pl.Int16),
            ("stint_number", pl.Int16), ("lap_start_time_ms", pl.Int64),
            ("lap_end_time_ms", pl.Int64), ("lap_duration_ms", pl.Int64),
            ("pit_in_time_ms", pl.Int64), ("pit_out_time_ms", pl.Int64),
            ("compound", pl.String), ("tyre_life", pl.Int16), ("is_fresh_tyre", pl.Boolean),
            ("track_status", pl.String), ("is_accurate", pl.Boolean), ("deleted", pl.Boolean),
            ("deleted_reason", pl.String),
        ]),
        ("stints", [
            ("session_id", pl.String), ("driver_id", pl.String), ("stint_number", pl.Int16),
            ("start_lap_number", pl.Int16), ("end_lap_number", pl.Int16),
            ("start_time_ms", pl.Int64), ("end_time_ms", pl.Int64), ("compound", pl.String),
            ("tyre_life_at_start", pl.Int16), ("is_fresh_tyre", pl.Boolean),
        ]),
        ("weather", [
            ("session_id", pl.String), ("session_time_ms", pl.Int64),
            ("air_temperature_c", pl.Float64), ("humidity_pct", pl.Float64),
            ("pressure_mbar", pl.Float64), ("rainfall", pl.Boolean),
            ("track_temperature_c", pl.Float64), ("wind_direction_deg", pl.Float64),
            ("wind_speed_mps", pl.Float64),
        ]),
        ("track_status_intervals", [
            ("session_id", pl.String), ("start_time_ms", pl.Int64), ("end_time_ms", pl.Int64),
            ("status", pl.String), ("message", pl.String),
        ]),
        ("race_control_messages", [
            ("session_id", pl.String), ("session_time_ms", pl.Int64), ("message_index", pl.Int32),
            ("category", pl.String), ("flag", pl.String), ("scope", pl.String),
            ("message", pl.String), ("driver_id", pl.String), ("lap_number", pl.Int16),
        ]),
        ("results", [
                ("session_id", pl.String), ("driver_id", pl.String), ("classified_position", pl.String),
            ("grid_position", pl.Int16), ("status", pl.String), ("points", pl.Float64),
            ("laps_completed", pl.Int16), ("result_time_ms", pl.Int64),
        ]),
    ],
)
def test_remaining_schemas_have_exact_column_order_and_dtypes(table_name, expected_schema):
    assert list(get_canonical_schema(table_name).items()) == expected_schema


@pytest.mark.parametrize("table_name", CANONICAL_TABLE_NAMES)
def test_canonical_schemas_construct_typed_empty_frames(table_name):
    expected_schema = get_canonical_schema(table_name)

    frame = pl.DataFrame(schema=expected_schema)

    assert frame.height == 0
    assert list(frame.schema.items()) == list(expected_schema.items())


def test_schema_lookup_is_immutable():
    schema = get_canonical_schema("car_telemetry")

    with pytest.raises(TypeError):
        schema["new_column"] = pl.String  # type: ignore[index]
