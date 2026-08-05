"""Focused legacy compatibility coverage for browser pit-loss delivery.

The legacy browser delivery path publishes a global prior-weighted pit-loss
model and, optionally, the status-aware ``track-status-median-v1`` estimate
sidecar.  These tests verify that pitLossModel-only and sidecar-free
deliveries remain readable, that legacy causal timelines round-trip, that
browser chunk shapes stay byte-identical with or without the additive
artifacts, and that publication never touches templates/ or R2 paths.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

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
    LEGACY_PIT_LOSS_ESTIMATE_METHOD,
    PIT_LOSS_ESTIMATE_SIDECAR_FILENAME,
    PIT_LOSS_MODEL_SCHEMA_ID,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_orchestration import (
    BrowserDeliveryBuild,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_publication import (
    publish_browser_delivery,
    validate_complete_browser_delivery,
)
from f1_replay_pipeline.delivery.browser.browser_pit_loss_model import (
    build_pit_loss_estimate_sidecar,
)
from f1_replay_pipeline.delivery.browser.browser_pit_loss_sidecar import (
    BrowserPitLossEstimateSidecar,
    BrowserPitLossEstimateTimeline,
)


SCHEMA_ROOT = Path(__file__).resolve().parents[4] / "contracts" / "replay-data" / "v1" / "schemas"


def _snapshot() -> CanonicalGenerationSnapshot:
    return CanonicalGenerationSnapshot("canonical-one", "a" * 64, {})


def _chunk() -> BrowserChunk:
    fields = BrowserDriverFields(
        "HAM", (0, 1000), (1.0, 2.0), (3.0, 4.0), (5.0, 6.0), (7.0, 8.0),
        (0, 1), (None, 7), (None, 1), ("OnTrack", "OnTrack"), (1, 1), ("SOFT", "SOFT"),
        (False, False), (None, None), (None, None), (None, None),
    )
    return BrowserChunk(
        "chunk-001", 1, 0, 2000, BrowserOverlap("none", None, None, None, None),
        (0, 1000), 0, {"HAM": fields}, (("HAM",), ("HAM",)), (1, 1),
        ("clear", "clear"), (BrowserEvent(1000, "notice", "green flag"),),
    )


def _delivery(
    *,
    pit_loss_model: BrowserPitLossModel | None = None,
    pit_loss_estimate_sidecar: BrowserPitLossEstimateSidecar | None = None,
) -> BrowserDeliveryBuild:
    manifest = BrowserManifest("race-one", "Race One", ({
        "id": "HAM", "displayName": "Hamilton", "teamName": "Team",
        "colorHex": "#000000", "carNumber": "44",
    },))
    point = {"x": 0.0, "y": 0.0}
    polyline = (point, {"x": 1.0, "y": 0.0}, {"x": 1.0, "y": 1.0}, {"x": 0.0, "y": 1.0})
    assets: dict[str, object] = {
        "contractVersion": "v1", "fixtureId": "race-one", "trackId": "track-one",
        "trackName": "Track One", "coordinateSpace": {"units": "meters", "origin": "test"},
        "circuitLengthMeters": 1.0, "rotationDegrees": 0.0,
        "startFinish": {"center": point, "inner": point, "outer": point},
        "centerLine": polyline, "innerBoundary": polyline, "outerBoundary": polyline,
    }
    return BrowserDeliveryBuild(
        _snapshot(), manifest, assets, (_chunk(),), pit_loss_model=pit_loss_model,
        pit_loss_estimate_sidecar=pit_loss_estimate_sidecar,
    )


def _model(*, start_ms: int = 0) -> BrowserPitLossModel:
    return BrowserPitLossModel(
        "race-one", "global-prior-weighted-mean-v1", 22_000, 2,
        (start_ms, 1_500), (22_000, 21_000), (0, 1),
    )


def _legacy_sidecar() -> BrowserPitLossEstimateSidecar:
    return BrowserPitLossEstimateSidecar(
        "race-one", "track-one", LEGACY_PIT_LOSS_ESTIMATE_METHOD,
        BrowserPitLossEstimateTimeline((0, 1_000), (22_000, 21_000), (0, 1)),
    )


def _publish(browser: Path, delivery: BrowserDeliveryBuild):
    return publish_browser_delivery(
        browser_parent=browser, delivery_version="delivery-one", delivery=delivery,
        schema_root=SCHEMA_ROOT,
    )


def test_pit_loss_model_only_delivery_remains_readable_and_valid(tmp_path: Path) -> None:
    # Arrange: a delivery carrying only the legacy pit-loss model.
    delivery = _delivery(pit_loss_model=_model())
    assert "pitLossModel" not in delivery.manifest.as_dict()

    # Act: publish and read back the model artifact and manifest.
    result = _publish(tmp_path / "browser", delivery)
    manifest = json.loads(result.manifest_path.read_bytes())
    model_path = result.pit_loss_model_path
    assert model_path is not None

    # Assert: the model is referenced, the estimate sidecar is absent, and the
    # complete delivery validates.
    assert manifest["pitLossModel"] == {
        "path": "pit-loss-model.json",
        "schemaId": PIT_LOSS_MODEL_SCHEMA_ID,
        "sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
    }
    assert json.loads(model_path.read_bytes()) == _model().as_dict()
    assert "pitLossEstimateSidecar" not in manifest
    assert result.pit_loss_estimate_sidecar_path is None
    validate_complete_browser_delivery(
        tmp_path / "browser", expected_generation_id="canonical-one",
        expected_manifest_sha256="a" * 64, schema_root=SCHEMA_ROOT,
    )


def test_sidecar_free_legacy_delivery_remains_readable_and_valid(tmp_path: Path) -> None:
    # Arrange / Act: publish a delivery with neither additive pit-loss artifact.
    result = _publish(tmp_path / "browser", _delivery())
    manifest = json.loads(result.manifest_path.read_bytes())

    # Assert: the legacy manifest omits both artifacts and still validates.
    assert "pitLossModel" not in manifest
    assert "pitLossEstimateSidecar" not in manifest
    assert result.pit_loss_model_path is None
    assert result.pit_loss_estimate_sidecar_path is None
    validate_complete_browser_delivery(
        tmp_path / "browser", expected_generation_id="canonical-one",
        expected_manifest_sha256="a" * 64, schema_root=SCHEMA_ROOT,
    )


def test_pit_loss_model_and_legacy_estimate_sidecar_coexist(tmp_path: Path) -> None:
    # Arrange: the legacy model and estimate sidecar coexist on one delivery.
    delivery = _delivery(pit_loss_model=_model(), pit_loss_estimate_sidecar=_legacy_sidecar())

    # Act: publish the combined delivery.
    result = _publish(tmp_path / "browser", delivery)
    manifest = json.loads(result.manifest_path.read_bytes())

    # Assert: both artifact references and payloads are present and valid.
    assert manifest["pitLossModel"]["path"] == "pit-loss-model.json"
    assert manifest["pitLossEstimateSidecar"]["path"] == PIT_LOSS_ESTIMATE_SIDECAR_FILENAME
    assert result.pit_loss_model_path is not None
    assert result.pit_loss_estimate_sidecar_path is not None
    validate_complete_browser_delivery(
        tmp_path / "browser", expected_generation_id="canonical-one",
        expected_manifest_sha256="a" * 64, schema_root=SCHEMA_ROOT,
    )


def test_legacy_pit_loss_artifacts_never_contain_source_status(tmp_path: Path) -> None:
    # Arrange / Act: publish the legacy model and estimate sidecar together.
    result = _publish(
        tmp_path / "browser",
        _delivery(pit_loss_model=_model(), pit_loss_estimate_sidecar=_legacy_sidecar()),
    )

    # Assert: catalog-only sourceStatus is absent from every stored JSON payload.
    assert result.pit_loss_model_path is not None
    assert result.pit_loss_estimate_sidecar_path is not None
    stored = (
        result.manifest_path,
        result.pit_loss_model_path,
        result.pit_loss_estimate_sidecar_path,
    )
    assert all(
        "sourceStatus" not in path.read_text(encoding="utf-8") for path in stored
    )


def test_legacy_causal_timeline_sidecar_round_trips_through_publication(tmp_path: Path) -> None:
    # Arrange: a legacy multi-point causal timeline remains a supported shape.
    sidecar = _legacy_sidecar()

    # Act: publish the legacy sidecar and parse its stored payload.
    result = _publish(tmp_path / "browser", _delivery(pit_loss_estimate_sidecar=sidecar))
    payload = json.loads((result.generation_path / PIT_LOSS_ESTIMATE_SIDECAR_FILENAME).read_bytes())

    # Assert: the causal timeline round-trips exactly and the delivery validates.
    assert payload == sidecar.as_dict()
    assert payload["race"] == {
        "timeMs": [0, 1_000],
        "estimatedLossMs": [22_000, 21_000],
        "observedSampleCount": [0, 1],
    }
    validate_complete_browser_delivery(
        tmp_path / "browser", expected_generation_id="canonical-one",
        expected_manifest_sha256="a" * 64, schema_root=SCHEMA_ROOT,
    )


def test_legacy_sidecar_emits_known_values_when_sc_vsc_absent() -> None:
    # Arrange: the race contains only the normal status code, so SC/VSC never
    # occurred in the canonical timeline.
    sidecar = build_pit_loss_estimate_sidecar(
        0, (), fixture_id="race-one", track_id="track-one", track_status_codes=(1,),
    )

    # Act: serialize the legacy sidecar.
    payload = sidecar.as_dict()

    # Assert: only the race value is emitted and it is the known 22-second
    # baseline with zero observations.
    assert payload["method"] == LEGACY_PIT_LOSS_ESTIMATE_METHOD
    assert payload["race"] == {
        "timeMs": [0],
        "estimatedLossMs": [22_000],
        "observedSampleCount": [0],
    }
    assert "safetyCar" not in payload
    assert "virtualSafetyCar" not in payload


def test_legacy_sidecar_marks_occurring_but_unobserved_status_unavailable() -> None:
    # Arrange: Safety Car occurs in the canonical timeline, but no eligible
    # stop can be assigned to it.
    sidecar = build_pit_loss_estimate_sidecar(
        0, (), fixture_id="race-one", track_id="track-one", track_status_codes=(1, 4),
    )

    # Act: serialize the legacy sidecar.
    payload = sidecar.as_dict()

    # Assert: the occurring status is explicitly unavailable rather than omitted.
    assert payload["safetyCar"] == {"status": "unavailable"}
    assert "virtualSafetyCar" not in payload


def test_additive_pit_loss_artifacts_do_not_change_browser_chunk_shapes(tmp_path: Path) -> None:
    # Arrange: one delivery without additive artifacts and one with the legacy
    # model and estimate sidecar.
    bare = _delivery()
    enriched = _delivery(pit_loss_model=_model(), pit_loss_estimate_sidecar=_legacy_sidecar())

    # Act: publish the identical chunk payload to two independent targets.
    bare_result = _publish(tmp_path / "browser-one", bare)
    enriched_result = _publish(tmp_path / "browser-two", enriched)

    # Assert: chunk bytes and chunk references are unchanged by the additives.
    assert [path.read_bytes() for path in bare_result.chunk_paths] == [
        path.read_bytes() for path in enriched_result.chunk_paths
    ]
    assert json.loads(bare_result.manifest_path.read_bytes())["chunks"] == json.loads(
        enriched_result.manifest_path.read_bytes(),
    )["chunks"]


def test_publication_never_touches_templates_or_r2_paths(tmp_path: Path) -> None:
    # Arrange: publish the richest legacy delivery (model + estimate sidecar).
    browser = tmp_path / "browser"
    result = _publish(browser, _delivery(
        pit_loss_model=_model(), pit_loss_estimate_sidecar=_legacy_sidecar(),
    ))
    manifest = json.loads(result.manifest_path.read_bytes())

    # Assert: no templates/ or R2 paths are referenced by the manifest.
    assert "templates" not in manifest
    assert "r2" not in manifest
    references = (
        manifest["trackAssets"],
        manifest["pitLossModel"],
        manifest["pitLossEstimateSidecar"],
        *manifest["chunks"],
    )
    assert all(
        "templates" not in str(reference.get("path", ""))
        and "r2" not in str(reference.get("path", ""))
        for reference in references
    )

    # Assert: publication writes only local artifacts under the browser root,
    # never a templates/ directory and never an R2 staging sibling.
    assert set(tmp_path.iterdir()) == {browser}
    relative_files = {
        path.relative_to(browser).as_posix() for path in browser.rglob("*") if path.is_file()
    }
    assert not any("templates" in path for path in relative_files)
    assert relative_files <= {
        "browser-current.json",
        "generations/delivery-one/manifest.json",
        "generations/delivery-one/track-assets.json",
        "generations/delivery-one/pit-loss-model.json",
        f"generations/delivery-one/{PIT_LOSS_ESTIMATE_SIDECAR_FILENAME}",
        "generations/delivery-one/chunks/chunk-001.json",
        ".canonical-parquet-recovery.lock",
    }
