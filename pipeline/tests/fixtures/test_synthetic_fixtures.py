import polars as pl
from polars.testing import assert_frame_equal

from f1_replay_pipeline.domain.canonical_schema import (
    CAR_TELEMETRY_SCHEMA,
    POSITION_TELEMETRY_SCHEMA,
)
from fixtures.synthetic_fixtures import (
    build_car_frame,
    build_car_source_frame,
    build_position_frame,
    build_position_source_frame,
)


def test_canonical_fixture_builders_are_deterministic_and_schema_exact():
    # Arrange
    expected_car = build_car_frame()
    expected_position = build_position_frame()

    # Act
    actual_car = build_car_frame()
    actual_position = build_position_frame()

    # Assert
    assert_frame_equal(actual_car, expected_car)
    assert_frame_equal(actual_position, expected_position)
    assert list(actual_car.schema.items()) == list(CAR_TELEMETRY_SCHEMA.items())
    assert list(actual_position.schema.items()) == list(POSITION_TELEMETRY_SCHEMA.items())


def test_source_fixture_builders_preserve_separate_native_cadences_without_resampling():
    # Arrange
    source_car = build_car_source_frame()
    source_position = build_position_source_frame()
    canonical_car = build_car_frame()
    canonical_position = build_position_frame()

    # Act
    source_car_times = source_car.get_column("session_time_ms").unique().sort().to_list()
    source_position_times = source_position.get_column("session_time_ms").unique().sort().to_list()
    canonical_car_times = canonical_car.get_column("session_time_ms").unique().sort().to_list()
    canonical_position_times = canonical_position.get_column("session_time_ms").unique().sort().to_list()

    # Assert
    assert source_car_times == canonical_car_times == [1000, 1012, 1025]
    assert source_position_times == canonical_position_times == [1003, 1023, 1048]
    assert set(canonical_car_times).isdisjoint(canonical_position_times)


def test_canonical_fixture_builders_sort_and_choose_deterministic_duplicate_winners():
    # Arrange
    source_car = build_car_source_frame()
    source_position = build_position_source_frame()
    car = build_car_frame()
    position = build_position_frame()

    # Act
    source_car_duplicates = source_car.filter(pl.col("session_time_ms") == 1000).height
    source_position_duplicates = source_position.filter(pl.col("session_time_ms") == 1023).height
    car_duplicate_winner = car.filter(pl.col("session_time_ms") == 1000).row(0, named=True)
    position_duplicate_winner = position.filter(pl.col("session_time_ms") == 1023).row(0, named=True)
    car_keys = car.select("session_id", "driver_id", "session_time_ms").rows()
    position_keys = position.select("session_id", "driver_id", "session_time_ms").rows()

    # Assert
    assert car_keys == [
        ("2026-example-race", "HAM", 1000),
        ("2026-example-race", "HAM", 1012),
        ("2026-example-race", "VER", 1025),
    ]
    assert position_keys == [
        ("2026-example-race", "HAM", 1003),
        ("2026-example-race", "HAM", 1023),
        ("2026-example-race", "HAM", 1048),
    ]
    assert source_car_duplicates == 2
    assert source_position_duplicates == 2
    assert car_duplicate_winner["speed_kph"] == 299.5
    assert position_duplicate_winner["x"] == 11.0


def test_canonical_fixture_builders_keep_missing_measurements_as_null():
    # Arrange
    car = build_car_frame()
    position = build_position_frame()

    # Act
    car_rpm = car.filter(pl.col("session_time_ms") == 1012).item(0, "rpm")
    position_x = position.filter(pl.col("session_time_ms") == 1048).item(0, "x")

    # Assert
    assert car_rpm is None
    assert position_x is None
