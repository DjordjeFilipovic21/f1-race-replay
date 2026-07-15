"""Duck-typed adapter for FastF1's native position telemetry stream."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import cast

import polars as pl

from .canonical_schema import POSITION_TELEMETRY_SCHEMA
from .normalizers import (
    NormalizationError,
    normalize_nullable_scalar,
    normalize_session_time_ms,
    sort_and_deduplicate_rows,
)
from .validators import validate_canonical_table


_MEASUREMENT_FIELDS = ("x", "y", "z", "status")


def adapt_position_telemetry(
    session: object,
    session_id: str,
    driver_ids: Mapping[str, str],
) -> pl.DataFrame:
    """Adapt only ``session.pos_data`` into ordered native position observations.

    ``driver_ids`` maps the original FastF1 string driver-number keys to their
    already-normalized canonical driver IDs. No car or merged telemetry is read.
    """
    _require_session_id(session_id)
    pos_data = _pos_data(session)
    rows = [
        _canonical_row(session_id, source_key, driver_ids, record)
        for source_key in sorted((_source_key(key) for key in pos_data))
        for record in _records(pos_data[source_key])
    ]
    normalized = sort_and_deduplicate_rows(
        rows,
        column_order=tuple(POSITION_TELEMETRY_SCHEMA),
        measurement_fields=_MEASUREMENT_FIELDS,
    )
    frame = pl.DataFrame(normalized, schema=POSITION_TELEMETRY_SCHEMA, strict=True)
    validate_canonical_table("position_telemetry", frame)
    return frame


normalize_position_telemetry = adapt_position_telemetry


def _pos_data(session: object) -> Mapping[object, object]:
    try:
        pos_data = getattr(session, "pos_data")
    except AttributeError as error:
        raise NormalizationError("loaded session is missing required pos_data") from error
    if not isinstance(pos_data, Mapping):
        raise NormalizationError("session pos_data must be a mapping")
    return pos_data


def _canonical_row(
    session_id: str,
    source_key: str,
    driver_ids: Mapping[str, str],
    record: Mapping[str, object],
) -> dict[str, object]:
    try:
        driver_id = driver_ids[source_key]
    except KeyError as error:
        raise NormalizationError(f"missing canonical driver ID for source key: {source_key}") from error
    return {
        "session_id": session_id,
        "driver_id": driver_id,
        "source_driver_key": source_key,
        "session_time_ms": normalize_session_time_ms(_required(record, "SessionTime")),
        "x": _coordinate(_value(record, "X"), "X"),
        "y": _coordinate(_value(record, "Y"), "Y"),
        "z": _coordinate(_value(record, "Z"), "Z"),
        "status": _status(_value(record, "Status")),
        "source": "pos",
    }


def _records(source: object) -> Iterable[Mapping[str, object]]:
    to_dicts = getattr(source, "to_dicts", None)
    records = to_dicts() if callable(to_dicts) else _pandas_records(source)
    if records is None:
        if not isinstance(source, Iterable) or isinstance(source, (str, bytes, Mapping)):
            raise NormalizationError("position data must be row-iterable or DataFrame-like")
        records = cast(Iterable[Mapping[str, object]], source)
    for record in cast(Iterable[Mapping[str, object]], records):
        if not isinstance(record, Mapping):
            raise NormalizationError("position telemetry rows must be mappings")
        yield record


def _pandas_records(source: object) -> Iterable[Mapping[str, object]] | None:
    to_dict = getattr(source, "to_dict", None)
    if not callable(to_dict):
        return None
    try:
        records = to_dict("records")
    except TypeError as error:
        raise NormalizationError("position DataFrame must support record conversion") from error
    if not isinstance(records, Iterable) or isinstance(records, (str, bytes, Mapping)):
        raise NormalizationError("position DataFrame record conversion must return rows")
    return records


def _required(record: Mapping[str, object], column: str) -> object:
    if column not in record:
        raise NormalizationError(f"position telemetry row is missing required {column}")
    return record[column]


def _value(record: Mapping[str, object], column: str) -> object | None:
    return record.get(column)


def _coordinate(value: object | None, label: str) -> float | None:
    normalized = normalize_nullable_scalar(value)
    if normalized is None:
        return None
    if isinstance(normalized, bool) or not isinstance(normalized, (int, float)):
        raise NormalizationError(f"position {label} must be numeric")
    return float(normalized)


def _status(value: object | None) -> str | None:
    normalized = normalize_nullable_scalar(value)
    if normalized is None:
        return None
    if not isinstance(normalized, str):
        raise NormalizationError("position Status must be a string")
    return normalized or None


def _source_key(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NormalizationError("position source driver key must be a non-empty string")
    return value


def _require_session_id(value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise NormalizationError("session_id must be a non-empty string")


__all__ = ["adapt_position_telemetry", "normalize_position_telemetry"]
