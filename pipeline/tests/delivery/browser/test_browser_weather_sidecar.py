"""Behavior-focused tests for BrowserWeatherSidecar derivation.

Covers:
  - build_weather_sidecar derivation (sparse native cadence, null preservation,
    missing/empty weather, fixture identity, zero-sentinel sanitization,
    determinism, no core-frame mutation)
  - BrowserWeatherSidecar model contract enforcement (alignment, ordering,
    ranges, nullability, finite values)
  - BrowserWeatherSidecarReference contract enforcement
  - WeatherSidecarBuilder facade determinism
"""

from __future__ import annotations

import polars as pl
import pytest

from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    MAX_INT64,
    WEATHER_SIDECAR_SCHEMA_ID,
    BrowserWeatherSidecar,
    BrowserWeatherSidecarReference,
    CanonicalGenerationSnapshot,
)
from f1_replay_pipeline.delivery.browser.browser_weather_sidecar import (
    WeatherSidecarBuilder,
    build_weather_sidecar,
)
from f1_replay_pipeline.domain.canonical_schema import WEATHER_SCHEMA


def _snapshot(
    fixture_id: str = "synthetic-race",
    weather_rows: list[dict[str, object]] | None = None,
    *,
    include_weather: bool = True,
) -> CanonicalGenerationSnapshot:
    """One deterministic snapshot with exactly one session-metadata row."""
    frames: dict[str, pl.DataFrame] = {
        "session_metadata": pl.DataFrame([{"session_id": fixture_id}]),
    }
    if include_weather:
        frames["weather"] = pl.DataFrame(
            weather_rows or [], schema=dict(WEATHER_SCHEMA), strict=True,
        )
    return CanonicalGenerationSnapshot("generation-one", "a" * 64, frames)


def _weather_row(session_time_ms: int, **overrides: object) -> dict[str, object]:
    """One canonical weather row with every measurement nullable by default."""
    row: dict[str, object] = {
        "session_id": "synthetic-race",
        "session_time_ms": session_time_ms,
        "air_temperature_c": None,
        "humidity_pct": None,
        "pressure_mbar": None,
        "rainfall": None,
        "track_temperature_c": None,
        "wind_direction_deg": None,
        "wind_speed_mps": None,
    }
    row.update(overrides)
    return row


def _weather_frame_with_dtypes(
    rows: list[dict[str, object]], dtypes: dict[str, pl.DataType],
) -> pl.DataFrame:
    """Build a weather frame with selected columns re-typed for dtype edges."""
    schema = dict(WEATHER_SCHEMA)
    schema.update(dtypes)
    return pl.DataFrame(rows, schema=schema, strict=True)


def _snapshot_with_weather_frame(frame: pl.DataFrame) -> CanonicalGenerationSnapshot:
    """Replace the default weather frame inside an otherwise identical snapshot."""
    frames = dict(_snapshot().frames)
    frames["weather"] = frame
    return CanonicalGenerationSnapshot("generation-one", "a" * 64, frames)


def _sidecar(
    *,
    fixture_id: str = "synthetic-race",
    time_ms: tuple[int, ...] = (0, 1_000),
    air_temp_c: tuple[float | None, ...] = (21.0, 22.0),
    humidity_pct: tuple[float | None, ...] = (50.0, None),
    pressure_mbar: tuple[float | None, ...] = (1013.0, 1012.0),
    rainfall: tuple[bool | None, ...] = (False, True),
    track_temp_c: tuple[float | None, ...] = (35.0, None),
    wind_direction_deg: tuple[int | None, ...] = (90, 0),
    wind_speed_mps: tuple[float | None, ...] = (2.5, 0.0),
) -> BrowserWeatherSidecar:
    """One valid sparse weather sidecar with corroborated zero samples."""
    return BrowserWeatherSidecar(
        fixture_id, time_ms, air_temp_c, humidity_pct, pressure_mbar,
        rainfall, track_temp_c, wind_direction_deg, wind_speed_mps,
    )


# ===========================================================================
# build_weather_sidecar derivation tests
# ===========================================================================


class TestBuildWeatherSidecar:
    """Behavior tests for the pure sidecar derivation function."""

    # -- Positive cases -----------------------------------------------------

    def test_build_returns_none_without_weather_frame(self) -> None:
        """✅ Positive: a snapshot without a weather frame yields no sidecar."""
        # Arrange
        snapshot = _snapshot(include_weather=False)

        # Act
        sidecar = build_weather_sidecar(snapshot)

        # Assert
        assert sidecar is None

    def test_build_returns_none_for_empty_weather_frame(self) -> None:
        """✅ Positive: an empty weather frame yields no sidecar."""
        # Arrange
        snapshot = _snapshot(weather_rows=[])

        # Act
        sidecar = build_weather_sidecar(snapshot)

        # Assert
        assert sidecar is None

    def test_build_preserves_sparse_native_cadence_without_filling(self) -> None:
        """✅ Positive: sparse canonical rows are retained at their native times."""
        # Arrange
        snapshot = _snapshot(weather_rows=[
            _weather_row(0, air_temperature_c=21.0, rainfall=False),
            _weather_row(60_000, air_temperature_c=22.0, rainfall=True),
            _weather_row(120_000, air_temperature_c=23.0, rainfall=False),
        ])

        # Act
        sidecar = build_weather_sidecar(snapshot)

        # Assert — exact native cadence, no interpolation or gap filling.
        assert sidecar is not None
        assert sidecar.time_ms == (0, 60_000, 120_000)
        assert sidecar.air_temp_c == (21.0, 22.0, 23.0)
        assert sidecar.rainfall == (False, True, False)

    def test_build_preserves_partial_nullable_observations(self) -> None:
        """✅ Positive: missing measurements stay null in the sidecar."""
        # Arrange
        snapshot = _snapshot(weather_rows=[
            _weather_row(0, air_temperature_c=21.0),
            _weather_row(60_000, humidity_pct=55.0, wind_speed_mps=2.5),
        ])

        # Act
        sidecar = build_weather_sidecar(snapshot)

        # Assert — nulls preserved per row, no invented values.
        assert sidecar is not None
        assert sidecar.air_temp_c == (21.0, None)
        assert sidecar.humidity_pct == (None, 55.0)
        assert sidecar.pressure_mbar == (None, None)
        assert sidecar.track_temp_c == (None, None)
        assert sidecar.wind_direction_deg == (None, None)
        assert sidecar.wind_speed_mps == (None, 2.5)

    def test_build_is_deterministic_for_identical_snapshots(self) -> None:
        """✅ Positive: the same snapshot always yields the same sidecar."""
        # Arrange
        snapshot = _snapshot(weather_rows=[
            _weather_row(0, air_temperature_c=21.0, humidity_pct=50.0, rainfall=False),
            _weather_row(1_000, air_temperature_c=22.0, wind_speed_mps=2.5),
        ])

        # Act
        first = build_weather_sidecar(snapshot)
        second = build_weather_sidecar(snapshot)

        # Assert
        assert first is not None and second is not None
        assert first.as_dict() == second.as_dict()

    def test_build_does_not_mutate_the_canonical_weather_frame(self) -> None:
        """✅ Positive: derivation reads the canonical frame without changing it."""
        # Arrange
        snapshot = _snapshot(weather_rows=[
            _weather_row(0, air_temperature_c=21.0, rainfall=False),
            _weather_row(1_000, humidity_pct=50.0),
        ])
        weather = snapshot.frames["weather"]
        before = weather.clone()

        # Act
        build_weather_sidecar(snapshot)

        # Assert — the canonical frame and snapshot mapping are untouched.
        assert weather.equals(before)
        assert tuple(snapshot.frames) == ("session_metadata", "weather")

    def test_build_uses_session_metadata_fixture_id(self) -> None:
        """✅ Positive: the sidecar inherits the canonical session_id."""
        # Arrange
        snapshot = _snapshot(fixture_id="monza-2026", weather_rows=[
            _weather_row(0, session_id="monza-2026", air_temperature_c=21.0),
        ])

        # Act
        sidecar = build_weather_sidecar(snapshot)

        # Assert
        assert sidecar is not None
        assert sidecar.fixture_id == "monza-2026"

    # -- ADR-003 zero-sentinel sanitization --------------------------------

    def test_build_sanitizes_zero_air_temp_to_null(self) -> None:
        """✅ Positive: a zero air temperature sentinel becomes null."""
        # Arrange
        snapshot = _snapshot(weather_rows=[
            _weather_row(0, air_temperature_c=0.0),
        ])

        # Act
        sidecar = build_weather_sidecar(snapshot)

        # Assert
        assert sidecar is not None
        assert sidecar.air_temp_c == (None,)

    def test_build_sanitizes_zero_pressure_to_null(self) -> None:
        """✅ Positive: a zero pressure sentinel becomes null."""
        # Arrange
        snapshot = _snapshot(weather_rows=[
            _weather_row(0, pressure_mbar=0.0),
        ])

        # Act
        sidecar = build_weather_sidecar(snapshot)

        # Assert
        assert sidecar is not None
        assert sidecar.pressure_mbar == (None,)

    def test_build_sanitizes_zero_track_temp_to_null(self) -> None:
        """✅ Positive: a zero track temperature sentinel becomes null."""
        # Arrange
        snapshot = _snapshot(weather_rows=[
            _weather_row(0, track_temperature_c=0.0),
        ])

        # Act
        sidecar = build_weather_sidecar(snapshot)

        # Assert
        assert sidecar is not None
        assert sidecar.track_temp_c == (None,)

    def test_build_keeps_zero_humidity_when_corroborated(self) -> None:
        """✅ Positive: a zero humidity sample survives when another value exists."""
        # Arrange
        snapshot = _snapshot(weather_rows=[
            _weather_row(0, humidity_pct=0.0, wind_speed_mps=2.5),
        ])

        # Act
        sidecar = build_weather_sidecar(snapshot)

        # Assert
        assert sidecar is not None
        assert sidecar.humidity_pct == (0.0,)

    def test_build_drops_zero_humidity_without_corroboration(self) -> None:
        """✅ Positive: an uncorroborated zero humidity sentinel becomes null."""
        # Arrange
        snapshot = _snapshot(weather_rows=[
            _weather_row(0, humidity_pct=0.0),
        ])

        # Act
        sidecar = build_weather_sidecar(snapshot)

        # Assert
        assert sidecar is not None
        assert sidecar.humidity_pct == (None,)

    def test_build_keeps_zero_wind_speed_when_corroborated(self) -> None:
        """✅ Positive: a zero wind speed survives when another value exists."""
        # Arrange
        snapshot = _snapshot(weather_rows=[
            _weather_row(0, wind_speed_mps=0.0, air_temperature_c=21.0),
        ])

        # Act
        sidecar = build_weather_sidecar(snapshot)

        # Assert
        assert sidecar is not None
        assert sidecar.wind_speed_mps == (0.0,)

    def test_build_drops_zero_wind_speed_without_corroboration(self) -> None:
        """✅ Positive: an uncorroborated zero wind speed sentinel becomes null."""
        # Arrange
        snapshot = _snapshot(weather_rows=[
            _weather_row(0, wind_speed_mps=0.0),
        ])

        # Act
        sidecar = build_weather_sidecar(snapshot)

        # Assert
        assert sidecar is not None
        assert sidecar.wind_speed_mps == (None,)

    def test_build_keeps_zero_wind_direction_when_corroborated(self) -> None:
        """✅ Positive: a zero wind direction survives as an integer degree."""
        # Arrange
        snapshot = _snapshot(weather_rows=[
            _weather_row(0, wind_direction_deg=0.0, air_temperature_c=21.0),
        ])

        # Act
        sidecar = build_weather_sidecar(snapshot)

        # Assert
        assert sidecar is not None
        assert sidecar.wind_direction_deg == (0,)

    def test_build_drops_zero_wind_direction_without_corroboration(self) -> None:
        """✅ Positive: an uncorroborated zero wind direction sentinel becomes null."""
        # Arrange
        snapshot = _snapshot(weather_rows=[
            _weather_row(0, wind_direction_deg=0.0),
        ])

        # Act
        sidecar = build_weather_sidecar(snapshot)

        # Assert
        assert sidecar is not None
        assert sidecar.wind_direction_deg == (None,)

    # -- Negative cases -----------------------------------------------------

    def test_build_rejects_non_numeric_measurement(self) -> None:
        """❌ Negative: a non-numeric measurement raises TypeError."""
        # Arrange — re-typed column keeps the canonical column names while
        # smuggling a non-numeric value past the name-only schema check.
        snapshot = _snapshot_with_weather_frame(_weather_frame_with_dtypes(
            [_weather_row(0, air_temperature_c="warm")],
            {"air_temperature_c": pl.String},
        ))

        # Act / Assert
        with pytest.raises(TypeError, match="numeric"):
            build_weather_sidecar(snapshot)

    def test_build_rejects_non_finite_measurement(self) -> None:
        """❌ Negative: a non-finite measurement raises ValueError."""
        # Arrange
        snapshot = _snapshot(weather_rows=[
            _weather_row(0, air_temperature_c=float("nan")),
        ])

        # Act / Assert
        with pytest.raises(ValueError, match="finite"):
            build_weather_sidecar(snapshot)

    def test_build_rejects_negative_session_time(self) -> None:
        """❌ Negative: a negative session time raises ValueError."""
        # Arrange
        snapshot = _snapshot(weather_rows=[
            _weather_row(-1, air_temperature_c=21.0),
        ])

        # Act / Assert
        with pytest.raises(ValueError, match="non-negative"):
            build_weather_sidecar(snapshot)

    def test_build_rejects_non_integer_session_time(self) -> None:
        """❌ Negative: a fractional session time raises ValueError."""
        # Arrange — re-typed Int64 time column to Float64 while keeping names.
        snapshot = _snapshot_with_weather_frame(_weather_frame_with_dtypes(
            [_weather_row(0, air_temperature_c=21.0)],
            {"session_time_ms": pl.Float64},
        ))

        # Act / Assert
        with pytest.raises(ValueError, match="non-negative integer"):
            build_weather_sidecar(snapshot)

    def test_build_rejects_non_boolean_rainfall(self) -> None:
        """❌ Negative: a non-boolean rainfall value raises TypeError."""
        # Arrange — re-typed Boolean rainfall column to Int64.
        snapshot = _snapshot_with_weather_frame(_weather_frame_with_dtypes(
            [_weather_row(0, rainfall=1)],
            {"rainfall": pl.Int64},
        ))

        # Act / Assert
        with pytest.raises(TypeError, match="booleans"):
            build_weather_sidecar(snapshot)

    def test_build_rejects_fractional_wind_direction(self) -> None:
        """❌ Negative: a fractional wind direction raises ValueError."""
        # Arrange
        snapshot = _snapshot(weather_rows=[
            _weather_row(0, wind_direction_deg=90.5, air_temperature_c=21.0),
        ])

        # Act / Assert
        with pytest.raises(ValueError, match="integer degrees"):
            build_weather_sidecar(snapshot)

    def test_build_rejects_wind_direction_out_of_range(self) -> None:
        """❌ Negative: wind direction outside 0..359 raises ValueError."""
        # Arrange
        snapshot = _snapshot(weather_rows=[
            _weather_row(0, wind_direction_deg=360.0, air_temperature_c=21.0),
        ])

        # Act / Assert
        with pytest.raises(ValueError, match="0 through 359"):
            build_weather_sidecar(snapshot)

    def test_build_rejects_unexpected_weather_schema(self) -> None:
        """❌ Negative: an unexpected canonical weather schema raises ValueError."""
        # Arrange
        snapshot = _snapshot_with_weather_frame(
            pl.DataFrame({"time_ms": [0], "value": [1.0]}, strict=True),
        )

        # Act / Assert
        with pytest.raises(ValueError, match="unexpected schema"):
            build_weather_sidecar(snapshot)

    def test_build_rejects_missing_session_metadata(self) -> None:
        """❌ Negative: a snapshot without session metadata raises ValueError."""
        # Arrange — weather exists but the fixture identity frame is absent.
        snapshot = _snapshot_with_weather_frame(
            pl.DataFrame([_weather_row(0, air_temperature_c=21.0)], schema=dict(WEATHER_SCHEMA), strict=True),
        )
        frames = dict(snapshot.frames)
        del frames["session_metadata"]
        snapshot = CanonicalGenerationSnapshot("generation-one", "a" * 64, frames)

        # Act / Assert
        with pytest.raises(ValueError, match="session metadata"):
            build_weather_sidecar(snapshot)

    def test_build_rejects_weather_fixture_disagreement(self) -> None:
        """❌ Negative: a weather row fixture_id disagreeing with metadata raises."""
        # Arrange
        snapshot = _snapshot(weather_rows=[
            _weather_row(0, session_id="other-race", air_temperature_c=21.0),
        ])

        # Act / Assert
        with pytest.raises(ValueError, match="fixture_id"):
            build_weather_sidecar(snapshot)

    def test_build_rejects_unsorted_canonical_weather_rows(self) -> None:
        """❌ Negative: unsorted canonical rows fail closed without reordering."""
        # Arrange — canonical validation requires ascending time; a descending
        # frame must not be silently reordered by the sidecar builder.
        snapshot = _snapshot(weather_rows=[
            _weather_row(1_000, air_temperature_c=22.0),
            _weather_row(0, air_temperature_c=21.0),
        ])

        # Act / Assert
        with pytest.raises(ValueError, match="strictly increasing"):
            build_weather_sidecar(snapshot)

    def test_build_rejects_non_snapshot_argument(self) -> None:
        """❌ Negative: a non-snapshot argument raises TypeError."""
        # Arrange / Act / Assert
        with pytest.raises(TypeError, match="CanonicalGenerationSnapshot"):
            build_weather_sidecar("not-a-snapshot")  # type: ignore[arg-type]


# ===========================================================================
# BrowserWeatherSidecar model tests
# ===========================================================================


class TestBrowserWeatherSidecarModel:
    """Positive and negative contract enforcement for the sidecar value."""

    def test_accepts_valid_sparse_arrays(self) -> None:
        """✅ Positive: a valid sparse sidecar preserves every field."""
        # Arrange & Act
        sidecar = _sidecar()

        # Assert
        assert sidecar.time_ms == (0, 1_000)
        assert sidecar.air_temp_c == (21.0, 22.0)
        assert sidecar.humidity_pct == (50.0, None)
        assert sidecar.pressure_mbar == (1013.0, 1012.0)
        assert sidecar.rainfall == (False, True)
        assert sidecar.wind_direction_deg == (90, 0)
        assert sidecar.wind_speed_mps == (2.5, 0.0)

    def test_accepts_max_int64_time(self) -> None:
        """✅ Positive: MAX_INT64 is an accepted timestamp boundary."""
        # Arrange & Act
        sidecar = _sidecar(
            time_ms=(MAX_INT64,), air_temp_c=(21.0,), humidity_pct=(50.0,),
            pressure_mbar=(1013.0,), rainfall=(False,), track_temp_c=(35.0,),
            wind_direction_deg=(90,), wind_speed_mps=(2.5,),
        )

        # Assert
        assert sidecar.time_ms == (MAX_INT64,)

    def test_as_dict_matches_contract_shape(self) -> None:
        """✅ Positive: as_dict emits the frozen contract field order."""
        # Arrange
        sidecar = _sidecar()

        # Act
        payload = sidecar.as_dict()

        # Assert
        assert tuple(payload) == (
            "contractVersion", "fixtureId", "timeMs", "airTempC", "humidityPct",
            "pressureMbar", "rainfall", "trackTempC", "windDirectionDeg",
            "windSpeedMps",
        )
        assert payload["contractVersion"] == "v1"
        assert payload["fixtureId"] == "synthetic-race"
        assert payload["timeMs"] == [0, 1_000]

    def test_rejects_misaligned_arrays(self) -> None:
        """❌ Negative: arrays of unequal length raise ValueError."""
        # Arrange / Act / Assert
        with pytest.raises(ValueError, match="equal length"):
            _sidecar(air_temp_c=(21.0,))

    def test_rejects_empty_arrays(self) -> None:
        """❌ Negative: an empty sidecar raises ValueError."""
        # Arrange / Act / Assert
        with pytest.raises(ValueError, match="non-empty"):
            _sidecar(time_ms=(), air_temp_c=(), humidity_pct=(), pressure_mbar=(),
                     rainfall=(), track_temp_c=(), wind_direction_deg=(),
                     wind_speed_mps=())

    def test_rejects_negative_time(self) -> None:
        """❌ Negative: a negative timestamp raises ValueError."""
        # Arrange / Act / Assert
        with pytest.raises(ValueError, match="weather time_ms"):
            _sidecar(
                time_ms=(-1,), air_temp_c=(21.0,), humidity_pct=(50.0,),
                pressure_mbar=(1013.0,), rainfall=(False,), track_temp_c=(35.0,),
                wind_direction_deg=(90,), wind_speed_mps=(2.5,),
            )

    def test_rejects_non_increasing_time(self) -> None:
        """❌ Negative: duplicate or descending timestamps raise ValueError."""
        # Arrange / Act / Assert
        with pytest.raises(ValueError, match="strictly increasing"):
            _sidecar(time_ms=(0, 0))
        with pytest.raises(ValueError, match="strictly increasing"):
            _sidecar(time_ms=(1_000, 0))

    def test_rejects_zero_or_negative_air_temp(self) -> None:
        """❌ Negative: air temperature must be strictly positive."""
        # Arrange / Act / Assert
        with pytest.raises(ValueError, match="air_temp_c"):
            _sidecar(air_temp_c=(0.0, 22.0))
        with pytest.raises(ValueError, match="air_temp_c"):
            _sidecar(air_temp_c=(-1.0, 22.0))

    def test_rejects_humidity_out_of_range(self) -> None:
        """❌ Negative: humidity must stay within 0..100."""
        # Arrange / Act / Assert
        with pytest.raises(ValueError, match="humidity_pct"):
            _sidecar(humidity_pct=(101.0, None))
        with pytest.raises(ValueError, match="humidity_pct"):
            _sidecar(humidity_pct=(-1.0, None))

    def test_rejects_zero_or_negative_pressure(self) -> None:
        """❌ Negative: pressure must be strictly positive."""
        # Arrange / Act / Assert
        with pytest.raises(ValueError, match="pressure_mbar"):
            _sidecar(pressure_mbar=(0.0, 1012.0))

    def test_rejects_non_boolean_rainfall(self) -> None:
        """❌ Negative: rainfall must be boolean or null."""
        # Arrange / Act / Assert
        with pytest.raises(TypeError, match="rainfall"):
            _sidecar(rainfall=(False, 1))  # type: ignore[arg-type]

    def test_rejects_zero_or_negative_track_temp(self) -> None:
        """❌ Negative: track temperature must be strictly positive."""
        # Arrange / Act / Assert
        with pytest.raises(ValueError, match="track_temp_c"):
            _sidecar(track_temp_c=(35.0, 0.0))

    def test_rejects_fractional_or_out_of_range_wind_direction(self) -> None:
        """❌ Negative: wind direction must be an integer from 0..359."""
        # Arrange / Act / Assert
        with pytest.raises(ValueError, match="wind_direction_deg"):
            _sidecar(wind_direction_deg=(12.5, 0))  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="wind_direction_deg"):
            _sidecar(wind_direction_deg=(360, 0))
        with pytest.raises(ValueError, match="wind_direction_deg"):
            _sidecar(wind_direction_deg=(-1, 0))

    def test_rejects_negative_wind_speed(self) -> None:
        """❌ Negative: wind speed must be non-negative."""
        # Arrange / Act / Assert
        with pytest.raises(ValueError, match="wind_speed_mps"):
            _sidecar(wind_speed_mps=(2.5, -1.0))

    def test_rejects_non_finite_value(self) -> None:
        """❌ Negative: a non-finite float measurement raises TypeError."""
        # Arrange / Act / Assert
        with pytest.raises(TypeError, match="finite"):
            _sidecar(humidity_pct=(float("nan"), None))
        with pytest.raises(TypeError, match="finite"):
            _sidecar(air_temp_c=(float("inf"), 22.0))

    def test_rejects_non_float_numeric_value(self) -> None:
        """❌ Negative: an integer (not float) measurement raises TypeError."""
        # Arrange / Act / Assert
        with pytest.raises(TypeError, match="air_temp_c"):
            _sidecar(air_temp_c=(21, 22))  # type: ignore[arg-type]

    def test_rejects_invalid_fixture_id_pattern(self) -> None:
        """❌ Negative: a fixture_id outside the canonical pattern raises."""
        # Arrange / Act / Assert
        with pytest.raises(ValueError, match="fixture_id"):
            _sidecar(fixture_id="Race One")
        with pytest.raises(ValueError, match="fixture_id"):
            _sidecar(fixture_id="")


# ===========================================================================
# BrowserWeatherSidecarReference tests
# ===========================================================================


class TestWeatherSidecarReference:
    """Positive and negative contract enforcement for the manifest reference."""

    def test_accepts_correct_path_and_schema(self) -> None:
        """✅ Positive: the exact weather path and schema id are accepted."""
        # Arrange & Act
        reference = BrowserWeatherSidecarReference(
            path="weather-sidecar.json",
            schema_id=WEATHER_SIDECAR_SCHEMA_ID,
            sha256="a" * 64,
        )

        # Assert
        assert reference.as_dict() == {
            "path": "weather-sidecar.json",
            "schemaId": WEATHER_SIDECAR_SCHEMA_ID,
            "sha256": "a" * 64,
        }

    def test_rejects_wrong_path(self) -> None:
        """❌ Negative: an unexpected path raises ValueError."""
        # Arrange / Act / Assert
        with pytest.raises(ValueError, match="path must be weather-sidecar.json"):
            BrowserWeatherSidecarReference(
                path="other.json", schema_id=WEATHER_SIDECAR_SCHEMA_ID, sha256="a" * 64,
            )

    def test_rejects_wrong_schema_id(self) -> None:
        """❌ Negative: an unexpected schema id raises ValueError."""
        # Arrange / Act / Assert
        with pytest.raises(ValueError, match="schema_id is invalid"):
            BrowserWeatherSidecarReference(
                path="weather-sidecar.json",
                schema_id="urn:f1-cache-replay:schema:replay-data:v1:wrong",
                sha256="a" * 64,
            )

    def test_rejects_invalid_sha256(self) -> None:
        """❌ Negative: a non-hex digest raises ValueError."""
        # Arrange / Act / Assert
        with pytest.raises(ValueError, match="sha256"):
            BrowserWeatherSidecarReference(
                path="weather-sidecar.json", schema_id=WEATHER_SIDECAR_SCHEMA_ID,
                sha256="not-a-sha",
            )


# ===========================================================================
# WeatherSidecarBuilder facade tests
# ===========================================================================


class TestWeatherSidecarBuilderFacade:
    """Behavior tests for the stateless callable facade."""

    def test_callable_delegates_to_build_function(self) -> None:
        """✅ Positive: the facade produces the same sidecar as the function."""
        # Arrange
        snapshot = _snapshot(weather_rows=[
            _weather_row(0, air_temperature_c=21.0, rainfall=False),
            _weather_row(1_000, air_temperature_c=22.0, rainfall=True),
        ])
        builder = WeatherSidecarBuilder()

        # Act
        through_callable = builder(snapshot)
        through_static = WeatherSidecarBuilder.build(snapshot)
        through_function = build_weather_sidecar(snapshot)

        # Assert
        assert through_callable is not None
        assert through_static is not None
        assert through_function is not None
        assert through_callable.as_dict() == through_static.as_dict()
        assert through_static.as_dict() == through_function.as_dict()

    def test_callable_returns_none_for_missing_weather(self) -> None:
        """✅ Positive: the facade mirrors None for a snapshot without weather."""
        # Arrange
        snapshot = _snapshot(include_weather=False)

        # Act
        result = WeatherSidecarBuilder()(snapshot)

        # Assert
        assert result is None

    def test_builder_outputs_are_deterministic(self) -> None:
        """✅ Positive: repeated facade derivation is byte-stable via as_dict."""
        # Arrange
        snapshot = _snapshot(weather_rows=[
            _weather_row(0, air_temperature_c=21.0, rainfall=False),
            _weather_row(1_000, wind_speed_mps=2.5, rainfall=True),
        ])
        builder = WeatherSidecarBuilder()

        # Act
        first = builder(snapshot)
        second = builder(snapshot)

        # Assert — identical contract payloads, in the frozen field order.
        assert first is not None and second is not None
        assert first.as_dict() == second.as_dict()
        assert tuple(first.as_dict()) == tuple(second.as_dict())
