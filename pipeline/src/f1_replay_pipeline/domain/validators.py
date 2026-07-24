"""In-memory validation for ordered canonical session tables."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
import re
from types import MappingProxyType

import polars as pl

from f1_replay_pipeline.domain.canonical_schema import CANONICAL_TABLE_SCHEMAS


_CANONICAL_DRIVER_ID = re.compile(r"(?:[A-Z]{3}|D(?:0|[1-9][0-9]*))\Z")


class CanonicalValidationError(ValueError):
    """Raised when a frame violates the canonical table contract."""


@dataclass(frozen=True)
class CanonicalTableMetadata:
    """Declarative validation policy for one canonical table."""

    key_columns: tuple[str, ...]
    required_columns: tuple[str, ...]
    required_nonblank_string_columns: tuple[str, ...]
    measurement_columns: tuple[str, ...] = ()
    time_columns: tuple[str, ...] = ()
    source_driver_mapping: bool = False
    interval_columns: tuple[str, str] | None = None
    exact_row_count: int | None = None


CANONICAL_TABLE_METADATA: Mapping[str, CanonicalTableMetadata] = MappingProxyType(
    {
        "car_telemetry": CanonicalTableMetadata(
            ("session_id", "driver_id", "session_time_ms"),
            ("session_id", "driver_id", "source_driver_key", "session_time_ms", "source"),
            ("session_id", "driver_id", "source_driver_key", "source"),
            ("speed_kph", "rpm", "throttle_pct"),
            ("session_time_ms",),
            True,
        ),
        "position_telemetry": CanonicalTableMetadata(
            ("session_id", "driver_id", "session_time_ms"),
            ("session_id", "driver_id", "source_driver_key", "session_time_ms", "source"),
            ("session_id", "driver_id", "source_driver_key", "source"),
            ("x", "y", "z"),
            ("session_time_ms",),
            True,
        ),
        "session_metadata": CanonicalTableMetadata(
            ("session_id",), ("session_id",), ("session_id",), exact_row_count=1
        ),
        "drivers": CanonicalTableMetadata(
            ("session_id", "driver_id"),
            ("session_id", "driver_id", "source_driver_key"),
            ("session_id", "driver_id", "source_driver_key"),
            source_driver_mapping=True,
        ),
        "laps": CanonicalTableMetadata(
            ("session_id", "driver_id", "lap_number"),
            ("session_id", "driver_id", "lap_number", "lap_start_time_ms"),
            ("session_id", "driver_id"),
            time_columns=(
                "lap_start_time_ms", "lap_end_time_ms", "lap_duration_ms",
                "pit_in_time_ms", "pit_out_time_ms",
            ),
            interval_columns=("lap_start_time_ms", "lap_end_time_ms"),
        ),
        "stints": CanonicalTableMetadata(
            ("session_id", "driver_id", "stint_number"),
            ("session_id", "driver_id", "stint_number", "start_lap_number"),
            ("session_id", "driver_id"),
            time_columns=("start_time_ms", "end_time_ms"),
            interval_columns=("start_time_ms", "end_time_ms"),
        ),
        "weather": CanonicalTableMetadata(
            ("session_id", "session_time_ms"),
            ("session_id", "session_time_ms"),
            ("session_id",),
            ("air_temperature_c", "humidity_pct", "pressure_mbar", "track_temperature_c", "wind_direction_deg", "wind_speed_mps"),
            ("session_time_ms",),
        ),
        "track_status_intervals": CanonicalTableMetadata(
            ("session_id", "start_time_ms"),
            ("session_id", "start_time_ms", "status"),
            ("session_id", "status"),
            time_columns=("start_time_ms", "end_time_ms"),
            interval_columns=("start_time_ms", "end_time_ms"),
        ),
        "race_control_messages": CanonicalTableMetadata(
            ("session_id", "session_time_ms", "message_index"),
            ("session_id", "session_time_ms", "message_index", "message"),
            ("session_id", "message"),
            time_columns=("session_time_ms",),
        ),
        "results": CanonicalTableMetadata(
            ("session_id", "driver_id"),
            ("session_id", "driver_id"),
            ("session_id", "driver_id"),
            ("points",),
            ("result_time_ms",),
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
    _validate_exact_row_count(table_name, frame, metadata.exact_row_count)
    _validate_required_values(table_name, frame, metadata.required_columns)
    _validate_nonblank_strings(table_name, frame, metadata.required_nonblank_string_columns)
    _validate_driver_identifiers(table_name, frame)
    _validate_finite_measurements(table_name, frame, metadata.measurement_columns)
    _validate_nonnegative_times(table_name, frame, metadata.time_columns)
    _validate_intervals(table_name, frame, metadata.interval_columns)
    if metadata.source_driver_mapping:
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
        problems.append(f"column order must be {expected_columns}; received {actual_columns}")
    mismatches = [
        f"{column} expected {dtype!s}, received {frame.schema[column]!s}"
        for column, dtype in expected.items()
        if column in frame.schema and frame.schema[column] != dtype
    ]
    if mismatches:
        problems.append(f"dtype mismatches: {'; '.join(mismatches)}")
    if problems:
        raise CanonicalValidationError(f"{table_name} schema mismatch: {'; '.join(problems)}")


def _validate_exact_row_count(table_name: str, frame: pl.DataFrame, expected_count: int | None) -> None:
    if expected_count is not None and frame.height != expected_count:
        raise CanonicalValidationError(
            f"{table_name} must contain exactly {expected_count} row(s); received {frame.height}"
        )


def _validate_required_values(table_name: str, frame: pl.DataFrame, columns: tuple[str, ...]) -> None:
    null_columns = [column for column in columns if frame.get_column(column).null_count()]
    if null_columns:
        raise CanonicalValidationError(
            f"{table_name} required values must be non-null: {', '.join(null_columns)}"
        )


def _validate_nonblank_strings(table_name: str, frame: pl.DataFrame, columns: tuple[str, ...]) -> None:
    invalid = [
        column for column in columns
        if any(not isinstance(value, str) or not value.strip() for value in frame.get_column(column))
    ]
    if invalid:
        raise CanonicalValidationError(
            f"{table_name} required string values must be non-empty and non-whitespace: "
            f"{', '.join(invalid)}"
        )


def _validate_driver_identifiers(table_name: str, frame: pl.DataFrame) -> None:
    if "driver_id" not in frame.schema:
        return
    invalid = [
        value for value in frame.get_column("driver_id")
        if value is not None and (not isinstance(value, str) or not _CANONICAL_DRIVER_ID.fullmatch(value))
    ]
    if invalid:
        raise CanonicalValidationError(
            f"{table_name} driver_id must contain canonical three-letter or D<number> identifiers"
        )


def _validate_finite_measurements(
    table_name: str, frame: pl.DataFrame, columns: tuple[str, ...]
) -> None:
    invalid = [
        column for column in columns
        if any(isinstance(value, float) and not math.isfinite(value) for value in frame.get_column(column))
    ]
    if invalid:
        raise CanonicalValidationError(
            f"{table_name} measurements contain NaN or infinity: {', '.join(invalid)}"
        )


def _validate_nonnegative_times(table_name: str, frame: pl.DataFrame, columns: tuple[str, ...]) -> None:
    invalid = [
        column for column in columns
        if frame.filter(pl.col(column).is_not_null() & (pl.col(column) < 0)).height
    ]
    if invalid:
        raise CanonicalValidationError(
            f"{table_name} time columns must contain non-negative integer milliseconds: "
            f"{', '.join(invalid)}"
        )


def _validate_intervals(
    table_name: str, frame: pl.DataFrame, interval: tuple[str, str] | None
) -> None:
    if interval is None:
        return
    start_column, end_column = interval
    invalid_count = frame.filter(
        pl.col(end_column).is_not_null() & (pl.col(end_column) < pl.col(start_column))
    ).height
    if invalid_count:
        raise CanonicalValidationError(
            f"{table_name} interval {end_column} must not precede {start_column} "
            f"({invalid_count} invalid interval(s) found)"
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


def _validate_key_order(table_name: str, frame: pl.DataFrame, key_columns: tuple[str, ...]) -> None:
    if not frame.is_sorted(list(key_columns)):
        raise CanonicalValidationError(
            f"{table_name} rows must be sorted ascending by canonical key: {', '.join(key_columns)}"
        )


def _validate_unique_keys(table_name: str, frame: pl.DataFrame, key_columns: tuple[str, ...]) -> None:
    duplicate_keys = (
        frame.group_by(list(key_columns)).len().filter(pl.col("len") > 1)
        .select(list(key_columns)).to_dicts()
    )
    if duplicate_keys:
        raise CanonicalValidationError(f"{table_name} has duplicate canonical key(s): {duplicate_keys}")


__all__ = [
    "CANONICAL_TABLE_METADATA", "CanonicalTableMetadata", "CanonicalValidationError",
    "validate_canonical_table",
]
