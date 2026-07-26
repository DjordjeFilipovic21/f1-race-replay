"""Behavior-focused tests for BrowserLapSectorSidecar derivation and publication.

Covers:
  - BrowserDriverLapSector model validation
  - BrowserLapSectorSidecar model validation
  - BrowserLapSectorSidecarReference contract enforcement
  - build_lap_sector_sidecar derivation (determinism, null preservation,
    ordering, zero-lap drivers, causal pairing)
  - Publication (artifact writing, SHA-256, schema validation,
    mutation rejection, backward compatibility, cross-checks)
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
    CanonicalGenerationSnapshot,
    MAX_INT64,
)
from f1_replay_pipeline.delivery.browser.browser_lap_sector_sidecar import (
    build_lap_sector_sidecar,
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
from f1_replay_pipeline.domain.canonical_schema import CANONICAL_TABLE_SCHEMAS
from f1_replay_pipeline.storage.canonical_writer import publish_canonical_generation
from f1_replay_pipeline.storage.parquet_io import CANONICAL_PARQUET_TABLE_NAMES


REPO_ROOT = Path(__file__).resolve().parents[4]
CONTRACT_ROOT = REPO_ROOT / "contracts" / "replay-data" / "v1"
SCHEMA_ROOT = CONTRACT_ROOT / "schemas"
FIXTURE_ROOT = CONTRACT_ROOT / "fixtures" / "deterministic-race"

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _snapshot() -> CanonicalGenerationSnapshot:
    """Minimal anonymous snapshot for delivery construction."""
    return CanonicalGenerationSnapshot("test-gen", "a" * 64, {})


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
        "contractVersion": "v1", "fixtureId": "test-race",
        "trackId": "track-one", "trackName": "Track One",
        "coordinateSpace": {"units": "meters", "origin": "test"},
        "circuitLengthMeters": 1.0, "rotationDegrees": 0.0,
        "startFinish": {"center": point, "inner": point, "outer": point},
        "centerLine": polyline, "innerBoundary": polyline, "outerBoundary": polyline,
    }


def _delivery(
    *,
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
    manifest = BrowserManifest("test-race", "Test Race", drivers)
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
        """✅ Positive: as_dict returns v1 contract with nested driver arrays."""
        # Arrange
        sidecar = _sidecar()

        # Act
        result = sidecar.as_dict()

        # Assert
        assert result["contractVersion"] == "v1"
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
                schema_id="urn:f1-cache-replay:schema:replay-data:v1:wrong",
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
    ) -> CanonicalGenerationSnapshot:
        """Build a minimal CanonicalGenerationSnapshot for sidecar tests."""
        session_row: dict[str, object] = {
            "session_id": fixture_id,
            "year": 2026, "round_number": 1,
            "event_name": "Test Grand Prix", "session_name": "Race",
            "session_type": "R",
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
        # Build laps frame using the canonical LAPS_SCHEMA — fill only
        # columns that build_lap_sector_sidecar reads, default others to null.
        lap_schema = dict(CANONICAL_TABLE_SCHEMAS["laps"])
        lap_rows: list[dict[str, object]] = []
        for row in laps_data:
            full: dict[str, object] = {col: None for col in lap_schema}
            full.update(row)
            lap_rows.append(full)
        laps = pl.DataFrame(lap_rows, schema=lap_schema, strict=True)
        frames: dict[str, pl.DataFrame] = {
            "session_metadata": pl.DataFrame(
                [session_row],
                schema=dict(CANONICAL_TABLE_SCHEMAS["session_metadata"]),
                strict=True,
            ),
            "drivers": pl.DataFrame(
                driver_rows,
                schema=dict(CANONICAL_TABLE_SCHEMAS["drivers"]),
                strict=True,
            ),
            "laps": laps,
        }
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
            BrowserManifest("test-race", "Test Race", manifest_drivers),
            _track_assets(),
            (chunk,),
            lap_sector_sidecar=sidecar,
        )

        # Act — publish and cross-check via full pipeline
        result = publish_browser_delivery(
            browser_parent=tmp_path / "browser",
            delivery_version="delivery-v1",
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
            BrowserManifest("test-race", "Test Race", manifest_drivers),
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
        assert sidecar_json["contractVersion"] == "v1"
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
            generation_id="canonical-v1",
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
            delivery_version="delivery-v1",
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
        assert sidecar["contractVersion"] == "v1"
        assert sidecar["fixtureId"] == "synthetic-race"
        assert "HAM" in sidecar["drivers"]
        assert "lapNumber" in sidecar["drivers"]["HAM"]
        assert "sector1DurationMs" in sidecar["drivers"]["HAM"]
        assert "sector1SessionTimeMs" in sidecar["drivers"]["HAM"]

        # Full validation — use the actual canonical manifest SHA
        validate_complete_browser_delivery(
            tmp_path / "browser",
            expected_generation_id="canonical-v1",
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
    row: dict[str, object] = {column: None for column in CANONICAL_TABLE_SCHEMAS[table]}
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
                "session_name": "Race", "session_type": "R",
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
        name: pl.DataFrame(value, schema=dict(CANONICAL_TABLE_SCHEMAS[name]), strict=True)
        for name, value in rows.items()
    }
