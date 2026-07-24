"""Pure boundary normalizers for canonical native-cadence telemetry."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import math
from numbers import Integral, Real
import re
from typing import TypeAlias, cast


NormalizedScalar: TypeAlias = str | int | float | bool
NormalizedRow: TypeAlias = dict[str, NormalizedScalar | None]

_DRIVER_ABBREVIATION = re.compile(r"[A-Za-z]{3}\Z")
_CAR_NUMBER = re.compile(r"[0-9]+\Z")
_CANONICAL_DRIVER_ID = re.compile(r"(?:[A-Z]{3}|D(?:0|[1-9][0-9]*))\Z")
_NANOSECONDS_PER_MILLISECOND = 1_000_000


class NormalizationError(ValueError):
    """Raised when a value cannot satisfy the canonical data contract."""


def normalize_session_time_ms(session_time: object) -> int:
    """Convert a non-negative SessionTime-like duration to half-up milliseconds."""
    if _is_missing_time(session_time):
        raise NormalizationError("SessionTime is missing required timestamp")
    if isinstance(session_time, timedelta):
        return _milliseconds_from_nanoseconds(_timedelta_nanoseconds(session_time))
    pandas_value = getattr(session_time, "value", None)
    if isinstance(pandas_value, int) and not isinstance(pandas_value, bool):
        return _milliseconds_from_nanoseconds(pandas_value)
    if isinstance(session_time, Decimal):
        return _milliseconds_from_decimal_seconds(session_time)
    if isinstance(session_time, (int, float)) and not isinstance(session_time, bool):
        return _milliseconds_from_decimal_seconds(Decimal(str(session_time)))
    raise NormalizationError("unsupported SessionTime value")


def normalize_race_control_time_ms(message_time: object, t0_date: object | None) -> int:
    """Normalize an absolute message timestamp or an explicit duration fallback.

    FastF1 race-control ``Time`` is normally an absolute UTC timestamp. Older
    or synthetic inputs can supply a session-relative duration instead, which
    deliberately remains a separate compatibility branch.
    """
    if _is_missing_time(message_time):
        raise NormalizationError("race-control Time is missing required timestamp")
    if _is_duration_like(message_time):
        return normalize_session_time_ms(message_time)
    if not isinstance(message_time, datetime):
        raise NormalizationError("race-control Time must be an absolute datetime or duration")
    if _is_missing_time(t0_date):
        raise NormalizationError("absolute race-control Time requires session.t0_date")
    if not isinstance(t0_date, datetime):
        raise NormalizationError("session.t0_date must be an absolute datetime")
    try:
        elapsed_nanoseconds = _datetime_nanoseconds(_as_naive_utc(message_time)) - _datetime_nanoseconds(
            _as_naive_utc(t0_date)
        )
    except (OverflowError, TypeError, ValueError) as error:
        raise NormalizationError("race-control Time cannot be compared with session.t0_date") from error
    try:
        return _milliseconds_from_nanoseconds(elapsed_nanoseconds)
    except NormalizationError as error:
        if "non-negative" in str(error):
            raise NormalizationError("race-control Time precedes session.t0_date") from error
        raise NormalizationError("race-control Time cannot be represented as milliseconds") from error


def normalize_nullable_scalar(value: object) -> NormalizedScalar | None:
    """Return null for missing or non-finite numeric measurements."""
    if value is None or _is_pandas_missing(value) or _is_nonfinite_numeric(value):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        try:
            normalized = float(value)
        except OverflowError:
            return None
        return normalized if math.isfinite(normalized) else None
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        normalized = float(value)
        return normalized if math.isfinite(normalized) else None
    if isinstance(value, str):
        return value
    raise NormalizationError(f"unsupported scalar type: {type(value).__name__}")


def normalize_driver_id(
    abbreviation: object | None,
    car_number: object | None,
    existing_driver_ids: Collection[str] = (),
) -> str:
    """Prefer a validated FastF1 abbreviation, else form a collision-free ``D<n>`` ID."""
    candidate = _normalize_abbreviation(abbreviation) or _normalize_car_number(car_number)
    if candidate in existing_driver_ids:
        raise NormalizationError(f"driver identifier collision: {candidate}")
    return candidate


def sort_and_deduplicate_rows(
    rows: Iterable[Mapping[str, object]],
    *,
    column_order: Sequence[str],
    measurement_fields: Sequence[str],
) -> list[NormalizedRow]:
    """Select deterministic duplicate winners, then sort by the canonical table key."""
    _validate_columns(column_order, measurement_fields)
    winners: dict[tuple[str, str, int], NormalizedRow] = {}
    for row in rows:
        normalized = _normalize_row(row, column_order)
        key = _canonical_key(normalized)
        current = winners.get(key)
        if current is None or _retention_key(normalized, column_order, measurement_fields) < _retention_key(
            current, column_order, measurement_fields
        ):
            winners[key] = normalized
    return [winners[key] for key in sorted(winners)]


def _timedelta_nanoseconds(value: timedelta) -> int:
    return ((value.days * 86_400 + value.seconds) * 1_000_000 + value.microseconds) * 1_000


def _as_naive_utc(value: datetime) -> datetime:
    """Put aware and FastF1's naive-UTC datetimes on one exact UTC timeline."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _is_missing_time(value: object | None) -> bool:
    return value is None or _is_pandas_missing(value) or _is_nonfinite_numeric(value)


def _is_duration_like(value: object) -> bool:
    value_type = type(value)
    return isinstance(value, timedelta) or (
        value_type.__module__.startswith("pandas.") and value_type.__name__ == "Timedelta"
    )


def _datetime_nanoseconds(value: datetime) -> int:
    """Return a datetime's exact UTC offset from the Unix epoch in nanoseconds."""
    nanoseconds = getattr(value, "value", None)
    if isinstance(nanoseconds, int) and not isinstance(nanoseconds, bool):
        return nanoseconds
    return _timedelta_nanoseconds(value - datetime(1970, 1, 1))


def _milliseconds_from_nanoseconds(nanoseconds: int) -> int:
    if nanoseconds < 0:
        raise NormalizationError("session time must be non-negative")
    return (nanoseconds + _NANOSECONDS_PER_MILLISECOND // 2) // _NANOSECONDS_PER_MILLISECOND


def _milliseconds_from_decimal_seconds(seconds: Decimal) -> int:
    if not seconds.is_finite():
        raise NormalizationError("session time must be finite")
    if seconds < 0:
        raise NormalizationError("session time must be non-negative")
    try:
        return int((seconds * Decimal("1000")).to_integral_value(rounding=ROUND_HALF_UP))
    except InvalidOperation as error:
        raise NormalizationError("session time cannot be represented as milliseconds") from error


def _normalize_abbreviation(value: object | None) -> str | None:
    if value is None or _is_pandas_missing(value) or _is_nonfinite_numeric(value):
        return None
    if not isinstance(value, str):
        raise NormalizationError("driver abbreviation must be a string")
    stripped = value.strip()
    if not stripped:
        raise NormalizationError("driver abbreviation must not be empty")
    if not _DRIVER_ABBREVIATION.fullmatch(stripped):
        raise NormalizationError("driver abbreviation must contain exactly three ASCII letters")
    return stripped.upper()


def _normalize_car_number(value: object | None) -> str:
    if value is None or _is_pandas_missing(value) or _is_nonfinite_numeric(value):
        raise NormalizationError("driver abbreviation or car number is required")
    number = str(value).strip()
    if not _CAR_NUMBER.fullmatch(number):
        raise NormalizationError("car number must contain only ASCII digits")
    return f"D{number.lstrip('0') or '0'}"


def _validate_columns(column_order: Sequence[str], measurement_fields: Sequence[str]) -> None:
    if len(set(column_order)) != len(column_order):
        raise NormalizationError("column order must not contain duplicates")
    required = {"session_id", "driver_id", "session_time_ms"}
    if not required.issubset(column_order):
        raise NormalizationError("column order omits a required canonical key")
    if not set(measurement_fields).issubset(column_order):
        raise NormalizationError("measurement fields must be declared columns")
    if len(set(measurement_fields)) != len(measurement_fields):
        raise NormalizationError("measurement fields must not contain duplicates")


def _normalize_row(row: Mapping[str, object], column_order: Sequence[str]) -> NormalizedRow:
    missing = [column for column in column_order if column not in row]
    if missing:
        raise NormalizationError(f"row is missing declared columns: {', '.join(missing)}")
    extra = [column for column in row if column not in column_order]
    if extra:
        raise NormalizationError(f"row has undeclared columns: {', '.join(extra)}")
    normalized = {column: normalize_nullable_scalar(row[column]) for column in column_order}
    _canonical_key(normalized)
    if "source_driver_key" in normalized:
        _source_driver_key(normalized)
    return normalized


def _canonical_key(row: Mapping[str, NormalizedScalar | None]) -> tuple[str, str, int]:
    session_id = row["session_id"]
    driver_id = row["driver_id"]
    time_ms = row["session_time_ms"]
    if not isinstance(session_id, str) or not session_id.strip():
        raise NormalizationError("session_id must be a non-empty, non-whitespace string")
    if not isinstance(driver_id, str) or not _CANONICAL_DRIVER_ID.fullmatch(driver_id):
        raise NormalizationError("driver_id must be a canonical three-letter or D<number> identifier")
    if not isinstance(time_ms, int) or isinstance(time_ms, bool) or time_ms < 0:
        raise NormalizationError("session_time_ms must be a non-negative integer")
    return session_id, driver_id, time_ms


def _source_driver_key(row: Mapping[str, NormalizedScalar | None]) -> str:
    source_driver_key = row.get("source_driver_key")
    if not isinstance(source_driver_key, str) or not source_driver_key.strip():
        raise NormalizationError("source_driver_key must be a non-empty, non-whitespace string")
    return source_driver_key


def _retention_key(
    row: Mapping[str, NormalizedScalar | None],
    column_order: Sequence[str],
    measurement_fields: Sequence[str],
) -> tuple[int, int, tuple[tuple[int, object], ...]]:
    completeness = sum(row[field] is not None for field in measurement_fields)
    provenance = 1 if row.get("source") in {"car", "pos"} else 0
    values = tuple(_lexical_scalar(row[column]) for column in column_order)
    return -completeness, -provenance, values


def _lexical_scalar(value: NormalizedScalar | None) -> tuple[int, object]:
    if value is None:
        return 0, 0
    if isinstance(value, bool):
        return 1, int(value)
    if isinstance(value, int):
        return 2, value
    if isinstance(value, float):
        return 3, value
    return 4, value


def canonical_scalar_sort_key(value: object) -> tuple[int, object]:
    """Return the declared type-aware scalar order after null normalization."""
    return _lexical_scalar(normalize_nullable_scalar(value))


def _is_pandas_missing(value: object) -> bool:
    value_type = type(value)
    return value_type.__module__.startswith("pandas.") and value_type.__name__ in {"NAType", "NaTType"}


def _is_nonfinite_numeric(value: object) -> bool:
    if isinstance(value, (bool, str, bytes)):
        return False
    try:
        return not math.isfinite(cast(Real, value))
    except (TypeError, ValueError):
        return False
