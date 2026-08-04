"""Pure derivation of the optional native-cadence weather sidecar."""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import TypedDict, cast

import polars as pl

from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
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

    The canonical frame is read without sorting, clipping, filling, or changing it.
    Zero sentinels are classified only while creating the derived sidecar; the
    frontend remains responsible for causal last-known sampling.
    """
    if not isinstance(snapshot, CanonicalGenerationSnapshot):
        raise TypeError("snapshot must be a CanonicalGenerationSnapshot")
    weather = snapshot.frames.get("weather")
    if weather is None or weather.is_empty():
        return None
    _validate_weather_frame(weather)
    fixture_id = _fixture_id(snapshot)
    rows = tuple(weather.select(_WEATHER_COLUMNS).to_dicts())
    sanitized = tuple(_sanitize_row(row, fixture_id) for row in rows)
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
        raise ValueError("canonical weather frame has an unexpected schema")


def _fixture_id(snapshot: CanonicalGenerationSnapshot) -> str:
    metadata = snapshot.frames.get("session_metadata")
    if metadata is None or metadata.height != 1 or "session_id" not in metadata.columns:
        raise ValueError("canonical session metadata must contain exactly one fixture row")
    fixture_id = metadata.item(0, "session_id")
    if not isinstance(fixture_id, str) or not fixture_id:
        raise ValueError("canonical fixture_id must be a non-empty string")
    return fixture_id


def _sanitize_row(row: Mapping[str, object], fixture_id: str) -> _SanitizedWeatherRow:
    if row.get("session_id") != fixture_id:
        raise ValueError("canonical weather row fixture_id disagrees with session metadata")
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
        raise TypeError(f"{label} must contain numeric values or null")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must contain finite values or null")
    return numeric


def _time_ms(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("session_time_ms must contain non-negative integer milliseconds")
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
        raise ValueError("wind_direction_deg must contain integer degrees from 0 through 359")
    return int(value)


def _rainfall(value: object) -> bool | None:
    if value is not None and type(value) is not bool:
        raise TypeError("rainfall must contain booleans or null")
    return cast(bool | None, value)


__all__ = ["WeatherSidecarBuilder", "build_weather_sidecar"]
