"""Duck-typed, in-memory normalization of FastF1 timing laps and stints."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from itertools import groupby
import math
import re
from typing import cast

import polars as pl

from .canonical_schema import LAPS_SCHEMA, STINTS_SCHEMA
from .normalizers import NormalizationError, normalize_nullable_scalar, normalize_session_time_ms
from .validators import validate_canonical_table


_INT16_MAX = 32_767
_CANONICAL_DRIVER_ID = re.compile(r"(?:[A-Z]{3}|D(?:0|[1-9][0-9]*))\Z")


def adapt_laps(session: object, session_id: str, driver_ids: Mapping[str, str]) -> pl.DataFrame:
    """Adapt ``session.laps`` without consulting source ``Driver`` labels.

    ``driver_ids`` is the already-normalized, session-scoped mapping from the
    original FastF1 driver-number key to its canonical driver identifier.
    """
    _require_session_id(session_id)
    rows = [_lap_row(record, session_id, driver_ids) for record in _records(session)]
    _reject_duplicate_laps(rows)
    frame = pl.DataFrame(sorted(rows, key=_lap_key), schema=LAPS_SCHEMA, strict=True)
    validate_canonical_table("laps", frame)
    return frame


def adapt_stints(
    session: object,
    session_id: str,
    driver_ids: Mapping[str, str],
    laps_frame: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Derive contiguous tyre-stint summaries solely from canonical timing laps.

    ``laps_frame`` lets an orchestrator derive stints from the exact already
    validated lap snapshot instead of reading mutable session data a second time.
    """
    laps = (laps_frame if laps_frame is not None else adapt_laps(session, session_id, driver_ids)).to_dicts()
    stints = [
        _stint_row(group)
        for _, driver_laps in groupby(laps, key=lambda row: (row["session_id"], row["driver_id"]))
        for _, group in _contiguous_stint_groups(list(driver_laps))
    ]
    frame = pl.DataFrame(sorted(stints, key=_stint_key), schema=STINTS_SCHEMA, strict=True)
    validate_canonical_table("stints", frame)
    return frame


normalize_laps = adapt_laps
normalize_stints = adapt_stints


def _records(session: object) -> Iterable[Mapping[str, object]]:
    try:
        laps = getattr(session, "laps")
    except AttributeError as error:
        raise NormalizationError("loaded session is missing required laps") from error
    if laps is None:
        return ()
    to_dicts = getattr(laps, "to_dicts", None)
    if callable(to_dicts):
        return _mapping_records(to_dicts())
    to_dict = getattr(laps, "to_dict", None)
    if callable(to_dict):
        try:
            return _mapping_records(to_dict("records"))
        except TypeError:
            return _mapping_records(to_dict(orient="records"))
    if isinstance(laps, Iterable) and not isinstance(laps, (str, bytes, Mapping)):
        return _mapping_records(laps)
    raise NormalizationError("session laps must provide iterable mapping records")


def _mapping_records(records: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(records, Iterable) or isinstance(records, (str, bytes, Mapping)):
        raise NormalizationError("lap records must be iterable mappings")
    materialized = tuple(records)
    if not all(isinstance(record, Mapping) for record in materialized):
        raise NormalizationError("lap records must be mappings")
    return tuple(cast(Mapping[str, object], record) for record in materialized)


def _lap_row(record: Mapping[str, object], session_id: str, driver_ids: Mapping[str, str]) -> dict[str, object | None]:
    source_key = _source_key(_required(record, "DriverNumber"))
    try:
        driver_id = driver_ids[source_key]
    except KeyError as error:
        raise NormalizationError(f"missing canonical driver ID for source key: {source_key}") from error
    if not isinstance(driver_id, str) or not _CANONICAL_DRIVER_ID.fullmatch(driver_id):
        raise NormalizationError(f"invalid canonical driver ID for source key: {source_key}")
    return {
        "session_id": session_id,
        "driver_id": driver_id,
        "lap_number": _required_int16(record, "LapNumber", "lap number"),
        "stint_number": _nullable_int16(record.get("Stint"), "stint number"),
        "lap_start_time_ms": _required_time(record, "LapStartTime", "lap start time"),
        "lap_end_time_ms": _nullable_time(record.get("Time"), "lap end time"),
        "lap_duration_ms": _nullable_time(record.get("LapTime"), "lap duration"),
        "pit_in_time_ms": _nullable_time(record.get("PitInTime"), "pit-in time"),
        "pit_out_time_ms": _nullable_time(record.get("PitOutTime"), "pit-out time"),
        "compound": _nullable_text(record.get("Compound"), "compound"),
        "tyre_life": _nullable_int16(record.get("TyreLife"), "tyre life"),
        "is_fresh_tyre": _nullable_bool(record.get("FreshTyre"), "fresh tyre"),
        "track_status": _nullable_text(record.get("TrackStatus"), "track status"),
        "is_accurate": _nullable_bool(record.get("IsAccurate"), "accuracy flag"),
        "deleted": _nullable_bool(record.get("Deleted"), "deleted flag"),
        "deleted_reason": _nullable_text(record.get("DeletedReason"), "deleted reason"),
    }


def _contiguous_stint_groups(laps: list[dict[str, object | None]]) -> Iterable[tuple[int, list[dict[str, object | None]]]]:
    seen: set[int] = set()
    for stint_number, group in groupby(laps, key=lambda row: row["stint_number"]):
        grouped_laps = list(group)
        if stint_number is None:
            continue
        assert isinstance(stint_number, int)
        if stint_number in seen:
            raise NormalizationError(f"stint number is not contiguous: {stint_number}")
        seen.add(stint_number)
        yield stint_number, grouped_laps


def _stint_row(laps: list[dict[str, object | None]]) -> dict[str, object | None]:
    first, last = laps[0], laps[-1]
    return {
        "session_id": first["session_id"], "driver_id": first["driver_id"],
        "stint_number": first["stint_number"], "start_lap_number": first["lap_number"],
        "end_lap_number": last["lap_number"], "start_time_ms": first["lap_start_time_ms"],
        "end_time_ms": last["lap_end_time_ms"], "compound": first["compound"],
        "tyre_life_at_start": first["tyre_life"], "is_fresh_tyre": first["is_fresh_tyre"],
    }


def _stint_key(row: Mapping[str, object | None]) -> tuple[str, str, int]:
    session_id, driver_id, stint_number = row["session_id"], row["driver_id"], row["stint_number"]
    assert isinstance(session_id, str) and isinstance(driver_id, str) and isinstance(stint_number, int)
    return session_id, driver_id, stint_number


def _reject_duplicate_laps(rows: Iterable[Mapping[str, object | None]]) -> None:
    keys = [_lap_key(row) for row in rows]
    if len(keys) != len(set(keys)):
        raise NormalizationError("duplicate canonical lap key")


def _lap_key(row: Mapping[str, object | None]) -> tuple[str, str, int]:
    session_id, driver_id, lap_number = row["session_id"], row["driver_id"], row["lap_number"]
    assert isinstance(session_id, str) and isinstance(driver_id, str) and isinstance(lap_number, int)
    return session_id, driver_id, lap_number


def _required(record: Mapping[str, object], field: str) -> object:
    if field not in record:
        raise NormalizationError(f"lap row is missing required {field}")
    return record[field]


def _required_time(record: Mapping[str, object], field: str, label: str) -> int:
    value = _required(record, field)
    if _is_missing(value):
        raise NormalizationError(f"{label} is required")
    return _time(value, label)


def _nullable_time(value: object | None, label: str) -> int | None:
    return None if _is_missing(value) else _time(value, label)


def _time(value: object, label: str) -> int:
    try:
        return normalize_session_time_ms(value)
    except NormalizationError as error:
        raise NormalizationError(f"invalid {label}: {error}") from error


def _required_int16(record: Mapping[str, object], field: str, label: str) -> int:
    value = _nullable_int16(_required(record, field), label)
    if value is None:
        raise NormalizationError(f"{label} is required")
    return value


def _nullable_int16(value: object | None, label: str) -> int | None:
    normalized = normalize_nullable_scalar(value)
    if normalized is None:
        return None
    if isinstance(normalized, bool) or not isinstance(normalized, (int, float)) or int(normalized) != normalized:
        raise NormalizationError(f"{label} must be an integer")
    if not 0 <= int(normalized) <= _INT16_MAX:
        raise NormalizationError(f"{label} must fit in Int16")
    return int(normalized)


def _nullable_text(value: object | None, label: str) -> str | None:
    normalized = normalize_nullable_scalar(value)
    if normalized is None:
        return None
    if not isinstance(normalized, str):
        raise NormalizationError(f"{label} must be a string")
    return normalized or None


def _nullable_bool(value: object | None, label: str) -> bool | None:
    normalized = normalize_nullable_scalar(value)
    if normalized is None:
        return None
    if not isinstance(normalized, bool):
        raise NormalizationError(f"{label} must be a boolean")
    return normalized


def _is_missing(value: object | None) -> bool:
    return (
        value is None
        or (isinstance(value, float) and not math.isfinite(value))
        or type(value).__name__ in {"NAType", "NaTType"}
    )


def _source_key(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NormalizationError("lap DriverNumber must be a non-empty string")
    return value


def _require_session_id(value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise NormalizationError("session_id must be a non-empty string")


__all__ = ["adapt_laps", "adapt_stints", "normalize_laps", "normalize_stints"]
