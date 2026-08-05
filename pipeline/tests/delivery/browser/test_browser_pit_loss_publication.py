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


def _chunk() -> BrowserChunk:
    fields = BrowserDriverFields(
        "HAM", (0, 1000), (1.0, 2.0), (3.0, 4.0), (5.0, 6.0), (7.0, 8.0),
        (0, 1), (None, 7), (None, 1), ("OnTrack", "OnTrack"), (1, 1), ("SOFT", "SOFT"),
        (False, False), (None, None), (None, None), (None, None),
    )
    return BrowserChunk(
        "chunk-001", 1, 0, 2000, BrowserOverlap("none", None, None, None, None),
        (0, 1000), 0, {"HAM": fields}, (('HAM',), ('HAM',)), (1, 1),
        ("clear", "clear"), (BrowserEvent(1000, "notice", "green flag"),),
    )


def _delivery(model: BrowserPitLossModel | None = None) -> BrowserDeliveryBuild:
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
        manifest, assets, (_chunk(),), pit_loss_model=model,
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
