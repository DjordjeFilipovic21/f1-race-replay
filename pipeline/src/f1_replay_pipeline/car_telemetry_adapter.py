"""Duck-typed adapter for FastF1's native car telemetry stream."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import cast

import polars as pl

from .canonical_schema import CAR_TELEMETRY_SCHEMA
from .normalizers import (
    NormalizationError,
    normalize_nullable_scalar,
    normalize_session_time_ms,
    sort_and_deduplicate_rows,
)
from .session_metadata_adapter import adapt_drivers, adapt_session_metadata
from .validators import validate_canonical_table


_MEASUREMENT_FIELDS = ("speed_kph", "rpm", "gear", "throttle_pct", "brake", "drs")
_CAR_FIELDS = (
    ("Speed", "speed_kph"),
    ("RPM", "rpm"),
    ("nGear", "gear"),
    ("Throttle", "throttle_pct"),
    ("Brake", "brake"),
    ("DRS", "drs"),
)
_INT16_MIN = -32_768
_INT16_MAX = 32_767


def adapt_car_telemetry(
    session: object,
    session_id: str | None = None,
    driver_ids: Mapping[str, str] | None = None,
) -> pl.DataFrame:
    """Return normalized native car samples without merging or resampling streams.

    Callers that already normalized the driver roster can pass ``driver_ids`` to
    keep every adapter on one session-scoped source-key mapping.
    """
    canonical_session_id = _session_id(session, session_id)
    canonical_driver_ids = driver_ids if driver_ids is not None else _driver_ids(session, canonical_session_id)
    rows = [
        _car_row(record, canonical_session_id, source_key, canonical_driver_ids)
        for source_key, stream in _car_data(session).items()
        for record in _records(stream, source_key)
    ]
    retained = sort_and_deduplicate_rows(
        rows,
        column_order=tuple(CAR_TELEMETRY_SCHEMA),
        measurement_fields=_MEASUREMENT_FIELDS,
    )
    frame = pl.DataFrame(retained, schema=CAR_TELEMETRY_SCHEMA)
    validate_canonical_table("car_telemetry", frame)
    return frame


def _driver_ids(session: object, session_id: str) -> Mapping[str, str]:
    drivers = adapt_drivers(session, session_id)
    return {
        row["source_driver_key"]: row["driver_id"]
        for row in drivers.select("source_driver_key", "driver_id").to_dicts()
    }


normalize_car_telemetry = adapt_car_telemetry


def _session_id(session: object, provided: str | None) -> str:
    session_id = provided if provided is not None else adapt_session_metadata(session).item(0, "session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise NormalizationError("session_id must be a non-empty string")
    return session_id


def _car_data(session: object) -> Mapping[object, object]:
    try:
        car_data = getattr(session, "car_data")
    except AttributeError as error:
        raise NormalizationError("loaded session is missing required car_data") from error
    if not isinstance(car_data, Mapping):
        raise NormalizationError("car_data must be a mapping keyed by source driver key")
    return car_data


def _records(stream: object, source_key: object) -> Iterable[Mapping[str, object]]:
    if isinstance(stream, Mapping):
        raise NormalizationError(f"car_data stream for {_source_key(source_key)} must contain records")
    to_dicts = getattr(stream, "to_dicts", None)
    if callable(to_dicts):
        return _mapping_records(cast(Iterable[object], to_dicts()), source_key)
    to_dict = getattr(stream, "to_dict", None)
    if callable(to_dict):
        try:
            records = to_dict("records")
        except TypeError:
            records = to_dict(orient="records")
        return _mapping_records(cast(Iterable[object], records), source_key)
    if isinstance(stream, Iterable) and not isinstance(stream, (str, bytes)):
        return _mapping_records(stream, source_key)
    raise NormalizationError(f"car_data stream for {_source_key(source_key)} must provide iterable mapping records")


def _mapping_records(records: Iterable[object], source_key: object) -> tuple[Mapping[str, object], ...]:
    materialized = tuple(records)
    if not all(isinstance(record, Mapping) for record in materialized):
        raise NormalizationError(f"car_data stream for {_source_key(source_key)} records must be mappings")
    return tuple(cast(Mapping[str, object], record) for record in materialized)


def _car_row(
    record: Mapping[str, object],
    session_id: str,
    source_key: object,
    driver_ids: Mapping[str, str],
) -> dict[str, object | None]:
    normalized_key = _source_key(source_key)
    try:
        driver_id = driver_ids[normalized_key]
    except KeyError as error:
        raise NormalizationError(f"car_data source driver key is not in the driver roster: {normalized_key}") from error
    return {
        "session_id": session_id,
        "driver_id": driver_id,
        "source_driver_key": normalized_key,
        "session_time_ms": _required_session_time(record),
        **{target: _measurement(record.get(source), target) for source, target in _CAR_FIELDS},
        "source": "car",
    }


def _required_session_time(record: Mapping[str, object]) -> int:
    if "SessionTime" not in record or record["SessionTime"] is None:
        raise NormalizationError("car telemetry SessionTime is required")
    try:
        return normalize_session_time_ms(record["SessionTime"])
    except NormalizationError as error:
        raise NormalizationError(f"invalid car telemetry SessionTime: {error}") from error


def _measurement(value: object | None, field: str) -> float | int | bool | None:
    normalized = normalize_nullable_scalar(value)
    if normalized is None:
        return None
    if field == "brake":
        if not isinstance(normalized, bool):
            raise NormalizationError("brake must be a boolean")
        return normalized
    if field in {"gear", "drs"}:
        if isinstance(normalized, bool) or not isinstance(normalized, (int, float)):
            raise NormalizationError(f"{field} must be an Int16")
        if int(normalized) != normalized or not _INT16_MIN <= int(normalized) <= _INT16_MAX:
            raise NormalizationError(f"{field} must fit in Int16")
        return int(normalized)
    if isinstance(normalized, bool) or not isinstance(normalized, (int, float)):
        raise NormalizationError(f"{field} must be numeric")
    return float(normalized)


def _source_key(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NormalizationError("car_data source driver key must be a non-empty string")
    return value


__all__ = ["adapt_car_telemetry", "normalize_car_telemetry"]
