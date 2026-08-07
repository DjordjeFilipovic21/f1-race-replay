"""Duck-typed, in-memory adapters for sparse session-condition tables."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import inspect
from typing import cast

import polars as pl

from ...domain.canonical_schema import TRACK_STATUS_INTERVALS_SCHEMA_V2, WEATHER_SCHEMA_V2
from ...domain.normalizers import (
    NormalizationError,
    canonical_scalar_sort_key,
    normalize_nullable_scalar,
    normalize_session_time_ms,
)
from .session_metadata import adapt_session_metadata
from ...domain.validators import validate_canonical_table


_WEATHER_FIELDS = (
    ("AirTemp", "air_temperature_c"),
    ("Humidity", "humidity_pct"),
    ("Pressure", "pressure_mbar"),
    ("Rainfall", "rainfall"),
    ("TrackTemp", "track_temperature_c"),
    ("WindDirection", "wind_direction_deg"),
    ("WindSpeed", "wind_speed_mps"),
)


def adapt_weather(session: object, session_id: str | None = None) -> pl.DataFrame:
    """Return native weather observations without filling gaps between samples."""
    canonical_session_id = _session_id(session, session_id)
    rows = [_weather_row(record, canonical_session_id) for record in _records(session, "weather_data", optional=True)]
    retained = _retain_weather_duplicates(rows)
    frame = pl.DataFrame(retained, schema=WEATHER_SCHEMA_V2).sort("session_id", "session_time_ms")
    validate_canonical_table("weather", frame, version="v2")
    return frame


def adapt_track_status_intervals(session: object, session_id: str | None = None) -> pl.DataFrame:
    """Return status-change intervals ending only at the next observed transition."""
    canonical_session_id = _session_id(session, session_id)
    records = [_status_row(record, canonical_session_id) for record in _records(session, "track_status")]
    ordered = sorted(records, key=_status_start_time)
    _reject_duplicate_starts(ordered)
    intervals = [
        {**row, "end_time_ms": ordered[index + 1]["start_time_ms"] if index + 1 < len(ordered) else None}
        for index, row in enumerate(ordered)
    ]
    frame = pl.DataFrame(intervals, schema=TRACK_STATUS_INTERVALS_SCHEMA_V2)
    validate_canonical_table("track_status_intervals", frame, version="v2")
    return frame


normalize_weather = adapt_weather
normalize_track_status_intervals = adapt_track_status_intervals


def _session_id(session: object, provided: str | None) -> str:
    session_id = provided if provided is not None else adapt_session_metadata(session).item(0, "session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise NormalizationError("session_id must be a non-empty string")
    return session_id


def _records(session: object, field: str, *, optional: bool = False) -> Iterable[Mapping[str, object]]:
    try:
        table = getattr(session, field)
    except AttributeError as error:
        if optional:
            if not _has_declared_attribute(session, field):
                return ()
            if _is_fastf1_data_not_loaded(error):
                return ()
            raise
        raise NormalizationError(f"loaded session is missing required {field}") from error
    except Exception as error:
        if optional and _is_fastf1_data_not_loaded(error):
            return ()
        raise
    if table is None:
        return ()
    try:
        to_dicts = getattr(table, "to_dicts", None)
        if callable(to_dicts):
            return _mapping_records(cast(Iterable[object], to_dicts()), field)
        to_dict = getattr(table, "to_dict", None)
        if callable(to_dict):
            try:
                return _mapping_records(cast(Iterable[object], to_dict("records")), field)
            except TypeError:
                return _mapping_records(cast(Iterable[object], to_dict(orient="records")), field)
        if isinstance(table, Iterable) and not isinstance(table, (str, bytes, Mapping)):
            return _mapping_records(table, field)
        raise NormalizationError(f"{field} must provide iterable mapping records")
    except Exception as error:
        # FastF1 raises this known error when its soft weather load did not
        # populate the lazy property. Do not hide malformed table errors.
        if optional and _is_fastf1_data_not_loaded(error):
            return ()
        raise


def _is_fastf1_data_not_loaded(error: Exception) -> bool:
    """Recognize FastF1's documented lazy-weather exception without importing it."""
    # The adapter is deliberately dependency-injected and duck-typed. Matching
    # this exact documented class name keeps fake sessions deterministic while
    # allowing every unrelated table/access error to propagate.
    return type(error).__name__ == "DataNotLoadedError"


def _has_declared_attribute(session: object, field: str) -> bool:
    """Distinguish a genuinely absent field from an accessor that failed."""
    try:
        inspect.getattr_static(session, field)
    except AttributeError:
        return False
    return True


def _mapping_records(records: Iterable[object], field: str) -> Iterable[Mapping[str, object]]:
    materialized = tuple(records)
    if not all(isinstance(record, Mapping) for record in materialized):
        raise NormalizationError(f"{field} records must be mappings")
    return tuple(cast(Mapping[str, object], record) for record in materialized)


def _weather_row(record: Mapping[str, object], session_id: str) -> dict[str, object | None]:
    return {
        "session_id": session_id,
        "session_time_ms": _required_time(record, "Time", "weather timestamp"),
        **{target: _measurement(record.get(source), target) for source, target in _WEATHER_FIELDS},
    }


def _status_row(record: Mapping[str, object], session_id: str) -> dict[str, object | None]:
    status = _text(record.get("Status"), "track status")
    if status is None:
        raise NormalizationError("track status is required")
    return {
        "session_id": session_id,
        "start_time_ms": _required_time(record, "Time", "track-status timestamp"),
        "status": status,
        "message": _text(record.get("Message"), "track-status message"),
    }


def _required_time(record: Mapping[str, object], field: str, label: str) -> int:
    if field not in record or record[field] is None:
        raise NormalizationError(f"{label} is required")
    try:
        return normalize_session_time_ms(record[field])
    except NormalizationError as error:
        raise NormalizationError(f"invalid {label}: {error}") from error


def _measurement(value: object | None, label: str) -> float | bool | None:
    normalized = normalize_nullable_scalar(value)
    if normalized is None:
        return None
    if label == "rainfall":
        if not isinstance(normalized, bool):
            raise NormalizationError("rainfall must be a boolean")
        return normalized
    if isinstance(normalized, bool) or not isinstance(normalized, (int, float)):
        raise NormalizationError(f"{label} must be numeric")
    return float(normalized)


def _text(value: object | None, label: str) -> str | None:
    normalized = normalize_nullable_scalar(value)
    if normalized is None:
        return None
    if not isinstance(normalized, str):
        raise NormalizationError(f"{label} must be a string")
    return normalized or None


def _retain_weather_duplicates(rows: list[dict[str, object | None]]) -> list[dict[str, object | None]]:
    winners: dict[int, dict[str, object | None]] = {}
    fields = tuple(target for _, target in _WEATHER_FIELDS)
    for row in rows:
        key = row["session_time_ms"]
        assert isinstance(key, int)
        current = winners.get(key)
        if current is None or _weather_retention_key(row, fields) < _weather_retention_key(current, fields):
            winners[key] = row
    return list(winners.values())


def _weather_retention_key(row: Mapping[str, object | None], fields: tuple[str, ...]) -> tuple[object, ...]:
    values = tuple(canonical_scalar_sort_key(row[field]) for field in WEATHER_SCHEMA_V2)
    return (-sum(row[field] is not None for field in fields), values)


def _reject_duplicate_starts(rows: list[dict[str, object | None]]) -> None:
    starts = [row["start_time_ms"] for row in rows]
    if len(starts) != len(set(starts)):
        raise NormalizationError("duplicate track-status start_time_ms")


def _status_start_time(row: Mapping[str, object | None]) -> int:
    start_time = row["start_time_ms"]
    assert isinstance(start_time, int)
    return start_time


__all__ = [
    "adapt_track_status_intervals",
    "adapt_weather",
    "normalize_track_status_intervals",
    "normalize_weather",
]
