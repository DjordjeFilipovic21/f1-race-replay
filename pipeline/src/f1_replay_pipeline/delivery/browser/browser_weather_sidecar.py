"""Pure derivation of the optional native-cadence weather sidecar.

Absence (no ``weather`` frame or an empty one) degrades to ``None``; corrupt or
invalid canonical weather fails closed with a classified
:class:`WeatherSidecarCorruptionError` carrying a
:class:`WeatherSidecarClassification`.  Only V2 canonical identities are used;
there is no V1 fallback path.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
import math
from typing import TypedDict, cast

import polars as pl

from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    MAX_INT64,
    _FIXTURE_ID,
    BrowserWeatherSidecar,
    CanonicalGenerationSnapshot,
)


_WEATHER_COLUMNS = (
    "session_id",
    "session_time_ms",
    "air_temperature_c",
    "humidity_pct",
    "pressure_mbar",
    "rainfall",
    "track_temperature_c",
    "wind_direction_deg",
    "wind_speed_mps",
)
_MEASUREMENT_COLUMNS = (
    "air_temperature_c",
    "humidity_pct",
    "pressure_mbar",
    "track_temperature_c",
    "wind_direction_deg",
    "wind_speed_mps",
)


class WeatherSidecarClassification(Enum):
    """Structured classification of weather-sidecar derivation outcomes.

    ``ABSENT`` documents the optional degradation path (surfaced as the ``None``
    contract, never raised); every other member is a fail-closed corruption
    cause raised via :class:`WeatherSidecarCorruptionError`.
    """

    ABSENT = "absent"
    UNEXPECTED_SCHEMA = "unexpected_schema"
    NON_NUMERIC_MEASUREMENT = "non_numeric_measurement"
    NON_FINITE_MEASUREMENT = "non_finite_measurement"
    INVALID_TIME = "invalid_time"
    UNSORTED_ROWS = "unsorted_rows"
    FIXTURE_ID_DISAGREEMENT = "fixture_id_disagreement"
    MISSING_FIXTURE_IDENTITY = "missing_fixture_identity"
    INVALID_FIXTURE_ID = "invalid_fixture_id"
    INVALID_RANGE = "invalid_range"
    INVALID_RAINFALL = "invalid_rainfall"


class WeatherSidecarCorruptionError(ValueError):
    """Fail-closed canonical weather corruption with a structured classification.

    Subclasses ``ValueError`` so existing fail-closed callers that catch
    ``ValueError`` keep working unchanged; ``classification`` makes the
    corruption cause diagnosable and distinguishable from optional absence
    (which remains the ``None`` contract).
    """

    classification: WeatherSidecarClassification

    def __init__(
        self,
        message: str,
        *,
        classification: WeatherSidecarClassification,
    ) -> None:
        super().__init__(message)
        self.classification = classification


class WeatherSidecarCorruptionTypeError(WeatherSidecarCorruptionError, TypeError):
    """Type-corruption variant catchable as either ``ValueError`` or ``TypeError``.

    Preserves the historical fail-closed ``TypeError`` contract for non-numeric
    measurements and non-boolean rainfall while still carrying the structured
    classification.
    """


class _SanitizedWeatherRow(TypedDict):
    time_ms: int
    air_temp_c: float | None
    humidity_pct: float | None
    pressure_mbar: float | None
    rainfall: bool | None
    track_temp_c: float | None
    wind_direction_deg: int | None
    wind_speed_mps: float | None


class WeatherSidecarBuilder:
    """Stateless callable facade for deterministic weather-sidecar derivation."""

    @staticmethod
    def build(snapshot: CanonicalGenerationSnapshot) -> BrowserWeatherSidecar | None:
        return build_weather_sidecar(snapshot)

    def __call__(self, snapshot: CanonicalGenerationSnapshot) -> BrowserWeatherSidecar | None:
        return self.build(snapshot)


def build_weather_sidecar(
    snapshot: CanonicalGenerationSnapshot,
) -> BrowserWeatherSidecar | None:
    """Build one immutable sidecar from native canonical weather rows.

    The canonical frame is read without sorting, clipping, filling, or changing
    it.  Zero sentinels are classified only while creating the derived sidecar;
    the frontend remains responsible for causal last-known sampling.

    Absence (no ``weather`` frame or an empty one that matches the canonical
    schema) returns ``None``; corrupt or invalid canonical weather fails closed
    with a classified :class:`WeatherSidecarCorruptionError` and is never
    repaired or reordered.  A malformed empty frame is schema-validated before
    the empty degradation, so it fails closed instead of being mistaken for
    optional absence.
    """
    if not isinstance(snapshot, CanonicalGenerationSnapshot):
        raise TypeError("snapshot must be a CanonicalGenerationSnapshot")
    weather = snapshot.frames.get("weather")
    if weather is None:
        return None
    # Schema is validated before the empty degradation: only a correctly
    # shaped empty frame is the optional-weather None contract; an empty
    # frame with an unexpected schema remains fail-closed corruption.
    _validate_weather_frame(weather)
    if weather.is_empty():
        return None
    fixture_id = _fixture_id(snapshot)
    rows = tuple(weather.select(_WEATHER_COLUMNS).to_dicts())
    sanitized = tuple(_sanitize_row(row, fixture_id) for row in rows)
    _validate_row_ordering(sanitized)
    for row in sanitized:
        _validate_sanitized_ranges(row)
    return BrowserWeatherSidecar(
        fixture_id,
        tuple(row["time_ms"] for row in sanitized),
        tuple(row["air_temp_c"] for row in sanitized),
        tuple(row["humidity_pct"] for row in sanitized),
        tuple(row["pressure_mbar"] for row in sanitized),
        tuple(row["rainfall"] for row in sanitized),
        tuple(row["track_temp_c"] for row in sanitized),
        tuple(row["wind_direction_deg"] for row in sanitized),
        tuple(row["wind_speed_mps"] for row in sanitized),
    )


def _validate_weather_frame(weather: pl.DataFrame) -> None:
    if tuple(weather.columns) != _WEATHER_COLUMNS:
        raise WeatherSidecarCorruptionError(
            "canonical weather frame has an unexpected schema",
            classification=WeatherSidecarClassification.UNEXPECTED_SCHEMA,
        )


def _fixture_id(snapshot: CanonicalGenerationSnapshot) -> str:
    metadata = snapshot.frames.get("session_metadata")
    if metadata is None or metadata.height != 1 or "session_id" not in metadata.columns:
        raise WeatherSidecarCorruptionError(
            "canonical session metadata must contain exactly one fixture row",
            classification=WeatherSidecarClassification.MISSING_FIXTURE_IDENTITY,
        )
    fixture_id = metadata.item(0, "session_id")
    if not isinstance(fixture_id, str) or not fixture_id:
        raise WeatherSidecarCorruptionError(
            "canonical fixture_id must be a non-empty string",
            classification=WeatherSidecarClassification.MISSING_FIXTURE_IDENTITY,
        )
    if not _FIXTURE_ID.fullmatch(fixture_id):
        # The model enforces the canonical pattern at construction; validating
        # the same compiled pattern here keeps the model-boundary failure a
        # classified corruption instead of a raw ValueError.
        raise WeatherSidecarCorruptionError(
            "canonical fixture_id must match the canonical identifier pattern",
            classification=WeatherSidecarClassification.INVALID_FIXTURE_ID,
        )
    return fixture_id


def _sanitize_row(row: Mapping[str, object], fixture_id: str) -> _SanitizedWeatherRow:
    if row.get("session_id") != fixture_id:
        raise WeatherSidecarCorruptionError(
            "canonical weather row fixture_id disagrees with session metadata",
            classification=WeatherSidecarClassification.FIXTURE_ID_DISAGREEMENT,
        )
    measurements = {
        column: _numeric(row.get(column), column) for column in _MEASUREMENT_COLUMNS
    }
    corroborated = any(
        value is not None and value != 0.0 for value in measurements.values()
    )
    return {
        "time_ms": _time_ms(row.get("session_time_ms")),
        "air_temp_c": _nonzero_or_null(measurements["air_temperature_c"]),
        "humidity_pct": _zero_with_corroboration(measurements["humidity_pct"], corroborated),
        "pressure_mbar": _nonzero_or_null(measurements["pressure_mbar"]),
        "rainfall": _rainfall(row.get("rainfall")),
        "track_temp_c": _nonzero_or_null(measurements["track_temperature_c"]),
        "wind_direction_deg": _wind_direction(measurements["wind_direction_deg"], corroborated),
        "wind_speed_mps": _zero_with_corroboration(measurements["wind_speed_mps"], corroborated),
    }


def _numeric(value: object, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherSidecarCorruptionTypeError(
            f"{label} must contain numeric values or null",
            classification=WeatherSidecarClassification.NON_NUMERIC_MEASUREMENT,
        )
    numeric: float
    try:
        numeric = float(value)
    except OverflowError:
        # A numeric value too large to represent in a finite Float64 is
        # effectively non-finite; fail closed instead of leaking OverflowError.
        raise WeatherSidecarCorruptionError(
            f"{label} must contain finite values or null",
            classification=WeatherSidecarClassification.NON_FINITE_MEASUREMENT,
        ) from None
    if not math.isfinite(numeric):
        raise WeatherSidecarCorruptionError(
            f"{label} must contain finite values or null",
            classification=WeatherSidecarClassification.NON_FINITE_MEASUREMENT,
        )
    return numeric


def _time_ms(value: object) -> int:
    # Wording keeps the public "non-negative integer" substring (asserted by
    # the legacy behavior test) while still documenting the signed-Int64 upper
    # bound enforced against MAX_INT64 below.
    if (
        type(value) is not int
        or value < 0
        or value > MAX_INT64
    ):
        raise WeatherSidecarCorruptionError(
            "session_time_ms must contain a non-negative integer within signed Int64 bounds",
            classification=WeatherSidecarClassification.INVALID_TIME,
        )
    return value


def _nonzero_or_null(value: float | None) -> float | None:
    return None if value == 0.0 else value


def _zero_with_corroboration(value: float | None, corroborated: bool) -> float | None:
    if value != 0.0 or corroborated:
        return value
    return None


def _wind_direction(value: float | None, corroborated: bool) -> int | None:
    if value is None or value == 0.0 and not corroborated:
        return None
    if value != math.trunc(value) or not 0 <= value <= 359:
        raise WeatherSidecarCorruptionError(
            "wind_direction_deg must contain integer degrees from 0 through 359",
            classification=WeatherSidecarClassification.INVALID_RANGE,
        )
    return int(value)


def _rainfall(value: object) -> bool | None:
    if value is not None and type(value) is not bool:
        raise WeatherSidecarCorruptionTypeError(
            "rainfall must contain booleans or null",
            classification=WeatherSidecarClassification.INVALID_RAINFALL,
        )
    return cast(bool | None, value)


def _validate_row_ordering(rows: tuple[_SanitizedWeatherRow, ...]) -> None:
    """Fail closed on unsorted canonical rows without ever reordering them."""
    times = tuple(row["time_ms"] for row in rows)
    if any(
        following <= current
        for current, following in zip(times, times[1:], strict=False)
    ):
        raise WeatherSidecarCorruptionError(
            "canonical weather rows must be strictly increasing by session_time_ms",
            classification=WeatherSidecarClassification.UNSORTED_ROWS,
        )


def _validate_sanitized_ranges(row: _SanitizedWeatherRow) -> None:
    """Enforce the sidecar model's measurement ranges before construction.

    Applied to sanitized values so zero sentinels (already ``None``) are not
    mistaken for invalid ranges; negative or out-of-bounds measurements remain
    fail-closed with a classified error.
    """
    if row["air_temp_c"] is not None and row["air_temp_c"] <= 0.0:
        raise WeatherSidecarCorruptionError(
            "air_temperature_c must be strictly positive or null",
            classification=WeatherSidecarClassification.INVALID_RANGE,
        )
    if row["humidity_pct"] is not None and not 0.0 <= row["humidity_pct"] <= 100.0:
        raise WeatherSidecarCorruptionError(
            "humidity_pct must be within 0 through 100 or null",
            classification=WeatherSidecarClassification.INVALID_RANGE,
        )
    if row["pressure_mbar"] is not None and row["pressure_mbar"] <= 0.0:
        raise WeatherSidecarCorruptionError(
            "pressure_mbar must be strictly positive or null",
            classification=WeatherSidecarClassification.INVALID_RANGE,
        )
    if row["track_temp_c"] is not None and row["track_temp_c"] <= 0.0:
        raise WeatherSidecarCorruptionError(
            "track_temperature_c must be strictly positive or null",
            classification=WeatherSidecarClassification.INVALID_RANGE,
        )
    if row["wind_speed_mps"] is not None and row["wind_speed_mps"] < 0.0:
        raise WeatherSidecarCorruptionError(
            "wind_speed_mps must be non-negative or null",
            classification=WeatherSidecarClassification.INVALID_RANGE,
        )


__all__ = [
    "WeatherSidecarBuilder",
    "WeatherSidecarClassification",
    "WeatherSidecarCorruptionError",
    "WeatherSidecarCorruptionTypeError",
    "build_weather_sidecar",
]
