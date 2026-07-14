import polars as pl
import pytest

from f1_replay_pipeline.canonical_schema import get_canonical_schema
from f1_replay_pipeline.validators import CanonicalValidationError, validate_canonical_table


def test_validate_canonical_table_accepts_ordered_typed_rows_with_null_measurements():
    # Arrange
    frame = pl.DataFrame(
        {
            "session_id": ["2026-race", "2026-race"],
            "driver_id": ["HAM", "VER"],
            "source_driver_key": ["44", "1"],
            "session_time_ms": [1, 2],
            "speed_kph": [None, 300.5],
            "rpm": [None, None],
            "gear": [None, 8],
            "throttle_pct": [None, 98.0],
            "brake": [None, False],
            "drs": [None, 12],
            "source": ["car", "car"],
        },
        schema=get_canonical_schema("car_telemetry"),
    )

    # Act / Assert
    validate_canonical_table("car_telemetry", frame)


def test_validate_canonical_table_reports_missing_extra_reordered_and_wrong_typed_columns():
    # Arrange
    frame = pl.DataFrame(
        {
            "driver_id": ["HAM"],
            "session_id": ["2026-race"],
            "source_driver_key": ["44"],
            "session_time_ms": [1.5],
            "speed_kph": [300.0],
            "rpm": [11_000.0],
            "gear": [8],
            "throttle_pct": [100.0],
            "brake": [False],
            "drs": [12],
            "unexpected": ["value"],
        }
    )

    # Act / Assert
    with pytest.raises(CanonicalValidationError) as error:
        validate_canonical_table("car_telemetry", frame)

    message = str(error.value)
    assert "missing columns: source" in message
    assert "unexpected columns: unexpected" in message
    assert "column order must be" in message
    assert "session_time_ms expected Int64, received Float64" in message


@pytest.mark.parametrize(
    ("frame", "expected_message"),
    [
        (
            pl.DataFrame(
                {
                    "session_id": ["2026-race"],
                    "driver_id": ["HAM"],
                    "source_driver_key": ["44"],
                    "session_time_ms": [-1],
                    "speed_kph": [300.0],
                    "rpm": [11_000.0],
                    "gear": [8],
                    "throttle_pct": [100.0],
                    "brake": [False],
                    "drs": [12],
                    "source": ["car"],
                },
                schema=get_canonical_schema("car_telemetry"),
            ),
            "non-negative integer milliseconds",
        ),
        (
            pl.DataFrame(
                {
                    "session_id": ["2026-race", "2026-race"],
                    "driver_id": ["VER", "HAM"],
                    "source_driver_key": ["1", "44"],
                    "session_time_ms": [2, 1],
                    "x": [1.0, 2.0],
                    "y": [1.0, 2.0],
                    "z": [1.0, 2.0],
                    "status": [None, None],
                    "source": ["pos", "pos"],
                },
                schema=get_canonical_schema("position_telemetry"),
            ),
            "must be sorted ascending by canonical key",
        ),
        (
            pl.DataFrame(
                {
                    "session_id": ["2026-race", "2026-race"],
                    "driver_id": ["HAM", "HAM"],
                    "source_driver_key": ["44", "44"],
                    "session_time_ms": [1, 1],
                    "x": [None, 1.0],
                    "y": [None, 1.0],
                    "z": [None, 1.0],
                    "status": [None, None],
                    "source": ["pos", "pos"],
                },
                schema=get_canonical_schema("position_telemetry"),
            ),
            "duplicate canonical key",
        ),
    ],
)
def test_validate_canonical_table_rejects_invalid_times_order_and_duplicate_keys(
    frame, expected_message
):
    # Act / Assert
    with pytest.raises(CanonicalValidationError, match=expected_message):
        validate_canonical_table(
            "car_telemetry" if "drs" in frame.columns else "position_telemetry", frame
        )


@pytest.mark.parametrize(
    ("column", "value", "expected_message"),
    [
        ("session_id", "  ", "session_id must contain non-empty"),
        ("source_driver_key", "  ", "source_driver_key must contain non-empty"),
        ("driver_id", "ham", "driver_id must contain canonical"),
        ("driver_id", "D044", "driver_id must contain canonical"),
        ("speed_kph", float("nan"), "contain NaN or infinity"),
        ("speed_kph", float("inf"), "contain NaN or infinity"),
        ("speed_kph", float("-inf"), "contain NaN or infinity"),
    ],
)
def test_validate_canonical_table_rejects_invalid_identifiers_and_nonfinite_measurements(
    column, value, expected_message
):
    frame = pl.DataFrame(
        {
            "session_id": ["2026-race"],
            "driver_id": ["HAM"],
            "source_driver_key": ["44"],
            "session_time_ms": [1],
            "speed_kph": [300.0],
            "rpm": [11_000.0],
            "gear": [8],
            "throttle_pct": [100.0],
            "brake": [False],
            "drs": [12],
            "source": ["car"],
        },
        schema=get_canonical_schema("car_telemetry"),
    ).with_columns(pl.lit(value).cast(get_canonical_schema("car_telemetry")[column]).alias(column))

    with pytest.raises(CanonicalValidationError, match=expected_message):
        validate_canonical_table("car_telemetry", frame)


@pytest.mark.parametrize(
    "source_keys, driver_ids",
    [(["44", "44"], ["HAM", "VER"]), (["44", "63"], ["HAM", "HAM"])],
)
def test_validate_canonical_table_rejects_non_bijective_session_driver_source_mapping(source_keys, driver_ids):
    frame = pl.DataFrame(
        {
            "session_id": ["2026-race", "2026-race"],
            "driver_id": driver_ids,
            "source_driver_key": source_keys,
            "session_time_ms": [1, 2],
            "speed_kph": [300.0, 301.0],
            "rpm": [11_000.0, 11_100.0],
            "gear": [8, 8],
            "throttle_pct": [100.0, 100.0],
            "brake": [False, False],
            "drs": [12, 12],
            "source": ["car", "car"],
        },
        schema=get_canonical_schema("car_telemetry"),
    )

    with pytest.raises(CanonicalValidationError, match="map one-to-one per session"):
        validate_canonical_table("car_telemetry", frame)
