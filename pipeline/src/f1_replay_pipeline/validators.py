"""In-memory validation for ordered canonical telemetry tables."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
import re
from types import MappingProxyType

import polars as pl

from f1_replay_pipeline.canonical_schema import CANONICAL_TABLE_SCHEMAS


_CANONICAL_DRIVER_ID = re.compile(r"(?:[A-Z]{3}|D(?:0|[1-9][0-9]*))\Z")


class CanonicalValidationError(ValueError):
    """Raised when a frame violates the canonical table contract."""


@dataclass(frozen=True)
class CanonicalTableMetadata:
    """Table-specific key and nullable measurement metadata."""

    key_columns: tuple[str, ...]
    nullable_measurement_columns: tuple[str, ...]


CANONICAL_TABLE_METADATA: Mapping[str, CanonicalTableMetadata] = MappingProxyType(
    {
        "car_telemetry": CanonicalTableMetadata(
            key_columns=("session_id", "driver_id", "session_time_ms"),
            nullable_measurement_columns=(
                "speed_kph",
                "rpm",
                "gear",
                "throttle_pct",
                "brake",
                "drs",
            ),
        ),
        "position_telemetry": CanonicalTableMetadata(
            key_columns=("session_id", "driver_id", "session_time_ms"),
            nullable_measurement_columns=("x", "y", "z", "status"),
        ),
    }
)


def validate_canonical_table(table_name: str, frame: pl.DataFrame) -> None:
    """Raise an actionable error unless ``frame`` satisfies its canonical contract."""
    try:
        schema = CANONICAL_TABLE_SCHEMAS[table_name]
        metadata = CANONICAL_TABLE_METADATA[table_name]
    except KeyError as error:
        raise CanonicalValidationError(f"unknown canonical table: {table_name}") from error

    _validate_schema(table_name, frame, schema)
    _validate_required_key_values(table_name, frame, metadata.key_columns)
    _validate_identifier_values(table_name, frame)
    _validate_nullable_measurements(table_name, frame, metadata.nullable_measurement_columns)
    _validate_time_values(table_name, frame)
    _validate_driver_source_mapping(table_name, frame)
    _validate_key_order(table_name, frame, metadata.key_columns)
    _validate_unique_keys(table_name, frame, metadata.key_columns)


def _validate_schema(table_name: str, frame: pl.DataFrame, expected: Mapping[str, pl.DataType]) -> None:
    expected_columns = list(expected)
    actual_columns = frame.columns
    problems: list[str] = []

    missing = [column for column in expected_columns if column not in frame.schema]
    extra = [column for column in actual_columns if column not in expected]
    if missing:
        problems.append(f"missing columns: {', '.join(missing)}")
    if extra:
        problems.append(f"unexpected columns: {', '.join(extra)}")
    if actual_columns != expected_columns:
        problems.append(
            f"column order must be {expected_columns}; received {actual_columns}"
        )

    type_mismatches = [
        f"{column} expected {dtype!s}, received {frame.schema[column]!s}"
        for column, dtype in expected.items()
        if column in frame.schema and frame.schema[column] != dtype
    ]
    if type_mismatches:
        problems.append(f"dtype mismatches: {'; '.join(type_mismatches)}")
    if problems:
        raise CanonicalValidationError(f"{table_name} schema mismatch: {'; '.join(problems)}")


def _validate_required_key_values(
    table_name: str, frame: pl.DataFrame, key_columns: tuple[str, ...]
) -> None:
    null_columns = [
        column for column in key_columns if frame.get_column(column).null_count() > 0
    ]
    if null_columns:
        raise CanonicalValidationError(
            f"{table_name} has null canonical key values in: {', '.join(null_columns)}"
        )


def _validate_identifier_values(table_name: str, frame: pl.DataFrame) -> None:
    invalid_session_ids = _invalid_nonblank_strings(frame, "session_id")
    invalid_source_keys = _invalid_nonblank_strings(frame, "source_driver_key")
    invalid_driver_ids = [
        value
        for value in frame.get_column("driver_id").to_list()
        if not isinstance(value, str) or not _CANONICAL_DRIVER_ID.fullmatch(value)
    ]
    problems: list[str] = []
    if invalid_session_ids:
        problems.append("session_id must contain non-empty, non-whitespace strings")
    if invalid_source_keys:
        problems.append("source_driver_key must contain non-empty, non-whitespace strings")
    if invalid_driver_ids:
        problems.append("driver_id must contain canonical three-letter or D<number> identifiers")
    if problems:
        raise CanonicalValidationError(f"{table_name} invalid identifier values: {'; '.join(problems)}")


def _invalid_nonblank_strings(frame: pl.DataFrame, column: str) -> list[object]:
    return [
        value
        for value in frame.get_column(column).to_list()
        if not isinstance(value, str) or not value.strip()
    ]


def _validate_nullable_measurements(
    table_name: str, frame: pl.DataFrame, nullable_measurement_columns: tuple[str, ...]
) -> None:
    invalid_columns = [
        column
        for column in nullable_measurement_columns
        if any(
            isinstance(value, float) and not math.isfinite(value)
            for value in frame.get_column(column).to_list()
        )
    ]
    if invalid_columns:
        raise CanonicalValidationError(
            f"{table_name} nullable measurement columns contain NaN or infinity: "
            f"{', '.join(invalid_columns)}"
        )


def _validate_time_values(table_name: str, frame: pl.DataFrame) -> None:
    negative_count = frame.filter(pl.col("session_time_ms") < 0).height
    if negative_count:
        raise CanonicalValidationError(
            f"{table_name} session_time_ms must contain non-negative integer milliseconds "
            f"({negative_count} negative value(s) found)"
        )


def _validate_driver_source_mapping(table_name: str, frame: pl.DataFrame) -> None:
    source_conflicts = (
        frame.group_by(["session_id", "source_driver_key"])
        .agg(pl.col("driver_id").n_unique().alias("driver_count"))
        .filter(pl.col("driver_count") > 1)
        .select(["session_id", "source_driver_key"])
        .to_dicts()
    )
    driver_conflicts = (
        frame.group_by(["session_id", "driver_id"])
        .agg(pl.col("source_driver_key").n_unique().alias("source_key_count"))
        .filter(pl.col("source_key_count") > 1)
        .select(["session_id", "driver_id"])
        .to_dicts()
    )
    if source_conflicts or driver_conflicts:
        raise CanonicalValidationError(
            f"{table_name} source_driver_key and driver_id must map one-to-one per session: "
            f"source conflicts={source_conflicts}; driver conflicts={driver_conflicts}"
        )


def _validate_key_order(
    table_name: str, frame: pl.DataFrame, key_columns: tuple[str, ...]
) -> None:
    if not frame.is_sorted(list(key_columns)):
        raise CanonicalValidationError(
            f"{table_name} rows must be sorted ascending by canonical key: "
            f"{', '.join(key_columns)}"
        )


def _validate_unique_keys(
    table_name: str, frame: pl.DataFrame, key_columns: tuple[str, ...]
) -> None:
    duplicate_keys = (
        frame.group_by(list(key_columns))
        .len()
        .filter(pl.col("len") > 1)
        .select(list(key_columns))
        .to_dicts()
    )
    if duplicate_keys:
        raise CanonicalValidationError(
            f"{table_name} has duplicate canonical key(s): {duplicate_keys}"
        )


__all__ = [
    "CANONICAL_TABLE_METADATA",
    "CanonicalTableMetadata",
    "CanonicalValidationError",
    "validate_canonical_table",
]
