"""Native-Polars Parquet serialization and exact canonical round-trip checks."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
from io import BytesIO
import inspect
from pathlib import Path
from types import MappingProxyType

import polars as pl
from polars.testing import assert_frame_equal

from f1_replay_pipeline.domain.canonical_schema import CANONICAL_TABLE_SCHEMAS
from f1_replay_pipeline.domain.validators import CanonicalValidationError, validate_canonical_table


CANONICAL_PARQUET_TABLE_NAMES = (
    "session_metadata", "drivers", "car_telemetry", "position_telemetry", "laps",
    "stints", "weather", "track_status_intervals", "race_control_messages", "results",
)
PARQUET_WRITE_SETTINGS: Mapping[str, object] = MappingProxyType(
    {
        "use_pyarrow": False,
        "compression": "zstd",
        "compression_level": 3,
        "statistics": "full",
        "row_group_size": 262144,
        "data_page_size": 1048576,
    }
)


class ParquetCompatibilityError(RuntimeError):
    """Raised when the installed Polars cannot honor the v1 writer contract."""


class ParquetRoundTripError(ValueError):
    """Raised when written bytes do not reconstruct the exact canonical frame."""


def ensure_native_parquet_compatibility() -> None:
    """Confirm the installed native writer accepts every contract option."""
    try:
        parameters = inspect.signature(pl.DataFrame.write_parquet).parameters
    except (TypeError, ValueError) as error:
        raise ParquetCompatibilityError(
            "cannot inspect polars.DataFrame.write_parquet; Polars >=1.40 with "
            "the native Parquet writer is required"
        ) from error
    missing = tuple(name for name in PARQUET_WRITE_SETTINGS if name not in parameters)
    if missing:
        raise ParquetCompatibilityError(
            "installed Polars DataFrame.write_parquet lacks required v1 option(s): "
            f"{', '.join(missing)}; install a compatible Polars >=1.40,<2 release"
        )


def validate_canonical_frames(frames: Mapping[str, pl.DataFrame]) -> None:
    """Require exactly the ten validated canonical frames at the write boundary."""
    if not isinstance(frames, Mapping):
        raise CanonicalValidationError("canonical Parquet frames must be a mapping")
    expected = set(CANONICAL_PARQUET_TABLE_NAMES)
    received = set(frames)
    if received != expected:
        missing = sorted(expected - received)
        extra = sorted(received - expected)
        raise CanonicalValidationError(
            f"canonical Parquet frames must contain exactly ten tables; missing={missing}; extra={extra}"
        )
    for table_name in CANONICAL_PARQUET_TABLE_NAMES:
        frame = frames[table_name]
        if not isinstance(frame, pl.DataFrame):
            raise CanonicalValidationError(f"{table_name} must be a Polars DataFrame")
        validate_canonical_table(table_name, frame)


def write_canonical_parquet_tables(
    frames: Mapping[str, pl.DataFrame], target_directory: Path,
) -> dict[str, Path]:
    """Write exactly one native Parquet file for every canonical table."""
    validate_canonical_frames(frames)
    ensure_native_parquet_compatibility()
    target_directory.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for table_name in CANONICAL_PARQUET_TABLE_NAMES:
        destination = target_directory / f"{table_name}.parquet"
        write_canonical_parquet(table_name, frames[table_name], destination)
        paths[table_name] = destination
    return paths


def write_canonical_parquet(table_name: str, frame: pl.DataFrame, destination: Path) -> str:
    """Validate, natively serialize, and return the final-file SHA-256 digest."""
    _validate_destination(table_name, destination)
    validate_canonical_table(table_name, frame)
    ensure_native_parquet_compatibility()
    try:
        frame.write_parquet(destination, **PARQUET_WRITE_SETTINGS)
    except (TypeError, ValueError) as error:
        raise ParquetCompatibilityError(
            "installed Polars rejected canonical native Parquet settings; install a "
            "compatible Polars >=1.40,<2 release"
        ) from error
    verify_canonical_parquet_round_trip(table_name, frame, destination)
    return parquet_byte_sha256(destination)


def verify_canonical_parquet_round_trip(
    table_name: str, expected: pl.DataFrame, source: Path | bytes, *, use_statistics: bool = True,
) -> None:
    """Verify schema, order, nulls, rows, and values without normalization.

    Byte input is intentionally supported for guarded readers: it prevents a
    second pathname open between checksum verification and native Polars reads.
    """
    if isinstance(source, Path):
        _validate_destination(table_name, source)
        schema_source: Path | BytesIO = source
        data_source: Path | BytesIO = source
    elif isinstance(source, bytes):
        schema_source = BytesIO(source)
        data_source = BytesIO(source)
    else:
        raise ValueError("canonical Parquet source must be a pathlib.Path or bytes")
    validate_canonical_table(table_name, expected)
    expected_schema = CANONICAL_TABLE_SCHEMAS[table_name]
    actual_schema = pl.read_parquet_schema(schema_source)
    if list(actual_schema.items()) != list(expected_schema.items()):
        raise ParquetRoundTripError(f"{table_name} Parquet schema or column order differs from contract")
    actual = pl.read_parquet(data_source, use_statistics=use_statistics, use_pyarrow=False)
    if actual.height != expected.height:
        raise ParquetRoundTripError(
            f"{table_name} Parquet row count differs: expected {expected.height}, got {actual.height}"
        )
    if actual.null_count().row(0) != expected.null_count().row(0):
        raise ParquetRoundTripError(f"{table_name} Parquet typed-null counts differ")
    try:
        assert_frame_equal(
            actual, expected, check_row_order=True, check_column_order=True,
            check_dtypes=True, check_exact=True,
        )
    except AssertionError as error:
        raise ParquetRoundTripError(
            f"{table_name} Parquet values are not logically equivalent in canonical order"
        ) from error


def parquet_byte_sha256(path: Path) -> str:
    """Hash exact final Parquet bytes after the native writer has closed the file."""
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_destination(table_name: str, destination: Path) -> None:
    if table_name not in CANONICAL_TABLE_SCHEMAS:
        raise CanonicalValidationError(f"unknown canonical table: {table_name}")
    if not isinstance(destination, Path) or destination.suffix != ".parquet":
        raise ValueError("canonical Parquet destination must be a .parquet pathlib.Path")
    if destination.exists() and destination.is_dir():
        raise ValueError("canonical Parquet destination must be a file, not a directory")


__all__ = [
    "CANONICAL_PARQUET_TABLE_NAMES", "PARQUET_WRITE_SETTINGS", "ParquetCompatibilityError",
    "ParquetRoundTripError", "ensure_native_parquet_compatibility", "parquet_byte_sha256",
    "validate_canonical_frames", "verify_canonical_parquet_round_trip", "write_canonical_parquet",
    "write_canonical_parquet_tables",
]
