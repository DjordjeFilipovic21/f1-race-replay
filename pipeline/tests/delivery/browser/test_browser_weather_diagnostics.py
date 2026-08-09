"""Focused diagnostics: absence vs corruption at the weather derivation boundary.

Distinguishes the optional degradation contract (no frame / correctly shaped
empty frame -> ``None``) from fail-closed corruption, which raises a classified
``WeatherSidecarCorruptionError`` carrying a ``WeatherSidecarClassification``.
Uses only V2 canonical identities; no V1 fixtures or mixed-version payloads.
"""

from __future__ import annotations

import polars as pl
import pytest

from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    MAX_INT64,
    CanonicalGenerationSnapshot,
)
from f1_replay_pipeline.delivery.browser.browser_weather_sidecar import (
    WeatherSidecarClassification,
    WeatherSidecarCorruptionError,
    WeatherSidecarCorruptionTypeError,
    _numeric,
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


def _snapshot_with_weather_frame(frame: pl.DataFrame) -> CanonicalGenerationSnapshot:
    """Replace the default weather frame inside an otherwise identical snapshot."""
    frames = dict(_snapshot().frames)
    frames["weather"] = frame
    return CanonicalGenerationSnapshot("generation-one", "a" * 64, frames)


def _weather_frame_with_dtypes(
    rows: list[dict[str, object]], dtypes: dict[str, type[pl.DataType]],
) -> pl.DataFrame:
    """Build a weather frame with selected columns re-typed for dtype edges."""
    schema = dict(WEATHER_SCHEMA)
    # Callers pass dtype classes (pl.String, pl.Float64, ...); instantiate them
    # so the merged schema stays uniformly typed for pl.DataFrame's schema arg.
    schema.update({name: dtype() for name, dtype in dtypes.items()})
    return pl.DataFrame(rows, schema=schema, strict=True)


class TestWeatherAbsenceClassification:
    """Optional degradation stays None and is distinguishable from corruption."""

    def test_no_weather_frame_is_absent_not_corruption(self) -> None:
        """✅ Positive: a snapshot without a weather frame yields None."""
        # Arrange
        snapshot = _snapshot(include_weather=False)

        # Act
        sidecar = build_weather_sidecar(snapshot)

        # Assert — absence is the None contract, never a classified raise.
        assert sidecar is None

    def test_empty_weather_frame_is_absent_not_corruption(self) -> None:
        """✅ Positive: an empty weather frame yields None."""
        # Arrange
        snapshot = _snapshot(weather_rows=[])

        # Act
        sidecar = build_weather_sidecar(snapshot)

        # Assert
        assert sidecar is None

    def test_malformed_empty_weather_frame_is_corruption_not_absent(self) -> None:
        """❌ Negative: an empty frame with an unexpected schema is corruption.

        Only correctly shaped empty optional weather degrades to ``None``; an
        empty frame that fails schema validation must fail closed instead of
        masquerading as absence.
        """
        # Arrange — an empty frame whose columns are not the canonical weather
        # schema, so it is malformed rather than correctly shaped.
        snapshot = _snapshot_with_weather_frame(
            pl.DataFrame({"time_ms": [], "value": []}, strict=True),
        )

        # Act / Assert
        with pytest.raises(WeatherSidecarCorruptionError) as exc_info:
            build_weather_sidecar(snapshot)
        assert exc_info.value.classification == (
            WeatherSidecarClassification.UNEXPECTED_SCHEMA
        )

    def test_absent_classification_member_documents_optional_degradation(self) -> None:
        """✅ Positive: the classification space covers absence as well as corruption."""
        # Assert — ABSENT is first-class even though absence surfaces as None.
        assert WeatherSidecarClassification.ABSENT.value == "absent"


class TestWeatherCorruptionClassification:
    """Fail-closed corruption raises classified, diagnosable errors."""

    def test_schema_mismatch_classifies_unexpected_schema(self) -> None:
        """❌ Negative: an unexpected weather schema is classified corruption."""
        # Arrange
        snapshot = _snapshot_with_weather_frame(
            pl.DataFrame({"time_ms": [0], "value": [1.0]}, strict=True),
        )

        # Act / Assert
        with pytest.raises(WeatherSidecarCorruptionError) as exc_info:
            build_weather_sidecar(snapshot)
        assert exc_info.value.classification == (
            WeatherSidecarClassification.UNEXPECTED_SCHEMA
        )

    def test_non_numeric_measurement_classifies_non_numeric(self) -> None:
        """❌ Negative: a non-numeric measurement is classified corruption."""
        # Arrange — re-typed column keeps canonical names while smuggling text.
        snapshot = _snapshot_with_weather_frame(_weather_frame_with_dtypes(
            [_weather_row(0, air_temperature_c="warm")],
            {"air_temperature_c": pl.String},
        ))

        # Act / Assert — the type-variant error remains classified corruption.
        with pytest.raises(WeatherSidecarCorruptionTypeError) as exc_info:
            build_weather_sidecar(snapshot)
        assert exc_info.value.classification == (
            WeatherSidecarClassification.NON_NUMERIC_MEASUREMENT
        )

    def test_non_finite_measurement_classifies_non_finite(self) -> None:
        """❌ Negative: a non-finite measurement is classified corruption."""
        # Arrange
        snapshot = _snapshot(weather_rows=[
            _weather_row(0, air_temperature_c=float("nan")),
        ])

        # Act / Assert
        with pytest.raises(WeatherSidecarCorruptionError) as exc_info:
            build_weather_sidecar(snapshot)
        assert exc_info.value.classification == (
            WeatherSidecarClassification.NON_FINITE_MEASUREMENT
        )

    def test_oversized_measurement_classifies_non_finite(self) -> None:
        """❌ Negative: an oversized numeric measurement is classified corruption.

        A value too large to convert to a finite Float64 must fail closed with
        the non-finite classification instead of leaking an unclassified
        overflow error from the numeric conversion.
        """
        # Arrange — exercise the numeric conversion boundary directly. Polars
        # rejects oversized ints before the weather code sees them, so no
        # snapshot round-trip can carry 10**400; the _numeric branch under
        # test is the deterministic conversion boundary.
        oversized_value = 10**400

        # Act / Assert
        with pytest.raises(WeatherSidecarCorruptionError) as exc_info:
            _numeric(oversized_value, "air_temperature_c")
        assert exc_info.value.classification == (
            WeatherSidecarClassification.NON_FINITE_MEASUREMENT
        )

    def test_negative_time_classifies_invalid_time(self) -> None:
        """❌ Negative: a negative session time is classified corruption."""
        # Arrange
        snapshot = _snapshot(weather_rows=[
            _weather_row(-1, air_temperature_c=21.0),
        ])

        # Act / Assert
        with pytest.raises(WeatherSidecarCorruptionError) as exc_info:
            build_weather_sidecar(snapshot)
        assert exc_info.value.classification == (
            WeatherSidecarClassification.INVALID_TIME
        )

    def test_fractional_time_classifies_invalid_time(self) -> None:
        """❌ Negative: a fractional session time is classified corruption."""
        # Arrange — re-typed Int64 time column to Float64 while keeping names.
        snapshot = _snapshot_with_weather_frame(_weather_frame_with_dtypes(
            [_weather_row(0, air_temperature_c=21.0)],
            {"session_time_ms": pl.Float64},
        ))

        # Act / Assert
        with pytest.raises(WeatherSidecarCorruptionError) as exc_info:
            build_weather_sidecar(snapshot)
        assert exc_info.value.classification == (
            WeatherSidecarClassification.INVALID_TIME
        )

    def test_over_bound_time_classifies_invalid_time(self) -> None:
        """❌ Negative: a time beyond signed Int64 is classified corruption.

        The model rejects over-bound timestamps at construction; the builder
        must surface that model-boundary failure as classified corruption
        instead of leaking a raw model error.
        """
        # Arrange — an Object column carries an int beyond Int64 (and beyond
        # the Float64 precision of a re-typed column) deterministically.
        snapshot = _snapshot_with_weather_frame(_weather_frame_with_dtypes(
            [_weather_row(MAX_INT64 + 1, air_temperature_c=21.0)],
            {"session_time_ms": pl.Object},
        ))

        # Act / Assert
        with pytest.raises(WeatherSidecarCorruptionError) as exc_info:
            build_weather_sidecar(snapshot)
        assert exc_info.value.classification == (
            WeatherSidecarClassification.INVALID_TIME
        )

    def test_unsorted_rows_classify_unsorted(self) -> None:
        """❌ Negative: unsorted canonical rows are classified corruption."""
        # Arrange — canonical validation requires ascending time; the sidecar
        # builder must fail closed instead of silently reordering.
        snapshot = _snapshot(weather_rows=[
            _weather_row(1_000, air_temperature_c=22.0),
            _weather_row(0, air_temperature_c=21.0),
        ])

        # Act / Assert
        with pytest.raises(WeatherSidecarCorruptionError) as exc_info:
            build_weather_sidecar(snapshot)
        assert exc_info.value.classification == (
            WeatherSidecarClassification.UNSORTED_ROWS
        )

    def test_fixture_disagreement_classifies_fixture_id(self) -> None:
        """❌ Negative: fixture-id disagreement is classified corruption."""
        # Arrange
        snapshot = _snapshot(weather_rows=[
            _weather_row(0, session_id="other-race", air_temperature_c=21.0),
        ])

        # Act / Assert
        with pytest.raises(WeatherSidecarCorruptionError) as exc_info:
            build_weather_sidecar(snapshot)
        assert exc_info.value.classification == (
            WeatherSidecarClassification.FIXTURE_ID_DISAGREEMENT
        )

    def test_missing_fixture_identity_classifies_missing_metadata(self) -> None:
        """❌ Negative: missing session metadata is classified corruption."""
        # Arrange — weather exists but the fixture identity frame is absent.
        snapshot = _snapshot_with_weather_frame(
            pl.DataFrame(
                [_weather_row(0, air_temperature_c=21.0)],
                schema=dict(WEATHER_SCHEMA), strict=True,
            ),
        )
        frames = dict(snapshot.frames)
        del frames["session_metadata"]
        snapshot = CanonicalGenerationSnapshot("generation-one", "a" * 64, frames)

        # Act / Assert
        with pytest.raises(WeatherSidecarCorruptionError) as exc_info:
            build_weather_sidecar(snapshot)
        assert exc_info.value.classification == (
            WeatherSidecarClassification.MISSING_FIXTURE_IDENTITY
        )

    def test_invalid_fixture_id_pattern_classifies_invalid_fixture_id(self) -> None:
        """❌ Negative: a fixture id outside the canonical pattern is corruption.

        The model rejects pattern violations at construction; the builder must
        surface that model-boundary failure as a precise classified corruption
        instead of a raw ValueError from the sidecar model.
        """
        # Arrange — metadata and weather rows agree on a non-canonical id, so
        # the only defect is the id pattern itself.
        snapshot = _snapshot(fixture_id="synthetic_race", weather_rows=[
            _weather_row(0, session_id="synthetic_race", air_temperature_c=21.0),
        ])

        # Act / Assert
        with pytest.raises(WeatherSidecarCorruptionError) as exc_info:
            build_weather_sidecar(snapshot)
        assert exc_info.value.classification == (
            WeatherSidecarClassification.INVALID_FIXTURE_ID
        )

    def test_invalid_range_classifies_invalid_range(self) -> None:
        """❌ Negative: an out-of-range measurement is classified corruption."""
        # Arrange
        snapshot = _snapshot(weather_rows=[
            _weather_row(0, humidity_pct=150.0, air_temperature_c=21.0),
        ])

        # Act / Assert
        with pytest.raises(WeatherSidecarCorruptionError) as exc_info:
            build_weather_sidecar(snapshot)
        assert exc_info.value.classification == (
            WeatherSidecarClassification.INVALID_RANGE
        )

    def test_invalid_rainfall_classifies_invalid_rainfall(self) -> None:
        """❌ Negative: a non-boolean rainfall value is classified corruption."""
        # Arrange — re-typed Boolean rainfall column to Int64.
        snapshot = _snapshot_with_weather_frame(_weather_frame_with_dtypes(
            [_weather_row(0, rainfall=1)],
            {"rainfall": pl.Int64},
        ))

        # Act / Assert — the type-variant error remains classified corruption.
        with pytest.raises(WeatherSidecarCorruptionTypeError) as exc_info:
            build_weather_sidecar(snapshot)
        assert exc_info.value.classification == (
            WeatherSidecarClassification.INVALID_RAINFALL
        )


class TestCorruptionErrorCompatibility:
    """Fail-closed compatibility: classified errors stay ValueError/TypeError."""

    def test_corruption_error_remains_value_error(self) -> None:
        """✅ Positive: existing ValueError callers keep catching corruption."""
        # Assert — both variants remain catchable as ValueError.
        assert issubclass(WeatherSidecarCorruptionError, ValueError)
        assert issubclass(WeatherSidecarCorruptionTypeError, ValueError)

    def test_type_corruption_error_remains_type_error(self) -> None:
        """✅ Positive: existing TypeError callers keep catching type corruption."""
        # Assert
        assert issubclass(WeatherSidecarCorruptionTypeError, TypeError)
