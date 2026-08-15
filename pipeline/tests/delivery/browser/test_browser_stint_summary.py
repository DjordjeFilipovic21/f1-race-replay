"""Behavior-focused tests for BrowserStintSummary derivation and publication.

Covers:
  - BrowserDriverStintSummary model validation (alignment, types, Int64 bounds)
  - BrowserStintSummary model validation (driver IDs, ordering, immutability)
  - BrowserStintSummaryReference contract enforcement (path, schemaId, sha256)
  - build_stint_summary derivation (determinism, null preservation, ordering,
    pit-in/pit-out mapping semantics, fail-closed error cases)
     - Publication (artifact writing, SHA-256, schema validation, mutation
     rejection, optional artifact absence, cross-checks)
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
from referencing import Registry, Resource

from f1_replay_pipeline.delivery.browser.browser_chunk_builder import (
    BrowserChunk,
    BrowserEvent,
    BrowserOverlap,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    BrowserDriverFields,
    BrowserDriverStintSummary,
    BrowserLapSectorSidecar,
    BrowserManifest,
    BrowserStintSummary,
    BrowserStintSummaryReference,
    CanonicalGenerationSnapshot,
    MAX_INT64,
    STINT_SUMMARY_SCHEMA_ID,
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
from f1_replay_pipeline.delivery.browser.browser_stint_summary import (
    build_stint_summary,
)
from f1_replay_pipeline.domain.canonical_schema import CANONICAL_TABLE_SCHEMAS_V2


REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMA_ROOT = REPO_ROOT / "contracts" / "replay-data" / "v2" / "schemas"
SCHEMA_ROOT_V2 = SCHEMA_ROOT

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _snapshot() -> CanonicalGenerationSnapshot:
    """Minimal v2 snapshot with the required practice session metadata."""
    session_metadata = pl.DataFrame([{
        "session_id": "test-race", "year": 2026, "round_number": 1,
        "event_name": "Practice", "session_name": "Practice 1", "session_type": "FP1",
        "session_mode": "practice", "session_start_time_utc": None,
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


def _empty_driver_summary() -> BrowserDriverStintSummary:
    """Return a BrowserDriverStintSummary with all-empty tuples."""
    return BrowserDriverStintSummary(
        stint_number=(),
        compound=(),
        start_lap=(),
        end_lap=(),
        start_time_ms=(),
        end_time_ms=(),
        tyre_life_at_start=(),
        is_fresh_tyre=(),
        pit_in_time_ms=(),
        pit_out_time_ms=(),
    )


def _delivery(
    *,
    stint_summary: BrowserStintSummary | None = None,
    stint_drivers: tuple[Mapping[str, object], ...] | None = None,
) -> BrowserDeliveryBuild:
    """Construct a minimal BrowserDeliveryBuild with optional stint summary."""
    drivers = stint_drivers or ({
        "id": "HAM", "displayName": "Hamilton", "teamName": "Team",
        "colorHex": "#000000", "carNumber": "44",
    },)
    manifest = BrowserManifest("test-race", "Test Race", drivers, session_mode="practice")
    return BrowserDeliveryBuild(
        _snapshot(), manifest, _track_assets(), (_chunk(),),
        stint_summary=stint_summary,
    )


def _publish(
    browser: Path,
    stint_summary: BrowserStintSummary | None = None,
) -> PublishedBrowserDelivery:
    return publish_browser_delivery(
        browser_parent=browser, delivery_version="delivery-one",
        delivery=_delivery(stint_summary=stint_summary), schema_root=SCHEMA_ROOT,
    )


def _v2_practice_delivery(
    stint_summary: BrowserStintSummary | None = None,
) -> BrowserDeliveryBuild:
    delivery = _delivery(stint_summary=stint_summary)
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
# BrowserDriverStintSummary model tests
# ===========================================================================


class TestBrowserDriverStintSummary:
    """Positive and negative contract enforcement for BrowserDriverStintSummary."""

    def test_constructs_with_all_fields_nominal(self) -> None:
        """✅ Positive: all fields populated with valid values."""
        driver = BrowserDriverStintSummary(
            stint_number=(1, 2),
            compound=("SOFT", "MEDIUM"),
            start_lap=(1, 15),
            end_lap=(14, 30),
            start_time_ms=(0, 1_500_000),
            end_time_ms=(1_400_000, 3_000_000),
            tyre_life_at_start=(0, 0),
            is_fresh_tyre=(True, True),
            pit_in_time_ms=(1_450_000, None),
            pit_out_time_ms=(None, 1_480_000),
        )

        assert driver.stint_number == (1, 2)
        assert driver.compound == ("SOFT", "MEDIUM")
        assert driver.pit_in_time_ms == (1_450_000, None)
        assert driver.pit_out_time_ms == (None, 1_480_000)

    def test_constructs_with_empty_tuples(self) -> None:
        """✅ Positive: empty tuples for a driver with no stints."""
        driver = _empty_driver_summary()

        assert driver.stint_number == ()
        assert len(driver.start_lap) == 0
        assert len(driver.pit_in_time_ms) == 0
        assert len(driver.pit_out_time_ms) == 0

    def test_rejects_misaligned_field_lengths(self) -> None:
        """❌ Negative: fields with different tuple lengths raise ValueError."""
        with pytest.raises(ValueError, match="aligned to stint_number"):
            BrowserDriverStintSummary(
                stint_number=(1, 2),
                compound=("SOFT",),              # shorter
                start_lap=(1, 15),
                end_lap=(14, None),
                start_time_ms=(0, 1_500_000),
                end_time_ms=(1_400_000, None),
                tyre_life_at_start=(0, 0),
                is_fresh_tyre=(True, True),
                pit_in_time_ms=(None, None),
                pit_out_time_ms=(None, None),
            )

    def test_rejects_non_positive_stint_number(self) -> None:
        """❌ Negative: stint_number <= 0 raises ValueError."""
        with pytest.raises(ValueError, match="positive integers"):
            BrowserDriverStintSummary(
                stint_number=(0,),
                compound=("SOFT",),
                start_lap=(1,),
                end_lap=(None,),
                start_time_ms=(0,),
                end_time_ms=(None,),
                tyre_life_at_start=(None,),
                is_fresh_tyre=(None,),
                pit_in_time_ms=(None,),
                pit_out_time_ms=(None,),
            )

    def test_rejects_non_strictly_increasing_stint_number(self) -> None:
        """❌ Negative: duplicate stint numbers raise ValueError."""
        with pytest.raises(ValueError, match="strictly increasing"):
            BrowserDriverStintSummary(
                stint_number=(1, 1),
                compound=("SOFT", "MEDIUM"),
                start_lap=(1, 15),
                end_lap=(14, None),
                start_time_ms=(0, 1_500_000),
                end_time_ms=(1_400_000, None),
                tyre_life_at_start=(0, 0),
                is_fresh_tyre=(True, True),
                pit_in_time_ms=(None, None),
                pit_out_time_ms=(None, None),
            )

    def test_rejects_non_positive_start_lap(self) -> None:
        """❌ Negative: start_lap < 1 raises ValueError."""
        with pytest.raises(ValueError, match="positive integers"):
            BrowserDriverStintSummary(
                stint_number=(1,),
                compound=("SOFT",),
                start_lap=(0,),
                end_lap=(None,),
                start_time_ms=(0,),
                end_time_ms=(None,),
                tyre_life_at_start=(None,),
                is_fresh_tyre=(None,),
                pit_in_time_ms=(None,),
                pit_out_time_ms=(None,),
            )

    def test_rejects_non_positive_end_lap(self) -> None:
        """❌ Negative: end_lap < 1 (when not null) raises ValueError."""
        with pytest.raises(ValueError, match="positive integers or null"):
            BrowserDriverStintSummary(
                stint_number=(1,),
                compound=("SOFT",),
                start_lap=(1,),
                end_lap=(0,),
                start_time_ms=(0,),
                end_time_ms=(None,),
                tyre_life_at_start=(None,),
                is_fresh_tyre=(None,),
                pit_in_time_ms=(None,),
                pit_out_time_ms=(None,),
            )

    def test_rejects_end_lap_before_start_lap(self) -> None:
        """❌ Negative: a non-null end_lap cannot precede its start_lap."""
        with pytest.raises(ValueError, match="must not precede start_lap"):
            BrowserDriverStintSummary(
                stint_number=(1,),
                compound=("SOFT",),
                start_lap=(15,),
                end_lap=(14,),
                start_time_ms=(0,),
                end_time_ms=(None,),
                tyre_life_at_start=(None,),
                is_fresh_tyre=(None,),
                pit_in_time_ms=(None,),
                pit_out_time_ms=(None,),
            )

    def test_rejects_non_tuple_field(self) -> None:
        """❌ Negative: a list (not tuple) field raises TypeError."""
        with pytest.raises(TypeError, match="must be a tuple"):
            BrowserDriverStintSummary(
                stint_number=[1],  # type: ignore[arg-type]
                compound=("SOFT",),
                start_lap=(1,),
                end_lap=(None,),
                start_time_ms=(0,),
                end_time_ms=(None,),
                tyre_life_at_start=(None,),
                is_fresh_tyre=(None,),
                pit_in_time_ms=(None,),
                pit_out_time_ms=(None,),
            )

    def test_rejects_int64_overflow_timing(self) -> None:
        """❌ Negative: timing fields > MAX_INT64 raise TypeError."""
        with pytest.raises(TypeError, match="non-negative signed Int64"):
            BrowserDriverStintSummary(
                stint_number=(1,),
                compound=("SOFT",),
                start_lap=(1,),
                end_lap=(None,),
                start_time_ms=(MAX_INT64 + 1,),
                end_time_ms=(None,),
                tyre_life_at_start=(None,),
                is_fresh_tyre=(None,),
                pit_in_time_ms=(None,),
                pit_out_time_ms=(None,),
            )

    def test_accepts_int64_max_timing(self) -> None:
        """✅ Positive: MAX_INT64 is accepted at the boundary."""
        driver = BrowserDriverStintSummary(
            stint_number=(1,),
            compound=("SOFT",),
            start_lap=(1,),
            end_lap=(None,),
            start_time_ms=(MAX_INT64,),
            end_time_ms=(MAX_INT64,),
            tyre_life_at_start=(None,),
            is_fresh_tyre=(None,),
            pit_in_time_ms=(None,),
            pit_out_time_ms=(None,),
        )
        assert driver.start_time_ms == (MAX_INT64,)

    def test_rejects_negative_timing(self) -> None:
        """❌ Negative: negative timing raises TypeError."""
        with pytest.raises(TypeError, match="non-negative"):
            BrowserDriverStintSummary(
                stint_number=(1,),
                compound=("SOFT",),
                start_lap=(1,),
                end_lap=(None,),
                start_time_ms=(-1,),
                end_time_ms=(None,),
                tyre_life_at_start=(None,),
                is_fresh_tyre=(None,),
                pit_in_time_ms=(None,),
                pit_out_time_ms=(None,),
            )

    def test_rejects_negative_tyre_life(self) -> None:
        """❌ Negative: negative tyre_life_at_start raises ValueError."""
        with pytest.raises(ValueError, match="non-negative"):
            BrowserDriverStintSummary(
                stint_number=(1,),
                compound=("SOFT",),
                start_lap=(1,),
                end_lap=(None,),
                start_time_ms=(None,),
                end_time_ms=(None,),
                tyre_life_at_start=(-1,),
                is_fresh_tyre=(None,),
                pit_in_time_ms=(None,),
                pit_out_time_ms=(None,),
            )

    def test_rejects_non_string_compound(self) -> None:
        """❌ Negative: non-string compound raises TypeError."""
        with pytest.raises(TypeError, match="compound must contain strings"):
            BrowserDriverStintSummary(
                stint_number=(1,),
                compound=(42,),  # type: ignore[arg-type]
                start_lap=(1,),
                end_lap=(None,),
                start_time_ms=(None,),
                end_time_ms=(None,),
                tyre_life_at_start=(None,),
                is_fresh_tyre=(None,),
                pit_in_time_ms=(None,),
                pit_out_time_ms=(None,),
            )

    def test_rejects_non_bool_fresh_tyre(self) -> None:
        """❌ Negative: non-bool is_fresh_tyre raises TypeError."""
        with pytest.raises(TypeError, match="is_fresh_tyre must contain booleans"):
            BrowserDriverStintSummary(
                stint_number=(1,),
                compound=("SOFT",),
                start_lap=(1,),
                end_lap=(None,),
                start_time_ms=(None,),
                end_time_ms=(None,),
                tyre_life_at_start=(None,),
                is_fresh_tyre=(1,),  # type: ignore[arg-type]
                pit_in_time_ms=(None,),
                pit_out_time_ms=(None,),
            )

    def test_null_fields_preserved_across_all_nullable(self) -> None:
        """✅ Positive: null values for all nullable fields are preserved."""
        driver = BrowserDriverStintSummary(
            stint_number=(1,),
            compound=(None,),
            start_lap=(1,),
            end_lap=(None,),
            start_time_ms=(None,),
            end_time_ms=(None,),
            tyre_life_at_start=(None,),
            is_fresh_tyre=(None,),
            pit_in_time_ms=(None,),
            pit_out_time_ms=(None,),
        )
        assert driver.compound == (None,)
        assert driver.end_lap == (None,)
        assert driver.start_time_ms == (None,)
        assert driver.tyre_life_at_start == (None,)
        assert driver.is_fresh_tyre == (None,)

    def test_as_dict_serializes_columnar_format(self) -> None:
        """✅ Positive: as_dict converts tuples to lists with camelCase keys."""
        driver = BrowserDriverStintSummary(
            stint_number=(1, 2),
            compound=("SOFT", None),
            start_lap=(1, 15),
            end_lap=(14, None),
            start_time_ms=(0, None),
            end_time_ms=(1_400_000, None),
            tyre_life_at_start=(0, 42),
            is_fresh_tyre=(True, None),
            pit_in_time_ms=(None, 1_450_000),
            pit_out_time_ms=(None, None),
        )

        result = driver.as_dict()

        assert result == {
            "stintNumber": [1, 2],
            "compound": ["SOFT", None],
            "startLap": [1, 15],
            "endLap": [14, None],
            "startTimeMs": [0, None],
            "endTimeMs": [1400000, None],
            "tyreLifeAtStart": [0, 42],
            "isFreshTyre": [True, None],
            "pitInTimeMs": [None, 1450000],
            "pitOutTimeMs": [None, None],
        }
        assert isinstance(result["stintNumber"], list)


# ===========================================================================
# BrowserStintSummary model tests
# ===========================================================================


class TestBrowserStintSummary:
    """Positive and negative contract enforcement for BrowserStintSummary."""

    def test_constructs_valid_summary(self) -> None:
        """✅ Positive: constructs with valid fixture_id and driver mapping."""
        driver = BrowserDriverStintSummary(
            stint_number=(1,),
            compound=("SOFT",),
            start_lap=(1,),
            end_lap=(None,),
            start_time_ms=(0,),
            end_time_ms=(None,),
            tyre_life_at_start=(0,),
            is_fresh_tyre=(True,),
            pit_in_time_ms=(None,),
            pit_out_time_ms=(None,),
        )

        summary = BrowserStintSummary("race-01", {"HAM": driver})

        assert summary.fixture_id == "race-01"
        assert summary.drivers["HAM"] is driver

    def test_sorts_drivers_alphabetically(self) -> None:
        """✅ Positive: driver entries are sorted by driver_id regardless of
        insertion order."""
        empty = _empty_driver_summary()

        summary = BrowserStintSummary("race-01", {"VER": empty, "ALO": empty, "HAM": empty})

        assert tuple(summary.drivers) == ("ALO", "HAM", "VER")

    def test_drivers_are_immutable(self) -> None:
        """✅ Positive: the drivers mapping is frozen after construction."""
        summary = BrowserStintSummary("race-01", {"HAM": _empty_driver_summary()})

        with pytest.raises(TypeError):
            summary.drivers["NEW"] = _empty_driver_summary()  # type: ignore[index]

    def test_rejects_empty_fixture_id(self) -> None:
        """❌ Negative: empty string fixture_id raises ValueError."""
        with pytest.raises(ValueError, match="fixture_id"):
            BrowserStintSummary("", {"HAM": _empty_driver_summary()})

    def test_rejects_empty_drivers(self) -> None:
        """❌ Negative: empty drivers mapping raises ValueError."""
        with pytest.raises(ValueError, match="non-empty mapping"):
            BrowserStintSummary("race-01", {})

    def test_rejects_invalid_driver_id_pattern(self) -> None:
        """❌ Negative: driver ID not matching [A-Z0-9]{2,4} raises ValueError."""
        with pytest.raises(ValueError, match="driver ID"):
            BrowserStintSummary("race-01", {"ham": _empty_driver_summary()})

    def test_rejects_non_browser_driver_stint_summary_value(self) -> None:
        """❌ Negative: a value not of type BrowserDriverStintSummary raises TypeError."""
        with pytest.raises(TypeError, match="BrowserDriverStintSummary"):
            BrowserStintSummary("race-01", {"HAM": "not-a-summary"})  # type: ignore[dict-item]

    def test_as_dict_returns_contract_format(self) -> None:
        """✅ Positive: as_dict returns the v2 contract with camelCase keys."""
        driver = BrowserDriverStintSummary(
            stint_number=(1,),
            compound=("SOFT",),
            start_lap=(1,),
            end_lap=(None,),
            start_time_ms=(0,),
            end_time_ms=(None,),
            tyre_life_at_start=(0,),
            is_fresh_tyre=(True,),
            pit_in_time_ms=(None,),
            pit_out_time_ms=(None,),
        )
        summary = BrowserStintSummary("test-race", {"HAM": driver})

        result = summary.as_dict()

        assert result["contractVersion"] == "v2"
        assert result["fixtureId"] == "test-race"
        assert "drivers" in result
        assert "HAM" in result["drivers"]
        assert result["drivers"]["HAM"]["stintNumber"] == [1]
        assert isinstance(result["drivers"]["HAM"]["stintNumber"], list)


# ===========================================================================
# BrowserStintSummaryReference tests
# ===========================================================================


class TestBrowserStintSummaryReference:
    """Positive and negative contract enforcement for the manifest reference."""

    def test_accepts_correct_path_and_schema(self) -> None:
        """✅ Positive: correct path and schema_id are accepted."""
        ref = BrowserStintSummaryReference(
            path="stint-summary.json",
            schema_id=STINT_SUMMARY_SCHEMA_ID,
            sha256="a" * 64,
        )

        assert ref.path == "stint-summary.json"
        assert ref.schema_id == STINT_SUMMARY_SCHEMA_ID

    def test_rejects_wrong_path(self) -> None:
        """❌ Negative: incorrect path raises ValueError."""
        with pytest.raises(ValueError, match="path must be stint-summary.json"):
            BrowserStintSummaryReference(
                path="wrong-file.json",
                schema_id=STINT_SUMMARY_SCHEMA_ID,
                sha256="a" * 64,
            )

    def test_rejects_wrong_schema_id(self) -> None:
        """❌ Negative: incorrect schema_id raises ValueError."""
        with pytest.raises(ValueError, match="schema_id is invalid"):
            BrowserStintSummaryReference(
                path="stint-summary.json",
                schema_id="urn:f1-cache-replay:schema:replay-data:unsupported:wrong",
                sha256="a" * 64,
            )

    def test_rejects_invalid_sha256(self) -> None:
        """❌ Negative: non-hex SHA-256 raises ValueError."""
        with pytest.raises(ValueError, match="sha256"):
            BrowserStintSummaryReference(
                path="stint-summary.json",
                schema_id=STINT_SUMMARY_SCHEMA_ID,
                sha256="not-a-sha",
            )


# ===========================================================================
# build_stint_summary derivation tests
# ===========================================================================


class TestBuildStintSummary:
    """Behavior tests for the pure derivation function."""

    @staticmethod
    def _snapshot_with_stints(
        stint_rows: list[dict[str, object]],
        lap_rows: list[dict[str, object]] | None = None,
        driver_ids: tuple[str, ...] = ("HAM",),
        fixture_id: str = "test-race",
    ) -> CanonicalGenerationSnapshot:
        """Build a minimal CanonicalGenerationSnapshot for stint summary tests."""
        session_row: dict[str, object] = {
            "session_id": fixture_id,
            "year": 2026, "round_number": 1,
            "event_name": "Test Grand Prix", "session_name": "Race",
            "session_type": "R",
            "session_mode": "race",
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
        stint_schema = dict(CANONICAL_TABLE_SCHEMAS_V2["stints"])
        full_stint_rows: list[dict[str, object]] = []
        for row in stint_rows:
            full: dict[str, object] = {col: None for col in stint_schema}
            full.update(row)
            full_stint_rows.append(full)

        lap_schema = dict(CANONICAL_TABLE_SCHEMAS_V2["laps"])
        full_lap_rows: list[dict[str, object]] = []
        if lap_rows:
            for row in lap_rows:
                full: dict[str, object] = {col: None for col in lap_schema}
                full.update(row)
                full_lap_rows.append(full)

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
            "stints": pl.DataFrame(
                full_stint_rows,
                schema=stint_schema,
                strict=True,
            ),
            "laps": pl.DataFrame(
                full_lap_rows,
                schema=lap_schema,
                strict=True,
            ),
        }
        return CanonicalGenerationSnapshot("generation", "a" * 64, frames)

    @staticmethod
    def _stint_row(
        driver_id: str, stint: int,
        **overrides: object,
    ) -> dict[str, object]:
        row: dict[str, object] = {
            "session_id": "test-race",
            "driver_id": driver_id,
            "stint_number": stint,
            "start_lap_number": 1 + (stint - 1) * 20,
            "end_lap_number": stint * 20,
            "start_time_ms": (stint - 1) * 2_000_000,
            "end_time_ms": stint * 2_000_000,
            "compound": "MEDIUM",
            "tyre_life_at_start": 0,
            "is_fresh_tyre": True,
        }
        row.update(overrides)
        return row

    @staticmethod
    def _lap_row(
        driver_id: str, lap: int,
        stint_number: int | None = None,
        pit_in_time_ms: int | None = None,
        pit_out_time_ms: int | None = None,
        **overrides: object,
    ) -> dict[str, object]:
        row: dict[str, object] = {
            "session_id": "test-race",
            "driver_id": driver_id,
            "lap_number": lap,
            "stint_number": stint_number,
            "lap_start_time_ms": lap * 100_000,
            "lap_end_time_ms": (lap + 1) * 100_000,
            "pit_in_time_ms": pit_in_time_ms,
            "pit_out_time_ms": pit_out_time_ms,
        }
        row.update(overrides)
        return row

    # -- Positive derivation tests -------------------------------------------

    def test_derives_deterministic_ordering(self) -> None:
        """✅ Positive: drivers ordered by driver_id, stints by stint_number."""
        snapshot = self._snapshot_with_stints(
            stint_rows=[
                self._stint_row("VER", 1, start_lap_number=1, end_lap_number=20),
                self._stint_row("HAM", 1, start_lap_number=1, end_lap_number=20),
                self._stint_row("HAM", 2, start_lap_number=21, end_lap_number=40),
            ],
            lap_rows=[
                self._lap_row("HAM", 20, stint_number=1, pit_in_time_ms=2_000_000),
                self._lap_row("HAM", 21, stint_number=2, pit_out_time_ms=2_030_000),
            ],
            driver_ids=("HAM", "VER"),
        )

        summary = build_stint_summary(snapshot)

        assert tuple(summary.drivers) == ("HAM", "VER")
        assert summary.drivers["HAM"].stint_number == (1, 2)

    def test_null_compound_preserved(self) -> None:
        """✅ Positive: null compound in canonical stint propagates as None."""
        snapshot = self._snapshot_with_stints(
            stint_rows=[self._stint_row("HAM", 1, compound=None)],
        )

        summary = build_stint_summary(snapshot)

        assert summary.drivers["HAM"].compound == (None,)

    def test_null_tyre_life_preserved(self) -> None:
        """✅ Positive: null tyre_life_at_start propagates as None."""
        snapshot = self._snapshot_with_stints(
            stint_rows=[self._stint_row("HAM", 1, tyre_life_at_start=None)],
        )

        summary = build_stint_summary(snapshot)

        assert summary.drivers["HAM"].tyre_life_at_start == (None,)

    def test_null_fresh_tyre_preserved(self) -> None:
        """✅ Positive: null is_fresh_tyre propagates as None."""
        snapshot = self._snapshot_with_stints(
            stint_rows=[self._stint_row("HAM", 1, is_fresh_tyre=None)],
        )

        summary = build_stint_summary(snapshot)

        assert summary.drivers["HAM"].is_fresh_tyre == (None,)

    def test_driver_with_zero_stints_produces_empty_tuples(self) -> None:
        """✅ Positive: a registered driver with no stints gets empty tuples."""
        snapshot = self._snapshot_with_stints(
            stint_rows=[self._stint_row("HAM", 1)],
            driver_ids=("HAM", "VER"),
        )

        summary = build_stint_summary(snapshot)

        assert set(summary.drivers) == {"HAM", "VER"}
        ver = summary.drivers["VER"]
        assert ver.stint_number == ()
        assert ver.start_lap == ()
        assert ver.pit_in_time_ms == ()
        assert ver.pit_out_time_ms == ()

    def test_pit_in_mapped_to_stint_by_stint_number(self) -> None:
        """✅ Positive: lap with pit_in_time_ms and stint_number N maps
        pit_in_time_ms to that stint."""
        snapshot = self._snapshot_with_stints(
            stint_rows=[
                self._stint_row("HAM", 1, start_lap_number=1, end_lap_number=20),
                self._stint_row("HAM", 2, start_lap_number=21, end_lap_number=40),
            ],
            lap_rows=[
                self._lap_row("HAM", 20, stint_number=1, pit_in_time_ms=2_000_000),
                self._lap_row("HAM", 21, stint_number=2, pit_out_time_ms=2_030_000),
                self._lap_row("HAM", 40, stint_number=2, pit_in_time_ms=4_100_000),
            ],
        )

        summary = build_stint_summary(snapshot)

        assert summary.drivers["HAM"].pit_in_time_ms == (2_000_000, 4_100_000)
        assert summary.drivers["HAM"].pit_out_time_ms == (None, 2_030_000)

    def test_pit_in_null_leaves_pit_in_null(self) -> None:
        """✅ Positive: lap rows with null pit_in_time_ms do not set that value
        for the stint — pit_in stays null."""
        snapshot = self._snapshot_with_stints(
            stint_rows=[self._stint_row("HAM", 1, start_lap_number=1, end_lap_number=20)],
            lap_rows=[
                self._lap_row("HAM", 10, stint_number=1, pit_in_time_ms=None, pit_out_time_ms=None),
            ],
        )

        summary = build_stint_summary(snapshot)

        assert summary.drivers["HAM"].pit_in_time_ms == (None,)

    def test_pit_out_null_leaves_pit_out_null(self) -> None:
        """✅ Positive: lap rows with null pit_out_time_ms do not set that value
        for the stint — pit_out stays null."""
        snapshot = self._snapshot_with_stints(
            stint_rows=[self._stint_row("HAM", 2, start_lap_number=21, end_lap_number=40)],
            lap_rows=[
                self._lap_row("HAM", 21, stint_number=2, pit_out_time_ms=None),
            ],
        )

        summary = build_stint_summary(snapshot)

        assert summary.drivers["HAM"].pit_out_time_ms == (None,)

    def test_pit_out_on_stint_one_is_preserved(self) -> None:
        """✅ Positive: if a lap in stint 1 has a pit_out_time_ms it is
        preserved — no hardcoded null-assertion for stint-1 pit-out.

        This represents a driver who starts from pit lane and has their
        effective first stint's 'beginning' logged as a pit-out event.
        """
        snapshot = self._snapshot_with_stints(
            stint_rows=[self._stint_row("HAM", 1, start_lap_number=1, end_lap_number=20)],
            lap_rows=[
                self._lap_row("HAM", 1, stint_number=1, pit_out_time_ms=15_000),
            ],
        )

        summary = build_stint_summary(snapshot)

        assert summary.drivers["HAM"].pit_out_time_ms == (15_000,)

    def test_single_stint_no_pit_events_has_null_pit_fields(self) -> None:
        """✅ Positive: a single-stint driver with no pit events at all
        in the canonical laps table has both pit fields null."""
        snapshot = self._snapshot_with_stints(
            stint_rows=[self._stint_row("HAM", 1, end_lap_number=None)],
            lap_rows=[
                self._lap_row("HAM", 1, stint_number=1),
            ],
        )

        summary = build_stint_summary(snapshot)

        ham = summary.drivers["HAM"]
        assert ham.pit_in_time_ms == (None,)
        assert ham.pit_out_time_ms == (None,)

    def test_ongoing_stint_has_null_end_fields(self) -> None:
        """✅ Positive: a stint with null end_lap_number produces null
        end_lap, end_time_ms, and pit_in_time_ms."""
        snapshot = self._snapshot_with_stints(
            stint_rows=[self._stint_row("HAM", 1, end_lap_number=None, end_time_ms=None)],
        )

        summary = build_stint_summary(snapshot)

        ham = summary.drivers["HAM"]
        assert ham.end_lap == (None,)
        assert ham.end_time_ms == (None,)
        assert ham.pit_in_time_ms == (None,)

    def test_null_start_time_ms_is_preserved(self) -> None:
        """✅ Positive: null start_time_ms in canonical stint propagates as
        None — start_time_ms is nullable."""
        snapshot = self._snapshot_with_stints(
            stint_rows=[self._stint_row("HAM", 1, start_time_ms=None)],
        )

        summary = build_stint_summary(snapshot)

        assert summary.drivers["HAM"].start_time_ms == (None,)

    def test_deterministic_identical_inputs_produce_equal_dicts(self) -> None:
        """✅ Positive: the same canonical snapshot always yields the same
        stint summary dict."""
        snapshot = self._snapshot_with_stints(
            stint_rows=[
                self._stint_row("HAM", 1, start_lap_number=1, end_lap_number=20),
                self._stint_row("HAM", 2, start_lap_number=21, end_lap_number=40),
            ],
            lap_rows=[
                self._lap_row("HAM", 20, stint_number=1, pit_in_time_ms=2_000_000),
                self._lap_row("HAM", 21, stint_number=2, pit_out_time_ms=2_030_000),
            ],
        )

        first = build_stint_summary(snapshot)
        second = build_stint_summary(snapshot)

        assert first.as_dict() == second.as_dict()

    def test_fixture_id_matches_session_metadata(self) -> None:
        """✅ Positive: summary fixture_id is taken from session_metadata."""
        snapshot = self._snapshot_with_stints(
            stint_rows=[self._stint_row("HAM", 1)],
            fixture_id="monza-2026",
        )

        summary = build_stint_summary(snapshot)

        assert summary.fixture_id == "monza-2026"

    def test_pit_in_and_pit_out_independently_on_same_stint(self) -> None:
        """✅ Positive: a non-first stint maps pit-out and pit-in independently,
        even when both values come from the same lap row."""
        snapshot = self._snapshot_with_stints(
            stint_rows=[
                self._stint_row("HAM", 1, start_lap_number=1, end_lap_number=20),
                self._stint_row("HAM", 2, start_lap_number=21, end_lap_number=40),
            ],
            lap_rows=[
                self._lap_row("HAM", 20, stint_number=1, pit_in_time_ms=2_000_000),
                self._lap_row("HAM", 21, stint_number=2, pit_out_time_ms=2_030_000, pit_in_time_ms=4_000_000),
            ],
        )

        summary = build_stint_summary(snapshot)

        ham = summary.drivers["HAM"]
        assert ham.pit_in_time_ms == (2_000_000, 4_000_000)
        assert ham.pit_out_time_ms == (None, 2_030_000)

    # -- Negative derivation tests -------------------------------------------

    def test_rejects_duplicate_canonical_stints(self) -> None:
        """❌ Negative: two canonical stint rows with same driver_id and
        stint_number raise ValueError when deriving the summary."""
        snapshot = self._snapshot_with_stints(stint_rows=[
            self._stint_row("HAM", 1, start_lap_number=1, end_lap_number=10),
            self._stint_row("HAM", 1, start_lap_number=1, end_lap_number=10),
        ])

        with pytest.raises(ValueError, match="duplicate canonical stints"):
            build_stint_summary(snapshot)

    def test_rejects_pit_event_with_null_stint_number(self) -> None:
        """❌ Negative: a lap with pit times but null stint_number raises
        ValueError."""
        snapshot = self._snapshot_with_stints(
            stint_rows=[self._stint_row("HAM", 1, start_lap_number=1, end_lap_number=20)],
            lap_rows=[
                self._lap_row("HAM", 10, stint_number=None, pit_in_time_ms=1_000_000),
            ],
        )

        with pytest.raises(ValueError, match="has no stint number"):
            build_stint_summary(snapshot)

    def test_rejects_pit_event_with_no_matching_stint(self) -> None:
        """❌ Negative: a pit event whose stint_number has no canonical stint
        raises ValueError."""
        snapshot = self._snapshot_with_stints(
            stint_rows=[self._stint_row("HAM", 1, start_lap_number=1, end_lap_number=20)],
            lap_rows=[
                self._lap_row("HAM", 10, stint_number=99, pit_in_time_ms=1_000_000),
            ],
        )

        with pytest.raises(ValueError, match="no matching canonical stint 99"):
            build_stint_summary(snapshot)

    def test_rejects_pit_event_outside_stint_range(self) -> None:
        """❌ Negative: a pit event lap number outside its stint's
        [start_lap_number, end_lap_number] raises ValueError."""
        snapshot = self._snapshot_with_stints(
            stint_rows=[self._stint_row("HAM", 1, start_lap_number=1, end_lap_number=10)],
            lap_rows=[
                self._lap_row("HAM", 99, stint_number=1, pit_in_time_ms=9_900_000),
            ],
        )

        with pytest.raises(ValueError, match="lies outside canonical stint 1 range"):
            build_stint_summary(snapshot)

    def test_ambiguous_pit_in_publishes_both_transitions_as_null(self) -> None:
        """✅ Multiple distinct pit-ins fail closed to a null transition pair."""
        snapshot = self._snapshot_with_stints(
            stint_rows=[self._stint_row("HAM", 1, start_lap_number=1, end_lap_number=20)],
            lap_rows=[
                self._lap_row("HAM", 10, stint_number=1, pit_in_time_ms=1_000_000),
                self._lap_row("HAM", 15, stint_number=1, pit_in_time_ms=1_500_000),
            ],
        )

        ham = build_stint_summary(snapshot).drivers["HAM"]

        assert ham.pit_in_time_ms == (None,)
        assert ham.pit_out_time_ms == (None,)

    def test_brazil_shaped_multiple_pit_outs_preserve_unique_pit_in(self) -> None:
        """✅ Brazil 2024 ALB-shaped candidates preserve unique pit-in and
        null ambiguous pit-out."""
        # Arrange: one unique pit-in is accompanied by two distinct pit-outs.
        snapshot = self._snapshot_with_stints(
            stint_rows=[self._stint_row("ALB", 5, start_lap_number=1, end_lap_number=22)],
            lap_rows=[
                self._lap_row("ALB", 18, stint_number=5, pit_out_time_ms=3_908_442),
                self._lap_row("ALB", 21, stint_number=5, pit_in_time_ms=4_295_963),
                self._lap_row("ALB", 22, stint_number=5, pit_out_time_ms=4_925_410),
            ],
            driver_ids=("ALB",),
        )

        # Act
        alb = build_stint_summary(snapshot).drivers["ALB"]

        # Assert: the unique transition survives; only ambiguous pit-out is null.
        assert alb.pit_in_time_ms == (4_295_963,)
        assert alb.pit_out_time_ms == (None,)

    def test_ambiguous_pit_ins_preserve_unique_pit_out(self) -> None:
        """✅ Multiple distinct pit-ins preserve a unique pit-out only."""
        # Arrange: two distinct pit-ins accompany one unique pit-out.
        snapshot = self._snapshot_with_stints(
            stint_rows=[self._stint_row("HAM", 1, start_lap_number=1, end_lap_number=22)],
            lap_rows=[
                self._lap_row("HAM", 18, stint_number=1, pit_in_time_ms=3_000_000),
                self._lap_row("HAM", 20, stint_number=1, pit_in_time_ms=3_500_000),
                self._lap_row("HAM", 21, stint_number=1, pit_out_time_ms=3_908_442),
            ],
        )

        # Act
        ham = build_stint_summary(snapshot).drivers["HAM"]

        # Assert: only the ambiguous pit-in is null.
        assert ham.pit_in_time_ms == (None,)
        assert ham.pit_out_time_ms == (3_908_442,)

    def test_duplicate_identical_pit_candidates_collapse_to_unique_pair(self) -> None:
        """✅ Repeated identical timestamps are one deterministic candidate."""
        snapshot = self._snapshot_with_stints(
            stint_rows=[self._stint_row("HAM", 1, start_lap_number=1, end_lap_number=20)],
            lap_rows=[
                self._lap_row("HAM", 10, stint_number=1, pit_in_time_ms=1_000_000, pit_out_time_ms=2_000_000),
                self._lap_row("HAM", 11, stint_number=1, pit_in_time_ms=1_000_000, pit_out_time_ms=2_000_000),
            ],
        )

        ham = build_stint_summary(snapshot).drivers["HAM"]

        assert ham.pit_in_time_ms == (1_000_000,)
        assert ham.pit_out_time_ms == (2_000_000,)


# ===========================================================================
# Publication tests
# ===========================================================================


class TestStintSummaryPublication:
    """Integration coverage for stint summary artifact publication and validation."""

    def test_stint_summary_artifact_written_to_generation_directory(
        self, tmp_path: Path,
    ) -> None:
        """✅ Positive: stint summary artifact is written to the generation directory."""
        summary = BrowserStintSummary("test-race", {"HAM": _empty_driver_summary()})

        result = _publish(tmp_path / "browser", stint_summary=summary)

        assert result.stint_summary_path is not None
        assert result.stint_summary_path.exists()
        assert result.stint_summary_path.name == "stint-summary.json"
        assert "stint-summary.json" in result.artifact_digests

    def test_manifest_references_stint_summary_with_correct_path(
        self, tmp_path: Path,
    ) -> None:
        """✅ Positive: manifest stintSummary path is exactly 'stint-summary.json'."""
        summary = BrowserStintSummary("test-race", {"HAM": _empty_driver_summary()})

        result = _publish(tmp_path / "browser", stint_summary=summary)
        manifest = _load_json(result.manifest_path)

        assert "stintSummary" in manifest
        assert manifest["stintSummary"]["path"] == "stint-summary.json"
        assert manifest["stintSummary"]["schemaId"] == STINT_SUMMARY_SCHEMA_ID

    def test_stint_summary_sha256_matches_stored_bytes(self, tmp_path: Path) -> None:
        """✅ Positive: SHA-256 in manifest matches the digested stint summary bytes."""
        summary = BrowserStintSummary("test-race", {"HAM": _empty_driver_summary()})

        result = _publish(tmp_path / "browser", stint_summary=summary)
        manifest = _load_json(result.manifest_path)

        stored_digest = hashlib.sha256(
            result.stint_summary_path.read_bytes()  # type: ignore[union-attr]
        ).hexdigest()
        assert manifest["stintSummary"]["sha256"] == stored_digest
        assert result.artifact_digests["stint-summary.json"] == stored_digest

    def test_mutated_stint_summary_rejected_on_validate(self, tmp_path: Path) -> None:
        """❌ Negative: tampering with stored stint summary bytes fails validation."""
        summary = BrowserStintSummary("test-race", {"HAM": _empty_driver_summary()})

        result = _publish(tmp_path / "browser", stint_summary=summary)
        stint_path = result.stint_summary_path
        assert stint_path is not None

        original = stint_path.read_bytes()
        mutated = original.replace(b'"HAM"', b'"MUT"')
        stint_path.write_bytes(mutated)

        with pytest.raises(BrowserDeliveryPublicationError, match="validation failed"):
            validate_complete_browser_delivery(
                tmp_path / "browser",
                expected_generation_id="test-gen",
                expected_manifest_sha256="a" * 64,
                schema_root=SCHEMA_ROOT,
            )

    def test_v2_delivery_without_stint_summary_remains_valid(self, tmp_path: Path) -> None:
        """✅ Positive: a v2 delivery without a stint summary publishes and validates."""
        result = publish_browser_delivery(
            browser_parent=tmp_path / "browser",
            delivery_version="delivery-v2-without-summary",
            delivery=_delivery(stint_summary=None),
            schema_root=SCHEMA_ROOT,
        )

        assert result.stint_summary_path is None
        assert "stint-summary.json" not in result.artifact_digests
        manifest = _load_json(result.manifest_path)
        assert "stintSummary" not in manifest

        validate_complete_browser_delivery(
            tmp_path / "browser",
            expected_generation_id="test-gen",
            expected_manifest_sha256="a" * 64,
            schema_root=SCHEMA_ROOT,
        )

    def test_v2_publication_emits_contract_v2_run_and_tyre_summary(
        self, tmp_path: Path,
    ) -> None:
        """✅ Positive: a v2 practice delivery publishes a v2 stint (run/tyre) summary."""
        summary = BrowserStintSummary(
            "test-race",
            {
                "HAM": BrowserDriverStintSummary(
                    stint_number=(1,),
                    compound=("MEDIUM",),
                    start_lap=(1,),
                    end_lap=(None,),
                    start_time_ms=(0,),
                    end_time_ms=(None,),
                    tyre_life_at_start=(0,),
                    is_fresh_tyre=(True,),
                    pit_in_time_ms=(None,),
                    pit_out_time_ms=(None,),
                ),
            },
        )

        result = publish_browser_delivery(
            browser_parent=tmp_path / "browser",
            delivery_version="delivery-v2",
            delivery=_v2_practice_delivery(stint_summary=summary),
            schema_root=SCHEMA_ROOT_V2,
            contract_version="v2",
        )
        manifest = _load_json(result.manifest_path)
        stint = _load_json(result.stint_summary_path)  # type: ignore[arg-type]

        assert manifest["sessionMode"] == "practice"
        assert manifest["stintSummary"]["schemaId"].endswith(":v2:stint-summary")
        assert manifest["schemas"]["stintSummary"].endswith(":v2:stint-summary")
        assert stint["contractVersion"] == "v2"
        assert stint["fixtureId"] == "test-race"
        assert stint["drivers"]["HAM"]["stintNumber"] == [1]
        assert stint["drivers"]["HAM"]["compound"] == ["MEDIUM"]
        validate_complete_browser_delivery(
            tmp_path / "browser",
            expected_generation_id="test-gen",
            expected_manifest_sha256="a" * 64,
            schema_root=SCHEMA_ROOT_V2,
        )

    def test_stint_summary_driver_ids_match_manifest_drivers(
        self, tmp_path: Path,
    ) -> None:
        """✅ Positive: every driver in the stint summary equals manifest driver set."""
        summary = BrowserStintSummary(
            "test-race",
            {"HAM": _empty_driver_summary(), "VER": _empty_driver_summary()},
        )
        manifest_drivers = (
            {"id": "HAM", "displayName": "H", "teamName": "T", "colorHex": "#000000", "carNumber": "44"},
            {"id": "VER", "displayName": "V", "teamName": "T", "colorHex": "#111111", "carNumber": "1"},
        )

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
                "test-race", "Test Race", manifest_drivers, session_mode="practice",
            ),
            _track_assets(),
            (chunk,),
            stint_summary=summary,
        )

        result = publish_browser_delivery(
            browser_parent=tmp_path / "browser",
            delivery_version="delivery-v2",
            delivery=delivery,
            schema_root=SCHEMA_ROOT,
        )

        manifest = _load_json(result.manifest_path)
        assert "stintSummary" in manifest
        assert manifest["stintSummary"]["path"] == "stint-summary.json"
        assert result.stint_summary_path is not None

    def test_stint_summary_contract_rejects_fixture_id_mismatch(
        self, tmp_path: Path,
    ) -> None:
        """❌ Negative: fixture_id in stint summary != manifest fixture_id raises error."""
        summary = BrowserStintSummary("other-race", {"HAM": _empty_driver_summary()})
        delivery = _delivery(stint_summary=summary)

        with pytest.raises(BrowserDeliveryPublicationError, match="disagrees with the manifest"):
            publish_browser_delivery(
                browser_parent=tmp_path / "browser",
                delivery_version="delivery-one",
                delivery=delivery,
                schema_root=SCHEMA_ROOT,
            )

    def test_stint_summary_contract_rejects_driver_id_mismatch(
        self, tmp_path: Path,
    ) -> None:
        """❌ Negative: stint summary has extra/missing driver vs manifest raises error."""
        summary = BrowserStintSummary(
            "test-race",
            {"HAM": _empty_driver_summary(), "VER": _empty_driver_summary()},
        )
        delivery = _delivery(stint_summary=summary)  # manifest default has only HAM

        with pytest.raises(BrowserDeliveryPublicationError, match="drivers disagree with the manifest"):
            publish_browser_delivery(
                browser_parent=tmp_path / "browser",
                delivery_version="delivery-one",
                delivery=delivery,
                schema_root=SCHEMA_ROOT,
            )

    def test_identical_stint_summary_bytes_across_publications(
        self, tmp_path: Path,
    ) -> None:
        """✅ Positive: same stint summary produces byte-identical artifacts twice."""
        summary = BrowserStintSummary("test-race", {"HAM": _empty_driver_summary()})

        first = _publish(tmp_path / "browser-one", stint_summary=summary)
        second = _publish(tmp_path / "browser-two", stint_summary=summary)

        assert first.stint_summary_path is not None
        assert second.stint_summary_path is not None
        assert first.stint_summary_path.read_bytes() == second.stint_summary_path.read_bytes()

    def test_stint_summary_schema_validates_against_its_own_schema(
        self, tmp_path: Path,
    ) -> None:
        """✅ Positive: v2 stint summary JSON passes its own schema validation."""
        summary = BrowserStintSummary("test-race", {"HAM": _empty_driver_summary()})

        result = _publish(tmp_path / "browser", stint_summary=summary)
        stint_json = _load_json(result.stint_summary_path)  # type: ignore[arg-type]

        stint_schema = _load_json(SCHEMA_ROOT / "stint-summary.schema.json")
        schemas = {
            name: _load_json(SCHEMA_ROOT / f"{name}.schema.json")
            for name in ("stint-summary",)
        }
        registry = Registry()
        for schema in schemas.values():
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        Draft202012Validator(
            stint_schema, registry=registry,
            format_checker=Draft202012Validator.FORMAT_CHECKER,
        ).validate(stint_json)

        assert stint_json["contractVersion"] == "v2"
        assert stint_json["fixtureId"] == "test-race"
        assert "drivers" in stint_json

    def test_complete_validator_covers_stint_summary(self, tmp_path: Path) -> None:
        """✅ Positive: validate_complete_browser_delivery processes stint summary ref."""
        summary = BrowserStintSummary("test-race", {"HAM": _empty_driver_summary()})

        result = _publish(tmp_path / "browser", stint_summary=summary)

        validate_complete_browser_delivery(
            tmp_path / "browser",
            expected_generation_id="test-gen",
            expected_manifest_sha256="a" * 64,
            schema_root=SCHEMA_ROOT,
        )

        assert result.stint_summary_path is not None
        assert result.stint_summary_path.exists()

    def test_mutated_stint_summary_sha_detected(self, tmp_path: Path) -> None:
        """❌ Negative: mutating the stored stint-summary bytes triggers
        digest mismatch during complete validation."""
        summary = BrowserStintSummary("test-race", {"HAM": _empty_driver_summary()})

        result = _publish(tmp_path / "browser", stint_summary=summary)
        stint_path = result.stint_summary_path
        assert stint_path is not None
        stint_path.write_bytes(
            stint_path.read_bytes().replace(b'"stintNumber":', b'"stintNumberX":')
        )

        with pytest.raises(BrowserDeliveryPublicationError, match="validation failed"):
            validate_complete_browser_delivery(
                tmp_path / "browser",
                expected_generation_id="test-gen",
                expected_manifest_sha256="a" * 64,
                schema_root=SCHEMA_ROOT,
            )

    def test_manifest_schema_validates_with_and_without_stint_summary(
        self, tmp_path: Path,
    ) -> None:
        """✅ Positive: manifest validates with stintSummary present and absent."""
        # With stint summary
        summary = BrowserStintSummary("test-race", {"HAM": _empty_driver_summary()})
        with_result = _publish(tmp_path / "browser-with", stint_summary=summary)
        with_manifest = _load_json(with_result.manifest_path)

        # Without stint summary
        without_result = publish_browser_delivery(
            browser_parent=tmp_path / "browser-without",
            delivery_version="delivery-two",
            delivery=_delivery(stint_summary=None),
            schema_root=SCHEMA_ROOT,
        )
        without_manifest = _load_json(without_result.manifest_path)

        manifest_schema = _load_json(SCHEMA_ROOT / "manifest.schema.json")
        schemas = {
            name: _load_json(SCHEMA_ROOT / f"{name}.schema.json")
            for name in ("manifest", "chunk", "track-assets", "stint-summary", "pit-loss-model")
        }
        registry = Registry()
        for schema in schemas.values():
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))

        Draft202012Validator(
            manifest_schema, registry=registry,
            format_checker=Draft202012Validator.FORMAT_CHECKER,
        ).validate(with_manifest)
        Draft202012Validator(
            manifest_schema, registry=registry,
            format_checker=Draft202012Validator.FORMAT_CHECKER,
        ).validate(without_manifest)

        assert "stintSummary" in with_manifest
        assert "stintSummary" not in without_manifest

    def test_schema_invalid_stint_summary_rejected(self, tmp_path: Path) -> None:
        """❌ Negative: schema-invalid stint summary content fails validation."""
        summary = BrowserStintSummary("test-race", {"HAM": _empty_driver_summary()})

        result = _publish(tmp_path / "browser", stint_summary=summary)
        stint_path = result.stint_summary_path
        assert stint_path is not None

        # Remove required 'contractVersion' field
        stint_json = _load_json(stint_path)
        del stint_json["contractVersion"]
        new_bytes = json.dumps(stint_json, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        stint_path.write_bytes(new_bytes)

        # Update manifest SHA to match mutated stint summary
        manifest = _load_json(result.manifest_path)
        new_sha = hashlib.sha256(new_bytes).hexdigest()
        manifest["stintSummary"]["sha256"] = new_sha
        manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        result.manifest_path.write_bytes(manifest_bytes)

        pointer = _load_json(result.pointer_path)
        pointer["manifestSha256"] = hashlib.sha256(manifest_bytes).hexdigest()
        result.pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

        with pytest.raises(BrowserDeliveryPublicationError, match="validation failed"):
            validate_complete_browser_delivery(
                tmp_path / "browser",
                expected_generation_id="test-gen",
                expected_manifest_sha256="a" * 64,
                schema_root=SCHEMA_ROOT,
            )
