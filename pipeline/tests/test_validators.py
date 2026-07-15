import math
from collections.abc import Mapping

import polars as pl
import pytest

from f1_replay_pipeline.canonical_schema import CANONICAL_TABLE_NAMES, get_canonical_schema
from f1_replay_pipeline.validators import CanonicalValidationError, validate_canonical_table


def _frame(table_name: str, rows: list[Mapping[str, object]] | None = None) -> pl.DataFrame:
    defaults = {
        "session_metadata": {"session_id": "2026-race", "year": 2026, "round_number": 1, "event_name": "Race", "session_name": "Race", "session_type": "R", "session_start_time_utc": None},
        "drivers": {"session_id": "2026-race", "driver_id": "HAM", "source_driver_key": "44", "driver_number": 44, "full_name": "Lewis Hamilton", "team_name": "Ferrari", "team_colour": "ff0000"},
        "car_telemetry": {"session_id": "2026-race", "driver_id": "HAM", "source_driver_key": "44", "session_time_ms": 1, "speed_kph": 300.0, "rpm": 11000.0, "gear": 8, "throttle_pct": 100.0, "brake": False, "drs": 12, "source": "car"},
        "position_telemetry": {"session_id": "2026-race", "driver_id": "HAM", "source_driver_key": "44", "session_time_ms": 1, "x": 1.0, "y": 2.0, "z": 3.0, "status": "OnTrack", "source": "pos"},
        "laps": {"session_id": "2026-race", "driver_id": "HAM", "lap_number": 1, "stint_number": 1, "lap_start_time_ms": 1, "lap_end_time_ms": 90001, "lap_duration_ms": 90000, "pit_in_time_ms": None, "pit_out_time_ms": None, "compound": "SOFT", "tyre_life": 1, "is_fresh_tyre": True, "track_status": "1", "is_accurate": True, "deleted": False, "deleted_reason": None},
        "stints": {"session_id": "2026-race", "driver_id": "HAM", "stint_number": 1, "start_lap_number": 1, "end_lap_number": 10, "start_time_ms": 1, "end_time_ms": 900001, "compound": "SOFT", "tyre_life_at_start": 1, "is_fresh_tyre": True},
        "weather": {"session_id": "2026-race", "session_time_ms": 1, "air_temperature_c": 20.0, "humidity_pct": 50.0, "pressure_mbar": 1000.0, "rainfall": False, "track_temperature_c": 30.0, "wind_direction_deg": 180.0, "wind_speed_mps": 2.0},
        "track_status_intervals": {"session_id": "2026-race", "start_time_ms": 1, "end_time_ms": 2, "status": "1", "message": None},
        "race_control_messages": {"session_id": "2026-race", "session_time_ms": 1, "message_index": 0, "category": None, "flag": None, "scope": None, "message": "Track clear", "driver_id": None, "lap_number": None},
        "results": {"session_id": "2026-race", "driver_id": "HAM", "classified_position": "1", "grid_position": 1, "status": "Finished", "points": 25.0, "laps_completed": 58, "result_time_ms": 5400000},
    }
    return pl.DataFrame(rows if rows is not None else [defaults[table_name]], schema=get_canonical_schema(table_name))


@pytest.mark.parametrize("table_name", [name for name in CANONICAL_TABLE_NAMES if name != "session_metadata"])
def test_validate_canonical_table_accepts_every_typed_empty_table(table_name):
    validate_canonical_table(table_name, _frame(table_name, []))


@pytest.mark.parametrize("rows", [[], [_frame("session_metadata").to_dicts()[0]] * 2])
def test_validate_canonical_table_requires_exactly_one_session_metadata_row(rows):
    with pytest.raises(CanonicalValidationError, match="exactly 1 row"):
        validate_canonical_table("session_metadata", _frame("session_metadata", rows))


@pytest.mark.parametrize("table_name", CANONICAL_TABLE_NAMES)
def test_validate_canonical_table_accepts_every_valid_table(table_name):
    validate_canonical_table(table_name, _frame(table_name))


def test_validate_canonical_table_reports_exact_schema_contract_errors():
    frame = _frame("car_telemetry").select(pl.all().exclude("source")).with_columns(
        pl.col("session_time_ms").cast(pl.Float64), pl.lit("x").alias("unexpected")
    ).select(["driver_id", *[column for column in _frame("car_telemetry").columns if column not in {"driver_id", "source"}], "unexpected"])
    with pytest.raises(CanonicalValidationError) as error:
        validate_canonical_table("car_telemetry", frame)
    message = str(error.value)
    assert "missing columns: source" in message
    assert "unexpected columns: unexpected" in message
    assert "column order must be" in message
    assert "session_time_ms expected Int64, received Float64" in message


@pytest.mark.parametrize(
    ("table_name", "column", "value", "message"),
    [
        ("drivers", "session_id", " ", "non-empty"),
        ("drivers", "driver_id", "ham", "canonical"),
        ("drivers", "source_driver_key", " ", "non-empty"),
        ("laps", "lap_start_time_ms", None, "required values"),
        ("weather", "session_time_ms", -1, "non-negative"),
        ("race_control_messages", "message", " ", "non-empty"),
    ],
)
def test_validate_canonical_table_rejects_invalid_required_identity_and_time_values(table_name, column, value, message):
    frame = _frame(table_name).with_columns(pl.lit(value).cast(get_canonical_schema(table_name)[column]).alias(column))
    with pytest.raises(CanonicalValidationError, match=message):
        validate_canonical_table(table_name, frame)


@pytest.mark.parametrize(
    ("table_name", "column"),
    [
        ("car_telemetry", "speed_kph"), ("position_telemetry", "x"),
        ("weather", "air_temperature_c"), ("results", "points"),
    ],
)
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_validate_canonical_table_rejects_nonfinite_measurements(table_name, column, value):
    frame = _frame(table_name).with_columns(pl.lit(value).alias(column))
    with pytest.raises(CanonicalValidationError, match="measurements contain NaN or infinity"):
        validate_canonical_table(table_name, frame)


@pytest.mark.parametrize("table_name", [name for name in CANONICAL_TABLE_NAMES if name != "session_metadata"])
def test_validate_canonical_table_rejects_unsorted_rows_and_duplicate_keys(table_name):
    first = _frame(table_name).to_dicts()[0]
    second = dict(first)
    key = {"session_metadata": "session_id", "drivers": "driver_id", "car_telemetry": "session_time_ms", "position_telemetry": "session_time_ms", "laps": "lap_number", "stints": "stint_number", "weather": "session_time_ms", "track_status_intervals": "start_time_ms", "race_control_messages": "message_index", "results": "driver_id"}[table_name]
    second[key] = "VER" if key == "driver_id" else (
        "2027-race" if key == "session_id" else first[key] + 1
    )
    if table_name == "drivers":
        second["source_driver_key"] = "1"
    ordered = _frame(table_name, [first, second])
    with pytest.raises(CanonicalValidationError, match="duplicate canonical key"):
        validate_canonical_table(table_name, _frame(table_name, [first, first]))
    with pytest.raises(CanonicalValidationError, match="must be sorted ascending"):
        validate_canonical_table(table_name, _frame(table_name, [second, first]))
    validate_canonical_table(table_name, ordered)


@pytest.mark.parametrize("table_name", ["car_telemetry", "position_telemetry", "drivers"])
def test_validate_canonical_table_rejects_non_bijective_source_driver_mapping(table_name):
    first = _frame(table_name).to_dicts()[0]
    second = dict(first, driver_id="VER")
    with pytest.raises(CanonicalValidationError, match="map one-to-one per session"):
        validate_canonical_table(table_name, _frame(table_name, [first, second]))


@pytest.mark.parametrize("table_name", ["car_telemetry", "position_telemetry", "drivers"])
def test_validate_canonical_table_rejects_multiple_source_keys_for_one_driver(table_name):
    first = _frame(table_name).to_dicts()[0]
    second = dict(first, source_driver_key="1")
    if table_name != "drivers":
        second["session_time_ms"] = 2
    with pytest.raises(CanonicalValidationError, match="map one-to-one per session"):
        validate_canonical_table(table_name, _frame(table_name, [first, second]))


@pytest.mark.parametrize("table_name", ["laps", "stints", "track_status_intervals"])
def test_validate_canonical_table_rejects_inconsistent_intervals(table_name):
    end_column = "lap_end_time_ms" if table_name == "laps" else "end_time_ms"
    frame = _frame(table_name).with_columns(pl.lit(0).cast(pl.Int64).alias(end_column))
    with pytest.raises(CanonicalValidationError, match="must not precede"):
        validate_canonical_table(table_name, frame)
