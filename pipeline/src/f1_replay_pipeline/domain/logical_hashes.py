"""Pure v1 wire encoding and SHA-256 logical hashes for canonical tables."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import math
import struct

import polars as pl

from f1_replay_pipeline.domain.canonical_schema import CANONICAL_TABLE_SCHEMAS
from f1_replay_pipeline.domain.validators import validate_canonical_table

_PREFIX = b"F1RP-LOGICAL-TABLE\0v1\0"
_INTEGER_WIDTHS = {pl.Int8: 1, pl.Int16: 2, pl.Int32: 4, pl.Int64: 8}
_DTYPE_TOKENS = {
    pl.String: "String",
    pl.Boolean: "Boolean",
    pl.Int8: "Int8",
    pl.Int16: "Int16",
    pl.Int32: "Int32",
    pl.Int64: "Int64",
    pl.Float64: "Float64",
    pl.Datetime("ms", "UTC"): "Datetime[ms,UTC]",
}
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class LogicalHashEncodingError(ValueError):
    """Raised when a validated scalar cannot satisfy the v1 wire format."""


def logical_table_sha256(table_name: str, frame: pl.DataFrame) -> str:
    """Validate and hash one named canonical frame using the v1 wire format."""
    return hashlib.sha256(encode_logical_table(table_name, frame)).hexdigest()


def encode_logical_table(table_name: str, frame: pl.DataFrame) -> bytes:
    """Validate and return the exact v1 logical-table wire bytes for ``frame``."""
    validate_canonical_table(table_name, frame)
    schema = CANONICAL_TABLE_SCHEMAS[table_name]
    payload = bytearray(_PREFIX)
    _append_text(payload, table_name)
    _append_u64(payload, len(schema))
    for name, dtype in schema.items():
        _append_text(payload, name)
        _append_text(payload, _dtype_token(dtype))
    _append_u64(payload, frame.height)
    for row in frame.iter_rows(named=False):
        for value, dtype in zip(row, schema.values(), strict=True):
            _append_cell(payload, value, dtype)
    return bytes(payload)


def _append_u64(payload: bytearray, value: int) -> None:
    if not isinstance(value, int) or value < 0 or value > (2**64 - 1):
        raise LogicalHashEncodingError("logical hash lengths and counts must fit U64BE")
    payload.extend(struct.pack(">Q", value))


def _append_text(payload: bytearray, value: str) -> None:
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise LogicalHashEncodingError("logical hash text must be valid UTF-8") from error
    _append_u64(payload, len(encoded))
    payload.extend(encoded)


def _dtype_token(dtype: pl.DataType) -> str:
    try:
        return _DTYPE_TOKENS[dtype]
    except KeyError as error:
        raise LogicalHashEncodingError(f"unsupported logical hash dtype: {dtype}") from error


def _append_cell(payload: bytearray, value: object, dtype: pl.DataType) -> None:
    if value is None:
        payload.append(0x00)
        return
    if dtype == pl.Boolean:
        _append_boolean(payload, value)
    elif dtype in _INTEGER_WIDTHS:
        _append_integer(payload, value, _INTEGER_WIDTHS[dtype])
    elif dtype == pl.Float64:
        _append_float(payload, value)
    elif dtype == pl.String:
        _append_string(payload, value)
    elif dtype == pl.Datetime("ms", "UTC"):
        _append_datetime(payload, value)
    else:
        raise LogicalHashEncodingError(f"unsupported logical hash dtype: {dtype}")


def _append_boolean(payload: bytearray, value: object) -> None:
    if type(value) is not bool:
        raise LogicalHashEncodingError("Boolean cell must contain a boolean")
    payload.extend((0x01, 0x01 if value else 0x00))


def _append_integer(payload: bytearray, value: object, width: int) -> None:
    if type(value) is not int:
        raise LogicalHashEncodingError("integer cell must contain an integer")
    minimum, maximum = -(2 ** (width * 8 - 1)), 2 ** (width * 8 - 1) - 1
    if not minimum <= value <= maximum:
        raise LogicalHashEncodingError("integer cell is outside its declared dtype range")
    payload.append(0x02)
    payload.extend(value.to_bytes(width, byteorder="big", signed=True))


def _append_float(payload: bytearray, value: object) -> None:
    if type(value) is not float or not math.isfinite(value):
        raise LogicalHashEncodingError("Float64 cell must contain a finite float")
    payload.append(0x03)
    payload.extend(struct.pack(">d", 0.0 if value == 0.0 else value))


def _append_string(payload: bytearray, value: object) -> None:
    if not isinstance(value, str):
        raise LogicalHashEncodingError("String cell must contain a string")
    payload.append(0x04)
    _append_text(payload, value)


def _append_datetime(payload: bytearray, value: object) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise LogicalHashEncodingError("Datetime[ms,UTC] cell must contain a UTC datetime")
    if value.microsecond % 1000:
        raise LogicalHashEncodingError("Datetime[ms,UTC] cell must have millisecond precision")
    delta = value - _EPOCH
    milliseconds = delta.days * 86_400_000 + delta.seconds * 1_000 + delta.microseconds // 1_000
    payload.append(0x05)
    payload.extend(struct.pack(">q", milliseconds))


__all__ = ["LogicalHashEncodingError", "encode_logical_table", "logical_table_sha256"]
