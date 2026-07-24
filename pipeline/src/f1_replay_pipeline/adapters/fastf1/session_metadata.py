"""Duck-typed, in-memory adapters for canonical session metadata and drivers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import math
import re
import polars as pl

from ...domain.canonical_schema import DRIVERS_SCHEMA, SESSION_METADATA_SCHEMA
from ...domain.normalizers import NormalizationError, normalize_driver_id, normalize_nullable_scalar
from ...domain.validators import validate_canonical_table


_INT16_MAX = 32_767
_SESSION_TYPE_TOKEN = re.compile(r"[^a-z0-9]+")


def adapt_session_metadata(session: object) -> pl.DataFrame:
    """Return the single typed canonical metadata row for a loaded session."""
    event = _attribute(session, "event")
    year = _required_int(_first_value(session, event, "year", "Year", "EventDate"), "year")
    round_number = _required_int(
        _first_value(session, event, "round_number", "RoundNumber"),
        "round number",
        minimum=0,
    )
    session_info = _field(session, "session_info")
    session_name = _nullable_text(_first_value(session, session_info, "name", "session_name"))
    session_type = _session_type(_first_value(session, session_info, "session_type", "type", "name"))
    session_id = f"{year:04d}-{round_number:02d}-{session_type}"
    row = {
        "session_id": session_id,
        "year": year,
        "round_number": round_number,
        "event_name": _nullable_text(_first_value(None, event, "EventName", "OfficialEventName")),
        "session_name": session_name,
        "session_type": session_type,
        "session_start_time_utc": _nullable_datetime(
            _first_value(session, event, "date", "SessionDateUtc", "SessionDate")
        ),
    }
    frame = pl.DataFrame([row], schema=SESSION_METADATA_SCHEMA).sort("session_id")
    validate_canonical_table("session_metadata", frame)
    return frame


def adapt_drivers(session: object, session_id: str | None = None) -> pl.DataFrame:
    """Return the typed canonical roster, rejecting ambiguous session identities."""
    canonical_session_id = session_id or adapt_session_metadata(session).item(0, "session_id")
    _require_session_id(canonical_session_id)
    rows: list[dict[str, object | None]] = []
    driver_ids: set[str] = set()
    source_keys: set[str] = set()
    for source_key, driver in _driver_records(session):
        if source_key in source_keys:
            raise NormalizationError(f"duplicate source driver key: {source_key}")
        source_keys.add(source_key)
        number = _field(driver, "DriverNumber")
        driver_id = normalize_driver_id(_field(driver, "Abbreviation"), number, driver_ids)
        driver_ids.add(driver_id)
        rows.append(
            {
                "session_id": canonical_session_id,
                "driver_id": driver_id,
                "source_driver_key": source_key,
                "driver_number": _nullable_int16(number, "driver number"),
                "full_name": _nullable_text(_first_field(driver, "FullName", "BroadcastName")),
                "team_name": _nullable_text(_field(driver, "TeamName")),
                "team_colour": _nullable_text(_first_field(driver, "TeamColor", "TeamColour")),
            }
        )
    frame = pl.DataFrame(rows, schema=DRIVERS_SCHEMA).sort("session_id", "driver_id")
    validate_canonical_table("drivers", frame)
    return frame


normalize_session_metadata = adapt_session_metadata
normalize_drivers = adapt_drivers


def _driver_records(session: object) -> Iterable[tuple[str, object]]:
    drivers = _attribute(session, "drivers")
    if isinstance(drivers, Mapping):
        for source_key, record in drivers.items():
            yield _source_key(source_key), record
        return
    if not isinstance(drivers, Iterable):
        raise NormalizationError("loaded session drivers must be iterable")
    get_driver = getattr(session, "get_driver", None)
    if not callable(get_driver):
        raise NormalizationError("loaded session is missing required get_driver")
    for source_key in drivers:
        normalized_key = _source_key(source_key)
        try:
            yield normalized_key, get_driver(source_key)
        except (AttributeError, KeyError, ValueError) as error:
            raise NormalizationError(
                f"unable to retrieve driver for source key: {normalized_key}"
            ) from error


def _attribute(source: object, name: str) -> object:
    try:
        return getattr(source, name)
    except AttributeError as error:
        raise NormalizationError(f"loaded session is missing required {name}") from error


def _first_value(primary: object | None, secondary: object | None, *names: str) -> object | None:
    for source in (primary, secondary):
        for name in names:
            value = _field(source, name)
            if value is not None and type(value).__name__ not in {"NAType", "NaTType"}:
                return value
    return None


def _field(source: object | None, name: str) -> object | None:
    if source is None:
        return None
    if isinstance(source, Mapping):
        return source.get(name)
    getter = getattr(source, "get", None)
    if callable(getter):
        return getter(name)
    return getattr(source, name, None)


def _first_field(source: object, *names: str) -> object | None:
    return next((value for name in names if (value := _field(source, name)) is not None), None)


def _required_int(value: object | None, label: str, *, minimum: int = 1) -> int:
    normalized = _nullable_int16(value, label)
    if normalized is None or normalized < minimum:
        requirement = "a non-negative" if minimum == 0 else "a positive"
        raise NormalizationError(f"{label} is required and must be {requirement} Int16")
    return normalized


def _nullable_int16(value: object | None, label: str) -> int | None:
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        if not candidate.isascii() or not candidate.isdecimal():
            raise NormalizationError(f"{label} must be an integer")
        normalized = int(candidate)
    else:
        timestamp_year = getattr(value, "year", None)
        if label == "year" and isinstance(timestamp_year, int) and not isinstance(timestamp_year, bool):
            normalized = timestamp_year
        else:
            normalized = normalize_nullable_scalar(value)
    if normalized is None:
        return None
    if isinstance(normalized, bool) or not isinstance(normalized, (int, float)):
        raise NormalizationError(f"{label} must be an integer")
    if int(normalized) != normalized or not 0 <= int(normalized) <= _INT16_MAX:
        raise NormalizationError(f"{label} must fit in Int16")
    return int(normalized)


def _nullable_text(value: object | None) -> str | None:
    normalized = normalize_nullable_scalar(value)
    if normalized is None:
        return None
    if not isinstance(normalized, str):
        raise NormalizationError("text metadata must be a string")
    return normalized or None


def _nullable_datetime(value: object | None) -> datetime | None:
    if value is None or type(value).__name__ in {"NAType", "NaTType"}:
        return None
    converter = getattr(value, "to_pydatetime", None)
    converted = converter() if callable(converter) else value
    if not isinstance(converted, datetime):
        raise NormalizationError("session start time must be a datetime")
    if converted.tzinfo is None:
        return converted.replace(tzinfo=timezone.utc)
    return converted.astimezone(timezone.utc)


def _session_type(value: object | None) -> str:
    name = _nullable_text(value)
    if name is None:
        raise NormalizationError("session type is required to build session_id")
    token = _SESSION_TYPE_TOKEN.sub("-", name.strip().lower()).strip("-")
    if not token:
        raise NormalizationError("session type is required to build session_id")
    return token


def _source_key(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NormalizationError("source driver key must be a non-empty string")
    return value


def _require_session_id(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise NormalizationError("session_id must be a non-empty string")


__all__ = ["adapt_drivers", "adapt_session_metadata", "normalize_drivers", "normalize_session_metadata"]
