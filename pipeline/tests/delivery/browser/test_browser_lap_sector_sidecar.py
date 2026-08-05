"""Behavior-focused tests for BrowserLapSectorSidecar derivation and publication.

Covers:
  - BrowserDriverLapSector model validation
  - BrowserLapSectorSidecar model validation
  - BrowserLapSectorSidecarReference contract enforcement
  - build_lap_sector_sidecar derivation (determinism, null preservation,
    ordering, zero-lap drivers, causal pairing)
     - Publication (artifact writing, SHA-256, schema validation,
     mutation rejection, cross-checks)
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import polars as pl
import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

from f1_replay_pipeline.adapters.fastf1.messages_results import (
    QualifyingIncidentEvidenceError,
    parse_qualifying_incident_markers,
)
from f1_replay_pipeline.delivery.browser.browser_chunk_builder import (
    BrowserChunk,
    BrowserEvent,
    BrowserOverlap,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    BROWSER_LAP_SECTOR_SIDECAR_SCHEMA_ID,
    BrowserDriverFields,
    BrowserDriverLapSector,
    BrowserLapSectorSidecar,
    BrowserLapSectorSidecarReference,
    BrowserManifest,
    BrowserQualifyingIncidentMarker,
    BrowserQualifyingPhaseBoundary,
    BrowserQualifyingTimeline,
    BrowserQualifyingTimelineInterval,
    CanonicalGenerationSnapshot,
    MAX_INT64,
)
from f1_replay_pipeline.delivery.browser.browser_lap_sector_sidecar import (
    build_lap_sector_sidecar,
    build_qualifying_timeline,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_orchestration import (
    BrowserDeliveryBuild,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_publication import (
    BrowserDeliveryPublicationError,
    PublishedBrowserDelivery,
    _artifact_payloads,
    publish_browser_delivery,
    validate_complete_browser_delivery,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_reader import read_validated_canonical_generation
from f1_replay_pipeline.domain.canonical_schema import CANONICAL_TABLE_SCHEMAS_V2
from f1_replay_pipeline.domain.session_modes import SessionMode
from f1_replay_pipeline.storage.canonical_writer import publish_canonical_generation
from f1_replay_pipeline.storage.parquet_io import CANONICAL_PARQUET_TABLE_NAMES


REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMA_ROOT = REPO_ROOT / "contracts" / "replay-data" / "v2" / "schemas"
SCHEMA_ROOT_V2 = SCHEMA_ROOT

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _snapshot() -> CanonicalGenerationSnapshot:
    """Minimal v2 snapshot with the required race session metadata."""
    session_metadata = pl.DataFrame([{
        "session_id": "test-race", "year": 2026, "round_number": 1,
        "event_name": "Race", "session_name": "Race", "session_type": "R",
        "session_mode": "race", "session_start_time_utc": None,
    }], schema=dict(CANONICAL_TABLE_SCHEMAS_V2["session_metadata"]), strict=True)
    return CanonicalGenerationSnapshot(
        "test-gen", "a" * 64, {"session_metadata": session_metadata},
    )


def _chunk() -> BrowserChunk:
    """One minimal authoritative chunk for publication tests."""
    fields = BrowserDriverFields(
        "HAM", (0, 1000),
        (1.0, 2.0), (3.0, 4.0), (5.0, 6.0), (7.0, 8.0),
        (0, 1), (None, 7), (None, 1),
        ("OnTrack", "OnTrack"), (1, 1), ("SOFT", "SOFT"),
        (False, False), (None, None), (None, None), (None, None),
    )
    return BrowserChunk(
        "chunk-001", 1, 0, 2000,
        BrowserOverlap("none", None, None, None, None),
        (0, 1000), 0, {"HAM": fields},
        (("HAM",), ("HAM",)), (1, 1), ("clear", "clear"),
        (BrowserEvent(1000, "notice", "green flag"),),
    )


def _track_assets() -> dict[str, object]:
    """Minimal valid track assets."""
    point: dict[str, object] = {"x": 0.0, "y": 0.0}
    polyline = (
        point, {"x": 1.0, "y": 0.0}, {"x": 1.0, "y": 1.0}, {"x": 0.0, "y": 1.0},
    )
    return {
        "contractVersion": "v2", "fixtureId": "test-race",
        "trackId": "track-one", "trackName": "Track One",
        "coordinateSpace": {"units": "meters", "origin": "test"},
        "circuitLengthMeters": 1.0, "rotationDegrees": 0.0,
        "startFinish": {"center": point, "inner": point, "outer": point},
        "centerLine": polyline, "innerBoundary": polyline, "outerBoundary": polyline,
    }


def _delivery(
    *,
    session_mode: SessionMode = "race",
    sidecar: BrowserLapSectorSidecar | None = None,
    sidecar_drivers: tuple[Mapping[str, object], ...] | None = None,
) -> BrowserDeliveryBuild:
    """Construct a minimal BrowserDeliveryBuild with optional sidecar.

    When ``sidecar_drivers`` is given, those drivers replace the default
    ``HAM`` manifest entry so that sidecar <-> manifest driver checks pass.
    """
    drivers = sidecar_drivers or ({
        "id": "HAM", "displayName": "Hamilton", "teamName": "Team",
        "colorHex": "#000000", "carNumber": "44",
    },)
    manifest = BrowserManifest(
        "test-race", "Test Race", drivers, session_mode=session_mode,
    )
    return BrowserDeliveryBuild(
        _snapshot(), manifest, _track_assets(), (_chunk(),),
        lap_sector_sidecar=sidecar,
    )


def _sidecar(*, fixture_id: str = "test-race", drivers: object = None) -> BrowserLapSectorSidecar:
    """Build a minimal sidecar with one driver and one completed lap."""
    if drivers is None:
        drivers = {
            "HAM": BrowserDriverLapSector(
                lap_number=(1,),
                lap_start_ms=(0,),
                lap_end_ms=(100_000,),
                lap_duration_ms=(100_000,),
                sector_1_duration_ms=(30_000,),
                sector_2_duration_ms=(30_000,),
                sector_3_duration_ms=(40_000,),
                sector_1_session_time_ms=(30_000,),
                sector_2_session_time_ms=(60_000,),
                sector_3_session_time_ms=(100_000,),
            ),
        }
    return BrowserLapSectorSidecar(fixture_id, cast(Mapping[str, BrowserDriverLapSector], drivers))


def _publish(  # type: ignore[no-any-unimported]
    browser: Path, sidecar: BrowserLapSectorSidecar | None = None,
) -> PublishedBrowserDelivery:
    return publish_browser_delivery(
        browser_parent=browser, delivery_version="delivery-one",
        delivery=_delivery(sidecar=sidecar), schema_root=SCHEMA_ROOT,
    )


def _v2_practice_delivery(
    sidecar: BrowserLapSectorSidecar | None = None,
) -> BrowserDeliveryBuild:
    delivery = _delivery(session_mode="practice", sidecar=sidecar)
    session_metadata = pl.DataFrame([{
        "session_id": "test-race", "year": 2026, "round_number": 1,
        "event_name": "Practice", "session_name": "Practice 1", "session_type": "FP1",
        "session_mode": "practice", "session_start_time_utc": None,
    }], schema=dict(CANONICAL_TABLE_SCHEMAS_V2["session_metadata"]), strict=True)
    return replace(
        delivery,
        source=CanonicalGenerationSnapshot(
            "test-gen", "a" * 64, {"session_metadata": session_metadata},
        ),
    )


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


# ===========================================================================
# BrowserDriverLapSector model tests
# ===========================================================================


class TestBrowserDriverLapSector:
    """Positive and negative contract enforcement for BrowserDriverLapSector."""

    def test_constructs_with_all_fields_nominal(self) -> None:
        """✅ Positive: all fields populated with valid Int64 values."""
        # Arrange & Act
        sector = BrowserDriverLapSector(
            lap_number=(1, 2),
            lap_start_ms=(0, 100_000),
            lap_end_ms=(100_000, 200_000),
            lap_duration_ms=(100_000, 100_000),
            sector_1_duration_ms=(30_000, 30_000),
            sector_2_duration_ms=(30_000, 30_000),
            sector_3_duration_ms=(40_000, 40_000),
            sector_1_session_time_ms=(30_000, 130_000),
            sector_2_session_time_ms=(60_000, 160_000),
            sector_3_session_time_ms=(100_000, 200_000),
        )

        # Assert
        assert sector.lap_number == (1, 2)
        assert sector.lap_duration_ms == (100_000, 100_000)

    def test_constructs_with_null_sectors_preserved(self) -> None:
        """✅ Positive: None values in nullable sector fields are preserved."""
        # Arrange & Act
        sector = BrowserDriverLapSector(
            lap_number=(1,),
            lap_start_ms=(0,),
            lap_end_ms=(100_000,),
            lap_duration_ms=(None,),
            sector_1_duration_ms=(None,),
            sector_2_duration_ms=(None,),
            sector_3_duration_ms=(None,),
            sector_1_session_time_ms=(None,),
            sector_2_session_time_ms=(None,),
            sector_3_session_time_ms=(None,),
        )

        # Assert
        assert sector.sector_1_duration_ms == (None,)
        assert sector.sector_2_session_time_ms == (None,)
        assert sector.lap_duration_ms == (None,)

    def test_rejects_misaligned_field_lengths(self) -> None:
        """❌ Negative: fields with different tuple lengths raise ValueError."""
        # Arrange & Act & Assert
        with pytest.raises(ValueError, match="aligned to lap_number"):
            BrowserDriverLapSector(
                lap_number=(1, 2),
                lap_start_ms=(0,),               # shorter than lap_number
                lap_end_ms=(100_000, 200_000),
                lap_duration_ms=(100_000, 100_000),
                sector_1_duration_ms=(30_000, 30_000),
                sector_2_duration_ms=(30_000, 30_000),
                sector_3_duration_ms=(40_000, 40_000),
                sector_1_session_time_ms=(30_000, 130_000),
                sector_2_session_time_ms=(60_000, 160_000),
                sector_3_session_time_ms=(100_000, 200_000),
            )

    def test_rejects_non_positive_lap_number(self) -> None:
        """❌ Negative: lap_number <= 0 raises TypeError."""
        with pytest.raises(TypeError, match="positive integers"):
            BrowserDriverLapSector(
                lap_number=(0,),          # lap 0 is invalid
                lap_start_ms=(0,),
                lap_end_ms=(100_000,),
                lap_duration_ms=(100_000,),
                sector_1_duration_ms=(30_000,),
                sector_2_duration_ms=(30_000,),
                sector_3_duration_ms=(40_000,),
                sector_1_session_time_ms=(30_000,),
                sector_2_session_time_ms=(60_000,),
                sector_3_session_time_ms=(100_000,),
            )

    def test_rejects_negative_lap_number(self) -> None:
        """❌ Negative: negative lap_number raises TypeError."""
        with pytest.raises(TypeError, match="positive integers"):
            BrowserDriverLapSector(
                lap_number=(-1,),
                lap_start_ms=(0,),
                lap_end_ms=(100_000,),
                lap_duration_ms=(100_000,),
                sector_1_duration_ms=(30_000,),
                sector_2_duration_ms=(30_000,),
                sector_3_duration_ms=(40_000,),
                sector_1_session_time_ms=(30_000,),
                sector_2_session_time_ms=(60_000,),
                sector_3_session_time_ms=(100_000,),
            )

    def test_rejects_non_tuple_field(self) -> None:
        """❌ Negative: a list (not tuple) field raises TypeError."""
        with pytest.raises(TypeError, match="must be a tuple"):
            BrowserDriverLapSector(
                lap_number=[1],           # type: ignore[arg-type]  # list, not tuple
                lap_start_ms=(0,),
                lap_end_ms=(100_000,),
                lap_duration_ms=(100_000,),
                sector_1_duration_ms=(30_000,),
                sector_2_duration_ms=(30_000,),
                sector_3_duration_ms=(40_000,),
                sector_1_session_time_ms=(30_000,),
                sector_2_session_time_ms=(60_000,),
                sector_3_session_time_ms=(100_000,),
            )

    def test_rejects_int64_overflow_duration(self) -> None:
        """❌ Negative: duration > MAX_INT64 raises TypeError."""
        with pytest.raises(TypeError, match="signed Int64"):
            BrowserDriverLapSector(
                lap_number=(1,),
                lap_start_ms=(0,),
                lap_end_ms=(100_000,),
                lap_duration_ms=(MAX_INT64 + 1,),
                sector_1_duration_ms=(30_000,),
                sector_2_duration_ms=(30_000,),
                sector_3_duration_ms=(40_000,),
                sector_1_session_time_ms=(30_000,),
                sector_2_session_time_ms=(60_000,),
                sector_3_session_time_ms=(100_000,),
            )

    def test_rejects_int64_overflow_session_time(self) -> None:
        """❌ Negative: session_time_ms > MAX_INT64 raises TypeError."""
        with pytest.raises(TypeError, match="signed Int64"):
            BrowserDriverLapSector(
                lap_number=(1,),
                lap_start_ms=(0,),
                lap_end_ms=(100_000,),
                lap_duration_ms=(100_000,),
                sector_1_duration_ms=(30_000,),
                sector_2_duration_ms=(30_000,),
                sector_3_duration_ms=(40_000,),
                sector_1_session_time_ms=(MAX_INT64 + 1,),
                sector_2_session_time_ms=(60_000,),
                sector_3_session_time_ms=(100_000,),
            )

    def test_rejects_negative_session_time(self) -> None:
        """❌ Negative: negative session_time_ms raises TypeError."""
        with pytest.raises(TypeError, match="non-negative"):
            BrowserDriverLapSector(
                lap_number=(1,),
                lap_start_ms=(0,),
                lap_end_ms=(100_000,),
                lap_duration_ms=(100_000,),
                sector_1_duration_ms=(30_000,),
                sector_2_duration_ms=(30_000,),
                sector_3_duration_ms=(40_000,),
                sector_1_session_time_ms=(-1,),
                sector_2_session_time_ms=(60_000,),
                sector_3_session_time_ms=(100_000,),
            )

    def test_rejects_null_lap_start_ms(self) -> None:
        """❌ Negative: null lap_start_ms is not allowed in a completed lap."""
        with pytest.raises(TypeError, match="lap start/end times"):
            BrowserDriverLapSector(
                lap_number=(1,),
                lap_start_ms=(None,),  # type: ignore[arg-type]
                lap_end_ms=(100_000,),
                lap_duration_ms=(100_000,),
                sector_1_duration_ms=(30_000,),
                sector_2_duration_ms=(30_000,),
                sector_3_duration_ms=(40_000,),
                sector_1_session_time_ms=(30_000,),
                sector_2_session_time_ms=(60_000,),
                sector_3_session_time_ms=(100_000,),
            )

    def test_rejects_null_lap_end_ms(self) -> None:
        """❌ Negative: null lap_end_ms is not allowed in a completed lap."""
        with pytest.raises(TypeError, match="lap start/end times"):
            BrowserDriverLapSector(
                lap_number=(1,),
                lap_start_ms=(0,),
                lap_end_ms=(None,),  # type: ignore[arg-type]
                lap_duration_ms=(100_000,),
                sector_1_duration_ms=(30_000,),
                sector_2_duration_ms=(30_000,),
                sector_3_duration_ms=(40_000,),
                sector_1_session_time_ms=(30_000,),
                sector_2_session_time_ms=(60_000,),
                sector_3_session_time_ms=(100_000,),
            )

    def test_rejects_non_int_lap_start_ms(self) -> None:
        """❌ Negative: non-integer lap_start_ms raises TypeError."""
        with pytest.raises(TypeError, match="lap start/end times"):
            BrowserDriverLapSector(
                lap_number=(1,),
                lap_start_ms=("0",),  # type: ignore[arg-type]
                lap_end_ms=(100_000,),
                lap_duration_ms=(100_000,),
                sector_1_duration_ms=(30_000,),
                sector_2_duration_ms=(30_000,),
                sector_3_duration_ms=(40_000,),
                sector_1_session_time_ms=(30_000,),
                sector_2_session_time_ms=(60_000,),
                sector_3_session_time_ms=(100_000,),
            )

    def test_rejects_out_of_bounds_lap_end_ms(self) -> None:
        """❌ Negative: lap_end_ms above MAX_INT64 raises TypeError."""
        with pytest.raises(TypeError, match="lap start/end times"):
            BrowserDriverLapSector(
                lap_number=(1,),
                lap_start_ms=(0,),
                lap_end_ms=(MAX_INT64 + 1,),
                lap_duration_ms=(100_000,),
                sector_1_duration_ms=(30_000,),
                sector_2_duration_ms=(30_000,),
                sector_3_duration_ms=(40_000,),
                sector_1_session_time_ms=(30_000,),
                sector_2_session_time_ms=(60_000,),
                sector_3_session_time_ms=(100_000,),
            )

    def test_accepts_int64_max_value(self) -> None:
        """✅ Positive: MAX_INT64 is accepted at the boundary."""
        # Arrange & Act
        sector = BrowserDriverLapSector(
            lap_number=(1,),
            lap_start_ms=(MAX_INT64,),
            lap_end_ms=(MAX_INT64,),
            lap_duration_ms=(MAX_INT64,),
            sector_1_duration_ms=(MAX_INT64,),
            sector_2_duration_ms=(MAX_INT64,),
            sector_3_duration_ms=(MAX_INT64,),
            sector_1_session_time_ms=(MAX_INT64,),
            sector_2_session_time_ms=(MAX_INT64,),
            sector_3_session_time_ms=(MAX_INT64,),
        )

        # Assert
        assert sector.lap_start_ms == (MAX_INT64,)

    def test_zero_lap_driver_is_empty_tuples(self) -> None:
        """✅ Positive: driver with zero laps gets all-empty tuples."""
        # Arrange & Act
        sector = BrowserDriverLapSector(
            lap_number=(),
            lap_start_ms=(),
            lap_end_ms=(),
            lap_duration_ms=(),
            sector_1_duration_ms=(),
            sector_2_duration_ms=(),
            sector_3_duration_ms=(),
            sector_1_session_time_ms=(),
            sector_2_session_time_ms=(),
            sector_3_session_time_ms=(),
        )

        # Assert
        assert sector.lap_number == ()
        assert len(sector.lap_number) == 0
        assert len(sector.lap_start_ms) == 0
        assert len(sector.sector_1_duration_ms) == 0

    def test_as_dict_serializes_columnar_format(self) -> None:
        """✅ Positive: as_dict converts tuples to lists with camelCase keys."""
        # Arrange
        sector = BrowserDriverLapSector(
            lap_number=(1, 2),
            lap_start_ms=(0, 1_000),
            lap_end_ms=(1_000, 2_000),
            lap_duration_ms=(1_000, 1_000),
            sector_1_duration_ms=(300, None),
            sector_2_duration_ms=(300, 400),
            sector_3_duration_ms=(400, None),
            sector_1_session_time_ms=(300, None),
            sector_2_session_time_ms=(600, 1_400),
            sector_3_session_time_ms=(1_000, None),
        )

        # Act
        result = sector.as_dict()

        # Assert
        assert result == {
            "lapNumber": [1, 2],
            "lapStartMs": [0, 1000],
            "lapEndMs": [1000, 2000],
            "lapDurationMs": [1000, 1000],
            "sector1DurationMs": [300, None],
            "sector2DurationMs": [300, 400],
            "sector3DurationMs": [400, None],
            "sector1SessionTimeMs": [300, None],
            "sector2SessionTimeMs": [600, 1400],
            "sector3SessionTimeMs": [1000, None],
        }
        assert isinstance(result["lapNumber"], list)
        assert result["sector1DurationMs"] == [300, None]

    def test_lap_kind_defaults_to_null_aligned(self) -> None:
        """✅ Positive: absent lap_kind aligns to all-null without changing shape."""
        # Arrange & Act
        sector = BrowserDriverLapSector(
            lap_number=(1, 2),
            lap_start_ms=(0, 100_000),
            lap_end_ms=(100_000, 200_000),
            lap_duration_ms=(100_000, 100_000),
            sector_1_duration_ms=(30_000, 30_000),
            sector_2_duration_ms=(30_000, 30_000),
            sector_3_duration_ms=(40_000, 40_000),
            sector_1_session_time_ms=(30_000, 130_000),
            sector_2_session_time_ms=(60_000, 160_000),
            sector_3_session_time_ms=(100_000, 200_000),
        )

        # Assert
        assert sector.lap_kind == (None, None)

    def test_lap_kind_accepts_valid_values(self) -> None:
        """✅ Positive: the four frozen lapKind values are accepted and aligned."""
        # Arrange & Act
        sector = BrowserDriverLapSector(
            lap_number=(1, 2, 3, 4),
            lap_start_ms=(0, 100_000, 200_000, 300_000),
            lap_end_ms=(100_000, 200_000, 300_000, 400_000),
            lap_duration_ms=(100_000, 100_000, 100_000, 100_000),
            sector_1_duration_ms=(30_000, 30_000, 30_000, 30_000),
            sector_2_duration_ms=(30_000, 30_000, 30_000, 30_000),
            sector_3_duration_ms=(40_000, 40_000, 40_000, 40_000),
            sector_1_session_time_ms=(30_000, 130_000, 230_000, 330_000),
            sector_2_session_time_ms=(60_000, 160_000, 260_000, 360_000),
            sector_3_session_time_ms=(100_000, 200_000, 300_000, 400_000),
            lap_kind=("flying", "outlap", "inlap", "unknown"),
        )

        # Assert
        assert sector.lap_kind == ("flying", "outlap", "inlap", "unknown")

    def test_lap_kind_rejects_invalid_value(self) -> None:
        """❌ Negative: an out-of-enum lapKind value raises ValueError."""
        with pytest.raises(ValueError, match="lap_kind"):
            BrowserDriverLapSector(
                lap_number=(1,),
                lap_start_ms=(0,),
                lap_end_ms=(100_000,),
                lap_duration_ms=(100_000,),
                sector_1_duration_ms=(30_000,),
                sector_2_duration_ms=(30_000,),
                sector_3_duration_ms=(40_000,),
                sector_1_session_time_ms=(30_000,),
                sector_2_session_time_ms=(60_000,),
                sector_3_session_time_ms=(100_000,),
                lap_kind=("fast",),  # type: ignore[arg-type]
            )

    def test_lap_kind_rejects_misaligned_length(self) -> None:
        """❌ Negative: a lap_kind length differing from lap_number raises ValueError."""
        with pytest.raises(ValueError, match="aligned to lap_number"):
            BrowserDriverLapSector(
                lap_number=(1, 2),
                lap_start_ms=(0, 100_000),
                lap_end_ms=(100_000, 200_000),
                lap_duration_ms=(100_000, 100_000),
                sector_1_duration_ms=(30_000, 30_000),
                sector_2_duration_ms=(30_000, 30_000),
                sector_3_duration_ms=(40_000, 40_000),
                sector_1_session_time_ms=(30_000, 130_000),
                sector_2_session_time_ms=(60_000, 160_000),
                sector_3_session_time_ms=(100_000, 200_000),
                lap_kind=("flying",),
            )

    def test_lap_kind_serialized_only_when_requested(self) -> None:
        """✅ Positive: lapKind appears only when explicitly requested with values."""
        # Arrange
        sector = BrowserDriverLapSector(
            lap_number=(1,),
            lap_start_ms=(0,),
            lap_end_ms=(100_000,),
            lap_duration_ms=(100_000,),
            sector_1_duration_ms=(30_000,),
            sector_2_duration_ms=(30_000,),
            sector_3_duration_ms=(40_000,),
            sector_1_session_time_ms=(30_000,),
            sector_2_session_time_ms=(60_000,),
            sector_3_session_time_ms=(100_000,),
            lap_kind=("flying",),
        )

        # Act
        without = sector.as_dict()
        with_kind = sector.as_dict(include_lap_kind=True)

        # Assert
        assert "lapKind" not in without
        assert with_kind["lapKind"] == ["flying"]

    def test_lap_kind_all_null_omitted_on_serialization(self) -> None:
        """✅ Positive: an all-null lap_kind is omitted (capability unavailable)."""
        # Arrange — a driver whose lap_kind was never derived.
        sector = BrowserDriverLapSector(
            lap_number=(1,),
            lap_start_ms=(0,),
            lap_end_ms=(100_000,),
            lap_duration_ms=(100_000,),
            sector_1_duration_ms=(30_000,),
            sector_2_duration_ms=(30_000,),
            sector_3_duration_ms=(40_000,),
            sector_1_session_time_ms=(30_000,),
            sector_2_session_time_ms=(60_000,),
            sector_3_session_time_ms=(100_000,),
        )

        # Act
        result = sector.as_dict(include_lap_kind=True)

        # Assert — absent column means capability unavailable, never a guess.
        assert "lapKind" not in result


# ===========================================================================
# BrowserLapSectorSidecar model tests
# ===========================================================================


class TestBrowserLapSectorSidecar:
    """Positive and negative contract enforcement for BrowserLapSectorSidecar."""

    def test_constructs_valid_sidecar(self) -> None:
        """✅ Positive: constructs with valid fixture_id and driver mapping."""
        # Arrange
        driver_sector = BrowserDriverLapSector(
            lap_number=(1,),
            lap_start_ms=(0,),
            lap_end_ms=(100_000,),
            lap_duration_ms=(100_000,),
            sector_1_duration_ms=(30_000,),
            sector_2_duration_ms=(30_000,),
            sector_3_duration_ms=(40_000,),
            sector_1_session_time_ms=(30_000,),
            sector_2_session_time_ms=(60_000,),
            sector_3_session_time_ms=(100_000,),
        )

        # Act
        sidecar = BrowserLapSectorSidecar("race-01", {"HAM": driver_sector})

        # Assert
        assert sidecar.fixture_id == "race-01"
        assert sidecar.drivers["HAM"] is driver_sector

    def test_sorts_drivers_alphabetically(self) -> None:
        """✅ Positive: driver entries are sorted by driver_id regardless of
        insertion order."""
        # Arrange
        empty = BrowserDriverLapSector(
            lap_number=(), lap_start_ms=(), lap_end_ms=(), lap_duration_ms=(),
            sector_1_duration_ms=(), sector_2_duration_ms=(), sector_3_duration_ms=(),
            sector_1_session_time_ms=(), sector_2_session_time_ms=(), sector_3_session_time_ms=(),
        )

        # Act
        sidecar = BrowserLapSectorSidecar("race-01", {"VER": empty, "ALO": empty, "HAM": empty})

        # Assert — keys iterate in sorted order because __post_init__ sorts
        assert tuple(sidecar.drivers) == ("ALO", "HAM", "VER")

    def test_drivers_are_immutable(self) -> None:
        """✅ Positive: the drivers mapping is frozen after construction."""
        sidecar = BrowserLapSectorSidecar("race-01", {"HAM": _empty_driver()})

        with pytest.raises(TypeError):
            sidecar.drivers["NEW"] = _empty_driver()  # type: ignore[index]

    def test_rejects_empty_fixture_id(self) -> None:
        """❌ Negative: empty string fixture_id raises ValueError."""
        with pytest.raises(ValueError, match="fixture_id"):
            BrowserLapSectorSidecar("", {"HAM": _empty_driver()})

    def test_rejects_empty_drivers(self) -> None:
        """❌ Negative: empty drivers mapping raises ValueError."""
        with pytest.raises(ValueError, match="non-empty mapping"):
            BrowserLapSectorSidecar("race-01", {})

    def test_rejects_invalid_driver_id_pattern(self) -> None:
        """❌ Negative: driver ID not matching [A-Z0-9]{2,4} raises ValueError."""
        with pytest.raises(ValueError, match="driver ID"):
            BrowserLapSectorSidecar("race-01", {"ham": _empty_driver()})  # lowercase

    def test_rejects_too_long_driver_id(self) -> None:
        """❌ Negative: driver ID > 4 chars raises ValueError."""
        with pytest.raises(ValueError, match="driver ID"):
            BrowserLapSectorSidecar("race-01", {"ABCDE": _empty_driver()})

    def test_rejects_non_browser_driver_lap_sector_value(self) -> None:
        """❌ Negative: a value not of type BrowserDriverLapSector raises TypeError."""
        with pytest.raises(TypeError, match="BrowserDriverLapSector"):
            BrowserLapSectorSidecar("race-01", {"HAM": "not-a-sector"})  # type: ignore[dict-item]

    def test_as_dict_returns_contract_format(self) -> None:
        """✅ Positive: as_dict returns v2 contract with nested driver arrays."""
        # Arrange
        sidecar = _sidecar()

        # Act
        result = sidecar.as_dict()

        # Assert
        assert result["contractVersion"] == "v2"
        assert result["fixtureId"] == "test-race"
        assert "drivers" in result
        assert "HAM" in result["drivers"]
        assert result["drivers"]["HAM"]["lapNumber"] == [1]
        assert isinstance(result["drivers"]["HAM"]["sector1DurationMs"], list)

    def test_driver_id_two_chars_accepted(self) -> None:
        """✅ Positive: 2-char driver codes like 'AA' are accepted."""
        sidecar = BrowserLapSectorSidecar("race-01", {"AA": _empty_driver()})
        assert "AA" in sidecar.drivers

    def test_driver_id_numeric_chars_accepted(self) -> None:
        """✅ Positive: numeric-only driver codes like '44' are accepted."""
        sidecar = BrowserLapSectorSidecar("race-01", {"44": _empty_driver()})
        assert "44" in sidecar.drivers


# ===========================================================================
# Qualifying timeline model tests
# ===========================================================================


class TestQualifyingTimelineModels:
    """Positive and negative contract enforcement for the qualifying timeline."""

    def _timeline(
        self,
        *,
        intervals: tuple[BrowserQualifyingTimelineInterval, ...] = (),
        markers: tuple[BrowserQualifyingIncidentMarker, ...] = (),
    ) -> BrowserQualifyingTimeline:
        return BrowserQualifyingTimeline(
            "race-01", 0, 400_000, intervals, markers,
        )

    def test_constructs_valid_timeline(self) -> None:
        """✅ Positive: a timeline with intervals and markers constructs."""
        # Arrange & Act
        timeline = self._timeline(
            intervals=(BrowserQualifyingTimelineInterval("yellow", 10_000, 20_000),),
            markers=(BrowserQualifyingIncidentMarker("HAM", 30_000, "CAR 44 CRASH"),),
        )

        # Assert
        assert timeline.fixture_id == "race-01"
        assert timeline.intervals[0].kind == "yellow"
        assert timeline.incident_markers[0].source == "race-control-car-event"

    def test_rejects_invalid_interval_kind(self) -> None:
        """❌ Negative: SC/VSC interval kinds are not exposed in this revision."""
        with pytest.raises(ValueError, match="interval kind"):
            BrowserQualifyingTimelineInterval("sc", 0, 100)  # type: ignore[arg-type]

    def test_rejects_interval_outside_bounds(self) -> None:
        """❌ Negative: an interval outside the artifact window raises ValueError."""
        with pytest.raises(ValueError, match="within replay bounds"):
            self._timeline(intervals=(BrowserQualifyingTimelineInterval("red", 500_000, 600_000),))

    def test_rejects_marker_outside_bounds(self) -> None:
        """❌ Negative: a marker at or beyond endMs raises ValueError."""
        with pytest.raises(ValueError, match="within replay bounds"):
            self._timeline(markers=(BrowserQualifyingIncidentMarker("HAM", 400_000, "CAR 44 CRASH"),))

    def test_rejects_unsorted_markers(self) -> None:
        """❌ Negative: markers must be ordered by timeMs, driverId, rawMessage."""
        with pytest.raises(ValueError, match="deterministically ordered"):
            self._timeline(markers=(
                BrowserQualifyingIncidentMarker("HAM", 30_000, "CAR 44 CRASH"),
                BrowserQualifyingIncidentMarker("HAM", 20_000, "CAR 44 STOPS"),
            ))

    def test_rejects_duplicate_markers(self) -> None:
        """❌ Negative: exact duplicate markers are rejected."""
        with pytest.raises(ValueError, match="must not contain duplicates"):
            self._timeline(markers=(
                BrowserQualifyingIncidentMarker("HAM", 30_000, "CAR 44 CRASH"),
                BrowserQualifyingIncidentMarker("HAM", 30_000, "CAR 44 CRASH"),
            ))

    def test_rejects_invalid_marker_source(self) -> None:
        """❌ Negative: a non-frozen marker source is rejected."""
        with pytest.raises(ValueError, match="source is invalid"):
            BrowserQualifyingIncidentMarker("HAM", 30_000, "CAR 44 CRASH", source="dnf")  # type: ignore[arg-type]

    def test_rejects_non_v2_contract(self) -> None:
        """❌ Negative: the qualifying timeline is available only as v2."""
        with pytest.raises(ValueError, match="contract version v2"):
            BrowserQualifyingTimeline(
                "race-01", 0, 400_000, (), (), contract_version="v1",  # type: ignore[arg-type]
            )

    def test_as_dict_emits_intervals_and_incident_markers(self) -> None:
        """✅ Positive: as_dict carries only qualifying-safe field names."""
        # Arrange
        timeline = self._timeline(
            intervals=(
                BrowserQualifyingTimelineInterval("yellow", 10_000, 20_000),
                BrowserQualifyingTimelineInterval("red", 30_000, 40_000),
            ),
            markers=(BrowserQualifyingIncidentMarker("HAM", 25_000, "CAR 44 CRASH", lap_number=7),),
        )

        # Act
        result = timeline.as_dict()

        # Assert — no DNF/OUT/finish/position semantics are ever exposed.
        assert result["contractVersion"] == "v2"
        assert result["intervals"] == [
            {"kind": "yellow", "startMs": 10_000, "endMs": 20_000},
            {"kind": "red", "startMs": 30_000, "endMs": 40_000},
        ]
        assert result["incidentMarkers"] == [{
            "driverId": "HAM", "timeMs": 25_000,
            "source": "race-control-car-event", "rawMessage": "CAR 44 CRASH",
            "lapNumber": 7,
        }]
        assert "dnfMarkers" not in result
        assert "OUT" not in result


# ===========================================================================
# BrowserLapSectorSidecarReference tests
# ===========================================================================


class TestBrowserLapSectorSidecarReference:
    """Positive and negative contract enforcement for the manifest reference."""

    def test_accepts_correct_path_and_schema(self) -> None:
        """✅ Positive: correct path and schema_id are accepted."""
        # Arrange & Act
        ref = BrowserLapSectorSidecarReference(
            path="lap-sector-sidecar.json",
            schema_id=BROWSER_LAP_SECTOR_SIDECAR_SCHEMA_ID,
            sha256="a" * 64,
        )

        # Assert
        assert ref.path == "lap-sector-sidecar.json"
        assert ref.schema_id == BROWSER_LAP_SECTOR_SIDECAR_SCHEMA_ID

    def test_rejects_wrong_path(self) -> None:
        """❌ Negative: incorrect path raises ValueError."""
        with pytest.raises(ValueError, match="path must be lap-sector-sidecar.json"):
            BrowserLapSectorSidecarReference(
                path="wrong-file.json",
                schema_id=BROWSER_LAP_SECTOR_SIDECAR_SCHEMA_ID,
                sha256="a" * 64,
            )

    def test_rejects_wrong_schema_id(self) -> None:
        """❌ Negative: incorrect schema_id raises ValueError."""
        with pytest.raises(ValueError, match="schema_id is invalid"):
            BrowserLapSectorSidecarReference(
                path="lap-sector-sidecar.json",
                schema_id="urn:f1-cache-replay:schema:replay-data:unsupported:lap-sector-sidecar",
                sha256="a" * 64,
            )

    def test_rejects_invalid_sha256(self) -> None:
        """❌ Negative: non-hex SHA-256 raises ValueError."""
        with pytest.raises(ValueError, match="sha256"):
            BrowserLapSectorSidecarReference(
                path="lap-sector-sidecar.json",
                schema_id=BROWSER_LAP_SECTOR_SIDECAR_SCHEMA_ID,
                sha256="not-a-sha",
            )


# ===========================================================================
# build_lap_sector_sidecar derivation tests
# ===========================================================================


class TestBuildLapSectorSidecar:
    """Behavior tests for the pure derivation function."""

    @staticmethod
    def _snapshot_with_laps(
        laps_data: list[dict[str, object]],
        driver_ids: tuple[str, ...] = ("HAM",),
        fixture_id: str = "test-race",
        session_mode: SessionMode = "race",
        track_status_data: list[dict[str, object]] | None = None,
        messages_data: list[dict[str, object]] | None = None,
    ) -> CanonicalGenerationSnapshot:
        """Build a minimal CanonicalGenerationSnapshot for sidecar tests."""
        session_row: dict[str, object] = {
            "session_id": fixture_id,
            "year": 2026, "round_number": 1,
            "event_name": "Test Grand Prix", "session_name": "Race",
            "session_type": "R",
            "session_mode": session_mode,
            "session_start_time_utc": datetime(2026, 1, 1, tzinfo=timezone.utc),
        }
        driver_rows: list[dict[str, object]] = []
        for did in driver_ids:
            driver_rows.append({
                "session_id": fixture_id, "driver_id": did,
                "source_driver_key": did, "driver_number": 1,
                "full_name": did, "team_name": f"Team {did}",
                "team_colour": "112233",
            })
        # Build the v2 laps frame — fill only columns that
        # build_lap_sector_sidecar reads, default others to null.
        lap_schema = dict(CANONICAL_TABLE_SCHEMAS_V2["laps"])
        lap_rows: list[dict[str, object]] = []
        for row in laps_data:
            full: dict[str, object] = {col: None for col in lap_schema}
            full.update(row)
            lap_rows.append(full)
        laps = pl.DataFrame(lap_rows, schema=lap_schema, strict=True)
        frames: dict[str, pl.DataFrame] = {
            "session_metadata": pl.DataFrame(
                [session_row],
                schema=dict(CANONICAL_TABLE_SCHEMAS_V2["session_metadata"]),
                strict=True,
            ),
            "drivers": pl.DataFrame(
                driver_rows,
                schema=dict(CANONICAL_TABLE_SCHEMAS_V2["drivers"]),
                strict=True,
            ),
            "laps": laps,
        }
        if track_status_data:
            frames["track_status_intervals"] = pl.DataFrame(
                track_status_data,
                schema=dict(CANONICAL_TABLE_SCHEMAS_V2["track_status_intervals"]),
                strict=True,
            )
        if messages_data:
            frames["race_control_messages"] = pl.DataFrame(
                messages_data,
                schema=dict(CANONICAL_TABLE_SCHEMAS_V2["race_control_messages"]),
                strict=True,
            )
        return CanonicalGenerationSnapshot("generation", "a" * 64, frames)

    @staticmethod
    def _lap_row(driver_id: str, lap: int, **overrides: object) -> dict[str, object]:
        row: dict[str, object] = {
            "session_id": "test-race",
            "driver_id": driver_id,
            "lap_number": lap,
            "lap_start_time_ms": lap * 100_000,
            "lap_end_time_ms": (lap + 1) * 100_000,
            "lap_duration_ms": 100_000,
        }
        row.update(overrides)
        return row

    @staticmethod
    def _flying_lap_row(
        driver_id: str, lap: int, *, phase: str | None = "Q1", duration: int = 100_000,
        **overrides: object,
    ) -> dict[str, object]:
        """Return a canonical completed lap that passes the flying evidence gate.

        The lap is pit-free, accurate, non-deleted, green-status, has all three
        sector durations and session timestamps, and its sector-duration sum
        equals its duration within the 3 ms FastF1 tolerance.
        """
        start = lap * 100_000
        sector_1, sector_2 = 30_000, 30_000
        sector_3 = duration - sector_1 - sector_2
        row = TestBuildLapSectorSidecar._lap_row(
            driver_id, lap,
            lap_end_time_ms=start + duration,
            lap_duration_ms=duration,
            qualifying_phase=phase,
            pit_in_time_ms=None,
            pit_out_time_ms=None,
            is_accurate=True,
            deleted=False,
            track_status="1",
            sector_1_duration_ms=sector_1,
            sector_2_duration_ms=sector_2,
            sector_3_duration_ms=sector_3,
            sector_1_session_time_ms=start + sector_1,
            sector_2_session_time_ms=start + sector_1 + sector_2,
            sector_3_session_time_ms=start + duration,
        )
        row.update(overrides)
        return row

    # -- Positive tests -----------------------------------------------------

    def test_derives_columnar_layout_from_canonical_laps(self) -> None:
        """✅ Positive: multi-lap driver produces correct column arrays."""
        # Arrange
        snapshot = self._snapshot_with_laps([
            self._lap_row("HAM", 1, sector_1_duration_ms=30_000),
            self._lap_row("HAM", 2, sector_2_duration_ms=40_000),
        ])

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert
        ham = sidecar.drivers["HAM"]
        assert ham.lap_number == (1, 2)
        assert ham.lap_start_ms == (100_000, 200_000)
        assert ham.lap_end_ms == (200_000, 300_000)
        assert ham.lap_duration_ms == (100_000, 100_000)

    def test_preserves_null_sector_fields(self) -> None:
        """✅ Positive: None canonical sector values propagate as None."""
        # Arrange
        snapshot = self._snapshot_with_laps([
            self._lap_row("HAM", 1,
                          sector_1_duration_ms=None,
                          sector_2_duration_ms=30_000,
                          sector_3_duration_ms=None,
                          sector_1_session_time_ms=None,
                          sector_2_session_time_ms=100_000,
                          sector_3_session_time_ms=None),
        ])

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert — nulls preserved, no values invented
        ham = sidecar.drivers["HAM"]
        assert ham.sector_1_duration_ms == (None,)
        assert ham.sector_2_duration_ms == (30_000,)
        assert ham.sector_3_duration_ms == (None,)
        assert ham.sector_1_session_time_ms == (None,)
        assert ham.sector_2_session_time_ms == (100_000,)
        assert ham.sector_3_session_time_ms == (None,)

    def test_pairs_sector_durations_with_session_timestamps(self) -> None:
        """✅ Positive: sector durations and session timestamps are aligned
        at the same index for causal reveal."""
        # Arrange
        snapshot = self._snapshot_with_laps([
            self._lap_row("HAM", 1,
                          sector_1_duration_ms=30_000,
                          sector_2_duration_ms=30_000,
                          sector_3_duration_ms=40_000,
                          sector_1_session_time_ms=30_000,
                          sector_2_session_time_ms=60_000,
                          sector_3_session_time_ms=100_000),
        ])

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert — each sector duration maps to its session time at same index
        ham = sidecar.drivers["HAM"]
        assert len(ham.sector_1_duration_ms) == len(ham.sector_1_session_time_ms)
        assert len(ham.sector_2_duration_ms) == len(ham.sector_2_session_time_ms)
        assert len(ham.sector_3_duration_ms) == len(ham.sector_3_session_time_ms)
        # Verify the causal pairing for lap 1:
        assert ham.sector_1_duration_ms[0] == 30_000
        assert ham.sector_1_session_time_ms[0] == 30_000

    def test_orders_laps_by_lap_number_ascending(self) -> None:
        """✅ Positive: laps inserted out of order come out sorted."""
        # Arrange — insert lap 3 before lap 1
        snapshot = self._snapshot_with_laps([
            self._lap_row("HAM", 3),
            self._lap_row("HAM", 1),
            self._lap_row("HAM", 2),
        ])

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert
        assert sidecar.drivers["HAM"].lap_number == (1, 2, 3)

    def test_orders_drivers_by_driver_id(self) -> None:
        """✅ Positive: sidecar iterates drivers in sorted order."""
        # Arrange
        snapshot = self._snapshot_with_laps(
            [self._lap_row("VER", 1), self._lap_row("ALO", 1)],
            driver_ids=("VER", "ALO"),
        )

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert — sorted by driver_id
        assert tuple(sidecar.drivers) == ("ALO", "VER")

    def test_drivers_with_zero_laps_get_empty_column_tuples(self) -> None:
        """✅ Positive: a registered driver with no laps produces empty tuples."""
        # Arrange — HAM has laps, VER does not
        snapshot = self._snapshot_with_laps(
            [self._lap_row("HAM", 1)],
            driver_ids=("HAM", "VER"),
        )

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert
        ver = sidecar.drivers["VER"]
        assert ver.lap_number == ()
        assert ver.lap_start_ms == ()
        assert ver.lap_duration_ms == ()
        assert ver.sector_1_duration_ms == ()
        assert ver.sector_2_duration_ms == ()
        assert ver.sector_3_duration_ms == ()
        assert ver.sector_1_session_time_ms == ()
        assert ver.sector_2_session_time_ms == ()
        assert ver.sector_3_session_time_ms == ()

    def test_includes_all_registered_drivers(self) -> None:
        """✅ Positive: every driver in the drivers table appears in the sidecar."""
        # Arrange
        snapshot = self._snapshot_with_laps(
            [self._lap_row("HAM", 1), self._lap_row("VER", 1)],
            driver_ids=("ALO", "HAM", "LEC", "VER"),
        )

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert — all four drivers present, ALO and LEC with empty data
        assert set(sidecar.drivers) == {"ALO", "HAM", "LEC", "VER"}
        assert len(sidecar.drivers["ALO"].lap_number) == 0
        assert len(sidecar.drivers["LEC"].lap_number) == 0

    def test_deterministic_identical_inputs_produce_identical_sidecar(self) -> None:
        """✅ Positive: the same canonical snapshot always yields the same sidecar."""
        # Arrange
        snapshot = self._snapshot_with_laps(
            [self._lap_row("HAM", 1, sector_1_duration_ms=30_000),
             self._lap_row("HAM", 2, sector_1_duration_ms=31_000)],
        )

        # Act — build twice from same snapshot
        first = build_lap_sector_sidecar(snapshot)
        second = build_lap_sector_sidecar(snapshot)

        # Assert
        assert first.as_dict() == second.as_dict()

    def test_fixture_id_matches_session_metadata(self) -> None:
        """✅ Positive: sidecar fixture_id is taken from session_metadata."""
        # Arrange
        snapshot = self._snapshot_with_laps(
            [self._lap_row("HAM", 1)], fixture_id="monza-2026",
        )

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert
        assert sidecar.fixture_id == "monza-2026"

    def test_all_sector_session_time_pairs_align_to_lap_rows(self) -> None:
        """✅ Positive: for each lap row, sector session times are at the
        same column index as their duration counterparts."""
        # Arrange
        snapshot = self._snapshot_with_laps([
            self._lap_row("HAM", 1,
                          sector_1_duration_ms=300, sector_2_duration_ms=400,
                          sector_3_duration_ms=300, sector_1_session_time_ms=300,
                          sector_2_session_time_ms=700, sector_3_session_time_ms=1000),
            self._lap_row("HAM", 2,
                          sector_1_duration_ms=310, sector_2_duration_ms=410,
                          sector_3_duration_ms=290, sector_1_session_time_ms=100_310,
                          sector_2_session_time_ms=100_720, sector_3_session_time_ms=101_010),
        ])

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)
        ham = sidecar.drivers["HAM"]

        # Assert — for each completed lap, sector times are at matching indices
        for idx in range(len(ham.lap_number)):
            s1d = ham.sector_1_duration_ms[idx]
            s1t = ham.sector_1_session_time_ms[idx]
            s2d = ham.sector_2_duration_ms[idx]
            s2t = ham.sector_2_session_time_ms[idx]
            s3d = ham.sector_3_duration_ms[idx]
            s3t = ham.sector_3_session_time_ms[idx]
            # All must be null or all must be integers at same index
            assert (s1d is None) == (s1t is None)
            assert (s2d is None) == (s2t is None)
            assert (s3d is None) == (s3t is None)

    def test_preserves_canonical_qualifying_phases_and_derives_phase_starts(self) -> None:
        snapshot = self._snapshot_with_laps([
            self._lap_row("HAM", 1, qualifying_phase="Q1"),
            self._lap_row("HAM", 2, qualifying_phase="Q2"),
            self._lap_row("HAM", 3, qualifying_phase="Q3"),
        ])

        sidecar = build_lap_sector_sidecar(snapshot)

        assert sidecar.drivers["HAM"].qualifying_phase == ("Q1", "Q2", "Q3")
        assert sidecar.qualifying_phase_boundaries == (
            BrowserQualifyingPhaseBoundary("Q1", 100_000),
            BrowserQualifyingPhaseBoundary("Q2", 200_000),
            BrowserQualifyingPhaseBoundary("Q3", 300_000),
        )

    def test_non_qualifying_schema_without_phase_column_preserves_null_phase_alignment(self) -> None:
        snapshot = self._snapshot_with_laps([self._lap_row("HAM", 1)])

        sidecar = build_lap_sector_sidecar(snapshot)

        assert sidecar.drivers["HAM"].qualifying_phase == (None,)
        assert sidecar.qualifying_phase_boundaries == ()

    def test_rejects_invalid_or_misaligned_qualifying_phase_values(self) -> None:
        with pytest.raises(ValueError, match="qualifying_phase"):
            BrowserDriverLapSector(
                lap_number=(1,), lap_start_ms=(0,), lap_end_ms=(1,),
                lap_duration_ms=(1,), sector_1_duration_ms=(1,),
                sector_2_duration_ms=(1,), sector_3_duration_ms=(1,),
                sector_1_session_time_ms=(1,), sector_2_session_time_ms=(1,),
                sector_3_session_time_ms=(1,), qualifying_phase=("Q4",),  # type: ignore[arg-type]
            )
        with pytest.raises(ValueError, match="aligned"):
            BrowserDriverLapSector(
                lap_number=(1,), lap_start_ms=(0,), lap_end_ms=(1,),
                lap_duration_ms=(1,), sector_1_duration_ms=(1,),
                sector_2_duration_ms=(1,), sector_3_duration_ms=(1,),
                sector_1_session_time_ms=(1,), sector_2_session_time_ms=(1,),
                sector_3_session_time_ms=(1,), qualifying_phase=("Q1", "Q2"),
            )

    # -- Qualifying lapKind derivation --------------------------------------

    def test_derives_flying_for_accurate_quicklap_in_qualifying(self) -> None:
        """✅ Positive: a complete accurate lap within the phase gate is flying."""
        # Arrange
        snapshot = self._snapshot_with_laps(
            [self._flying_lap_row("HAM", 1, phase="Q1")],
            session_mode="qualifying",
        )

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert
        assert sidecar.drivers["HAM"].lap_kind == ("flying",)

    def test_derives_outlap_from_pit_out_signal(self) -> None:
        """✅ Positive: non-null pit-out classifies the lap outlap, never flying."""
        # Arrange — pit-out is authoritative even with otherwise perfect timing.
        snapshot = self._snapshot_with_laps(
            [self._flying_lap_row("HAM", 1, phase="Q1", pit_out_time_ms=90_000)],
            session_mode="qualifying",
        )

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert
        assert sidecar.drivers["HAM"].lap_kind == ("outlap",)

    def test_derives_inlap_from_pit_in_signal(self) -> None:
        """✅ Positive: non-null pit-in classifies the lap inlap, never flying."""
        # Arrange
        snapshot = self._snapshot_with_laps(
            [self._flying_lap_row("HAM", 1, phase="Q1", pit_in_time_ms=90_000)],
            session_mode="qualifying",
        )

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert
        assert sidecar.drivers["HAM"].lap_kind == ("inlap",)

    def test_both_pit_signals_resolve_to_unknown(self) -> None:
        """✅ Positive: a row with both pit signals is unknown, never guessed."""
        # Arrange — ADR-003 refuses to pick between outlap and inlap here.
        snapshot = self._snapshot_with_laps(
            [self._flying_lap_row(
                "HAM", 1, phase="Q1",
                pit_in_time_ms=90_000, pit_out_time_ms=95_000,
            )],
            session_mode="qualifying",
        )

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert
        assert sidecar.drivers["HAM"].lap_kind == ("unknown",)

    def test_unknown_when_accuracy_missing(self) -> None:
        """✅ Positive: missing accuracy evidence fails closed to unknown."""
        # Arrange
        snapshot = self._snapshot_with_laps(
            [self._flying_lap_row("HAM", 1, phase="Q1", is_accurate=None)],
            session_mode="qualifying",
        )

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert
        assert sidecar.drivers["HAM"].lap_kind == ("unknown",)

    def test_unknown_when_deleted_flag_missing(self) -> None:
        """✅ Positive: missing deletion evidence fails closed to unknown."""
        # Arrange
        snapshot = self._snapshot_with_laps(
            [self._flying_lap_row("HAM", 1, phase="Q1", deleted=None)],
            session_mode="qualifying",
        )

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert
        assert sidecar.drivers["HAM"].lap_kind == ("unknown",)

    def test_unknown_when_lap_deleted(self) -> None:
        """✅ Positive: a deleted lap is never flying."""
        # Arrange
        snapshot = self._snapshot_with_laps(
            [self._flying_lap_row("HAM", 1, phase="Q1", deleted=True)],
            session_mode="qualifying",
        )

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert
        assert sidecar.drivers["HAM"].lap_kind == ("unknown",)

    def test_unknown_when_track_status_missing_or_non_green(self) -> None:
        """✅ Positive: absent or non-green track status fails closed to unknown."""
        # Arrange & Act
        missing = build_lap_sector_sidecar(self._snapshot_with_laps(
            [self._flying_lap_row("HAM", 1, phase="Q1", track_status=None)],
            session_mode="qualifying",
        ))
        red_flag = build_lap_sector_sidecar(self._snapshot_with_laps(
            [self._flying_lap_row("HAM", 1, phase="Q1", track_status="5")],
            session_mode="qualifying",
        ))

        # Assert
        assert missing.drivers["HAM"].lap_kind == ("unknown",)
        assert red_flag.drivers["HAM"].lap_kind == ("unknown",)

    def test_unknown_when_sector_evidence_incomplete(self) -> None:
        """✅ Positive: a missing sector duration fails closed to unknown."""
        # Arrange
        snapshot = self._snapshot_with_laps(
            [self._flying_lap_row("HAM", 1, phase="Q1", sector_1_duration_ms=None)],
            session_mode="qualifying",
        )

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert
        assert sidecar.drivers["HAM"].lap_kind == ("unknown",)

    def test_unknown_when_sector_sum_mismatches_duration(self) -> None:
        """✅ Positive: an inconsistent sector sum beyond 3 ms fails closed."""
        # Arrange — sectors sum to 110_000 while the duration is 100_000.
        snapshot = self._snapshot_with_laps(
            [self._flying_lap_row("HAM", 1, phase="Q1", sector_3_duration_ms=50_000)],
            session_mode="qualifying",
        )

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert
        assert sidecar.drivers["HAM"].lap_kind == ("unknown",)

    def test_unknown_when_lap_exceeds_quicklap_threshold(self) -> None:
        """✅ Positive: a slow accurate non-pit lap above 107% resolves to unknown."""
        # Arrange — phase best is 100_000; 107% gate = 107_000; 110_000 fails.
        snapshot = self._snapshot_with_laps(
            [
                self._flying_lap_row("HAM", 1, phase="Q1", duration=100_000),
                self._flying_lap_row("VER", 2, phase="Q1", duration=110_000),
            ],
            driver_ids=("HAM", "VER"),
            session_mode="qualifying",
        )

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert — never promoted to flying; unknown is the fail-closed value.
        assert sidecar.drivers["HAM"].lap_kind == ("flying",)
        assert sidecar.drivers["VER"].lap_kind == ("unknown",)

    def test_quicklap_gate_is_cross_driver_per_phase(self) -> None:
        """✅ Positive: the gate uses the phase aggregate, not the driver's own."""
        # Arrange — VER's 106_000 lap is within 107% of HAM's 100_000 phase best.
        snapshot = self._snapshot_with_laps(
            [
                self._flying_lap_row("HAM", 1, phase="Q1", duration=100_000),
                self._flying_lap_row("VER", 2, phase="Q1", duration=106_000),
            ],
            driver_ids=("HAM", "VER"),
            session_mode="qualifying",
        )

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert
        assert sidecar.drivers["HAM"].lap_kind == ("flying",)
        assert sidecar.drivers["VER"].lap_kind == ("flying",)

    def test_quicklap_gate_is_phase_local_not_global(self) -> None:
        """✅ Positive: a lap is compared against its own phase, not Q3's best."""
        # Arrange — HAM's Q1 105_000 lap is within Q1's own gate but above 107%
        # of VER's faster 90_000 Q2 best; phase locality keeps it flying.
        snapshot = self._snapshot_with_laps(
            [
                self._flying_lap_row("HAM", 1, phase="Q1", duration=105_000),
                self._flying_lap_row("VER", 2, phase="Q2", duration=90_000),
            ],
            driver_ids=("HAM", "VER"),
            session_mode="qualifying",
        )

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert
        assert sidecar.drivers["HAM"].lap_kind == ("flying",)
        assert sidecar.drivers["VER"].lap_kind == ("flying",)

    def test_unknown_when_lap_has_no_phase_assignment(self) -> None:
        """✅ Positive: a phase-less lap cannot pass the per-phase gate."""
        # Arrange — the lap is complete and accurate but has no Q phase.
        snapshot = self._snapshot_with_laps(
            [self._flying_lap_row("HAM", 1, phase=None)],
            session_mode="qualifying",
        )

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert
        assert sidecar.drivers["HAM"].lap_kind == ("unknown",)

    def test_non_qualifying_session_omits_lap_kind(self) -> None:
        """✅ Positive: race-shaped sessions keep lap_kind absent (capability off)."""
        # Arrange — the same flying lap under a race session mode.
        snapshot = self._snapshot_with_laps(
            [self._flying_lap_row("HAM", 1, phase=None)],
            session_mode="race",
        )

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert — all-null capability, never a guessed flying classification.
        assert sidecar.drivers["HAM"].lap_kind == (None,)
        payload = sidecar.as_dict(include_qualifying_phase=True)
        assert "lapKind" not in payload["drivers"]["HAM"]

    def test_qualifying_v2_serialization_includes_lap_kind_values(self) -> None:
        """✅ Positive: a v2 qualifying sidecar serializes the aligned lapKind."""
        # Arrange
        snapshot = self._snapshot_with_laps(
            [self._flying_lap_row("HAM", 1, phase="Q1")],
            session_mode="qualifying",
        )

        # Act
        payload = build_lap_sector_sidecar(snapshot).as_dict(
            include_qualifying_phase=True,
        )

        # Assert
        assert payload["drivers"]["HAM"]["qualifyingPhase"] == ["Q1"]
        assert payload["drivers"]["HAM"]["lapKind"] == ["flying"]

    def test_slow_cooldown_lap_after_flying_stays_unknown(self) -> None:
        """✅ Positive: a later slow cooldown lap in the same phase is unknown
        and never replaces the earlier flying lap.

        Requirement 8: finish is driven by the last *flying* lap in the phase;
        a later cooldown row must not become flying or delay the displayed time.
        """
        # Arrange — HAM completes a quick flying lap, then a slow cooldown lap
        # in the same Q1 phase (well above the 107% quicklap gate).
        snapshot = self._snapshot_with_laps(
            [
                self._flying_lap_row("HAM", 1, phase="Q1", duration=100_000),
                self._flying_lap_row("HAM", 2, phase="Q1", duration=130_000),
            ],
            session_mode="qualifying",
        )

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert — only the quick lap is flying; the cooldown fails closed.
        assert sidecar.drivers["HAM"].lap_number == (1, 2)
        assert sidecar.drivers["HAM"].lap_kind == ("flying", "unknown")

    def test_inlap_after_flying_keeps_earlier_lap_flying(self) -> None:
        """✅ Positive: a later pit-in/inlap never displaces the earlier flying lap."""
        # Arrange — lap 1 is a clean flying lap; lap 2 ends in the pits.
        snapshot = self._snapshot_with_laps(
            [
                self._flying_lap_row("HAM", 1, phase="Q1", duration=100_000),
                self._flying_lap_row(
                    "HAM", 2, phase="Q1", duration=110_000, pit_in_time_ms=190_000,
                ),
            ],
            session_mode="qualifying",
        )

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert — the inlap is never flying and the earlier flying lap survives.
        assert sidecar.drivers["HAM"].lap_kind == ("flying", "inlap")

    def test_incomplete_later_lap_is_dropped_before_kind_derivation(self) -> None:
        """✅ Positive: an incomplete (null-end) later lap cannot participate
        in or delay the flying classification of a causally completed lap."""
        # Arrange — lap 2 has no lap end, so it is not causally completed and
        # must not become a later non-flying "last lap" in the phase.
        snapshot = self._snapshot_with_laps(
            [
                self._flying_lap_row("HAM", 1, phase="Q1", duration=100_000),
                self._flying_lap_row(
                    "HAM", 2, phase="Q1", duration=100_000, lap_end_time_ms=None,
                ),
            ],
            session_mode="qualifying",
        )

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert — only the causally completed lap is retained and it is flying.
        assert sidecar.drivers["HAM"].lap_number == (1,)
        assert sidecar.drivers["HAM"].lap_end_ms == (200_000,)
        assert sidecar.drivers["HAM"].lap_kind == ("flying",)

    def test_phase_boundaries_skip_an_absent_middle_phase(self) -> None:
        """✅ Positive: boundaries reflect only phases with completed laps."""
        # Arrange — HAM has Q1 and Q3 laps but no Q2 lap.
        snapshot = self._snapshot_with_laps(
            [
                self._flying_lap_row("HAM", 1, phase="Q1", duration=100_000),
                self._flying_lap_row("HAM", 2, phase="Q3", duration=95_000),
            ],
            session_mode="qualifying",
        )

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert — boundaries skip Q2 and each phase uses its own quicklap gate.
        assert sidecar.drivers["HAM"].qualifying_phase == ("Q1", "Q3")
        assert sidecar.qualifying_phase_boundaries == (
            BrowserQualifyingPhaseBoundary("Q1", 100_000),
            BrowserQualifyingPhaseBoundary("Q3", 200_000),
        )
        assert sidecar.drivers["HAM"].lap_kind == ("flying", "flying")

    # -- Negative tests ------------------------------------------------------

    def test_rejects_non_tuple_lap_field_from_converted_data(self) -> None:
        """❌ Negative: a driver_lap_sector constructed with a non-tuple
        (through direct construction bypassing derivation) raises TypeError."""
        with pytest.raises(TypeError, match="must be a tuple"):
            BrowserDriverLapSector(
                lap_number=[1],  # type: ignore[arg-type]
                lap_start_ms=(0,),
                lap_end_ms=(100_000,),
                lap_duration_ms=(100_000,),
                sector_1_duration_ms=(30_000,),
                sector_2_duration_ms=(30_000,),
                sector_3_duration_ms=(40_000,),
                sector_1_session_time_ms=(30_000,),
                sector_2_session_time_ms=(60_000,),
                sector_3_session_time_ms=(100_000,),
            )

    def test_omits_canonical_lap_rows_with_null_start_time(self) -> None:
        """❌ Negative: a canonical lap with null start is excluded from the sidecar."""
        # Arrange
        snapshot = self._snapshot_with_laps([
            self._lap_row("HAM", 1, lap_start_time_ms=None),
            self._lap_row("HAM", 2, lap_start_time_ms=200_000),
        ])

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert — only lap 2 is completed and retained
        ham = sidecar.drivers["HAM"]
        assert ham.lap_number == (2,)
        assert ham.lap_start_ms == (200_000,)

    def test_omits_canonical_lap_rows_with_null_end_time(self) -> None:
        """❌ Negative: a canonical lap with null end is excluded from the sidecar."""
        # Arrange
        snapshot = self._snapshot_with_laps([
            self._lap_row("HAM", 1, lap_end_time_ms=None),
            self._lap_row("HAM", 2, lap_end_time_ms=300_000),
        ])

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert — only lap 2 is completed and retained
        ham = sidecar.drivers["HAM"]
        assert ham.lap_number == (2,)
        assert ham.lap_end_ms == (300_000,)

    def test_driver_with_all_null_boundaries_has_empty_tuples(self) -> None:
        """❌ Negative: a driver whose laps are all incomplete produces empty tuples."""
        # Arrange
        snapshot = self._snapshot_with_laps(
            [self._lap_row("HAM", 1, lap_start_time_ms=None, lap_end_time_ms=None)],
        )

        # Act
        sidecar = build_lap_sector_sidecar(snapshot)

        # Assert
        ham = sidecar.drivers["HAM"]
        assert ham.lap_number == ()
        assert ham.lap_start_ms == ()
        assert ham.lap_end_ms == ()
        assert ham.sector_1_duration_ms == ()


# ===========================================================================
# Qualifying incident marker evidence tests
# ===========================================================================


class TestQualifyingIncidentMarkers:
    """Fail-closed derivation of qualifying incident markers from canonical rows."""

    @staticmethod
    def _message(
        time_ms: int = 40_000,
        *,
        category: object = "CarEvent",
        message: str = "CAR 44 CRASH",
        driver_id: object = "HAM",
        lap_number: object = 7,
    ) -> dict[str, object]:
        return {
            "session_id": "2026-03-qualifying",
            "session_time_ms": time_ms,
            "message_index": 0,
            "category": category,
            "flag": None,
            "scope": None,
            "message": message,
            "driver_id": driver_id,
            "lap_number": lap_number,
        }

    def test_parses_terminal_car_event_with_canonical_identity_and_time(self) -> None:
        """✅ Positive: CarEvent + CRASH + canonical driver + causal time => marker."""
        # Arrange & Act
        markers = parse_qualifying_incident_markers([self._message()])

        # Assert
        assert len(markers) == 1
        marker = markers[0]
        assert marker.driver_id == "HAM"
        assert marker.time_ms == 40_000
        assert marker.source == "race-control-car-event"
        assert marker.raw_message == "CAR 44 CRASH"
        assert marker.lap_number == 7

    def test_resolves_driver_from_message_when_canonical_id_missing(self) -> None:
        """✅ Positive: CAR <number> resolves through driver metadata aliases."""
        # Arrange — the canonical row has no driver_id; the text carries CAR 1.
        markers = parse_qualifying_incident_markers(
            [self._message(driver_id=None, message="CAR 1 STOPS", lap_number=9)],
            {"44": "HAM", "1": "VER"},
        )

        # Assert
        assert len(markers) == 1
        assert markers[0].driver_id == "VER"
        assert markers[0].raw_message == "CAR 1 STOPS"

    def test_ignores_non_car_event_categories(self) -> None:
        """✅ Positive: Flag/Other rows are never per-driver incident evidence."""
        # Arrange & Act
        markers = parse_qualifying_incident_markers([
            self._message(category="Flag", message="YELLOW FLAG", driver_id=None),
            self._message(category="Other", message="CAR 44 CRASH", driver_id="HAM"),
        ])

        # Assert
        assert markers == ()

    def test_ignores_non_terminal_car_events(self) -> None:
        """✅ Positive: a CarEvent without a terminal form is omitted silently."""
        # Arrange — OFF TRACK is not proof of an incident (OffTrack backfill).
        markers = parse_qualifying_incident_markers([
            self._message(message="CAR 44 OFF TRACK"),
            self._message(time_ms=50_000, message="CAR 44 PIT STOP"),
        ])

        # Assert
        assert markers == ()

    def test_recognizes_all_terminal_forms(self) -> None:
        """✅ Positive: CRASH/STOPS/STOPPED/RETIRED/STALLED are recognized."""
        # Arrange & Act
        markers = parse_qualifying_incident_markers([
            self._message(time_ms=10_000, message="CAR 44 CRASH"),
            self._message(time_ms=20_000, message="CAR 44 STOPS"),
            self._message(time_ms=30_000, message="CAR 44 STOPPED"),
            self._message(time_ms=40_000, message="CAR 44 RETIRED"),
            self._message(time_ms=50_000, message="CAR 44 STALLED"),
        ])

        # Assert
        assert [marker.time_ms for marker in markers] == [10_000, 20_000, 30_000, 40_000, 50_000]

    def test_sorts_markers_deterministically(self) -> None:
        """✅ Positive: markers order by timeMs, then driverId, then rawMessage."""
        # Arrange — deliberately out of order with a same-time tie.
        markers = parse_qualifying_incident_markers([
            self._message(time_ms=30_000, driver_id="VER", message="CAR 1 STOPS"),
            self._message(time_ms=10_000, driver_id="HAM", message="CAR 44 CRASH"),
            self._message(time_ms=30_000, driver_id="HAM", message="CAR 44 STALLED"),
        ])

        # Assert
        assert [(marker.time_ms, marker.driver_id, marker.raw_message) for marker in markers] == [
            (10_000, "HAM", "CAR 44 CRASH"),
            (30_000, "HAM", "CAR 44 STALLED"),
            (30_000, "VER", "CAR 1 STOPS"),
        ]

    def test_fails_closed_on_ambiguous_driver(self) -> None:
        """❌ Negative: a terminal form without a resolvable driver raises."""
        with pytest.raises(QualifyingIncidentEvidenceError, match="ambiguous or missing driver"):
            parse_qualifying_incident_markers([
                self._message(driver_id=None, message="A CAR CRASH"),
            ])

    def test_fails_closed_on_contradictory_identity(self) -> None:
        """❌ Negative: canonical driver and text disagreement raise, never guess."""
        # Arrange — canonical driver_id is HAM but the text says CAR 1 (VER).
        with pytest.raises(QualifyingIncidentEvidenceError, match="ambiguous or missing driver"):
            parse_qualifying_incident_markers(
                [self._message(driver_id="HAM", message="CAR 1 STOPS")],
                {"44": "HAM", "1": "VER"},
            )

    def test_fails_closed_on_missing_timestamp(self) -> None:
        """❌ Negative: a terminal form without a canonical timestamp raises."""
        # Arrange & Act & Assert
        with pytest.raises(QualifyingIncidentEvidenceError, match="no canonical timestamp"):
            parse_qualifying_incident_markers([
                {**self._message(), "session_time_ms": None},
            ])

    def test_never_derives_markers_from_position_status(self) -> None:
        """✅ Positive: OffTrack status evidence cannot fabricate a marker."""
        # Arrange — a position-style OffTrack record is not race-control evidence.
        markers = parse_qualifying_incident_markers([
            self._message(category=None, message="OffTrack", driver_id="HAM"),
        ])

        # Assert — no marker, and nothing in the parser consults position data.
        assert markers == ()

    def test_qualifying_incident_fixture_normalizes_to_markers(self) -> None:
        """✅ Positive: the incident fixture yields deterministic markers."""
        # Arrange — normalize the fixture's race-control stream through the
        # canonical adapter, then derive markers from canonical evidence.
        from fixtures.fake_fastf1_session import build_qualifying_session_with_incidents
        from f1_replay_pipeline.adapters.fastf1.messages_results import (
            adapt_race_control_messages,
        )

        session = build_qualifying_session_with_incidents()
        messages = adapt_race_control_messages(
            session, {"44": "HAM", "1": "VER"}, "2026-03-qualifying",
        )

        # Act
        markers = parse_qualifying_incident_markers(
            messages, {"44": "HAM", "1": "VER"},
        )

        # Assert — CRASH and STOPS are actionable; Flag and OFF TRACK are not.
        assert [(marker.driver_id, marker.time_ms, marker.raw_message) for marker in markers] == [
            ("HAM", 40_000, "CAR 44 CRASH"),
            ("VER", 50_000, "CAR 1 STOPS"),
        ]
        assert markers[0].lap_number == 7
        assert markers[1].lap_number == 9


# ===========================================================================
# Qualifying timeline derivation tests
# ===========================================================================


class TestBuildQualifyingTimeline:
    """Derivation of the optional qualifying-safe timeline artifact."""

    @staticmethod
    def _message_row(
        time_ms: int, message: str, *, driver_id: object = "HAM", lap_number: object = 7,
    ) -> dict[str, object]:
        return {
            "session_id": "test-race",
            "session_time_ms": time_ms,
            "message_index": 0,
            "category": "CarEvent",
            "flag": None,
            "scope": None,
            "message": message,
            "driver_id": driver_id,
            "lap_number": lap_number,
        }

    @staticmethod
    def _status_row(
        start_ms: int, end_ms: object, status: str, message: object = None,
    ) -> dict[str, object]:
        return {
            "session_id": "test-race",
            "start_time_ms": start_ms,
            "end_time_ms": end_ms,
            "status": status,
            "message": message,
        }

    def test_derives_intervals_and_incident_markers(self) -> None:
        """✅ Positive: yellow/red intervals and CarEvent markers are derived."""
        # Arrange
        snapshot = TestBuildLapSectorSidecar._snapshot_with_laps(
            [TestBuildLapSectorSidecar._flying_lap_row("HAM", 1, phase="Q1")],
            session_mode="qualifying",
            track_status_data=[
                self._status_row(10_000, 20_000, "2"),   # yellow
                self._status_row(30_000, None, "5"),     # red to window end
            ],
            messages_data=[
                self._message_row(15_000, "CAR 44 CRASH"),
            ],
        )

        # Act
        timeline = build_qualifying_timeline(snapshot, 0, 400_000)

        # Assert
        assert timeline is not None
        assert timeline.start_ms == 0
        assert timeline.end_ms == 400_000
        assert [interval.kind for interval in timeline.intervals] == ["yellow", "red"]
        assert timeline.intervals[0] == BrowserQualifyingTimelineInterval("yellow", 10_000, 20_000)
        assert timeline.intervals[1] == BrowserQualifyingTimelineInterval("red", 30_000, 400_000)
        assert timeline.incident_markers == (
            BrowserQualifyingIncidentMarker("HAM", 15_000, "CAR 44 CRASH", lap_number=7),
        )

    def test_merges_adjacent_same_kind_intervals(self) -> None:
        """✅ Positive: adjacent same-kind intervals merge into one interval."""
        # Arrange
        snapshot = TestBuildLapSectorSidecar._snapshot_with_laps(
            [TestBuildLapSectorSidecar._flying_lap_row("HAM", 1, phase="Q1")],
            session_mode="qualifying",
            track_status_data=[
                self._status_row(10_000, 15_000, "2"),
                self._status_row(15_000, 20_000, "2"),
            ],
        )

        # Act
        timeline = build_qualifying_timeline(snapshot, 0, 400_000)

        # Assert
        assert timeline is not None
        assert timeline.intervals == (
            BrowserQualifyingTimelineInterval("yellow", 10_000, 20_000),
        )

    def test_clips_intervals_and_markers_to_window(self) -> None:
        """✅ Positive: out-of-window evidence is dropped from the artifact."""
        # Arrange — a red interval and a marker both outside [0, 400_000).
        snapshot = TestBuildLapSectorSidecar._snapshot_with_laps(
            [TestBuildLapSectorSidecar._flying_lap_row("HAM", 1, phase="Q1")],
            session_mode="qualifying",
            track_status_data=[
                self._status_row(450_000, None, "5"),
            ],
            messages_data=[
                self._message_row(500_000, "CAR 44 CRASH"),
            ],
        )

        # Act
        timeline = build_qualifying_timeline(snapshot, 0, 400_000)

        # Assert
        assert timeline is None

    def test_omitted_for_non_qualifying_sessions(self) -> None:
        """✅ Positive: race-shaped sessions never carry the artifact."""
        # Arrange
        snapshot = TestBuildLapSectorSidecar._snapshot_with_laps(
            [TestBuildLapSectorSidecar._flying_lap_row("HAM", 1, phase=None)],
            session_mode="race",
            track_status_data=[self._status_row(10_000, 20_000, "2")],
        )

        # Act
        timeline = build_qualifying_timeline(snapshot, 0, 400_000)

        # Assert
        assert timeline is None

    def test_omitted_when_no_actionable_evidence(self) -> None:
        """✅ Positive: qualifying with no intervals and no markers omits the artifact."""
        # Arrange
        snapshot = TestBuildLapSectorSidecar._snapshot_with_laps(
            [TestBuildLapSectorSidecar._flying_lap_row("HAM", 1, phase="Q1")],
            session_mode="qualifying",
        )

        # Act
        timeline = build_qualifying_timeline(snapshot, 0, 400_000)

        # Assert
        assert timeline is None

    def test_ignores_sc_vsc_status_for_intervals(self) -> None:
        """✅ Positive: SC/VSC status codes are not exposed as qualifying intervals."""
        # Arrange — only yellow (2) and red (5) map to intervals.
        snapshot = TestBuildLapSectorSidecar._snapshot_with_laps(
            [TestBuildLapSectorSidecar._flying_lap_row("HAM", 1, phase="Q1")],
            session_mode="qualifying",
            track_status_data=[
                self._status_row(10_000, 20_000, "4"),   # SC
                self._status_row(20_000, 30_000, "6"),   # VSC
            ],
        )

        # Act
        timeline = build_qualifying_timeline(snapshot, 0, 400_000)

        # Assert
        assert timeline is None

    def test_rejects_invalid_window(self) -> None:
        """❌ Negative: a reversed or empty replay window raises ValueError."""
        # Arrange
        snapshot = TestBuildLapSectorSidecar._snapshot_with_laps(
            [TestBuildLapSectorSidecar._flying_lap_row("HAM", 1, phase="Q1")],
            session_mode="qualifying",
        )

        # Act & Assert
        with pytest.raises(ValueError, match="non-empty interval"):
            build_qualifying_timeline(snapshot, 400_000, 400_000)


# ===========================================================================
# Publication tests
# ===========================================================================


class TestSidecarPublication:
    """Integration coverage for sidecar artifact publication and validation."""

    def test_sidecar_artifact_written_to_generation_directory(self, tmp_path: Path) -> None:
        """✅ Positive: sidecar artifact is written to the generation directory."""
        # Arrange & Act
        result = _publish(tmp_path / "browser", sidecar=_sidecar())

        # Assert
        assert result.lap_sector_sidecar_path is not None
        assert result.lap_sector_sidecar_path.exists()
        assert result.lap_sector_sidecar_path.name == "lap-sector-sidecar.json"
        assert "lap-sector-sidecar.json" in result.artifact_digests

    def test_manifest_references_sidecar_with_correct_path(self, tmp_path: Path) -> None:
        """✅ Positive: manifest lapSectorSidecar path is exactly 'lap-sector-sidecar.json'."""
        # Arrange & Act
        result = _publish(tmp_path / "browser", sidecar=_sidecar())
        manifest = _load_json(result.manifest_path)

        # Assert
        assert "lapSectorSidecar" in manifest
        assert manifest["lapSectorSidecar"]["path"] == "lap-sector-sidecar.json"
        assert manifest["lapSectorSidecar"]["schemaId"] == BROWSER_LAP_SECTOR_SIDECAR_SCHEMA_ID

    def test_sidecar_sha256_matches_stored_bytes(self, tmp_path: Path) -> None:
        """✅ Positive: SHA-256 in manifest matches the digested sidecar bytes."""
        # Arrange & Act
        result = _publish(tmp_path / "browser", sidecar=_sidecar())
        manifest = _load_json(result.manifest_path)

        # Assert
        stored_digest = hashlib.sha256(
            result.lap_sector_sidecar_path.read_bytes()  # type: ignore[union-attr]
        ).hexdigest()
        assert manifest["lapSectorSidecar"]["sha256"] == stored_digest
        assert result.artifact_digests["lap-sector-sidecar.json"] == stored_digest

    def test_publication_sidecar_digest_in_manifest_matches_result(self, tmp_path: Path) -> None:
        """✅ Positive: PublishedBrowserDelivery artifact_digests agrees with manifest."""
        # Arrange & Act
        result = _publish(tmp_path / "browser", sidecar=_sidecar())
        manifest = _load_json(result.manifest_path)

        # Assert
        assert result.artifact_digests["lap-sector-sidecar.json"] == manifest["lapSectorSidecar"]["sha256"]

    def test_mutated_sidecar_rejected_on_validate(self, tmp_path: Path) -> None:
        """❌ Negative: tampering with stored sidecar bytes fails validation."""
        # Arrange
        result = _publish(tmp_path / "browser", sidecar=_sidecar())
        sidecar_path = result.lap_sector_sidecar_path
        assert sidecar_path is not None

        # Mutate one byte
        original = sidecar_path.read_bytes()
        mutated = original.replace(b'"HAM"', b'"MUT"')
        sidecar_path.write_bytes(mutated)

        # Act & Assert
        with pytest.raises(BrowserDeliveryPublicationError, match="browser delivery validation failed"):
            validate_complete_browser_delivery(
                tmp_path / "browser",
                expected_generation_id="test-gen",
                expected_manifest_sha256="a" * 64,
                schema_root=SCHEMA_ROOT,
            )

    def test_mutated_sidecar_sha256_mismatch_in_digest_check(self, tmp_path: Path) -> None:
        """❌ Negative: detection through explicit digest disagreement."""
        # Arrange — publish, then mutate
        result = _publish(tmp_path / "browser", sidecar=_sidecar())
        sidecar_path = result.lap_sector_sidecar_path
        assert sidecar_path is not None
        sidecar_path.write_bytes(
            sidecar_path.read_bytes().replace(b'"lapNumber":', b'"lapNumberX":')
        )

        # Act & Assert — manifest-read checksum disagrees with stored bytes
        with pytest.raises(BrowserDeliveryPublicationError, match="browser delivery validation failed"):
            validate_complete_browser_delivery(
                tmp_path / "browser",
                expected_generation_id="test-gen",
                expected_manifest_sha256="a" * 64,
                schema_root=SCHEMA_ROOT,
            )

    def test_backward_compatible_without_sidecar(self, tmp_path: Path) -> None:
        """✅ Positive: a delivery without a sidecar publishes and validates."""
        # Arrange & Act
        result = publish_browser_delivery(
            browser_parent=tmp_path / "browser",
            delivery_version="legacy-delivery",
            delivery=_delivery(sidecar=None),
            schema_root=SCHEMA_ROOT,
        )

        # Assert
        assert result.lap_sector_sidecar_path is None
        assert "lap-sector-sidecar.json" not in result.artifact_digests
        manifest = _load_json(result.manifest_path)
        assert "lapSectorSidecar" not in manifest

        # Full validation also passes
        validate_complete_browser_delivery(
            tmp_path / "browser",
            expected_generation_id="test-gen",
            expected_manifest_sha256="a" * 64,
            schema_root=SCHEMA_ROOT,
        )

    def test_sidecar_driver_ids_match_manifest_drivers(self, tmp_path: Path) -> None:
        """✅ Positive: every driver in the sidecar equals manifest driver set.

        The sidecar contract cross-check happens during publication. This test
        exercises the full publish path with a multi-driver chunk so that the
        sidecar driver set is validated against the manifest driver set.
        """
        # Arrange
        sidecar = BrowserLapSectorSidecar(
            "test-race",
            {"HAM": _empty_driver(), "VER": _empty_driver()},
        )
        manifest_drivers = (
            {"id": "HAM", "displayName": "H", "teamName": "T", "colorHex": "#000000", "carNumber": "44"},
            {"id": "VER", "displayName": "V", "teamName": "T", "colorHex": "#111111", "carNumber": "1"},
        )
        # Build a two-driver chunk so chunk validation passes
        fields_ham = BrowserDriverFields(
            "HAM", (0, 1000),
            (1.0, 2.0), (3.0, 4.0), (5.0, 6.0), (7.0, 8.0),
            (0, 1), (None, 7), (None, 1),
            ("OnTrack", "OnTrack"), (1, 1), ("SOFT", "SOFT"),
            (False, False), (None, None), (None, None), (None, None),
        )
        fields_ver = BrowserDriverFields(
            "VER", (0, 1000),
            (10.0, 20.0), (30.0, 40.0), (50.0, 60.0), (70.0, 80.0),
            (0, 1), (None, 7), (None, 1),
            ("OnTrack", "OnTrack"), (1, 1), ("MEDIUM", "MEDIUM"),
            (False, False), (None, None), (None, None), (None, None),
        )
        chunk = BrowserChunk(
            "chunk-001", 1, 0, 2000,
            BrowserOverlap("none", None, None, None, None),
            (0, 1000), 0, {"HAM": fields_ham, "VER": fields_ver},
            (("HAM", "VER"), ("HAM", "VER")), (1, 1), ("clear", "clear"),
            (),
        )

        delivery = BrowserDeliveryBuild(
            _snapshot(),
            BrowserManifest(
                "test-race", "Test Race", manifest_drivers, session_mode="race",
            ),
            _track_assets(),
            (chunk,),
            lap_sector_sidecar=sidecar,
        )

        # Act — publish and cross-check via full pipeline
        result = publish_browser_delivery(
            browser_parent=tmp_path / "browser",
            delivery_version="delivery-v2",
            delivery=delivery,
            schema_root=SCHEMA_ROOT,
        )

        # Assert — sidecar is in manifest and references the artifact
        manifest = _load_json(result.manifest_path)
        assert "lapSectorSidecar" in manifest
        assert manifest["lapSectorSidecar"]["path"] == "lap-sector-sidecar.json"
        assert result.lap_sector_sidecar_path is not None

    def test_sidecar_contract_rejects_fixture_id_mismatch(self, tmp_path: Path) -> None:
        """❌ Negative: fixture_id in sidecar != manifest fixture_id raises error."""
        # Arrange
        sidecar = _sidecar(fixture_id="other-race")  # mismatch
        delivery = _delivery(sidecar=sidecar)          # manifest uses "test-race"

        # Act & Assert
        with pytest.raises(BrowserDeliveryPublicationError, match="disagrees with the manifest"):
            publish_browser_delivery(
                browser_parent=tmp_path / "browser",
                delivery_version="delivery-one",
                delivery=delivery,
                schema_root=SCHEMA_ROOT,
            )

    def test_sidecar_contract_rejects_driver_id_mismatch(self, tmp_path: Path) -> None:
        """❌ Negative: sidecar has extra/missing driver vs manifest raises error."""
        # Arrange — sidecar has VER but manifest only has HAM
        sidecar = BrowserLapSectorSidecar(
            "test-race",
            {
                "HAM": _empty_driver(),
                "VER": _empty_driver(),
            },
        )
        delivery = _delivery(sidecar=sidecar)  # manifest default has only HAM

        # Act & Assert
        with pytest.raises(BrowserDeliveryPublicationError, match="drivers disagree with the manifest"):
            publish_browser_delivery(
                browser_parent=tmp_path / "browser",
                delivery_version="delivery-one",
                delivery=delivery,
                schema_root=SCHEMA_ROOT,
            )

    def test_sidecar_contract_rejects_missing_driver_in_manifest(self, tmp_path: Path) -> None:
        """❌ Negative: manifest has extra driver not in sidecar raises error."""
        # Arrange — manifest has HAM + VER, sidecar only has HAM
        manifest_drivers = (
            {"id": "HAM", "displayName": "Hamilton", "teamName": "Team", "colorHex": "#000000", "carNumber": "44"},
            {"id": "VER", "displayName": "Verstappen", "teamName": "Team", "colorHex": "#111111", "carNumber": "1"},
        )
        delivery = BrowserDeliveryBuild(
            _snapshot(),
            BrowserManifest(
                "test-race", "Test Race", manifest_drivers, session_mode="race",
            ),
            _track_assets(),
            (_chunk(),),
            lap_sector_sidecar=_sidecar(),  # only HAM
        )

        # Act & Assert
        with pytest.raises(BrowserDeliveryPublicationError, match="drivers disagree with the manifest"):
            publish_browser_delivery(
                browser_parent=tmp_path / "browser",
                delivery_version="delivery-one",
                delivery=delivery,
                schema_root=SCHEMA_ROOT,
            )

    def test_identical_sidecar_bytes_across_publications(self, tmp_path: Path) -> None:
        """✅ Positive: same sidecar produces byte-identical artifacts twice."""
        # Arrange
        sidecar = _sidecar()

        # Act
        first = _publish(tmp_path / "browser-one", sidecar=sidecar)
        second = _publish(tmp_path / "browser-two", sidecar=sidecar)

        # Assert
        assert first.lap_sector_sidecar_path is not None
        assert second.lap_sector_sidecar_path is not None
        assert first.lap_sector_sidecar_path.read_bytes() == second.lap_sector_sidecar_path.read_bytes()

    def test_complete_validator_covers_sidecar_validation(self, tmp_path: Path) -> None:
        """✅ Positive: validate_complete_browser_delivery processes sidecar ref."""
        # Arrange
        result = _publish(tmp_path / "browser", sidecar=_sidecar())

        # Act
        validate_complete_browser_delivery(
            tmp_path / "browser",
            expected_generation_id="test-gen",
            expected_manifest_sha256="a" * 64,
            schema_root=SCHEMA_ROOT,
        )

        # Assert — no exception raised; validation completed with sidecar
        assert result.lap_sector_sidecar_path is not None
        assert result.lap_sector_sidecar_path.exists()

    def test_full_validation_with_sidecar_passes_schema_checks(self, tmp_path: Path) -> None:
        """✅ Positive: schema validation accepts a manifest with lapSectorSidecar."""
        # Arrange & Act
        result = _publish(tmp_path / "browser", sidecar=_sidecar())
        manifest = _load_json(result.manifest_path)

        # Assert — manifest schema validates with lapSectorSidecar reference
        manifest_schema = _load_json(SCHEMA_ROOT / "manifest.schema.json")
        schemas = {
            name: _load_json(SCHEMA_ROOT / f"{name}.schema.json")
            for name in ("manifest", "chunk", "track-assets", "timeline-summary",
                         "browser-lap-sector-sidecar", "pit-loss-model")
        }
        registry = Registry()
        for schema in schemas.values():
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        Draft202012Validator(
            manifest_schema, registry=registry,
            format_checker=Draft202012Validator.FORMAT_CHECKER,
        ).validate(manifest)

        # Verify the reference shape
        assert manifest["lapSectorSidecar"] == {
            "path": "lap-sector-sidecar.json",
            "schemaId": BROWSER_LAP_SECTOR_SIDECAR_SCHEMA_ID,
            "sha256": hashlib.sha256(
                result.lap_sector_sidecar_path.read_bytes()  # type: ignore[union-attr]
            ).hexdigest(),
        }

    def test_manifest_schema_validates_without_sidecar(self, tmp_path: Path) -> None:
        """✅ Positive: manifest without lapSectorSidecar is also schema-valid."""
        # Arrange & Act
        result = publish_browser_delivery(
            browser_parent=tmp_path / "browser",
            delivery_version="delivery-one",
            delivery=_delivery(sidecar=None),
            schema_root=SCHEMA_ROOT,
        )
        manifest = _load_json(result.manifest_path)

        # Assert — manifest schema validates without the optional field
        manifest_schema = _load_json(SCHEMA_ROOT / "manifest.schema.json")
        schemas = {
            name: _load_json(SCHEMA_ROOT / f"{name}.schema.json")
            for name in ("manifest", "chunk", "track-assets", "timeline-summary",
                         "browser-lap-sector-sidecar", "pit-loss-model")
        }
        registry = Registry()
        for schema in schemas.values():
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        Draft202012Validator(
            manifest_schema, registry=registry,
            format_checker=Draft202012Validator.FORMAT_CHECKER,
        ).validate(manifest)
        assert "lapSectorSidecar" not in manifest

    def test_v2_publication_emits_contract_v2_lap_sector_sidecar(self, tmp_path: Path) -> None:
        """✅ Positive: a v2 practice delivery publishes a v2 sidecar artifact."""
        # Arrange & Act
        result = publish_browser_delivery(
            browser_parent=tmp_path / "browser",
            delivery_version="delivery-v2",
            delivery=_v2_practice_delivery(_sidecar()),
            schema_root=SCHEMA_ROOT_V2,
            contract_version="v2",
        )
        manifest = _load_json(result.manifest_path)
        sidecar = _load_json(result.lap_sector_sidecar_path)  # type: ignore[arg-type]

        # Assert — v2 identity, schema registry entry, and aligned payload
        assert manifest["sessionMode"] == "practice"
        assert manifest["lapSectorSidecar"]["schemaId"].endswith(":v2:browser-lap-sector-sidecar")
        assert manifest["schemas"]["lapSectorSidecar"].endswith(":v2:browser-lap-sector-sidecar")
        assert sidecar["contractVersion"] == "v2"
        assert sidecar["fixtureId"] == "test-race"
        assert sidecar["drivers"]["HAM"]["lapNumber"] == [1]
        assert sidecar["drivers"]["HAM"]["qualifyingPhase"] == [None]
        assert sidecar["phaseBoundaries"] == []
        validate_complete_browser_delivery(
            tmp_path / "browser",
            expected_generation_id="test-gen",
            expected_manifest_sha256="a" * 64,
            schema_root=SCHEMA_ROOT_V2,
        )

    def test_sidecar_artifact_validates_against_sidecar_schema(self, tmp_path: Path) -> None:
        """✅ Positive: sidecar JSON passes its own schema validation."""
        # Arrange & Act
        result = _publish(tmp_path / "browser", sidecar=_sidecar())
        sidecar_json = _load_json(result.lap_sector_sidecar_path)  # type: ignore[arg-type]

        # Assert — validate against the sidecar schema
        sidecar_schema = _load_json(SCHEMA_ROOT / "browser-lap-sector-sidecar.schema.json")
        schemas = {
            name: _load_json(SCHEMA_ROOT / f"{name}.schema.json")
            for name in ("browser-lap-sector-sidecar",)
        }
        registry = Registry()
        for schema in schemas.values():
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        Draft202012Validator(
            sidecar_schema, registry=registry,
            format_checker=Draft202012Validator.FORMAT_CHECKER,
        ).validate(sidecar_json)

        # Verify key contract fields
        assert sidecar_json["contractVersion"] == "v2"
        assert sidecar_json["fixtureId"] == "test-race"
        assert "drivers" in sidecar_json

    def test_validator_rejects_schema_invalid_sidecar(self, tmp_path: Path) -> None:
        """❌ Negative: schema-invalid sidecar content fails validation.

        Mutates the published JSON to remove a required field, then re-runs
        the complete validator. The revalidation must detect the schema breach.
        """
        # Arrange
        result = _publish(tmp_path / "browser", sidecar=_sidecar())
        sidecar_path = result.lap_sector_sidecar_path
        assert sidecar_path is not None

        # Remove required 'contractVersion' field
        sidecar = _load_json(sidecar_path)
        del sidecar["contractVersion"]
        new_bytes = json.dumps(sidecar, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        sidecar_path.write_bytes(new_bytes)

        # Update manifest SHA to match mutated sidecar
        manifest = _load_json(result.manifest_path)
        new_sha = hashlib.sha256(new_bytes).hexdigest()
        manifest["lapSectorSidecar"]["sha256"] = new_sha
        manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        result.manifest_path.write_bytes(manifest_bytes)

        # Update pointer
        pointer = _load_json(result.pointer_path)
        pointer["manifestSha256"] = hashlib.sha256(manifest_bytes).hexdigest()
        result.pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

        # Act & Assert
        with pytest.raises(BrowserDeliveryPublicationError, match="validation failed"):
            validate_complete_browser_delivery(
                tmp_path / "browser",
                expected_generation_id="test-gen",
                expected_manifest_sha256="a" * 64,
                schema_root=SCHEMA_ROOT,
            )

    def test_publication_omits_incomplete_lap_rows(self, tmp_path: Path) -> None:
        """❌ Negative: laps with null start/end are not written to the sidecar artifact."""
        # Arrange
        snapshot = TestBuildLapSectorSidecar._snapshot_with_laps([
            TestBuildLapSectorSidecar._lap_row("HAM", 1, lap_start_time_ms=None),
            TestBuildLapSectorSidecar._lap_row("HAM", 2, lap_start_time_ms=200_000, lap_end_time_ms=300_000),
        ])
        delivery = _delivery(
            sidecar=build_lap_sector_sidecar(snapshot),
        )

        # Act
        result = publish_browser_delivery(
            browser_parent=tmp_path / "browser",
            delivery_version="delivery-one",
            delivery=delivery,
            schema_root=SCHEMA_ROOT,
        )

        # Assert
        sidecar_json = _load_json(result.lap_sector_sidecar_path)  # type: ignore[arg-type]
        assert sidecar_json["drivers"]["HAM"]["lapNumber"] == [2]
        assert sidecar_json["drivers"]["HAM"]["lapStartMs"] == [200_000]
        assert sidecar_json["drivers"]["HAM"]["lapEndMs"] == [300_000]

    def test_full_regression_with_sidecar_delivery(self, tmp_path: Path) -> None:
        """✅ Positive: full build → publish → validate round-trip passes."""
        # Arrange — use the real canonical pipeline to create a complete
        # generation with sector data on laps, then derive and publish.
        canonical_parent = tmp_path / "canonical"
        frames = _canonical_frames_with_sectors()
        published_canonical = publish_canonical_generation(
            frames=frames, target_parent=canonical_parent,
            generation_id="canonical-v2",
        )

        from f1_replay_pipeline.delivery.browser.browser_delivery_orchestration import (
            build_browser_delivery,
        )

        snapshot = read_validated_canonical_generation(canonical_parent)
        # Track assets must match the canonical session_id
        assets = _track_assets()
        assets["fixtureId"] = "synthetic-race"
        delivery = build_browser_delivery(snapshot, assets)

        # Act
        result = publish_browser_delivery(
            browser_parent=tmp_path / "browser",
            delivery_version="delivery-v2",
            delivery=delivery,
            schema_root=SCHEMA_ROOT,
        )

        # Assert — sidecar must be present and referenced
        assert result.lap_sector_sidecar_path is not None
        assert result.lap_sector_sidecar_path.exists()
        manifest = _load_json(result.manifest_path)
        assert "lapSectorSidecar" in manifest
        assert manifest["lapSectorSidecar"]["path"] == "lap-sector-sidecar.json"
        assert manifest["lapSectorSidecar"]["schemaId"] == BROWSER_LAP_SECTOR_SIDECAR_SCHEMA_ID

        # Verify sidecar content from real pipeline has expected shape
        sidecar = _load_json(result.lap_sector_sidecar_path)
        assert sidecar["contractVersion"] == "v2"
        assert sidecar["fixtureId"] == "synthetic-race"
        assert "HAM" in sidecar["drivers"]
        assert "lapNumber" in sidecar["drivers"]["HAM"]
        assert "sector1DurationMs" in sidecar["drivers"]["HAM"]
        assert "sector1SessionTimeMs" in sidecar["drivers"]["HAM"]

        # Full validation — use the actual canonical manifest SHA
        validate_complete_browser_delivery(
            tmp_path / "browser",
            expected_generation_id="canonical-v2",
            expected_manifest_sha256=published_canonical.manifest_sha256,
            schema_root=SCHEMA_ROOT,
        )


# ===========================================================================
# Helpers
# ===========================================================================


def _empty_driver() -> BrowserDriverLapSector:
    """Return a BrowserDriverLapSector with all-empty tuples."""
    return BrowserDriverLapSector(
        lap_number=(),
        lap_start_ms=(),
        lap_end_ms=(),
        lap_duration_ms=(),
        sector_1_duration_ms=(),
        sector_2_duration_ms=(),
        sector_3_duration_ms=(),
        sector_1_session_time_ms=(),
        sector_2_session_time_ms=(),
        sector_3_session_time_ms=(),
    )


def _canonical_row(table: str, **changes: object) -> dict[str, object]:
    """Build one canonical row with all schema fields defaulted to None."""
    row: dict[str, object] = {column: None for column in CANONICAL_TABLE_SCHEMAS_V2[table]}
    row.update(
        {
            "session_id": "synthetic-race",
            "driver_id": "HAM",
        }
    )
    row.update(
        {
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
            "car_telemetry": {
                "source_driver_key": "44", "session_time_ms": 0, "source": "car",
            },
            "position_telemetry": {
                "source_driver_key": "44", "session_time_ms": 0, "source": "pos",
            },
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
        }.get(table, {})
    )
    row.update(changes)
    return row


def _canonical_frames_with_sectors() -> dict[str, pl.DataFrame]:
    """Return a full canonical frame set with sector data on laps."""
    rows: dict[str, list[dict[str, object]]] = {
        name: [_canonical_row(name)] for name in CANONICAL_PARQUET_TABLE_NAMES
    }

    rows["car_telemetry"] = [
        _canonical_row("car_telemetry", session_time_ms=time, speed_kph=speed,
                       throttle_pct=throttle, brake=brake, gear=gear, drs=drs)
        for time, speed, throttle, brake, gear, drs in (
            (0, 100.0, 50.0, False, 4, 0),
            (10_000, 200.0, 70.0, True, 6, 1),
            (11_000, 210.0, 75.0, False, 7, 1),
            (20_000, 220.0, 80.0, None, None, None),
        )
    ]

    rows["position_telemetry"] = [
        _canonical_row("position_telemetry", session_time_ms=time, x=x, y=y, status=status)
        for time, x, y, status in (
            (0, 0.0, 1.0, "OnTrack"),
            (9_500, None, 9.5, None),
            (10_000, 10.0, 11.0, "OnTrack"),
            (19_999, 20.0, None, "OnTrack"),
        )
    ]

    rows["weather"] = [
        _canonical_row("weather", session_time_ms=0, rainfall=False),
        _canonical_row("weather", session_time_ms=6_000, rainfall=True),
    ]

    rows["track_status_intervals"] = [
        _canonical_row("track_status_intervals", start_time_ms=0, end_time_ms=5_000, status="1"),
        _canonical_row("track_status_intervals", start_time_ms=5_000, end_time_ms=None, status="4"),
    ]

    rows["race_control_messages"] = [
        _canonical_row("race_control_messages", session_time_ms=10_000,
                       message_index=0, message="boundary event", driver_id="HAM"),
    ]

    # Key: laps with sector data
    rows["laps"] = [
        _canonical_row(
            "laps", driver_id="HAM", lap_number=1, lap_start_time_ms=0,
            lap_end_time_ms=20_001, compound="MEDIUM",
            sector_1_duration_ms=30_000, sector_2_duration_ms=30_000,
            sector_3_duration_ms=35_000, sector_1_session_time_ms=30_000,
            sector_2_session_time_ms=60_000, sector_3_session_time_ms=95_000,
        ),
    ]

    return {
        name: pl.DataFrame(value, schema=dict(CANONICAL_TABLE_SCHEMAS_V2[name]), strict=True)
        for name, value in rows.items()
    }
