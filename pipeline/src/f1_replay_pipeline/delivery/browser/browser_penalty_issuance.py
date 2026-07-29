"""Parse definitive issued-penalty messages for browser delivery."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import timedelta
import re

from .browser_delivery_models import BrowserPenaltyIssuance


_DRIVER_ID = re.compile(r"[A-Z0-9]{2,4}\Z")
_ISSUANCE = re.compile(
    r"^\s*FIA\s+STEWARDS?\s*:\s*"
    r"(?P<descriptor>.+?)\s+FOR\s+CAR\s+(?P<number>\d+)"
    r"(?:\s*\((?P<abbreviation>[A-Za-z0-9]{2,4})\))?"
    r"(?:\s*-\s*(?P<reason>.*))?\s*$",
    re.IGNORECASE,
)
_NON_DEFINITIVE = re.compile(
    r"\b(?:INVESTIGATION|PENDING|NOTED|SUMMONED|HEARING|DECISION|SERVED|"
    r"NO\s+PENALTY|WILL\s+BE|MAY\s+BE)\b",
    re.IGNORECASE,
)
_METADATA_ABBREVIATION_KEYS = (
    "abbreviation", "driver_abbreviation", "code", "short_code", "short_name",
)
_MESSAGE_KEYS = ("message", "Message", "raw_message", "rawMessage")
_TIME_KEYS = ("session_time_ms", "sessionTimeMs", "SessionTime", "Time", "time_ms")
_DRIVER_KEYS = ("driver_id", "driverId", "DriverId")
_LAP_KEYS = ("lap_number", "lapNumber", "Lap", "lap")


def parse_race_control_penalties(
    messages: object,
    driver_metadata: object | None = None,
) -> tuple[BrowserPenaltyIssuance, ...]:
    """Extract issued penalties from canonical or FastF1-style message rows.

    Only the explicit ``FIA STEWARDS: ... PENALTY FOR CAR ...`` form (and the
    equivalent reprimand/disqualification forms) is accepted.  The function
    deliberately emits no served-state inference: pit data is not an input and
    no lifecycle field is present on :class:`BrowserPenaltyIssuance`.

    Rows whose car number or abbreviation cannot be resolved against supplied
    driver metadata are ignored because the contract requires a canonical
    driver ID.  This keeps an uncertain identity from becoming a false marker.
    """
    lookup = _driver_lookup(driver_metadata)
    issuances = [
        issuance
        for record in _records(messages, "race_control_messages")
        for issuance in (_parse_record(record, lookup),)
        if issuance is not None
    ]
    return tuple(sorted(issuances, key=_issuance_sort_key))


def _parse_record(
    record: Mapping[str, object], lookup: Mapping[str, frozenset[str]],
) -> BrowserPenaltyIssuance | None:
    raw_value = _first_value(record, *_MESSAGE_KEYS)
    if not isinstance(raw_value, str) or not raw_value.strip():
        return None
    raw_message = raw_value
    match = _ISSUANCE.match(raw_message)
    if match is None or _NON_DEFINITIVE.search(raw_message):
        return None
    descriptor = _clean_text(match.group("descriptor"))
    if not _is_penalty_descriptor(descriptor):
        return None

    driver_id = _resolve_driver(record, match.group("number"), match.group("abbreviation"), lookup)
    session_time_ms = _session_time_ms(_first_value(record, *_TIME_KEYS))
    if driver_id is None or session_time_ms is None:
        return None

    reason = _clean_text(match.group("reason") or descriptor)
    lap = _positive_int(_first_value(record, *_LAP_KEYS))
    return BrowserPenaltyIssuance(
        driver_id=driver_id,
        session_time_ms=session_time_ms,
        penalty_type=_penalty_type(descriptor),
        reason=reason,
        raw_message=raw_message,
        lap_number=lap,
    )


def _is_penalty_descriptor(descriptor: str) -> bool:
    normalized = descriptor.upper()
    return bool(
        re.search(r"\bPENALTY\b|\bREPRIMAND\b|\bDISQUALIF(?:ICATION|IED)\b", normalized)
    )


def _penalty_type(descriptor: str) -> str:
    normalized = re.sub(r"\s+", " ", descriptor.upper()).strip()
    time_match = re.search(r"\b(\d+)\s+SECONDS?\s+TIME\s+PENALTY\b", normalized)
    if time_match:
        return f"TIME_{time_match.group(1)}S"
    grid_match = re.search(r"\b(\d+)\s+PLACES?\s+GRID\s+PENALTY\b", normalized)
    if grid_match:
        return f"GRID_{grid_match.group(1)}PLACES"
    if re.search(r"\bDRIVE\s*[- ]\s*THROUGH\b", normalized):
        return "DRIVE_THROUGH"
    if re.search(r"\bSTOP\s*[- ]\s*(?:AND\s*)?GO\b", normalized):
        return "STOP_GO"
    if "REPRIMAND" in normalized:
        return "REPRIMAND"
    if "DISQUALIF" in normalized:
        return "DISQUALIFICATION"
    return re.sub(r"[^A-Z0-9]+", "_", normalized).strip("_")


def _resolve_driver(
    record: Mapping[str, object], number: str, abbreviation: str | None,
    lookup: Mapping[str, frozenset[str]],
) -> str | None:
    explicit = _canonical_driver_id(_first_value(record, *_DRIVER_KEYS))
    if explicit is not None:
        return explicit
    candidates: set[str] = set()
    candidates.update(lookup.get(_number_key(number), frozenset()))
    if abbreviation is not None:
        candidates.update(lookup.get(abbreviation.upper(), frozenset()))
    return next(iter(candidates)) if len(candidates) == 1 else None


def _driver_lookup(driver_metadata: object | None) -> dict[str, frozenset[str]]:
    mappings: dict[str, set[str]] = {}
    for record in _metadata_records(driver_metadata):
        driver_id = _canonical_driver_id(record.get("driver_id") or record.get("driverId"))
        if driver_id is None:
            continue
        aliases: list[str] = []
        for key in ("driver_number", "driverNumber", "source_driver_key", "sourceDriverKey"):
            value = record.get(key)
            if value is not None:
                aliases.append(
                    _number_key(value)
                    if key in {"driver_number", "driverNumber"}
                    else str(value).strip().upper()
                )
        aliases.extend(_string_alias(record.get(key)) for key in _METADATA_ABBREVIATION_KEYS)
        aliases.append(driver_id)
        for alias in aliases:
            if alias:
                mappings.setdefault(alias, set()).add(driver_id)
    return {alias: frozenset(driver_ids) for alias, driver_ids in mappings.items()}


def _metadata_records(source: object | None) -> tuple[Mapping[str, object], ...]:
    if source is None:
        return ()
    if isinstance(source, Mapping):
        if all(isinstance(value, str) for value in source.values()):
            return tuple({"driver_number": key, "driver_id": value} for key, value in source.items())
        records = []
        for key, value in source.items():
            if isinstance(value, Mapping):
                records.append({**value, "driver_id": value.get("driver_id", key)})
        return tuple(records)
    return tuple(_records(source, "drivers"))


def _records(source: object, table_attribute: str) -> tuple[Mapping[str, object], ...]:
    table = getattr(source, table_attribute, None)
    if table is None:
        frames = getattr(source, "frames", None)
        table = frames.get(table_attribute) if isinstance(frames, Mapping) else source
    if table is None:
        return ()
    to_dicts = getattr(table, "to_dicts", None)
    if callable(to_dicts):
        records = to_dicts()
        return _mapping_records(records) if isinstance(records, Iterable) else ()
    to_dict = getattr(table, "to_dict", None)
    if callable(to_dict):
        try:
            records = to_dict("records")
            return _mapping_records(records) if isinstance(records, Iterable) else ()
        except TypeError:
            pass
    if isinstance(table, Mapping):
        return (table,)
    if isinstance(table, Iterable) and not isinstance(table, (str, bytes)):
        return _mapping_records(table)
    return ()


def _mapping_records(records: Iterable[object]) -> tuple[Mapping[str, object], ...]:
    return tuple(record for record in records if isinstance(record, Mapping))


def _string_alias(value: object | None) -> str:
    return value.strip().upper() if isinstance(value, str) else ""


def _first_value(record: Mapping[str, object], *keys: str) -> object | None:
    return next((record[key] for key in keys if key in record and record[key] is not None), None)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _canonical_driver_id(value: object | None) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().upper()
    return candidate if _DRIVER_ID.fullmatch(candidate) else None


def _number_key(value: object) -> str:
    if isinstance(value, bool):
        return str(value).upper()
    text = str(value).strip()
    return str(int(text)) if text.isdigit() else text.upper()


def _positive_int(value: object | None) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        return int(value) if value.isdigit() and int(value) > 0 else None
    if not isinstance(value, (int, float)):
        return None
    try:
        candidate = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return candidate if candidate > 0 and candidate == value else None


def _session_time_ms(value: object | None) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if type(value) is int:
        return value if value >= 0 else None
    if isinstance(value, timedelta):
        milliseconds = round(value.total_seconds() * 1000)
        return milliseconds if milliseconds >= 0 else None
    total_seconds = getattr(value, "total_seconds", None)
    if callable(total_seconds):
        seconds = total_seconds()
        if not isinstance(seconds, (int, float)):
            return None
        milliseconds = round(seconds * 1000)
        return milliseconds if milliseconds >= 0 else None
    return None


def _issuance_sort_key(value: BrowserPenaltyIssuance) -> tuple[object, ...]:
    return (
        value.session_time_ms, value.driver_id, value.penalty_type,
        value.reason, value.raw_message, value.lap_number or 0,
    )


__all__ = ["parse_race_control_penalties"]
