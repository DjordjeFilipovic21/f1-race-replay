"""Focused deterministic publication coverage for browser delivery artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest

from f1_replay_pipeline.browser_chunk_builder import BrowserChunk, BrowserEvent, BrowserOverlap
from f1_replay_pipeline.browser_delivery_models import (
    BrowserDriverFields, BrowserManifest, CanonicalGenerationSnapshot,
)
from f1_replay_pipeline.browser_delivery_orchestration import BrowserDeliveryBuild
from f1_replay_pipeline.browser_delivery_publication import (
    BrowserDeliveryPublicationError, BrowserValidationProgress, _artifact_payloads,
    _load_contract_schemas, _validate_delivery_payloads, publish_browser_delivery,
)


SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "contracts" / "replay-data" / "v1" / "schemas"


def _snapshot() -> CanonicalGenerationSnapshot:
    return CanonicalGenerationSnapshot("canonical-one", "a" * 64, {})


def _chunk() -> BrowserChunk:
    fields = BrowserDriverFields(
        "HAM", (0, 1000), (1.0, 2.0), (3.0, 4.0), (5.0, 6.0), (7.0, 8.0),
        (0, 1), (None, 7), (None, 1), ("OnTrack", "OnTrack"), (1, 1), ("SOFT", "SOFT"),
        (False, False), (None, None), (None, None), (None, None),
    )
    return BrowserChunk(
        "chunk-001", 1, 0, 2000, BrowserOverlap("none", None, None, None, None), (0, 1000), 0,
        {"HAM": fields}, (("HAM",), ("HAM",)), (1, 1), ("clear", "clear"),
        (BrowserEvent(1000, "notice", "green flag"),),
    )


def _delivery(track_assets: dict[str, object] | None = None) -> BrowserDeliveryBuild:
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
    if track_assets is not None:
        assets.update(track_assets)
    return BrowserDeliveryBuild(_snapshot(), manifest, assets, (_chunk(),))


def _publish(browser: Path):
    return publish_browser_delivery(
        browser_parent=browser, delivery_version="delivery-one", delivery=_delivery(),
        schema_root=SCHEMA_ROOT,
    )


def test_publication_is_byte_identical(tmp_path: Path) -> None:
    first = _publish(tmp_path / "browser-one")
    second = _publish(tmp_path / "browser-two")

    assert [path.read_bytes() for path in (first.manifest_path, first.track_assets_path, *first.chunk_paths)] == [
        path.read_bytes() for path in (second.manifest_path, second.track_assets_path, *second.chunk_paths)
    ]


def test_manifest_references_are_ordered_and_digested(tmp_path: Path) -> None:
    result = _publish(tmp_path / "browser")
    manifest = json.loads(result.manifest_path.read_bytes())

    assert manifest["chunks"] == [{
        "endMs": 2000, "overlapWithPreviousMs": 0,
        "path": "chunks/chunk-001.json",
        "schemaId": "urn:f1-cache-replay:schema:replay-data:v1:chunk", "sequence": 1,
        "sha256": hashlib.sha256(result.chunk_paths[0].read_bytes()).hexdigest(), "startMs": 0,
    }]


def test_publication_reports_completed_validation_boundaries(tmp_path: Path) -> None:
    progress: list[str | BrowserValidationProgress] = []

    publish_browser_delivery(
        browser_parent=tmp_path / "browser", delivery_version="delivery-one",
        delivery=_delivery(), schema_root=SCHEMA_ROOT, progress=progress.append,
    )

    assert [update for update in progress if isinstance(update, BrowserValidationProgress)] == [
        BrowserValidationProgress("browser_schema_artifact_validating", 1, 6, "manifest"),
        BrowserValidationProgress("browser_schema_artifact_validating", 2, 6, "track assets"),
        BrowserValidationProgress("browser_schema_artifact_validating", 3, 6, "chunk 1/1"),
        BrowserValidationProgress("browser_schema_artifact_validating", 4, 6, "manifest schema"),
        BrowserValidationProgress("browser_schema_artifact_validating", 5, 6, "track assets schema"),
        BrowserValidationProgress("browser_schema_artifact_validating", 6, 6, "chunk schema 1/1"),
    ]


@pytest.mark.parametrize("version", ["../escape", "bad version", ""])
def test_publication_rejects_unsafe_delivery_id(tmp_path: Path, version: str) -> None:
    with pytest.raises(BrowserDeliveryPublicationError, match="safe path component"):
        publish_browser_delivery(
            browser_parent=tmp_path / "browser", delivery_version=version, delivery=_delivery(),
            schema_root=SCHEMA_ROOT,
        )


def test_publication_rejects_non_finite_metadata_before_creating_output(tmp_path: Path) -> None:
    browser = tmp_path / "browser"
    with pytest.raises(ValueError, match="finite"):
        publish_browser_delivery(
            browser_parent=browser, delivery_version="delivery-one",
            delivery=_delivery({"bad": float("nan")}),
            schema_root=SCHEMA_ROOT,
        )

    assert not browser.exists()


def test_publication_rejects_a_symlinked_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    browser = tmp_path / "browser"
    browser.symlink_to(outside, target_is_directory=True)

    with pytest.raises(BrowserDeliveryPublicationError, match="symlink"):
        _publish(browser)

    assert tuple(outside.iterdir()) == ()


def test_publication_rejects_a_symlinked_generations_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    browser = tmp_path / "browser"
    browser.mkdir()
    (browser / "generations").symlink_to(outside, target_is_directory=True)

    with pytest.raises(BrowserDeliveryPublicationError, match="secure"):
        _publish(browser)

    assert tuple(outside.iterdir()) == ()


def test_complete_validator_rejects_chunk_bytes_that_disagree_with_manifest_digest() -> None:
    delivery = _delivery()
    payloads = list(_artifact_payloads("delivery-one", delivery))
    index = next(index for index, (path, _) in enumerate(payloads) if path.startswith("chunks/"))
    path, payload = payloads[index]
    payloads[index] = (path, payload.replace(b'"startMs":0', b'"startMs":1'))

    schemas, registry = _load_contract_schemas(SCHEMA_ROOT)
    with pytest.raises(BrowserDeliveryPublicationError, match="digest"):
        _validate_delivery_payloads(tuple(payloads), delivery, schemas, registry)


def test_publication_rejects_schema_invalid_nested_track_assets_before_staging(tmp_path: Path) -> None:
    browser = tmp_path / "browser"
    delivery = _delivery({"coordinateSpace": {"units": "feet", "origin": "test"}})

    with pytest.raises(BrowserDeliveryPublicationError, match="schema validation"):
        publish_browser_delivery(
            browser_parent=browser,
            delivery_version="delivery-one",
            delivery=delivery,
            schema_root=SCHEMA_ROOT,
        )

    assert not browser.exists()


def test_delivery_build_deep_freezes_nested_track_assets() -> None:
    delivery = _delivery()
    coordinate_space = delivery.track_assets["coordinateSpace"]
    assert isinstance(coordinate_space, Mapping)

    with pytest.raises(TypeError):
        coordinate_space["units"] = "feet"  # type: ignore[index]


def test_publication_round_trips_nested_immutable_event_payload(tmp_path: Path) -> None:
    event = BrowserEvent(
        1_000, "race_control", "nested payload", "HAM",
        {"metadata": {"flags": ["GREEN", "CLEAR"]}},
    )
    delivery = _delivery()
    delivery = replace(delivery, chunks=(replace(delivery.chunks[0], events=(event,)),))

    result = publish_browser_delivery(
        browser_parent=tmp_path / "browser", delivery_version="delivery-nested",
        delivery=delivery, schema_root=SCHEMA_ROOT,
    )
    chunk = json.loads(result.chunk_paths[0].read_bytes())

    assert chunk["events"][0]["payload"] == {"metadata": {"flags": ["GREEN", "CLEAR"]}}
