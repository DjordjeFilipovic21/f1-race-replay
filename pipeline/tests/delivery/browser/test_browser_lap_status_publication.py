"""Focused offline publication coverage for the V2 qualifying lap-status sidecar.

Covers:
  - qualifying-like V2 publication creating and referencing the sidecar with a
    valid digest and schema
  - rejection of tampered payloads, bad references, invalid ordering, wrong
    driver sets, and incomplete delivery sets
  - mode gating: non-qualifying sessions omit the artifact and add no
    unexpected manifest reference; qualifying sessions without deletion
    messages or with incomplete inputs fail closed
  - v2 schema validation of the complete publication

No network access and no web imports: all fixtures are synthetic, in-memory
Polars frames and publication targets under ``tmp_path``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, cast

import polars as pl
import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

from f1_replay_pipeline.delivery.browser.browser_chunk_builder import (
    BrowserChunk,
    BrowserOverlap,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    BROWSER_QUALIFYING_LAP_STATUS_SCHEMA_ID,
    BrowserDriverFields,
    BrowserManifest,
    BrowserQualifyingLapStatusRecord,
    BrowserQualifyingLapStatusSidecar,
    CanonicalGenerationSnapshot,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_orchestration import (
    BrowserDeliveryBuild,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_publication import (
    BrowserDeliveryPublicationError,
    PublishedBrowserDelivery,
    _artifact_payloads,
    _validate_delivery_payloads,
    publish_browser_delivery,
    validate_complete_browser_delivery,
)
from f1_replay_pipeline.domain.canonical_schema import CANONICAL_TABLE_SCHEMAS_V2
from f1_replay_pipeline.domain.session_modes import SessionMode


REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMA_ROOT = REPO_ROOT / "contracts" / "replay-data" / "v2" / "schemas"
SCHEMA_ROOT_V2 = SCHEMA_ROOT
_V2_SCHEMA_NAMES = (
    "manifest", "chunk", "track-assets", "timeline-summary",
    "browser-lap-sector-sidecar", "penalty-sidecar", "stint-summary", "pit-loss-model",
    "qualifying-summary", "browser-qualifying-lap-status",
)

_DELETED_MESSAGE = "CAR 44 TIME 1:30.000 DELETED - TRACK LIMITS"
_REINSTATED_MESSAGE = "CAR 44 TIME 1:30.000 REINSTATED"

_FIXTURE_ID = "monza-2026"


# ---------------------------------------------------------------------------
# Shared synthetic fixtures
# ---------------------------------------------------------------------------


def _driver_metadata() -> tuple[dict[str, object], ...]:
    """Manifest driver metadata matching the canonical drivers frame."""
    return (
        {"id": "HAM", "displayName": "Lewis Hamilton", "teamName": "Ferrari",
         "colorHex": "#E8002D", "carNumber": "44"},
        {"id": "VER", "displayName": "Max Verstappen", "teamName": "Red Bull Racing",
         "colorHex": "#3671C6", "carNumber": "1"},
    )


def _track_assets() -> dict[str, object]:
    """Minimal valid track assets bound to the fixture session."""
    point: dict[str, object] = {"x": 0.0, "y": 0.0}
    polyline = (
        point, {"x": 1.0, "y": 0.0}, {"x": 1.0, "y": 1.0}, {"x": 0.0, "y": 1.0},
    )
    return {
        "contractVersion": "v2", "fixtureId": _FIXTURE_ID,
        "trackId": "monza", "trackName": "Monza",
        "coordinateSpace": {"units": "meters", "origin": "test"},
        "circuitLengthMeters": 1.0, "rotationDegrees": 0.0,
        "startFinish": {"center": point, "inner": point, "outer": point},
        "centerLine": polyline, "innerBoundary": polyline, "outerBoundary": polyline,
    }


def _fields(driver_id: str) -> BrowserDriverFields:
    """One minimal two-sample driver field set for a valid chunk."""
    return BrowserDriverFields(
        driver_id, (0, 1000),
        (1.0, 2.0), (3.0, 4.0), (5.0, 6.0), (7.0, 8.0),
        (0, 1), (None, 7), (None, 1),
        ("OnTrack", "OnTrack"), (1, 1), ("SOFT", "SOFT"),
        (False, False), (None, None), (None, None), (None, None),
    )


def _chunk() -> BrowserChunk:
    """One minimal authoritative chunk with the two manifest drivers."""
    return BrowserChunk(
        "chunk-001", 1, 0, 2000,
        BrowserOverlap("none", None, None, None, None),
        (0, 1000), 0, {"HAM": _fields("HAM"), "VER": _fields("VER")},
        (("HAM", "VER"), ("HAM", "VER")), (1, 1), ("clear", "clear"),
        (),
    )


def _session_metadata(session_mode: str = "qualifying") -> pl.DataFrame:
    """One v2 session_metadata row with a normalized session mode."""
    return pl.DataFrame([{
        "session_id": _FIXTURE_ID,
        "year": 2026,
        "round_number": 1,
        "event_name": "Italian Grand Prix",
        "session_name": "Qualifying",
        "session_type": "Q",
        "session_mode": session_mode,
        "session_start_time_utc": None,
    }], schema=dict(CANONICAL_TABLE_SCHEMAS_V2["session_metadata"]), strict=True)


def _drivers_frame() -> pl.DataFrame:
    """Canonical drivers frame providing number/source-key aliases."""
    return pl.DataFrame([
        {"session_id": _FIXTURE_ID, "driver_id": "HAM", "source_driver_key": "44",
         "driver_number": 44, "full_name": "Lewis Hamilton", "team_name": "Ferrari",
         "team_colour": "E8002D"},
        {"session_id": _FIXTURE_ID, "driver_id": "VER", "source_driver_key": "1",
         "driver_number": 1, "full_name": "Max Verstappen",
         "team_name": "Red Bull Racing", "team_colour": "3671C6"},
    ], schema=dict(CANONICAL_TABLE_SCHEMAS_V2["drivers"]), strict=True)


def _lap_row(
    driver_id: str, lap_number: int, start_ms: int, end_ms: int, duration_ms: int,
    *, deleted: bool = False, deleted_reason: str | None = None,
) -> dict[str, object]:
    """One canonical laps row with status-relevant columns."""
    return {
        "session_id": _FIXTURE_ID, "driver_id": driver_id, "lap_number": lap_number,
        "lap_start_time_ms": start_ms, "lap_end_time_ms": end_ms,
        "lap_duration_ms": duration_ms, "deleted": deleted,
        "deleted_reason": deleted_reason,
    }


def _laps_frame() -> pl.DataFrame:
    """Canonical final state: only HAM lap 2 is deleted."""
    rows = [
        _lap_row("HAM", 1, 0, 90_000, 90_000),
        _lap_row("HAM", 2, 90_000, 180_000, 90_000, deleted=True,
                 deleted_reason="TRACK LIMITS"),
        _lap_row("HAM", 3, 180_000, 270_000, 90_000),
        _lap_row("VER", 1, 0, 91_000, 91_000),
        _lap_row("VER", 2, 91_000, 182_000, 91_000),
    ]
    return pl.DataFrame(rows, schema=dict(CANONICAL_TABLE_SCHEMAS_V2["laps"]), strict=True)


def _lap_status_messages_frame() -> pl.DataFrame:
    """Deleted -> reinstated -> deleted causal sequence for HAM lap 2."""
    return pl.DataFrame([
        {"session_id": _FIXTURE_ID, "session_time_ms": 185_000, "message_index": 0,
         "message": _DELETED_MESSAGE, "driver_id": "HAM", "lap_number": 2},
        {"session_id": _FIXTURE_ID, "session_time_ms": 190_000, "message_index": 1,
         "message": _REINSTATED_MESSAGE, "driver_id": "HAM", "lap_number": 2},
        {"session_id": _FIXTURE_ID, "session_time_ms": 195_000, "message_index": 2,
         "message": _DELETED_MESSAGE, "driver_id": "HAM", "lap_number": 2},
    ], schema=dict(CANONICAL_TABLE_SCHEMAS_V2["race_control_messages"]), strict=True)


def _unrelated_messages_frame() -> pl.DataFrame:
    """Race-control rows without any lap-status marker."""
    return pl.DataFrame([
        {"session_id": _FIXTURE_ID, "session_time_ms": 185_000, "message_index": 0,
         "message": "GREEN FLAG", "driver_id": None, "lap_number": None},
    ], schema=dict(CANONICAL_TABLE_SCHEMAS_V2["race_control_messages"]), strict=True)


def _results_frame() -> pl.DataFrame:
    """Canonical qualifying results for the two manifest drivers."""
    return pl.DataFrame([
        {"session_id": _FIXTURE_ID, "driver_id": "HAM", "classified_position": "1",
         "grid_position": 1, "status": "Finished", "points": 0.0, "laps_completed": 3,
         "result_time_ms": None, "q1_time_ms": 90_000, "q2_time_ms": 88_500,
         "q3_time_ms": 87_200},
        {"session_id": _FIXTURE_ID, "driver_id": "VER", "classified_position": "2",
         "grid_position": 2, "status": "Finished", "points": 0.0, "laps_completed": 2,
         "result_time_ms": None, "q1_time_ms": 91_000, "q2_time_ms": None,
         "q3_time_ms": None},
    ], schema=dict(CANONICAL_TABLE_SCHEMAS_V2["results"]), strict=True)


def _frames(
    session_mode: str = "qualifying",
    *,
    include_race_control: bool = True,
    messages: Literal["lap_status", "unrelated"] = "lap_status",
) -> dict[str, pl.DataFrame]:
    """Build the canonical frames needed by qualifying-like publication."""
    frames: dict[str, pl.DataFrame] = {
        "session_metadata": _session_metadata(session_mode),
        "drivers": _drivers_frame(),
        "laps": _laps_frame(),
        "results": _results_frame(),
    }
    if include_race_control:
        messages_frame = (
            _lap_status_messages_frame()
            if messages == "lap_status" else _unrelated_messages_frame()
        )
        frames["race_control_messages"] = messages_frame
    return frames


def _snapshot(
    session_mode: str = "qualifying",
    *,
    include_race_control: bool = True,
    messages: Literal["lap_status", "unrelated"] = "lap_status",
) -> CanonicalGenerationSnapshot:
    return CanonicalGenerationSnapshot(
        "canonical-one", "a" * 64,
        _frames(session_mode, include_race_control=include_race_control, messages=messages),
    )


def _delivery(
    session_mode: str = "qualifying",
    *,
    qualifying_lap_status_sidecar: BrowserQualifyingLapStatusSidecar | None = None,
    include_race_control: bool = True,
    messages: Literal["lap_status", "unrelated"] = "lap_status",
) -> BrowserDeliveryBuild:
    """Build one immutable delivery for qualifying-like publication tests."""
    manifest = BrowserManifest(
        _FIXTURE_ID, "Italian Grand Prix Qualifying", _driver_metadata(),
        session_mode=cast(SessionMode, session_mode),
        contract_version="v2",
    )
    return BrowserDeliveryBuild(
        _snapshot(
            session_mode, include_race_control=include_race_control, messages=messages,
        ),
        manifest,
        _track_assets(),
        (_chunk(),),
        qualifying_lap_status_sidecar=qualifying_lap_status_sidecar,
    )


def _record_for(driver_id: str) -> BrowserQualifyingLapStatusRecord:
    """One canonical status record aligned to the laps frame."""
    if driver_id == "HAM":
        return BrowserQualifyingLapStatusRecord(
            lap_number=(1, 2, 3),
            lap_start_ms=(0, 90_000, 180_000),
            lap_end_ms=(90_000, 180_000, 270_000),
            status=("valid", "deleted", "valid"),
            deleted_reason=(None, "TRACK LIMITS", None),
        )
    return BrowserQualifyingLapStatusRecord(
        lap_number=(1, 2),
        lap_start_ms=(0, 91_000),
        lap_end_ms=(91_000, 182_000),
        status=("valid", "valid"),
        deleted_reason=(None, None),
    )


def _publish(
    browser: Path, delivery: BrowserDeliveryBuild,
) -> PublishedBrowserDelivery:
    return publish_browser_delivery(
        browser_parent=browser,
        delivery_version="delivery-v2",
        delivery=delivery,
        schema_root=SCHEMA_ROOT,
        contract_version="v2",
    )


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _serialize(value: object) -> bytes:
    """Deterministic publication-shaped JSON bytes used by the pipeline."""
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def _redigest_manifest_and_pointer(result: PublishedBrowserDelivery) -> None:
    """Rebind the pointer after a test rewrites the manifest bytes."""
    manifest_bytes = result.manifest_path.read_bytes()
    pointer = json.loads(result.pointer_path.read_bytes())
    pointer["manifestSha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    result.pointer_path.write_text(json.dumps(pointer), encoding="utf-8")


def _schema_registry(
    root: Path, names: tuple[str, ...],
) -> tuple[dict[str, Mapping[str, object]], Registry]:
    schemas: dict[str, Mapping[str, object]] = {
        name: _load_json(root / f"{name}.schema.json") for name in names
    }
    registry = Registry()
    for schema in schemas.values():
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    return schemas, registry


# ===========================================================================
# Qualifying-like publication
# ===========================================================================


class TestQualifyingLikePublication:
    """Positive publication of the V2 qualifying lap-status sidecar."""

    @pytest.mark.parametrize(
        "session_mode", ["qualifying", "sprint-qualifying", "sprint-shootout"],
    )
    def test_publication_creates_and_references_lap_status_sidecar(
        self, tmp_path: Path, session_mode: str,
    ) -> None:
        """✅ Positive: qualifying-like modes create and reference the sidecar."""
        # Arrange & Act
        result = _publish(tmp_path / "browser", _delivery(session_mode))

        # Assert
        assert result.qualifying_lap_status_path is not None
        assert result.qualifying_lap_status_path.exists()
        assert result.qualifying_lap_status_path.name == "qualifying-lap-status.json"
        manifest = _load_json(result.manifest_path)
        assert manifest["sessionMode"] == session_mode
        assert manifest["qualifyingLapStatus"] == {
            "path": "qualifying-lap-status.json",
            "schemaId": BROWSER_QUALIFYING_LAP_STATUS_SCHEMA_ID,
            "sha256": hashlib.sha256(
                result.qualifying_lap_status_path.read_bytes()
            ).hexdigest(),
        }
        assert manifest["schemas"]["qualifyingLapStatus"] == (
            BROWSER_QUALIFYING_LAP_STATUS_SCHEMA_ID
        )

    def test_sidecar_digest_matches_stored_bytes_and_result(
        self, tmp_path: Path,
    ) -> None:
        """✅ Positive: manifest, stored bytes, and result digests agree."""
        # Arrange & Act
        result = _publish(tmp_path / "browser", _delivery())
        sidecar_path = result.qualifying_lap_status_path
        assert sidecar_path is not None
        manifest = _load_json(result.manifest_path)

        # Assert
        stored_digest = hashlib.sha256(sidecar_path.read_bytes()).hexdigest()
        assert manifest["qualifyingLapStatus"]["sha256"] == stored_digest
        assert result.artifact_digests["qualifying-lap-status.json"] == stored_digest

    def test_sidecar_payload_reconciles_canonical_final_state(
        self, tmp_path: Path,
    ) -> None:
        """✅ Positive: payload mirrors canonical status and causal events."""
        # Arrange & Act
        result = _publish(tmp_path / "browser", _delivery())
        sidecar_path = result.qualifying_lap_status_path
        assert sidecar_path is not None
        sidecar = _load_json(sidecar_path)

        # Assert
        assert sidecar["contractVersion"] == "v2"
        assert sidecar["fixtureId"] == _FIXTURE_ID
        assert list(sidecar["drivers"]) == ["HAM", "VER"]
        assert sidecar["drivers"]["HAM"]["status"] == ["valid", "deleted", "valid"]
        assert sidecar["drivers"]["HAM"]["deletedReason"] == [
            None, "TRACK LIMITS", None,
        ]
        assert sidecar["drivers"]["VER"]["status"] == ["valid", "valid"]
        assert [event["status"] for event in sidecar["events"]] == [
            "deleted", "reinstated", "deleted",
        ]
        assert [event["eventTimeMs"] for event in sidecar["events"]] == [
            185_000, 190_000, 195_000,
        ]

    def test_sidecar_payload_passes_local_schema_registry(self, tmp_path: Path) -> None:
        """✅ Positive: sidecar and manifest satisfy the v2 schema registry."""
        # Arrange & Act
        result = _publish(tmp_path / "browser", _delivery())
        sidecar_path = result.qualifying_lap_status_path
        assert sidecar_path is not None
        sidecar = _load_json(sidecar_path)
        manifest = _load_json(result.manifest_path)
        schemas, registry = _schema_registry(SCHEMA_ROOT_V2, _V2_SCHEMA_NAMES)

        # Assert
        Draft202012Validator(
            schemas["browser-qualifying-lap-status"], registry=registry,
        ).validate(sidecar)
        Draft202012Validator(
            schemas["manifest"], registry=registry,
            format_checker=Draft202012Validator.FORMAT_CHECKER,
        ).validate(manifest)

    def test_publication_is_byte_identical(self, tmp_path: Path) -> None:
        """✅ Positive: the same delivery publishes byte-identical sidecars."""
        # Arrange & Act
        first = _publish(tmp_path / "browser-one", _delivery())
        second = _publish(tmp_path / "browser-two", _delivery())

        # Assert
        assert first.qualifying_lap_status_path is not None
        assert second.qualifying_lap_status_path is not None
        assert (
            first.qualifying_lap_status_path.read_bytes()
            == second.qualifying_lap_status_path.read_bytes()
        )

    def test_complete_validator_accepts_published_sidecar(self, tmp_path: Path) -> None:
        """✅ Positive: the published delivery passes deep validation."""
        # Arrange & Act
        _publish(tmp_path / "browser", _delivery())

        # Assert
        validate_complete_browser_delivery(
            tmp_path / "browser",
            expected_generation_id="canonical-one",
            expected_manifest_sha256="a" * 64,
            schema_root=SCHEMA_ROOT_V2,
        )


# ===========================================================================
# Publication rejection
# ===========================================================================


class TestPublicationRejection:
    """Negative publication and validation behavior for the sidecar."""

    def test_tampered_sidecar_bytes_rejected_on_validate(self, tmp_path: Path) -> None:
        """❌ Negative: a tampered stored sidecar fails digest validation."""
        # Arrange
        result = _publish(tmp_path / "browser", _delivery())
        sidecar_path = result.qualifying_lap_status_path
        assert sidecar_path is not None
        sidecar_path.write_bytes(
            sidecar_path.read_bytes().replace(b'"valid"', b'"VALID"')
        )

        # Act & Assert
        with pytest.raises(BrowserDeliveryPublicationError, match="validation failed") as error:
            validate_complete_browser_delivery(
                tmp_path / "browser",
                expected_generation_id="canonical-one",
                expected_manifest_sha256="a" * 64,
                schema_root=SCHEMA_ROOT_V2,
            )
        assert error.value.__cause__ is not None
        assert "checksum disagrees for qualifying-lap-status.json" in str(
            error.value.__cause__
        )

    def test_bad_reference_digest_rejected_on_validate(self, tmp_path: Path) -> None:
        """❌ Negative: a manifest reference digest disagreeing with the payload fails."""
        # Arrange: corrupt the reference digest without touching the artifact.
        result = _publish(tmp_path / "browser", _delivery())
        manifest = _load_json(result.manifest_path)
        manifest["qualifyingLapStatus"]["sha256"] = "0" * 64
        result.manifest_path.write_bytes(_serialize(manifest))
        _redigest_manifest_and_pointer(result)

        # Act & Assert
        with pytest.raises(BrowserDeliveryPublicationError, match="validation failed") as error:
            validate_complete_browser_delivery(
                tmp_path / "browser",
                expected_generation_id="canonical-one",
                expected_manifest_sha256="a" * 64,
                schema_root=SCHEMA_ROOT_V2,
            )
        assert error.value.__cause__ is not None
        assert "checksum disagrees for qualifying-lap-status.json" in str(
            error.value.__cause__
        )

    def test_bad_reference_path_rejected_on_validate(self, tmp_path: Path) -> None:
        """❌ Negative: a reference pointing at a missing artifact fails closed."""
        # Arrange: point the sidecar reference at a non-existent artifact.
        result = _publish(tmp_path / "browser", _delivery())
        manifest = _load_json(result.manifest_path)
        manifest["qualifyingLapStatus"]["path"] = "lap-status.json"
        result.manifest_path.write_bytes(_serialize(manifest))
        _redigest_manifest_and_pointer(result)

        # Act & Assert
        with pytest.raises(BrowserDeliveryPublicationError, match="validation failed"):
            validate_complete_browser_delivery(
                tmp_path / "browser",
                expected_generation_id="canonical-one",
                expected_manifest_sha256="a" * 64,
                schema_root=SCHEMA_ROOT_V2,
            )

    def test_invalid_event_ordering_rejected_on_validate(self, tmp_path: Path) -> None:
        """❌ Negative: an unordered event sequence fails contract validation."""
        # Arrange: reverse the three causal events and rebind all digests.
        result = _publish(tmp_path / "browser", _delivery())
        sidecar_path = result.qualifying_lap_status_path
        assert sidecar_path is not None
        sidecar = _load_json(sidecar_path)
        sidecar["events"] = list(reversed(sidecar["events"]))
        sidecar_bytes = _serialize(sidecar)
        sidecar_path.write_bytes(sidecar_bytes)
        manifest = _load_json(result.manifest_path)
        manifest["qualifyingLapStatus"]["sha256"] = hashlib.sha256(sidecar_bytes).hexdigest()
        result.manifest_path.write_bytes(_serialize(manifest))
        _redigest_manifest_and_pointer(result)

        # Act & Assert
        with pytest.raises(BrowserDeliveryPublicationError, match="validation failed") as error:
            validate_complete_browser_delivery(
                tmp_path / "browser",
                expected_generation_id="canonical-one",
                expected_manifest_sha256="a" * 64,
                schema_root=SCHEMA_ROOT_V2,
            )
        assert error.value.__cause__ is not None
        assert "not deterministically ordered" in str(error.value.__cause__)

    def test_wrong_driver_set_rejected_at_publication(self, tmp_path: Path) -> None:
        """❌ Negative: a sidecar driver set disagreeing with the manifest fails."""
        # Arrange: an explicit sidecar publishes an unpublished driver.
        sidecar = BrowserQualifyingLapStatusSidecar(
            _FIXTURE_ID,
            {
                "HAM": _record_for("HAM"),
                "LEG": BrowserQualifyingLapStatusRecord(
                    lap_number=(1,),
                    lap_start_ms=(0,),
                    lap_end_ms=(90_000,),
                    status=("valid",),
                    deleted_reason=(None,),
                ),
            },
        )
        delivery = _delivery(qualifying_lap_status_sidecar=sidecar)

        # Act & Assert
        with pytest.raises(
            BrowserDeliveryPublicationError,
            match="sidecar drivers disagree with the manifest",
        ):
            _publish(tmp_path / "browser", delivery)
        assert not (tmp_path / "browser").exists()

    def test_incomplete_prepared_set_rejected(self) -> None:
        """❌ Negative: a prepared delivery missing the sidecar payload is rejected."""
        # Arrange
        delivery = _delivery()
        payloads = tuple(_artifact_payloads("delivery-v2", delivery, SCHEMA_ROOT_V2))
        # A complete prepared set passes the digest-bound completeness check.
        _validate_delivery_payloads(payloads, delivery)
        reduced = tuple(
            artifact for artifact in payloads
            if artifact.path != "qualifying-lap-status.json"
        )

        # Act & Assert
        with pytest.raises(
            BrowserDeliveryPublicationError, match="prepared artifact digest disagrees",
        ):
            _validate_delivery_payloads(reduced, delivery)

    def test_missing_referenced_artifact_rejected_on_validate(
        self, tmp_path: Path,
    ) -> None:
        """❌ Negative: deleting a referenced artifact fails deep validation."""
        # Arrange
        result = _publish(tmp_path / "browser", _delivery())
        sidecar_path = result.qualifying_lap_status_path
        assert sidecar_path is not None
        sidecar_path.unlink()

        # Act & Assert
        with pytest.raises(BrowserDeliveryPublicationError, match="validation failed"):
            validate_complete_browser_delivery(
                tmp_path / "browser",
                expected_generation_id="canonical-one",
                expected_manifest_sha256="a" * 64,
                schema_root=SCHEMA_ROOT_V2,
            )


# ===========================================================================
# Mode gating
# ===========================================================================


class TestModeGating:
    """Qualifying-like gating and fail-closed input handling."""

    @pytest.mark.parametrize("session_mode", ["race", "practice", "sprint"])
    def test_non_qualifying_modes_omit_sidecar_even_with_messages(
        self, tmp_path: Path, session_mode: str,
    ) -> None:
        """✅ Positive: non-qualifying modes never publish the artifact."""
        # Arrange & Act: messages are present, but the mode is not qualifying-like.
        result = _publish(tmp_path / "browser", _delivery(session_mode))

        # Assert
        assert result.qualifying_lap_status_path is None
        assert "qualifying-lap-status.json" not in result.artifact_digests
        manifest = _load_json(result.manifest_path)
        assert "qualifyingLapStatus" not in manifest
        assert "qualifyingLapStatus" not in cast(
            dict[str, object], manifest["schemas"],
        )

    def test_qualifying_without_messages_omits_sidecar(self, tmp_path: Path) -> None:
        """✅ Positive: no deletion messages means no causal sidecar."""
        # Arrange & Act
        result = _publish(tmp_path / "browser", _delivery(messages="unrelated"))

        # Assert: the summary remains, but the causal sidecar is omitted.
        assert result.qualifying_lap_status_path is None
        manifest = _load_json(result.manifest_path)
        assert "qualifyingLapStatus" not in manifest
        assert manifest["qualifyingSummary"]["path"] == "qualifying-summary.json"

    def test_incomplete_lap_status_frames_fail_closed(self, tmp_path: Path) -> None:
        """❌ Negative: qualifying inputs missing one frame fail closed."""
        # Arrange & Act
        delivery = _delivery(include_race_control=False)

        # Act & Assert
        with pytest.raises(
            BrowserDeliveryPublicationError,
            match="qualifying lap-status inputs are incomplete",
        ):
            _publish(tmp_path / "browser", delivery)
        assert not (tmp_path / "browser").exists()

    def test_manifest_rejects_lap_status_for_non_qualifying_mode(self) -> None:
        """❌ Negative: the manifest model forbids the reference outside qualifying."""
        # Arrange
        reference = {
            "path": "qualifying-lap-status.json",
            "schemaId": BROWSER_QUALIFYING_LAP_STATUS_SCHEMA_ID,
            "sha256": "a" * 64,
        }

        # Act & Assert
        with pytest.raises(
            ValueError,
            match="qualifying_lap_status is valid only for qualifying-like modes",
        ):
            BrowserManifest(
                _FIXTURE_ID, "Monza", _driver_metadata(),
                qualifying_lap_status=reference, session_mode="race",
                contract_version="v2",
            )

    def test_v2_schema_forbids_lap_status_reference_outside_qualifying(
        self, tmp_path: Path,
    ) -> None:
        """❌ Negative: the v2 manifest schema rejects a race-mode reference."""
        # Arrange: publish a race delivery, then inject a qualifying reference.
        result = _publish(tmp_path / "browser", _delivery("race"))
        manifest = _load_json(result.manifest_path)
        manifest["qualifyingLapStatus"] = {
            "path": "qualifying-lap-status.json",
            "schemaId": BROWSER_QUALIFYING_LAP_STATUS_SCHEMA_ID,
            "sha256": "a" * 64,
        }
        schemas, registry = _schema_registry(SCHEMA_ROOT_V2, _V2_SCHEMA_NAMES)

        # Act & Assert
        with pytest.raises(ValidationError):
            Draft202012Validator(
                schemas["manifest"], registry=registry,
                format_checker=Draft202012Validator.FORMAT_CHECKER,
            ).validate(manifest)

