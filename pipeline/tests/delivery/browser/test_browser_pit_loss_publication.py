"""Focused publication integration coverage for the optional pit-loss model."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import polars as pl
import pytest

from f1_replay_pipeline.delivery.browser.browser_chunk_builder import (
    BrowserChunk,
    BrowserEvent,
    BrowserOverlap,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    BrowserDriverFields,
    BrowserManifest,
    BrowserPitLossModel,
    CanonicalGenerationSnapshot,
    PIT_LOSS_ESTIMATE_METHOD,
    PIT_LOSS_ESTIMATE_SIDECAR_FILENAME,
    PIT_LOSS_ESTIMATE_SIDECAR_SCHEMA_ID,
    PIT_LOSS_MODEL_SCHEMA_ID,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_orchestration import (
    BrowserDeliveryBuild,
)
from f1_replay_pipeline.domain.canonical_schema import CANONICAL_TABLE_SCHEMAS_V2
from f1_replay_pipeline.delivery.browser.browser_delivery_publication import (
    BrowserDeliveryPublicationError,
    publish_browser_delivery,
    validate_complete_browser_delivery,
)
from f1_replay_pipeline.delivery.browser.browser_pit_loss_sidecar import (
    BrowserPitLossEstimateSidecar,
    BrowserPitLossEstimateTimeline,
)


REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMA_ROOT = REPO_ROOT / "contracts" / "replay-data" / "v2" / "schemas"


def _snapshot() -> CanonicalGenerationSnapshot:
    session_metadata = pl.DataFrame([{
        "session_id": "race-one", "year": 2026, "round_number": 1,
        "event_name": "Race", "session_name": "Race", "session_type": "R",
        "session_mode": "race", "session_start_time_utc": None,
    }], schema=dict(CANONICAL_TABLE_SCHEMAS_V2["session_metadata"]), strict=True)
    return CanonicalGenerationSnapshot(
        "canonical-one", "a" * 64, {"session_metadata": session_metadata},
    )


def _chunk(track_status: tuple[int, int] = (1, 1)) -> BrowserChunk:
    fields = BrowserDriverFields(
        "HAM", (0, 1000), (1.0, 2.0), (3.0, 4.0), (5.0, 6.0), (7.0, 8.0),
        (0, 1), (None, 7), (None, 1), ("OnTrack", "OnTrack"), (1, 1), ("SOFT", "SOFT"),
        (False, False), (None, None), (None, None), (None, None),
    )
    return BrowserChunk(
        "chunk-001", 1, 0, 2000, BrowserOverlap("none", None, None, None, None),
         (0, 1000), 0, {"HAM": fields}, (('HAM',), ('HAM',)), track_status,
        ("clear", "clear"), (BrowserEvent(1000, "notice", "green flag"),),
    )


def _delivery(
    model: BrowserPitLossModel | None = None,
    pit_loss_estimate_sidecar: BrowserPitLossEstimateSidecar | None = None,
    track_status: tuple[int, int] = (1, 1),
) -> BrowserDeliveryBuild:
    manifest = BrowserManifest("race-one", "Race One", ({
        "id": "HAM", "displayName": "Hamilton", "teamName": "Team",
        "colorHex": "#000000", "carNumber": "44",
    },), session_mode="race")
    point = {"x": 0.0, "y": 0.0}
    polyline = (point, {"x": 1.0, "y": 0.0}, {"x": 1.0, "y": 1.0}, {"x": 0.0, "y": 1.0})
    assets = {
        "contractVersion": "v2", "fixtureId": "race-one", "trackId": "track-one",
        "trackName": "Track One", "coordinateSpace": {"units": "meters", "origin": "test"},
        "circuitLengthMeters": 1.0, "rotationDegrees": 0.0,
        "startFinish": {"center": point, "inner": point, "outer": point},
        "centerLine": polyline, "innerBoundary": polyline, "outerBoundary": polyline,
    }
    return BrowserDeliveryBuild(
        _snapshot(),
        manifest, assets, (_chunk(track_status),), pit_loss_model=model,
        pit_loss_estimate_sidecar=pit_loss_estimate_sidecar,
    )


def _model(*, start_ms: int = 0) -> BrowserPitLossModel:
    return BrowserPitLossModel(
        "race-one", "global-prior-weighted-mean-v1", 22_000, 2,
        (start_ms, 1_500), (22_000, 21_000), (0, 1),
    )


def _publish(browser: Path, delivery: BrowserDeliveryBuild):
    return publish_browser_delivery(
        browser_parent=browser, delivery_version="delivery-one", delivery=delivery,
        schema_root=SCHEMA_ROOT,
    )


def test_pit_loss_model_round_trip_binds_digest_and_validates(tmp_path: Path) -> None:
    delivery = _delivery(_model())
    assert "pitLossModel" not in delivery.manifest.as_dict()

    result = _publish(tmp_path / "browser", delivery)
    model_path = result.generation_path / "pit-loss-model.json"
    manifest = json.loads(result.manifest_path.read_bytes())

    assert result.pit_loss_model_path == model_path
    assert manifest["pitLossModel"] == {
        "path": "pit-loss-model.json",
        "schemaId": PIT_LOSS_MODEL_SCHEMA_ID,
        "sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
    }
    assert result.artifact_digests["pit-loss-model.json"] == manifest["pitLossModel"]["sha256"]
    validate_complete_browser_delivery(
        tmp_path / "browser", expected_generation_id="canonical-one",
        expected_manifest_sha256="a" * 64, schema_root=SCHEMA_ROOT,
    )


def test_mutated_pit_loss_model_bytes_are_rejected_by_secure_validation(tmp_path: Path) -> None:
    result = _publish(tmp_path / "browser", _delivery(_model()))
    model_path = result.generation_path / "pit-loss-model.json"
    model_path.write_bytes(model_path.read_bytes().replace(b"21000", b"21001"))

    with pytest.raises(BrowserDeliveryPublicationError, match="validation failed"):
        validate_complete_browser_delivery(
            tmp_path / "browser", expected_generation_id="canonical-one",
            expected_manifest_sha256="a" * 64, schema_root=SCHEMA_ROOT,
        )


def test_v2_manifest_without_pit_loss_model_remains_valid(tmp_path: Path) -> None:
    result = _publish(tmp_path / "browser", _delivery())
    manifest = json.loads(result.manifest_path.read_bytes())

    assert "pitLossModel" not in manifest
    assert result.pit_loss_model_path is None
    validate_complete_browser_delivery(
        tmp_path / "browser", expected_generation_id="canonical-one",
        expected_manifest_sha256="a" * 64, schema_root=SCHEMA_ROOT,
    )


def test_pit_loss_model_refinement_must_start_at_replay_start(tmp_path: Path) -> None:
    with pytest.raises(BrowserDeliveryPublicationError, match="timestamps"):
        _publish(tmp_path / "browser", _delivery(_model(start_ms=1)))


def test_browser_delivery_build_rejects_an_untyped_pit_loss_model() -> None:
    with pytest.raises(TypeError, match="BrowserPitLossModel"):
        BrowserDeliveryBuild(
            _snapshot(),
            _delivery().manifest, _delivery().track_assets, (_chunk(),),
            pit_loss_model=object(),  # type: ignore[arg-type]
        )


def _sidecar() -> BrowserPitLossEstimateSidecar:
    return BrowserPitLossEstimateSidecar(
        "race-one", "track-one", PIT_LOSS_ESTIMATE_METHOD,
        BrowserPitLossEstimateTimeline((0,), (21_500,), (1,)),
    )


def test_pit_loss_estimate_sidecar_round_trip_binds_digest_and_validates(tmp_path: Path) -> None:
    delivery = _delivery(pit_loss_estimate_sidecar=_sidecar())
    assert "pitLossEstimateSidecar" not in delivery.manifest.as_dict()

    result = _publish(tmp_path / "browser", delivery)
    sidecar_path = result.generation_path / PIT_LOSS_ESTIMATE_SIDECAR_FILENAME
    manifest = json.loads(result.manifest_path.read_bytes())

    assert result.pit_loss_estimate_sidecar_path == sidecar_path
    assert manifest["pitLossEstimateSidecar"] == {
        "path": PIT_LOSS_ESTIMATE_SIDECAR_FILENAME,
        "schemaId": PIT_LOSS_ESTIMATE_SIDECAR_SCHEMA_ID,
        "sha256": hashlib.sha256(sidecar_path.read_bytes()).hexdigest(),
    }
    assert result.artifact_digests[PIT_LOSS_ESTIMATE_SIDECAR_FILENAME] == manifest["pitLossEstimateSidecar"]["sha256"]
    validate_complete_browser_delivery(
        tmp_path / "browser", expected_generation_id="canonical-one",
        expected_manifest_sha256="a" * 64, schema_root=SCHEMA_ROOT,
    )


def test_legacy_delivery_without_pit_loss_estimate_sidecar_remains_valid(tmp_path: Path) -> None:
    result = _publish(tmp_path / "browser", _delivery())
    manifest = json.loads(result.manifest_path.read_bytes())

    assert "pitLossEstimateSidecar" not in manifest
    assert result.pit_loss_estimate_sidecar_path is None
    validate_complete_browser_delivery(
        tmp_path / "browser", expected_generation_id="canonical-one",
        expected_manifest_sha256="a" * 64, schema_root=SCHEMA_ROOT,
    )


def test_legacy_pit_loss_model_and_estimate_sidecar_can_coexist(tmp_path: Path) -> None:
    delivery = _delivery(_model(), pit_loss_estimate_sidecar=_sidecar())
    result = _publish(tmp_path / "browser", delivery)
    manifest = json.loads(result.manifest_path.read_bytes())

    assert manifest["pitLossModel"]["path"] == "pit-loss-model.json"
    assert manifest["pitLossEstimateSidecar"]["path"] == PIT_LOSS_ESTIMATE_SIDECAR_FILENAME
    assert result.pit_loss_model_path is not None
    assert result.pit_loss_estimate_sidecar_path is not None
    validate_complete_browser_delivery(
        tmp_path / "browser", expected_generation_id="canonical-one",
        expected_manifest_sha256="a" * 64, schema_root=SCHEMA_ROOT,
    )


def test_status_timeline_without_observations_must_be_unavailable(tmp_path: Path) -> None:
    sidecar = BrowserPitLossEstimateSidecar(
        "race-one", "track-one", PIT_LOSS_ESTIMATE_METHOD,
        BrowserPitLossEstimateTimeline((0, 1_000), (22_000, 21_500), (0, 1)),
        safety_car=BrowserPitLossEstimateTimeline((0,), (22_000,), (0,)),
    )

    with pytest.raises(BrowserDeliveryPublicationError, match="without observations"):
        _publish(
            tmp_path / "browser",
            _delivery(pit_loss_estimate_sidecar=sidecar, track_status=(4, 4)),
        )


def test_legacy_sidecar_payload_and_manifest_never_expose_source_status(
    tmp_path: Path,
) -> None:
    # Arrange / Act: publish the legacy estimate sidecar delivery.
    result = _publish(tmp_path / "browser", _delivery(pit_loss_estimate_sidecar=_sidecar()))
    sidecar_text = result.generation_path.joinpath(
        PIT_LOSS_ESTIMATE_SIDECAR_FILENAME,
    ).read_text(encoding="utf-8")
    manifest_text = result.manifest_path.read_text(encoding="utf-8")

    # Assert: catalog-only sourceStatus is absent from both browser artifacts.
    assert "sourceStatus" not in sidecar_text
    assert "sourceStatus" not in manifest_text


def test_schema_rejects_legacy_sidecar_with_source_status(tmp_path: Path) -> None:
    # Arrange: publish a valid legacy sidecar, then re-digest a mutated copy
    # that injects the catalog-only sourceStatus into the payload.
    _publish(tmp_path / "browser", _delivery(pit_loss_estimate_sidecar=_sidecar()))
    sidecar_path = (
        tmp_path / "browser" / "generations" / "delivery-one"
        / PIT_LOSS_ESTIMATE_SIDECAR_FILENAME
    )
    sidecar = json.loads(sidecar_path.read_bytes())
    sidecar["sourceStatus"] = "derived"
    sidecar_bytes = json.dumps(sidecar, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    sidecar_path.write_bytes(sidecar_bytes)

    manifest_path = tmp_path / "browser" / "generations" / "delivery-one" / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["pitLossEstimateSidecar"]["sha256"] = hashlib.sha256(sidecar_bytes).hexdigest()
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    manifest_path.write_bytes(manifest_bytes)

    pointer_path = tmp_path / "browser" / "browser-current.json"
    pointer = json.loads(pointer_path.read_bytes())
    pointer["manifestSha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

    # Act / Assert: the complete validator rejects the leaked field.
    with pytest.raises(BrowserDeliveryPublicationError, match="validation failed") as error:
        validate_complete_browser_delivery(
            tmp_path / "browser", expected_generation_id="canonical-one",
            expected_manifest_sha256="a" * 64, schema_root=SCHEMA_ROOT,
        )
    assert error.value.__cause__ is not None
    assert "pit loss estimate sidecar fails replay-data v2 schema validation" in str(
        error.value.__cause__,
    )
