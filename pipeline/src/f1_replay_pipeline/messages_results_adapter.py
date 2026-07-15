"""Pure, in-memory FastF1 adapters for race-control messages and results."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import math
import re

import polars as pl

from .canonical_schema import RACE_CONTROL_MESSAGES_SCHEMA, RESULTS_SCHEMA
from .normalizers import (
    NormalizationError,
    normalize_nullable_scalar,
    normalize_race_control_time_ms,
    normalize_session_time_ms,
)
from .validators import validate_canonical_table


def adapt_race_control_messages(
    session_or_messages: object,
    drivers: pl.DataFrame | Mapping[str, str],
    session_id: str,
) -> pl.DataFrame:
    """Normalize sparse messages without resampling, expanding, or interpolating them."""
    canonical_session_id = _required_session_id(session_id)
    driver_lookup = _driver_lookup(drivers, canonical_session_id)
    t0_date = getattr(session_or_messages, "t0_date", None)
    rows = [_message_row(record, canonical_session_id, driver_lookup, t0_date) for record in _records(
        session_or_messages, "race_control_messages"
    )]
    rows.sort(key=_message_sort_key)
    for index, row in enumerate(rows):
        row["message_index"] = index
    frame = pl.DataFrame(rows, schema=RACE_CONTROL_MESSAGES_SCHEMA)
    validate_canonical_table("race_control_messages", frame)
    return frame


def adapt_results(
    session_or_results: object,
    drivers: pl.DataFrame | Mapping[str, str],
    session_id: str,
) -> pl.DataFrame:
    """Normalize one nullable classified result per canonical driver."""
    canonical_session_id = _required_session_id(session_id)
    driver_lookup = _driver_lookup(drivers, canonical_session_id)
    rows = [
        _result_row(record, canonical_session_id, driver_lookup)
        for record in _records(session_or_results, "results")
    ]
    _reject_duplicate_results(rows)
    frame = pl.DataFrame(sorted(rows, key=lambda row: (row["session_id"], row["driver_id"])), schema=RESULTS_SCHEMA)
    validate_canonical_table("results", frame)
    return frame


normalize_race_control_messages = adapt_race_control_messages
normalize_results = adapt_results


def _message_row(
    record: Mapping[str, object], session_id: str, driver_lookup: "DriverLookup", t0_date: object | None
) -> dict[str, object | None]:
    message = _required_text(_value(record, "Message"), "race-control message")
    return {
        "session_id": session_id,
        "session_time_ms": normalize_race_control_time_ms(
            _first_value(record, "SessionTime", "Time"), t0_date
        ),
        "message_index": 0,
        "category": _nullable_text(_value(record, "Category")),
        "flag": _nullable_text(_value(record, "Flag")),
        "scope": _nullable_text(_value(record, "Scope")),
        "message": message,
        "driver_id": _resolve_driver(_value(record, "RacingNumber"), driver_lookup, "RacingNumber"),
        "lap_number": _nullable_int16(_value(record, "Lap"), "lap number"),
    }


def _result_row(record: Mapping[str, object], session_id: str, driver_lookup: "DriverLookup") -> dict[str, object | None]:
    return {
        "session_id": session_id,
        "driver_id": _resolve_driver(_value(record, "DriverNumber"), driver_lookup, "DriverNumber", required=True),
        "classified_position": _nullable_classified_position(_classification_value(record), "classified position"),
        "grid_position": _nullable_int16(_value(record, "GridPosition"), "grid position"),
        "status": _nullable_text(_value(record, "Status")),
        "points": _nullable_float(_value(record, "Points"), "points"),
        "laps_completed": _nullable_int16(_value(record, "Laps"), "laps completed"),
        "result_time_ms": _nullable_time(_value(record, "Time"), "result time"),
    }


def _records(source: object, table_attribute: str) -> Iterable[Mapping[str, object]]:
    table = getattr(source, table_attribute, source)
    if table is None:
        return ()
    to_dicts = getattr(table, "to_dicts", None)
    if callable(to_dicts):
        records = to_dicts()
        if isinstance(records, Iterable):
            return _mapping_records(records)
    to_dict = getattr(table, "to_dict", None)
    if callable(to_dict):
        try:
            records = to_dict("records")
            if isinstance(records, Iterable):
                return _mapping_records(records)
        except TypeError:
            pass
    if isinstance(table, Iterable) and not isinstance(table, (str, bytes, Mapping)):
        return _mapping_records(table)
    raise NormalizationError(f"{table_attribute} must be an iterable table of records")


def _mapping_records(records: Iterable[object]) -> Iterable[Mapping[str, object]]:
    normalized = []
    for record in records:
        if not isinstance(record, Mapping):
            raise NormalizationError("source table records must be mappings")
        normalized.append(record)
    return normalized


@dataclass(frozen=True)
class DriverLookup:
    """Roster source keys plus optional numeric aliases for FastF1 payloads."""

    source_keys: Mapping[str, str]
    aliases: Mapping[str, str]


def _driver_lookup(drivers: object, session_id: str) -> DriverLookup:
    if isinstance(drivers, pl.DataFrame):
        required_columns = {"session_id", "source_driver_key", "driver_id"}
        columns = getattr(drivers, "columns")
        missing_columns = required_columns.difference(columns)
        if missing_columns:
            raise NormalizationError(
                "driver metadata frame is missing required columns: "
                f"{', '.join(sorted(missing_columns))}"
            )
        to_dicts = getattr(drivers, "to_dicts")
        records: Iterable[Mapping[str, object]] = to_dicts()
        records = (
            record
            for record in records
            if record.get("session_id") == session_id
        )
    elif isinstance(drivers, Mapping):
        records = (
            {"source_driver_key": source_key, "driver_id": driver_id}
            for source_key, driver_id in drivers.items()
        )
    else:
        raise NormalizationError("drivers must map source driver keys to canonical driver IDs")
    source_keys: dict[str, str] = {}
    aliases: dict[str, str] = {}
    source_ids: dict[str, str] = {}
    for record in records:
        key = _driver_key(record.get("source_driver_key"), "driver metadata source_driver_key")
        driver_id = record.get("driver_id")
        if not isinstance(driver_id, str) or not re.fullmatch(r"(?:[A-Z]{3}|D(?:0|[1-9][0-9]*))", driver_id):
            raise NormalizationError("driver metadata contains an invalid canonical driver_id")
        if key in source_keys:
            raise NormalizationError(f"duplicate source driver key in driver metadata: {key!r}")
        if driver_id in source_ids:
            raise NormalizationError(
                f"canonical driver {driver_id!r} maps to multiple source keys: "
                f"{source_ids[driver_id]!r}, {key!r}"
            )
        if key in aliases and aliases[key] != driver_id:
            raise NormalizationError(f"source driver key conflicts with driver-number alias: {key!r}")
        source_keys[key] = driver_id
        source_ids[driver_id] = key
        if "driver_number" not in record or _is_missing(record.get("driver_number")):
            continue
        alias = _driver_key(record["driver_number"], "driver metadata driver_number")
        if alias == key:
            continue
        if alias in source_keys and source_keys[alias] != driver_id:
            raise NormalizationError(f"driver-number alias conflicts with source driver key: {alias!r}")
        if alias in aliases and aliases[alias] != driver_id:
            raise NormalizationError(f"ambiguous driver-number alias in driver metadata: {alias!r}")
        aliases[alias] = driver_id
    return DriverLookup(source_keys, aliases)


def _resolve_driver(value: object | None, drivers: DriverLookup, field: str, required: bool = False) -> str | None:
    if _is_missing(value):
        if required:
            raise NormalizationError(f"result is missing required {field} for canonical driver mapping")
        return None
    key = _driver_key(value, field)
    try:
        return drivers.source_keys[key]
    except KeyError:
        alias = drivers.aliases.get(key)
        if alias is not None:
            return alias
        available = ", ".join(sorted(drivers.source_keys)) or "none"
        raise NormalizationError(
            f"unknown driver {key!r} in {field}; add it to canonical driver metadata source_driver_key "
            f"(available source keys: {available})"
        ) from None


def _message_sort_key(row: Mapping[str, object | None]) -> tuple[object, ...]:
    context_columns = ("category", "flag", "scope", "message", "driver_id", "lap_number")
    return (
        row["session_id"],
        row["session_time_ms"],
        *("" if row[column] is None else str(row[column]) for column in context_columns),
    )


def _reject_duplicate_results(rows: Iterable[Mapping[str, object | None]]) -> None:
    seen: set[tuple[object | None, object | None]] = set()
    for row in rows:
        key = row["session_id"], row["driver_id"]
        if key in seen:
            raise NormalizationError(f"duplicate result for canonical driver: {key[1]}")
        seen.add(key)


def _value(record: Mapping[str, object], name: str) -> object | None:
    return record.get(name)


def _first_value(record: Mapping[str, object], *names: str) -> object | None:
    return next((value for name in names if not _is_missing(value := _value(record, name))), None)


def _classification_value(record: Mapping[str, object]) -> object | None:
    for name in ("ClassifiedPosition", "Position"):
        value = _value(record, name)
        if _is_missing(value):
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _required_session_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NormalizationError("session_id must be a non-empty string")
    return value


def _required_text(value: object | None, label: str) -> str:
    text = _nullable_text(value)
    if text is None:
        raise NormalizationError(f"{label} is required")
    return text


def _nullable_text(value: object | None) -> str | None:
    normalized = normalize_nullable_scalar(value)
    if normalized is None:
        return None
    if not isinstance(normalized, str):
        raise NormalizationError("text fields must be strings")
    return normalized or None


def _required_time(value: object | None, label: str) -> int:
    if _is_missing(value):
        raise NormalizationError(f"{label} is missing required SessionTime or Time")
    return normalize_session_time_ms(value)


def _nullable_time(value: object | None, label: str) -> int | None:
    if _is_missing(value):
        return None
    try:
        return normalize_session_time_ms(value)
    except NormalizationError as error:
        raise NormalizationError(f"{label} must be a non-negative duration") from error


def _nullable_int16(value: object | None, label: str) -> int | None:
    normalized = normalize_nullable_scalar(value)
    if normalized is None:
        return None
    if isinstance(normalized, str):
        if not normalized.strip().isdigit():
            return None if label == "classified position" else _invalid_integer(label)
        normalized = int(normalized.strip())
    if isinstance(normalized, bool) or not isinstance(normalized, (int, float)) or int(normalized) != normalized:
        return _invalid_integer(label)
    if not 0 <= int(normalized) <= 32_767:
        return _invalid_integer(label)
    return int(normalized)


def _nullable_classified_position(value: object | None, label: str) -> str | None:
    normalized = normalize_nullable_scalar(value)
    if normalized is None:
        return None
    if isinstance(normalized, str):
        classification = normalized.strip()
        return classification or None
    if isinstance(normalized, bool) or not isinstance(normalized, (int, float)) or int(normalized) != normalized:
        raise NormalizationError(f"{label} must be a classification string or integer")
    if not 0 <= int(normalized) <= 32_767:
        raise NormalizationError(f"{label} must fit in Int16")
    return str(int(normalized))


def _invalid_integer(label: str) -> None:
    raise NormalizationError(f"{label} must fit in Int16")


def _nullable_float(value: object | None, label: str) -> float | None:
    normalized = normalize_nullable_scalar(value)
    if normalized is None:
        return None
    if isinstance(normalized, bool) or not isinstance(normalized, (int, float)):
        raise NormalizationError(f"{label} must be numeric")
    return float(normalized)


def _driver_key(value: object, field: str) -> str:
    if _is_missing(value):
        raise NormalizationError(f"{field} must be a non-empty driver number")
    if isinstance(value, bool):
        raise NormalizationError(f"{field} must be a non-empty driver number")
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise NormalizationError(f"{field} must be a non-empty driver number")
        return str(int(value))
    key = str(value).strip()
    if not key:
        raise NormalizationError(f"{field} must be a non-empty driver number")
    return key


def _is_missing(value: object | None) -> bool:
    if value is None or type(value).__name__ in {"NAType", "NaTType"}:
        return True
    try:
        return normalize_nullable_scalar(value) is None
    except NormalizationError:
        return False


__all__ = [
    "adapt_race_control_messages",
    "adapt_results",
    "normalize_race_control_messages",
    "normalize_results",
]
