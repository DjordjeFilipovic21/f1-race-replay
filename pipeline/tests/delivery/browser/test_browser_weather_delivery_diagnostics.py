"""Focused delivery diagnostics for weather at the browser build boundary.

Builds the full browser delivery in memory from V2 canonical frames and
asserts the subtask-04 diagnostic boundary is consumed at the
``build_weather_sidecar`` call site:

- valid canonical weather still yields an unchanged sidecar-bearing build,
- absent (missing frame) weather degrades to ``weather_sidecar=None`` while
  the delivery build stays valid,
- corrupt/invalid weather fails the build closed with a classified
  ``BrowserDeliveryBuildError`` whose cause identifies the weather boundary
  and is not double-wrapped by the outer ``ValueError`` guard,
- incomplete/V1-shaped session metadata that omits ``session_mode`` fails
  the build closed instead of being labeled a V2 race delivery,
- publishing a degraded build omits the weather artifact and manifest
  reference.

Only V2 canonical identities are used; there is no V1 fixture or
mixed-version payload.  Shared mutable resource: pytest ``tmp_path`` only.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import cast

import polars as pl
import pytest

from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    CanonicalGenerationSnapshot,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_orchestration import (
    BrowserDeliveryBuildError,
    build_browser_delivery,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_publication import (
    publish_browser_delivery,
)
from f1_replay_pipeline.delivery.browser.browser_weather_sidecar import (
    WeatherSidecarClassification,
    WeatherSidecarCorruptionError,
)
from f1_replay_pipeline.domain.canonical_schema import CANONICAL_TABLE_SCHEMAS
from f1_replay_pipeline.domain.normalizers import NormalizationError
from f1_replay_pipeline.storage.parquet_io import CANONICAL_PARQUET_TABLE_NAMES


REPO_ROOT = Path(__file__).resolve().parents[4]
CONTRACT_ROOT_V2 = REPO_ROOT / "contracts" / "replay-data" / "v2"
FIXTURE_ROOT = CONTRACT_ROOT_V2 / "fixtures" / "deterministic-race"
SCHEMA_ROOT = CONTRACT_ROOT_V2 / "schemas"


def _row(table: str, **changes: object) -> dict[str, object]:
    """One canonical row whose keys stay within the table's V2 schema."""
    row: dict[str, object] = {column: None for column in CANONICAL_TABLE_SCHEMAS[table]}
    if "session_id" in row:
        row["session_id"] = "synthetic-race"
    if "driver_id" in row:
        row["driver_id"] = "HAM"
    row.update({
        "session_metadata": {
            "year": 2026, "round_number": 1, "event_name": "Synthetic Grand Prix",
            "session_name": "Race", "session_type": "R", "session_mode": "race",
            "session_start_time_utc": datetime(2026, 1, 1, tzinfo=timezone.utc),
        },
        "drivers": {
            "source_driver_key": "44", "driver_number": 44,
            "full_name": "Lewis Hamilton", "team_name": "Mercedes",
            "team_colour": "00D2BE",
        },
        "car_telemetry": {"source_driver_key": "44", "session_time_ms": 0, "source": "car"},
        "position_telemetry": {"source_driver_key": "44", "session_time_ms": 0, "source": "pos"},
        "laps": {
            "lap_number": 1, "lap_start_time_ms": 0, "lap_end_time_ms": 20_001,
            "compound": "MEDIUM",
        },
        "stints": {"stint_number": 1, "start_lap_number": 1},
        "weather": {"session_time_ms": 0},
        "track_status_intervals": {"start_time_ms": 0, "status": "1"},
        "race_control_messages": {
            "session_time_ms": 10_000, "message_index": 0,
            "message": "boundary event", "driver_id": "HAM",
        },
        "results": {"classified_position": "1"},
    }[table])
    row.update(changes)
    return row


def _frames(
    *,
    weather_rows: list[dict[str, object]] | None = None,
    include_weather: bool = True,
    session_mode: object = "race",
) -> dict[str, pl.DataFrame]:
    """One deterministic in-memory V2 canonical generation."""
    rows = {name: [_row(name)] for name in CANONICAL_PARQUET_TABLE_NAMES}
    rows["session_metadata"] = [_row("session_metadata", session_mode=session_mode)]
    rows["car_telemetry"] = [
        _row(
            "car_telemetry", session_time_ms=time, speed_kph=speed,
            throttle_pct=throttle, brake=brake, gear=gear, drs=drs,
        )
        for time, speed, throttle, brake, gear, drs in (
            (0, 100.0, 50.0, False, 4, 0),
            (10_000, 200.0, 70.0, True, 6, 1),
            (11_000, 210.0, 75.0, False, 7, 1),
            (20_000, 220.0, 80.0, None, None, None),
        )
    ]
    rows["position_telemetry"] = [
        _row("position_telemetry", session_time_ms=time, x=x, y=y, status=status)
        for time, x, y, status in (
            (0, 0.0, 1.0, "OnTrack"),
            (9_500, None, 9.5, None),
            (10_000, 10.0, 11.0, "OnTrack"),
            (19_999, 20.0, None, "OnTrack"),
        )
    ]
    if include_weather:
        rows["weather"] = weather_rows if weather_rows is not None else [
            _row("weather", session_time_ms=0, air_temperature_c=21.0, rainfall=False),
            _row("weather", session_time_ms=6_000, air_temperature_c=22.5, rainfall=True),
        ]
    else:
        del rows["weather"]
    rows["track_status_intervals"] = [
        _row("track_status_intervals", start_time_ms=0, end_time_ms=5_000, status="1"),
        _row("track_status_intervals", start_time_ms=5_000, end_time_ms=None, status="4"),
    ]
    rows["laps"] = [
        _row(
            "laps", driver_id="HAM", lap_number=1, lap_start_time_ms=0,
            lap_end_time_ms=20_001, compound="MEDIUM",
        )
    ]
    rows["drivers"].sort(key=lambda row: cast(str, row["driver_id"]))
    rows["results"].sort(key=lambda row: cast(str, row["driver_id"]))
    rows["car_telemetry"].sort(
        key=lambda row: (cast(str, row["driver_id"]), cast(int, row["session_time_ms"])),
    )
    rows["position_telemetry"].sort(
        key=lambda row: (cast(str, row["driver_id"]), cast(int, row["session_time_ms"])),
    )
    rows["laps"].sort(
        key=lambda row: (cast(str, row["driver_id"]), cast(int, row["lap_number"])),
    )
    return {
        name: pl.DataFrame(value, schema=dict(CANONICAL_TABLE_SCHEMAS[name]), strict=True)
        for name, value in rows.items()
    }


def _snapshot(
    *,
    weather_rows: list[dict[str, object]] | None = None,
    include_weather: bool = True,
    session_mode: object = "race",
) -> CanonicalGenerationSnapshot:
    """One immutable snapshot over the deterministic V2 generation."""
    return CanonicalGenerationSnapshot(
        "canonical-one", "a" * 64, _frames(
            weather_rows=weather_rows, include_weather=include_weather,
            session_mode=session_mode,
        ),
    )


def _track_assets() -> dict[str, object]:
    assets = _load_json(FIXTURE_ROOT / "track-assets.json")
    assets["contractVersion"] = "v2"
    assets["fixtureId"] = "synthetic-race"
    return assets


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


class TestBrowserDeliveryWeatherDiagnostics:
    """Weather diagnostics consumed at the browser delivery build boundary."""

    def test_valid_weather_builds_sidecar_unchanged(self) -> None:
        # Arrange — one valid V2 generation with native-cadence weather rows.
        snapshot = _snapshot()
        source_weather = snapshot.frames["weather"]

        # Act
        delivery = build_browser_delivery(snapshot, _track_assets())

        # Assert — the sidecar carries the canonical rows and the frame is untouched.
        assert delivery.weather_sidecar is not None
        assert delivery.weather_sidecar.fixture_id == "synthetic-race"
        assert delivery.weather_sidecar.time_ms == (0, 6_000)
        assert delivery.weather_sidecar.air_temp_c == (21.0, 22.5)
        assert delivery.weather_sidecar.rainfall == (False, True)
        assert snapshot.frames["weather"].equals(source_weather)

    def test_absent_weather_frame_degrades_to_no_sidecar(self) -> None:
        # Arrange — a valid generation whose weather frame is missing entirely.
        snapshot = _snapshot(include_weather=False)

        # Act
        delivery = build_browser_delivery(snapshot, _track_assets())

        # Assert — absence is the None contract and the delivery stays valid.
        assert delivery.weather_sidecar is None
        assert delivery.manifest.contract_version == "v2"

    def test_corrupt_weather_fails_closed_with_classification_not_double_wrapped(
        self,
    ) -> None:
        # Arrange — canonical weather rows that violate strict time ordering.
        snapshot = _snapshot(weather_rows=[
            _row("weather", session_time_ms=6_000, air_temperature_c=22.5),
            _row("weather", session_time_ms=0, air_temperature_c=21.0),
        ])

        # Act / Assert — the build fails closed through the weather boundary...
        with pytest.raises(BrowserDeliveryBuildError) as exc_info:
            build_browser_delivery(snapshot, _track_assets())
        assert exc_info.value.classification == (
            WeatherSidecarClassification.UNSORTED_ROWS
        )
        assert "weather sidecar" in str(exc_info.value)
        # ...and the classified error is not double-wrapped by the outer
        # ValueError guard: the cause chain identifies the weather boundary.
        assert isinstance(exc_info.value.__cause__, WeatherSidecarCorruptionError)

    def test_corrupt_weather_fixture_disagreement_classifies_corruption(self) -> None:
        # Arrange — canonical weather rows bound to a different fixture id.
        snapshot = _snapshot(weather_rows=[
            _row(
                "weather", session_time_ms=0, session_id="other-race",
                air_temperature_c=21.0,
            ),
            _row("weather", session_time_ms=6_000, air_temperature_c=22.5),
        ])

        # Act / Assert — corruption stays diagnosable through the build wrap.
        with pytest.raises(BrowserDeliveryBuildError) as exc_info:
            build_browser_delivery(snapshot, _track_assets())
        assert exc_info.value.classification == (
            WeatherSidecarClassification.FIXTURE_ID_DISAGREEMENT
        )

    def test_missing_session_mode_metadata_fails_closed(self) -> None:
        # Arrange — a V2-shaped generation whose session metadata omits the
        # canonical session_mode (incomplete/V1-shaped metadata).
        snapshot = _snapshot(session_mode=None)

        # Act / Assert — the boundary refuses to emit a V2-labeled delivery
        # from incomplete metadata; the session-mode normalization is the
        # cause and is not double-wrapped by the outer ValueError guard.
        with pytest.raises(BrowserDeliveryBuildError) as exc_info:
            build_browser_delivery(snapshot, _track_assets())
        assert "session mode" in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, NormalizationError)

    def test_publication_omits_weather_artifact_when_build_degraded(
        self, tmp_path: Path,
    ) -> None:
        # Arrange — a delivery built from a generation without a weather frame.
        delivery = build_browser_delivery(
            _snapshot(include_weather=False), _track_assets(),
        )
        assert delivery.weather_sidecar is None

        # Act — publish the degraded delivery end to end.
        result = publish_browser_delivery(
            browser_parent=tmp_path / "browser", delivery_version="delivery-v1",
            delivery=delivery, schema_root=SCHEMA_ROOT, contract_version="v2",
        )
        manifest = _load_json(result.manifest_path)

        # Assert — no weather artifact is written, digested, or referenced.
        assert result.weather_sidecar_path is None
        assert "weather-sidecar.json" not in result.artifact_digests
        assert "weatherSidecar" not in manifest
