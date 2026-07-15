from datetime import datetime, timezone
import math

import polars as pl
import pytest

from f1_replay_pipeline.canonical_schema import get_canonical_schema
from f1_replay_pipeline.logical_hashes import encode_logical_table, logical_table_sha256
from f1_replay_pipeline.validators import CanonicalValidationError


def _frame(table_name: str, rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=get_canonical_schema(table_name))


def _drivers(**changes: object) -> pl.DataFrame:
    row = {
        "session_id": "2026-race", "driver_id": "HAM", "source_driver_key": "44",
        "driver_number": -44, "full_name": "Lewis Hamilton", "team_name": "Ferrari",
        "team_colour": "ff0000",
    }
    row.update(changes)
    return _frame("drivers", [row])


def _car(**changes: object) -> pl.DataFrame:
    row = {
        "session_id": "2026-race", "driver_id": "HAM", "source_driver_key": "44",
        "session_time_ms": 1, "speed_kph": 0.0, "rpm": -1.7976931348623157e308,
        "gear": -8, "throttle_pct": 1.7976931348623157e308, "brake": True, "drs": 12,
        "source": "car",
    }
    row.update(changes)
    return _frame("car_telemetry", [row])


@pytest.mark.parametrize(
    ("label", "table_name", "frame", "digest"),
    [
        ("empty typed frame", "drivers", _frame("drivers", []), "6c96ca35293dfee57d4c3fe077de962daffe7339b7ec27a461725752504c6a8f"),
        ("null and Unicode", "drivers", _drivers(full_name=None, team_name="Scuderia café 🏎️"), "3a43fc5210ee9ecadf67ad715a4b7903043b22351d985c80480bc446935d0ee7"),
        ("booleans signed integers and float edges", "car_telemetry", _car(), "56e077a0dfe784d26b0a370121135426d684b777cc505709ae7c4843d706b83d"),
        ("utc datetime", "session_metadata", _frame("session_metadata", [{
            "session_id": "2026-race", "year": 2026, "round_number": 1, "event_name": "Race",
            "session_name": "Race", "session_type": "R",
            "session_start_time_utc": datetime(1969, 12, 31, 23, 59, 59, 999000, tzinfo=timezone.utc),
        }]), "a993d7b0afd85dffc47a71b3ee7fd1df87ec17095ba1df2c027cb572f3262147"),
    ],
)
def test_logical_hash_v1_golden_vectors_are_exact(label, table_name, frame, digest):
    assert logical_table_sha256(table_name, frame) == digest, label


def test_negative_zero_has_the_same_logical_hash_as_positive_zero():
    assert logical_table_sha256("car_telemetry", _car(speed_kph=-0.0)) == logical_table_sha256("car_telemetry", _car(speed_kph=0.0))


def test_canonical_normalization_restores_permuted_input_hash_stability():
    rows = [_drivers(driver_id="HAM", source_driver_key="44").to_dicts()[0], _drivers(driver_id="VER", source_driver_key="1", driver_number=1).to_dicts()[0]]
    normalized = _frame("drivers", rows)
    permuted_normalized = _frame("drivers", list(reversed(rows))).sort(["session_id", "driver_id"])

    assert logical_table_sha256("drivers", normalized) == logical_table_sha256("drivers", permuted_normalized)


def test_logical_hash_changes_for_valid_ordered_value_schema_and_declared_type_changes():
    changed_value = _drivers(team_name="McLaren")
    schema_changed = _frame("results", [{
        "session_id": "2026-race", "driver_id": "HAM", "classified_position": "1", "grid_position": 1,
        "status": "Finished", "points": 25.0, "laps_completed": 58, "result_time_ms": 5_400_000,
    }])

    assert logical_table_sha256("drivers", _drivers()) != logical_table_sha256("drivers", changed_value)
    assert logical_table_sha256("drivers", _drivers()) != logical_table_sha256("results", schema_changed)


def test_public_hash_validates_named_frame_before_encoding():
    invalid_schema = _drivers().with_columns(pl.col("driver_number").cast(pl.Int32))
    invalid_row_order = _frame("drivers", [_drivers(driver_id="VER", source_driver_key="1", driver_number=1).to_dicts()[0], _drivers().to_dicts()[0]])

    with pytest.raises(CanonicalValidationError, match="dtype mismatches"):
        logical_table_sha256("drivers", invalid_schema)
    with pytest.raises(CanonicalValidationError, match="must be sorted ascending"):
        logical_table_sha256("drivers", invalid_row_order)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nonfinite_float_values_are_rejected_before_encoding(value):
    with pytest.raises(CanonicalValidationError, match="NaN or infinity"):
        encode_logical_table("car_telemetry", _car(speed_kph=value))
