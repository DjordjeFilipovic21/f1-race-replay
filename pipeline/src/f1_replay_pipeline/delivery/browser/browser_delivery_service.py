"""Application orchestration for canonical-to-browser publication."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from f1_replay_pipeline.delivery.browser.browser_delivery_models import CanonicalGenerationSnapshot
from f1_replay_pipeline.delivery.browser.browser_delivery_orchestration import BrowserDeliveryBuild, build_browser_delivery
from f1_replay_pipeline.delivery.browser.browser_delivery_orchestration import BrowserDeliveryBuildError
from f1_replay_pipeline.delivery.browser.browser_delivery_publication import (
    BrowserValidationProgress,
    BrowserDeliveryPublicationError,
    PublishedBrowserDelivery,
    publish_browser_delivery,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_reader import BrowserDeliveryReadError, read_validated_canonical_generation
from f1_replay_pipeline.delivery.browser.browser_delivery_request import (
    BrowserDeliveryServiceError,
    BrowserPublishRequest,
    BrowserPublishResult,
)
from f1_replay_pipeline.domain.generation_identity import validate_generation_id
from f1_replay_pipeline.storage.generation_publication import GenerationPublicationError
from f1_replay_pipeline.app.track_assets_generator import (
    TrackAssetsGenerationError,
    generate_track_assets,
)


Reader = Callable[[Path], CanonicalGenerationSnapshot]
AssetGenerator = Callable[[CanonicalGenerationSnapshot], Mapping[str, object]]
Builder = Callable[[CanonicalGenerationSnapshot, Mapping[str, object]], BrowserDeliveryBuild]
Publisher = Callable[..., PublishedBrowserDelivery]
ProgressCallback = Callable[[str | BrowserValidationProgress], None]


@dataclass(frozen=True)
class BrowserDeliveryDependencies:
    reader: Reader = read_validated_canonical_generation
    asset_generator: AssetGenerator = generate_track_assets
    builder: Builder = build_browser_delivery
    publisher: Publisher = publish_browser_delivery


def publish_browser_delivery_from_canonical(
    request: BrowserPublishRequest,
    *,
    dependencies: BrowserDeliveryDependencies = BrowserDeliveryDependencies(),
    progress: ProgressCallback | None = None,
) -> BrowserPublishResult:
    """Resolve, derive, and publish one browser generation without network access."""
    _validate_request(request)
    emit = progress or (lambda _stage: None)
    try:
        emit("canonical_snapshot_reading")
        snapshot = dependencies.reader(request.canonical_parent)
        emit("track_assets_generating")
        assets = dependencies.asset_generator(snapshot)
        emit("browser_building")
        delivery = dependencies.builder(snapshot, assets)
        publication = _publish_delivery(request, delivery, dependencies.publisher, emit)
    except (BrowserDeliveryReadError, BrowserDeliveryBuildError, TrackAssetsGenerationError, BrowserDeliveryPublicationError) as error:
        raise BrowserDeliveryServiceError(str(error)) from error
    return BrowserPublishResult(request, publication.delivery_version, publication)


def _publish_delivery(
    request: BrowserPublishRequest,
    delivery: BrowserDeliveryBuild,
    publisher: Publisher,
    emit: ProgressCallback,
) -> PublishedBrowserDelivery:
    """Expose publication boundaries only for the callback-aware default publisher."""
    if publisher is publish_browser_delivery:
        return publisher(
            browser_parent=request.browser_parent,
            delivery_version=request.delivery_version,
            delivery=delivery,
            schema_root=request.schema_root,
            progress=emit,
        )
    emit("browser_publishing")
    return publisher(
        browser_parent=request.browser_parent,
        delivery_version=request.delivery_version,
        delivery=delivery,
        schema_root=request.schema_root,
    )


def _validate_request(request: BrowserPublishRequest) -> None:
    if not isinstance(request, BrowserPublishRequest):
        raise TypeError("request must be a BrowserPublishRequest")
    if any(not isinstance(path, Path) for path in (
        request.canonical_parent, request.browser_parent, request.schema_root,
    )):
        raise TypeError("browser publication paths must be pathlib.Path values")
    if request.canonical_parent.absolute() == request.browser_parent.absolute():
        raise BrowserDeliveryServiceError("canonical_parent and browser_parent must be separate directories")
    try:
        validate_generation_id(request.delivery_version)
    except ValueError as error:
        raise BrowserDeliveryServiceError(str(error)) from error


__all__ = [
    "BrowserDeliveryDependencies", "BrowserDeliveryServiceError",
    "ProgressCallback", "publish_browser_delivery_from_canonical",
]
