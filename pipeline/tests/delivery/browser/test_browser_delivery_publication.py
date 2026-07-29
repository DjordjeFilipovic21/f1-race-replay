"""Focused deterministic publication coverage for browser delivery artifacts."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
import jsonschema_rs

from f1_replay_pipeline.delivery.browser.browser_chunk_builder import BrowserChunk, BrowserEvent, BrowserOverlap
from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    BrowserDnfMarker,
    BrowserDriverFields,
    BrowserLapStart,
    BrowserManifest,
    BrowserTimelineInterval,
    BrowserTimelineSummary,
    CanonicalGenerationSnapshot,
    TIMELINE_SUMMARY_SCHEMA_ID,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_orchestration import BrowserDeliveryBuild
import f1_replay_pipeline.delivery.browser.browser_delivery_publication as publication
from f1_replay_pipeline.delivery.browser.browser_delivery_publication import (
    BrowserDeliveryCleanupError, BrowserDeliveryDurabilityUncertainError,
    BrowserDeliveryPublicationError, BrowserValidationProgress, _artifact_payloads,
    _contract_validators, _load_contract_schemas, _prepared_artifacts,
    _validate_delivery_payloads, publish_browser_delivery, validate_complete_browser_delivery,
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
        "chunk-001", 1, 0, 2000, BrowserOverlap("none", None, None, None, None), (0, 1000), 0,
        {"HAM": fields}, (("HAM",), ("HAM",)), (1, 1), ("clear", "clear"),
        (BrowserEvent(1000, "notice", "green flag"),),
    )


def _delivery(
    track_assets: dict[str, object] | None = None,
    timeline_summary: BrowserTimelineSummary | None = None,
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
    if track_assets is not None:
        assets.update(track_assets)
    return BrowserDeliveryBuild(
        _snapshot(), manifest, assets, (_chunk(),), timeline_summary=timeline_summary,
    )


def _timeline_summary() -> BrowserTimelineSummary:
    return BrowserTimelineSummary(
        "race-one",
        0,
        2_000,
        (BrowserTimelineInterval("yellow", 100, 200),),
        (BrowserDnfMarker("HAM", 1_500),),
    )


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


def test_directory_durability_is_ordered_before_pointer_selection(tmp_path: Path, monkeypatch) -> None:
    events: list[tuple[str, str]] = []
    real_fsync = publication.os.fsync
    real_replace = publication.os.replace

    def record_fsync(descriptor: int) -> None:
        path = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            events.append(("fsync", path.name))
        real_fsync(descriptor)

    def record_replace(source, destination, **kwargs) -> None:
        if destination == "delivery-one":
            events.append(("rename", "generation"))
        elif destination == "browser-current.json":
            events.append(("replace", "pointer"))
        real_replace(source, destination, **kwargs)

    monkeypatch.setattr(publication.os, "fsync", record_fsync)
    monkeypatch.setattr(publication.os, "replace", record_replace)

    _publish(tmp_path / "browser")

    generation_rename = events.index(("rename", "generation"))
    pointer_replace = events.index(("replace", "pointer"))
    assert events.index(("fsync", "browser")) < generation_rename
    assert events.index(("fsync", "generations"), generation_rename) < pointer_replace
    assert events.index(("fsync", "browser"), pointer_replace) > pointer_replace


@pytest.mark.parametrize("unsupported_errno", [errno.EINVAL, errno.ENOTSUP, errno.EBADF])
def test_unsupported_directory_fsync_is_tolerated(
    tmp_path: Path, monkeypatch, unsupported_errno: int,
) -> None:
    real_fsync = publication.os.fsync

    def reject_directory_fsync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError(unsupported_errno, "directory fsync unsupported")
        real_fsync(descriptor)

    monkeypatch.setattr(publication.os, "fsync", reject_directory_fsync)

    result = _publish(tmp_path / "browser")

    assert result.delivery_version == "delivery-one"
    assert result.pointer_path.exists()


def test_unsupported_postcommit_fsync_with_release_failure_is_uncertain(
    tmp_path: Path, monkeypatch,
) -> None:
    class FailingLease:
        def release(self) -> None:
            raise OSError("injected browser lease release")

    class FailingLock:
        def acquire(self, _target_parent: Path) -> FailingLease:
            return FailingLease()

    real_fsync = publication.os.fsync

    def reject_directory_fsync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError(errno.ENOTSUP, "directory fsync unsupported")
        real_fsync(descriptor)

    monkeypatch.setattr(publication, "LocalRecoveryLock", FailingLock)
    monkeypatch.setattr(publication.os, "fsync", reject_directory_fsync)

    with pytest.raises(BrowserDeliveryDurabilityUncertainError) as raised:
        _publish(tmp_path / "browser")

    assert raised.value.result.delivery_version == "delivery-one"
    assert not raised.value.durability_confirmed
    assert {str(error) for error in raised.value.cleanup_errors} == {
        "unable to release verifiable browser publication ownership",
    }


def test_precommit_directory_fsync_failure_preserves_prior_pointer(tmp_path: Path, monkeypatch) -> None:
    browser = tmp_path / "browser"
    previous = _publish(browser)
    pointer_before = previous.pointer_path.read_bytes()
    real_fsync = publication.os.fsync

    def fail_staged_directory(descriptor: int) -> None:
        if Path(os.readlink(f"/proc/self/fd/{descriptor}")).name == "chunks":
            raise OSError("injected staged directory fsync")
        real_fsync(descriptor)

    monkeypatch.setattr(publication.os, "fsync", fail_staged_directory)

    with pytest.raises(BrowserDeliveryPublicationError, match="secure browser publication failed") as raised:
        publish_browser_delivery(
            browser_parent=browser, delivery_version="delivery-two",
            delivery=_delivery(), schema_root=SCHEMA_ROOT,
        )

    assert not isinstance(raised.value, BrowserDeliveryDurabilityUncertainError)
    assert raised.value.__cause__ is not None
    assert "staged directory fsync" in str(raised.value.__cause__)
    assert previous.pointer_path.read_bytes() == pointer_before


def test_postcommit_directory_fsync_failure_preserves_committed_result(tmp_path: Path, monkeypatch) -> None:
    browser = tmp_path / "browser"
    _publish(browser)
    real_fsync = publication.os.fsync

    def fail_browser_root_fsync(descriptor: int) -> None:
        if Path(os.readlink(f"/proc/self/fd/{descriptor}")).name == browser.name:
            raise OSError("injected post-commit directory fsync")
        real_fsync(descriptor)

    monkeypatch.setattr(publication.os, "fsync", fail_browser_root_fsync)

    with pytest.raises(BrowserDeliveryDurabilityUncertainError, match="durability is uncertain") as raised:
        publish_browser_delivery(
            browser_parent=browser, delivery_version="delivery-two",
            delivery=_delivery(), schema_root=SCHEMA_ROOT,
        )

    assert raised.value.result.delivery_version == "delivery-two"
    assert json.loads((browser / "browser-current.json").read_bytes())["deliveryVersion"] == "delivery-two"


def test_ambiguous_pointer_replace_reports_observed_commit_as_uncertain(tmp_path: Path, monkeypatch) -> None:
    browser = tmp_path / "browser"
    _publish(browser)
    real_replace = publication.os.replace

    def replace_then_fail(source, destination, **kwargs) -> None:
        real_replace(source, destination, **kwargs)
        if destination == "browser-current.json":
            raise OSError("injected ambiguous pointer replace")

    monkeypatch.setattr(publication.os, "replace", replace_then_fail)

    with pytest.raises(BrowserDeliveryDurabilityUncertainError) as raised:
        publish_browser_delivery(
            browser_parent=browser, delivery_version="delivery-two",
            delivery=_delivery(), schema_root=SCHEMA_ROOT,
        )

    assert raised.value.result.delivery_version == "delivery-two"
    assert json.loads((browser / "browser-current.json").read_bytes())["deliveryVersion"] == "delivery-two"


def test_release_failure_is_cleanup_error_after_successful_commit(tmp_path: Path, monkeypatch) -> None:
    class FailingLease:
        def release(self) -> None:
            raise OSError("injected browser lease release")

    class FailingLock:
        def acquire(self, _target_parent: Path) -> FailingLease:
            return FailingLease()

    monkeypatch.setattr(publication, "LocalRecoveryLock", FailingLock)

    with pytest.raises(BrowserDeliveryCleanupError, match="cleanup failures") as raised:
        _publish(tmp_path / "browser")

    assert raised.value.committed
    assert raised.value.result is not None
    assert raised.value.result.delivery_version == "delivery-one"
    assert {str(error) for error in raised.value.cleanup_errors} == {
        "unable to release verifiable browser publication ownership",
    }


def test_root_descriptor_is_closed_before_lease_release(tmp_path: Path, monkeypatch) -> None:
    root_descriptors: list[int] = []

    real_open_directory = publication._open_directory_no_follow

    def capture_root_descriptor(path: Path) -> int:
        descriptor = real_open_directory(path)
        root_descriptors.append(descriptor)
        return descriptor

    class Lease:
        closed_before_release = False

        def release(self) -> None:
            try:
                os.fstat(root_descriptors[0])
            except OSError:
                self.closed_before_release = True

    lease = Lease()

    class Lock:
        def acquire(self, _target_parent: Path) -> Lease:
            return lease

    monkeypatch.setattr(publication, "_open_directory_no_follow", capture_root_descriptor)
    monkeypatch.setattr(publication, "LocalRecoveryLock", Lock)

    _publish(tmp_path / "browser")

    assert lease.closed_before_release


def test_release_failure_does_not_mask_precommit_failure(tmp_path: Path, monkeypatch) -> None:
    class FailingLease:
        def release(self) -> None:
            raise OSError("injected browser lease release")

    class FailingLock:
        def acquire(self, _target_parent: Path) -> FailingLease:
            return FailingLease()

    real_fsync = publication.os.fsync

    def fail_staged_directory(descriptor: int) -> None:
        if Path(os.readlink(f"/proc/self/fd/{descriptor}")).name == "chunks":
            raise OSError("injected primary staged fsync")
        real_fsync(descriptor)

    monkeypatch.setattr(publication, "LocalRecoveryLock", FailingLock)
    monkeypatch.setattr(publication.os, "fsync", fail_staged_directory)

    with pytest.raises(BrowserDeliveryPublicationError, match="secure browser publication failed") as raised:
        _publish(tmp_path / "browser")

    assert not isinstance(raised.value, BrowserDeliveryCleanupError)
    assert raised.value.__cause__ is not None
    assert "primary staged fsync" in str(raised.value.__cause__)
    assert {str(error) for error in raised.value.cleanup_errors} == {
        "unable to release verifiable browser publication ownership",
    }


def test_publication_writes_and_references_timeline_summary(tmp_path: Path) -> None:
    result = publish_browser_delivery(
        browser_parent=tmp_path / "browser",
        delivery_version="delivery-one",
        delivery=_delivery(timeline_summary=_timeline_summary()),
        schema_root=SCHEMA_ROOT,
    )
    manifest = json.loads(result.manifest_path.read_bytes())
    summary_path = tmp_path / "browser" / "generations" / "delivery-one" / "timeline-summary.json"
    summary = json.loads(summary_path.read_bytes())

    assert summary == _timeline_summary().as_dict()
    assert manifest["timelineSummary"] == {
        "path": "timeline-summary.json",
        "schemaId": TIMELINE_SUMMARY_SCHEMA_ID,
        "sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
    }
    assert result.timeline_summary_path == summary_path
    assert result.artifact_digests["timeline-summary.json"] == manifest["timelineSummary"]["sha256"]


def test_publication_rejects_timeline_summary_with_mismatched_replay_bounds(tmp_path: Path) -> None:
    summary = BrowserTimelineSummary(
        "race-one", 1, 2_000, (BrowserTimelineInterval("yellow", 100, 200),), (),
    )

    with pytest.raises(BrowserDeliveryPublicationError, match="bounds disagree with replay bounds"):
        publish_browser_delivery(
            browser_parent=tmp_path / "browser", delivery_version="delivery-one",
            delivery=_delivery(timeline_summary=summary), schema_root=SCHEMA_ROOT,
        )


def test_timeline_summary_publication_is_byte_identical(tmp_path: Path) -> None:
    delivery = _delivery(timeline_summary=_timeline_summary())
    first = publish_browser_delivery(
        browser_parent=tmp_path / "browser-one", delivery_version="delivery-one",
        delivery=delivery, schema_root=SCHEMA_ROOT,
    )
    second = publish_browser_delivery(
        browser_parent=tmp_path / "browser-two", delivery_version="delivery-one",
        delivery=delivery, schema_root=SCHEMA_ROOT,
    )

    first_paths = (
        first.manifest_path,
        first.track_assets_path,
        first.generation_path / "timeline-summary.json",
        *first.chunk_paths,
    )
    second_paths = (
        second.manifest_path,
        second.track_assets_path,
        second.generation_path / "timeline-summary.json",
        *second.chunk_paths,
    )
    assert [path.read_bytes() for path in first_paths] == [path.read_bytes() for path in second_paths]


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
        BrowserValidationProgress("browser_schema_artifact_validating", 1, 6, "track assets"),
        BrowserValidationProgress("browser_schema_artifact_validating", 2, 6, "track assets schema"),
        BrowserValidationProgress("browser_schema_artifact_validating", 3, 6, "chunk 1/1"),
        BrowserValidationProgress("browser_schema_artifact_validating", 4, 6, "chunk schema 1/1"),
        BrowserValidationProgress("browser_schema_artifact_validating", 5, 6, "manifest"),
        BrowserValidationProgress("browser_schema_artifact_validating", 6, 6, "manifest schema"),
    ]


def test_prepared_digests_bind_manifest_references_pointer_and_result(tmp_path: Path) -> None:
    result = _publish(tmp_path / "browser")
    manifest = json.loads(result.manifest_path.read_bytes())
    pointer = json.loads(result.pointer_path.read_bytes())

    assert (
        manifest["trackAssets"]["sha256"],
        manifest["chunks"][0]["sha256"],
        pointer["manifestSha256"],
    ) == (
        result.artifact_digests["track-assets.json"],
        result.artifact_digests["chunks/chunk-001.json"],
        result.artifact_digests["manifest.json"],
    )


def test_complete_validator_uses_secure_stored_delivery_validation(tmp_path: Path) -> None:
    result = _publish(tmp_path / "browser")
    assert "timelineSummary" not in json.loads(result.manifest_path.read_bytes())
    progress: list[str | BrowserValidationProgress] = []

    validate_complete_browser_delivery(
        tmp_path / "browser",
        expected_generation_id="canonical-one",
        expected_manifest_sha256="a" * 64,
        schema_root=SCHEMA_ROOT,
        progress=progress.append,
    )

    assert progress == [
        "browser_contract_schema_loading",
        BrowserValidationProgress("browser_schema_artifact_validating", 1, 3, "manifest schema"),
        BrowserValidationProgress("browser_schema_artifact_validating", 2, 3, "track assets schema"),
        BrowserValidationProgress("browser_schema_artifact_validating", 3, 3, "chunk schema 1/1"),
    ]
    assert result.pointer_path.exists()


def test_complete_validator_rejects_a_timeline_summary_digest_mismatch(tmp_path: Path) -> None:
    result = publish_browser_delivery(
        browser_parent=tmp_path / "browser", delivery_version="delivery-one",
        delivery=_delivery(timeline_summary=_timeline_summary()), schema_root=SCHEMA_ROOT,
    )
    summary_path = result.generation_path / "timeline-summary.json"
    summary_path.write_bytes(summary_path.read_bytes().replace(b'"endMs":2000', b'"endMs":1999'))

    with pytest.raises(BrowserDeliveryPublicationError, match="validation failed") as error:
        validate_complete_browser_delivery(
            tmp_path / "browser", expected_generation_id="canonical-one",
            expected_manifest_sha256="a" * 64, schema_root=SCHEMA_ROOT,
        )

    assert error.value.__cause__ is not None
    assert "checksum disagrees for timeline-summary.json" in str(error.value.__cause__)


@pytest.mark.parametrize(("field", "value"), [("startMs", 1), ("endMs", 1_999)])
def test_complete_validator_rejects_a_redigested_timeline_summary_with_mismatched_replay_bounds(
    tmp_path: Path, field: str, value: int,
) -> None:
    result = publish_browser_delivery(
        browser_parent=tmp_path / "browser", delivery_version="delivery-one",
        delivery=_delivery(timeline_summary=_timeline_summary()), schema_root=SCHEMA_ROOT,
    )
    summary_path = result.generation_path / "timeline-summary.json"
    summary = json.loads(summary_path.read_bytes())
    summary[field] = value
    summary_bytes = json.dumps(summary, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    summary_path.write_bytes(summary_bytes)

    manifest = json.loads(result.manifest_path.read_bytes())
    manifest["timelineSummary"]["sha256"] = hashlib.sha256(summary_bytes).hexdigest()
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    result.manifest_path.write_bytes(manifest_bytes)
    pointer = json.loads(result.pointer_path.read_bytes())
    pointer["manifestSha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    result.pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

    with pytest.raises(BrowserDeliveryPublicationError, match="validation failed") as error:
        validate_complete_browser_delivery(
            tmp_path / "browser", expected_generation_id="canonical-one",
            expected_manifest_sha256="a" * 64, schema_root=SCHEMA_ROOT,
        )

    assert error.value.__cause__ is not None
    assert "bounds disagree with replay bounds" in str(error.value.__cause__)


def test_complete_validator_rejects_a_schema_invalid_timeline_summary(tmp_path: Path) -> None:
    result = publish_browser_delivery(
        browser_parent=tmp_path / "browser", delivery_version="delivery-one",
        delivery=_delivery(timeline_summary=_timeline_summary()), schema_root=SCHEMA_ROOT,
    )
    summary_path = result.generation_path / "timeline-summary.json"
    summary = json.loads(summary_path.read_bytes())
    summary["intervals"][0]["kind"] = "blue"
    summary_bytes = json.dumps(summary, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    summary_path.write_bytes(summary_bytes)

    manifest = json.loads(result.manifest_path.read_bytes())
    manifest["timelineSummary"]["sha256"] = hashlib.sha256(summary_bytes).hexdigest()
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    result.manifest_path.write_bytes(manifest_bytes)
    pointer = json.loads(result.pointer_path.read_bytes())
    pointer["manifestSha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    result.pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

    with pytest.raises(BrowserDeliveryPublicationError, match="validation failed") as error:
        validate_complete_browser_delivery(
            tmp_path / "browser", expected_generation_id="canonical-one",
            expected_manifest_sha256="a" * 64, schema_root=SCHEMA_ROOT,
        )

    assert error.value.__cause__ is not None
    assert "timeline summary fails replay-data v1 schema validation" in str(error.value.__cause__)


@pytest.mark.parametrize("pointer", [
    {"formatVersion": "browser-delivery-v1", "deliveryVersion": "delivery-one", "manifestPath": "generations/delivery-one/manifest.json"},
    {"formatVersion": "browser-delivery-v0", "deliveryVersion": "delivery-one", "manifestPath": "generations/delivery-one/manifest.json", "manifestSha256": "a" * 64},
    {"formatVersion": "browser-delivery-v1", "deliveryVersion": "delivery-one", "manifestPath": "manifest.json", "manifestSha256": "a" * 64},
    {"formatVersion": "browser-delivery-v1", "deliveryVersion": "delivery-one", "manifestPath": "generations/delivery-one/manifest.json", "manifestSha256": "a" * 64, "unexpected": True},
])
def test_complete_validator_rejects_noncanonical_browser_pointer_shapes(tmp_path: Path, pointer: dict[str, object]) -> None:
    result = _publish(tmp_path / "browser")
    result.pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

    with pytest.raises(BrowserDeliveryPublicationError, match="validation failed"):
        validate_complete_browser_delivery(
            tmp_path / "browser", expected_generation_id="canonical-one",
            expected_manifest_sha256="a" * 64, schema_root=SCHEMA_ROOT,
        )


@pytest.mark.parametrize("lap_starts", [
    [{"lap": 2, "startMs": 1_000}, {"lap": 1, "startMs": 1_500}],
    [{"lap": 1, "startMs": 2_000}],
])
def test_complete_validator_rejects_schema_valid_semantically_invalid_lap_starts(
    tmp_path: Path, lap_starts: list[dict[str, int]],
) -> None:
    result = _publish(tmp_path / "browser")
    manifest = json.loads(result.manifest_path.read_bytes())
    manifest["lapStarts"] = lap_starts
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    result.manifest_path.write_bytes(manifest_bytes)
    pointer = json.loads(result.pointer_path.read_bytes())
    pointer["manifestSha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    result.pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

    with pytest.raises(BrowserDeliveryPublicationError, match="validation failed") as error:
        validate_complete_browser_delivery(
            tmp_path / "browser", expected_generation_id="canonical-one",
            expected_manifest_sha256="a" * 64, schema_root=SCHEMA_ROOT,
        )
    assert error.value.__cause__ is not None
    assert "lap starts" in str(error.value.__cause__)


def test_manifest_lap_starts_are_immutable_and_validate_order() -> None:
    manifest = BrowserManifest("race-one", "Race One", ({
        "id": "HAM", "displayName": "Hamilton", "teamName": "Team",
        "colorHex": "#000000", "carNumber": "44",
    },), (BrowserLapStart(1, 0), BrowserLapStart(3, 2_000)))

    assert manifest.as_dict()["lapStarts"] == [{"lap": 1, "startMs": 0}, {"lap": 3, "startMs": 2_000}]
    with pytest.raises(ValueError, match="increasing"):
        BrowserManifest("race-one", "Race One", manifest.drivers, (BrowserLapStart(2, 1_000), BrowserLapStart(1, 2_000)))


def test_publication_rejects_a_lap_start_at_the_exclusive_replay_end(tmp_path: Path) -> None:
    delivery = _delivery()
    manifest = replace(delivery.manifest, lap_starts=(BrowserLapStart(1, 2_000),))

    with pytest.raises(BrowserDeliveryPublicationError, match="within replay bounds"):
        publish_browser_delivery(
            browser_parent=tmp_path / "browser", delivery_version="delivery-one",
            delivery=replace(delivery, manifest=manifest), schema_root=SCHEMA_ROOT,
        )


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
    payloads = list(_artifact_payloads("delivery-one", delivery, SCHEMA_ROOT))
    index = next(index for index, artifact in enumerate(payloads) if artifact.path.startswith("chunks/"))
    artifact = payloads[index]
    payloads[index] = replace(artifact, payload=artifact.payload.replace(b'"startMs":0', b'"startMs":1'))

    with pytest.raises(BrowserDeliveryPublicationError, match="digest"):
        _validate_delivery_payloads(tuple(payloads), delivery)


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


def test_full_schema_validates_direct_tuple_and_immutable_mapping_contracts() -> None:
    schemas, registry = _load_contract_schemas(SCHEMA_ROOT)

    artifacts = _prepared_artifacts("delivery-one", _delivery(), _contract_validators(schemas, registry))

    assert tuple(artifact.path for artifact in artifacts) == (
        "manifest.json", "track-assets.json", "chunks/chunk-001.json",
    )


def test_rust_and_python_schema_engines_agree_on_browser_contracts() -> None:
    schemas, registry = _load_contract_schemas(SCHEMA_ROOT)
    artifacts = _prepared_artifacts("delivery-one", _delivery(), _contract_validators(schemas, registry))
    instances = {
        "manifest": json.loads(next(item.payload for item in artifacts if item.path == "manifest.json")),
        "track-assets": json.loads(next(item.payload for item in artifacts if item.path == "track-assets.json")),
        "chunk": json.loads(next(item.payload for item in artifacts if item.path.startswith("chunks/"))),
    }
    rust_validators = _contract_validators(schemas, registry)
    python_registry = _python_registry(schemas)
    python_validators = {
        name: Draft202012Validator(
            schema, registry=python_registry, format_checker=Draft202012Validator.FORMAT_CHECKER,
        )
        for name, schema in schemas.items()
    }

    assert all(_accepts(rust_validators[name], instance) for name, instance in instances.items())
    assert all(_accepts(python_validators[name], instance) for name, instance in instances.items())

    invalid = {
        "track-assets": {
            **instances["track-assets"],
            "coordinateSpace": {"units": "feet", "origin": "test"},
        },
        "chunk": {**instances["chunk"], "timeMs": ["not-an-integer"]},
        "manifest": {**instances["manifest"], "createdAt": "not-a-date-time"},
        "manifest-reference": {
            **instances["manifest"],
            "trackAssets": {
                **instances["manifest"]["trackAssets"],
                "path": "not-track-assets.json",
            },
        },
    }

    assert not _accepts(rust_validators["track-assets"], invalid["track-assets"])
    assert not _accepts(python_validators["track-assets"], invalid["track-assets"])
    assert not _accepts(rust_validators["chunk"], invalid["chunk"])
    assert not _accepts(python_validators["chunk"], invalid["chunk"])
    assert not _accepts(rust_validators["manifest"], invalid["manifest"])
    assert not _accepts(python_validators["manifest"], invalid["manifest"])
    assert not _accepts(rust_validators["manifest"], invalid["manifest-reference"])
    assert not _accepts(python_validators["manifest"], invalid["manifest-reference"])


def _python_registry(schemas: Mapping[str, Mapping[str, object]]):
    from referencing import Registry, Resource

    registry = Registry()
    for schema in schemas.values():
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    return registry


def _accepts(
    validator: Draft202012Validator | jsonschema_rs.Draft202012Validator, instance: object,
) -> bool:
    try:
        validator.validate(instance)
    except (ValidationError, jsonschema_rs.ValidationError):
        return False
    return True


def test_publication_constructs_one_reusable_validator_per_contract_type(tmp_path: Path, monkeypatch) -> None:
    calls = 0
    original = publication._make_contract_validator

    def counted_validator(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(publication, "_make_contract_validator", counted_validator)

    _publish(tmp_path / "browser")

    assert calls == 8


def test_publication_rejects_staged_short_write(tmp_path: Path, monkeypatch) -> None:
    original = publication._write_open

    def truncate_after_write(descriptor: int, payload: bytes) -> None:
        original(descriptor, payload)
        os.ftruncate(descriptor, max(0, len(payload) - 1))

    monkeypatch.setattr(publication, "_write_open", truncate_after_write)

    with pytest.raises(BrowserDeliveryPublicationError, match="staged artifact differs"):
        _publish(tmp_path / "browser")


def test_publication_rejects_staged_same_size_corruption(tmp_path: Path, monkeypatch) -> None:
    original = publication._write_open

    def corrupt_after_write(descriptor: int, payload: bytes) -> None:
        original(descriptor, payload)
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.write(descriptor, b"X")
        os.fsync(descriptor)

    monkeypatch.setattr(publication, "_write_open", corrupt_after_write)

    with pytest.raises(BrowserDeliveryPublicationError, match="staged artifact differs"):
        _publish(tmp_path / "browser")


def test_staged_validation_rejects_a_file_changed_during_verification(tmp_path: Path, monkeypatch) -> None:
    staging = tmp_path / "staging"
    chunks = staging / "chunks"
    chunks.mkdir(parents=True)
    path = staging / "manifest.json"
    payload = b'{}\n'
    path.write_bytes(payload)
    artifact = publication.PreparedArtifact("manifest.json", payload, hashlib.sha256(payload).hexdigest())
    target_inode = path.stat().st_ino
    target_fstats = 0
    real_fstat = os.fstat

    def mutate_before_second_target_fstat(descriptor: int):
        nonlocal target_fstats
        metadata = real_fstat(descriptor)
        if metadata.st_ino == target_inode:
            target_fstats += 1
            if target_fstats == 2:
                with path.open("ab") as destination:
                    destination.write(b"x")
                metadata = real_fstat(descriptor)
        return metadata

    monkeypatch.setattr(publication.os, "fstat", mutate_before_second_target_fstat)
    staging_fd = os.open(staging, os.O_RDONLY)
    chunks_fd = os.open(chunks, os.O_RDONLY)
    try:
        with pytest.raises(BrowserDeliveryPublicationError, match="staged artifact differs"):
            publication._validate_staged(staging_fd, chunks_fd, (artifact,))
    finally:
        os.close(chunks_fd)
        os.close(staging_fd)


def test_schema_normalization_preserves_large_scalar_sequences() -> None:
    values = tuple(range(10_000))

    assert publication._schema_compatible_value(values) is values
