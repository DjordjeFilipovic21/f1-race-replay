from pathlib import Path
from types import SimpleNamespace

import pytest

from f1_replay_pipeline.delivery.browser.browser_delivery_request import (
    BrowserDeliveryServiceError,
    BrowserPublishRequest,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_service import (
    BrowserDeliveryDependencies,
    publish_browser_delivery_from_canonical,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_publication import BrowserValidationProgress
from f1_replay_pipeline.app.track_assets_generator import TrackAssetsGenerationError


def test_service_runs_reader_assets_builder_and_publisher_in_order() -> None:
    calls = []
    progress: list[str | BrowserValidationProgress] = []
    snapshot = object()
    assets = {"fixtureId": "race"}
    delivery = object()
    publication = SimpleNamespace(delivery_version="delivery-v1")

    def reader(path):
        calls.append(("reader", path))
        return snapshot

    def generator(value):
        calls.append(("assets", value))
        return assets

    def builder(source, track_assets):
        calls.append(("builder", source, track_assets))
        return delivery

    def publisher(**keywords):
        calls.append(("publisher", keywords))
        return publication

    request = _request()
    result = publish_browser_delivery_from_canonical(
        request,
        dependencies=BrowserDeliveryDependencies(reader, generator, builder, publisher),
        progress=progress.append,
    )

    assert result.delivery_version == "delivery-v1"
    assert [call[0] for call in calls] == ["reader", "assets", "builder", "publisher"]
    assert progress == [
        "canonical_snapshot_reading", "track_assets_generating", "browser_building", "browser_publishing",
    ]
    assert calls[-1][1] == {
        "browser_parent": request.browser_parent,
        "delivery_version": request.delivery_version,
        "delivery": delivery,
        "schema_root": request.schema_root,
    }


def test_service_forwards_default_publisher_operation_boundaries(monkeypatch) -> None:
    progress: list[str | BrowserValidationProgress] = []
    publication = SimpleNamespace(delivery_version="delivery-v1")

    def publisher(*, progress, **_keywords):
        progress("browser_payload_preparing")
        progress("browser_contract_schema_loading")
        progress(BrowserValidationProgress("browser_schema_artifact_validating", 1, 1, "manifest"))
        progress("browser_artifacts_staging")
        progress("browser_pointer_committing_durability")
        return publication

    monkeypatch.setattr("f1_replay_pipeline.delivery.browser.browser_delivery_service.publish_browser_delivery", publisher)
    dependencies = BrowserDeliveryDependencies(
        reader=lambda _: object(), asset_generator=lambda _: {}, builder=lambda *_: object(),
        publisher=publisher,
    )

    publish_browser_delivery_from_canonical(_request(), dependencies=dependencies, progress=progress.append)

    assert progress[:5] == [
        "canonical_snapshot_reading", "track_assets_generating", "browser_building",
        "browser_payload_preparing", "browser_contract_schema_loading",
    ]
    assert progress[5] == BrowserValidationProgress(
        "browser_schema_artifact_validating", 1, 1, "manifest",
    )
    assert progress[6:] == [
        "browser_artifacts_staging", "browser_pointer_committing_durability",
    ]


def test_service_stops_after_expected_track_generation_failure() -> None:
    calls = []

    def generator(_):
        calls.append("assets")
        raise TrackAssetsGenerationError("no usable reference lap")

    def unexpected(*_args, **_kwargs):
        calls.append("unexpected")
        raise AssertionError("must not continue")

    dependencies = BrowserDeliveryDependencies(
        reader=lambda _: object(), asset_generator=generator,
        builder=unexpected, publisher=unexpected,
    )

    with pytest.raises(BrowserDeliveryServiceError, match="no usable reference lap"):
        publish_browser_delivery_from_canonical(_request(), dependencies=dependencies)

    assert calls == ["assets"]


def test_service_does_not_mask_an_unexpected_dependency_value_error() -> None:
    dependencies = BrowserDeliveryDependencies(
        reader=lambda _: (_ for _ in ()).throw(ValueError("unexpected defect")),
        asset_generator=lambda _: {}, builder=lambda *_: None, publisher=lambda **_: None,
    )

    with pytest.raises(ValueError, match="unexpected defect"):
        publish_browser_delivery_from_canonical(_request(), dependencies=dependencies)


def test_service_rejects_identical_canonical_and_browser_parents() -> None:
    request = BrowserPublishRequest(Path("same"), Path("same"), "delivery-v1", Path("schemas"))

    with pytest.raises(BrowserDeliveryServiceError, match="separate directories"):
        publish_browser_delivery_from_canonical(request)


def _request() -> BrowserPublishRequest:
    return BrowserPublishRequest(
        Path("canonical"), Path("browser"), "delivery-v1", Path("schemas"),
    )
