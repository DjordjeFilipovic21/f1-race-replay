from datetime import timedelta
from decimal import Decimal
from itertools import permutations
import math

import numpy as np
import pandas as pd
import pytest

from f1_replay_pipeline.domain.normalizers import (
    NormalizationError,
    normalize_driver_id,
    normalize_nullable_scalar,
    normalize_session_time_ms,
    sort_and_deduplicate_rows,
)


class NanosecondDuration:
    def __init__(self, value: int) -> None:
        self.value = value


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("12.4995"), 12_500),
        (Decimal("12.4994"), 12_499),
        (timedelta(seconds=12, microseconds=499_500), 12_500),
        (NanosecondDuration(12_499_500_000), 12_500),
        (NanosecondDuration(12_499_499_999), 12_499),
    ],
)
def test_normalize_session_time_ms_uses_nanosecond_safe_half_up_rounding(value, expected):
    assert normalize_session_time_ms(value) == expected


@pytest.mark.parametrize("value", [None, -1, Decimal("NaN"), math.inf, True])
def test_normalize_session_time_ms_rejects_invalid_values(value):
    with pytest.raises(NormalizationError):
        normalize_session_time_ms(value)


@pytest.mark.parametrize("value", [None, pd.NA, pd.NaT, np.float32("nan"), np.float32("inf"), Decimal("-Infinity")])
def test_normalize_nullable_scalar_converts_missing_and_non_finite_values_to_null(value):
    assert normalize_nullable_scalar(value) is None


@pytest.mark.parametrize("value", [pd.NA, pd.NaT])
def test_normalize_session_time_ms_rejects_pandas_missing_values_before_scalar_conversion(value):
    with pytest.raises(NormalizationError, match="missing required timestamp"):
        normalize_session_time_ms(value)


@pytest.mark.parametrize("value", [np.float32("nan"), np.float32("inf")])
def test_normalize_driver_id_rejects_nonfinite_numpy_car_numbers_before_string_conversion(value):
    with pytest.raises(NormalizationError, match="driver abbreviation or car number is required"):
        normalize_driver_id(None, value)


@pytest.mark.parametrize("abbreviation", [float("nan"), np.float32("nan"), np.float32("inf")])
def test_normalize_driver_id_uses_car_number_fallback_for_nonfinite_abbreviations(abbreviation):
    assert normalize_driver_id(abbreviation, "44") == "D44"


def test_normalize_nullable_scalar_preserves_finite_values():
    assert normalize_nullable_scalar(12.5) == 12.5


def test_normalize_driver_id_prefers_trimmed_uppercase_abbreviation():
    assert normalize_driver_id(" ham ", "44") == "HAM"


@pytest.mark.parametrize("abbreviation", ["", "   ", "HA", "HAM4", "HÄM", 44])
def test_normalize_driver_id_rejects_malformed_abbreviations(abbreviation):
    with pytest.raises(NormalizationError):
        normalize_driver_id(abbreviation, "44")


def test_normalize_driver_id_uses_car_number_fallback_and_rejects_collisions():
    assert normalize_driver_id(None, "44") == "D44"
    with pytest.raises(NormalizationError, match="collision"):
        normalize_driver_id(None, "44", {"D44"})


def test_normalize_driver_id_collapses_leading_zero_car_numbers_before_collision_checking():
    assert normalize_driver_id(None, "044") == "D44"
    with pytest.raises(NormalizationError, match="collision"):
        normalize_driver_id(None, "044", {"D44"})


@pytest.mark.parametrize("car_numbers", list(permutations(("44", "044"))))
def test_normalize_driver_id_rejects_equivalent_fallback_numbers_in_every_input_permutation(car_numbers):
    first, second = car_numbers

    assert normalize_driver_id(None, first) == "D44"
    with pytest.raises(NormalizationError, match="collision"):
        normalize_driver_id(None, second, {"D44"})


COLUMNS = (
    "session_id",
    "driver_id",
    "source_driver_key",
    "session_time_ms",
    "speed_kph",
    "rpm",
    "source",
)
MEASUREMENTS = ("speed_kph", "rpm")


def test_sort_and_deduplicate_rows_is_independent_of_input_order():
    less_complete = _row(speed_kph=300.0, rpm=None, source="car")
    native_complete = _row(speed_kph=299.0, rpm=11_000.0, source="car")
    non_native_complete = _row(speed_kph=298.0, rpm=11_000.0, source="derived")

    expected = [native_complete]
    assert sort_and_deduplicate_rows(
        [less_complete, non_native_complete, native_complete],
        column_order=COLUMNS,
        measurement_fields=MEASUREMENTS,
    ) == expected
    assert sort_and_deduplicate_rows(
        [native_complete, non_native_complete, less_complete],
        column_order=COLUMNS,
        measurement_fields=MEASUREMENTS,
    ) == expected


def test_sort_and_deduplicate_rows_uses_lexical_values_after_equal_priority():
    first = _row(speed_kph=301.0, rpm=11_000.0, source="car")
    second = _row(speed_kph=300.0, rpm=11_000.0, source="car")

    assert sort_and_deduplicate_rows(
        [first, second], column_order=COLUMNS, measurement_fields=MEASUREMENTS
    ) == [second]


def test_sort_and_deduplicate_rows_returns_canonical_key_order():
    later = _row(driver_id="VER", session_time_ms=2, speed_kph=1.0, rpm=None, source="car")
    earlier = _row(driver_id="HAM", session_time_ms=1, speed_kph=1.0, rpm=None, source="car")

    assert sort_and_deduplicate_rows(
        [later, earlier], column_order=COLUMNS, measurement_fields=MEASUREMENTS
    ) == [earlier, later]


def test_sort_and_deduplicate_rows_rejects_duplicate_measurement_fields():
    with pytest.raises(NormalizationError, match="must not contain duplicates"):
        sort_and_deduplicate_rows(
            [_row()], column_order=COLUMNS, measurement_fields=("speed_kph", "speed_kph")
        )


@pytest.mark.parametrize(
    "changes, expected_message",
    [
        ({"session_id": "  "}, "non-whitespace"),
        ({"driver_id": "ham"}, "canonical three-letter"),
        ({"source_driver_key": "  "}, "source_driver_key"),
        ({"unexpected": "value"}, "undeclared columns"),
    ],
)
def test_sort_and_deduplicate_rows_rejects_invalid_identity_and_undeclared_fields(changes, expected_message):
    with pytest.raises(NormalizationError, match=expected_message):
        sort_and_deduplicate_rows(
            [_row(**changes)], column_order=COLUMNS, measurement_fields=MEASUREMENTS
        )


def _row(**changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "session_id": "2026-race",
        "driver_id": "HAM",
        "source_driver_key": "44",
        "session_time_ms": 1,
        "speed_kph": None,
        "rpm": None,
        "source": "car",
    }
    row.update(changes)
    return row
