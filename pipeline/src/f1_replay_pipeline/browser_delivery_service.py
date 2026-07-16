"""Application orchestration for canonical-to-browser publication."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from f1_replay_pipeline.browser_delivery_models import CanonicalGenerationSnapshot
from f1_replay_pipeline.browser_delivery_orchestration import BrowserDeliveryBuild, build_browser_delivery
from f1_replay_pipeline.browser_delivery_orchestration import BrowserDeliveryBuildError
from f1_replay_pipeline.browser_delivery_publication import (
    BrowserDeliveryPublicationError,
    PublishedBrowserDelivery,
    publish_browser_delivery,
)
from f1_replay_pipeline.browser_delivery_reader import BrowserDeliveryReadError, read_validated_canonical_generation
from f1_replay_pipeline.browser_delivery_request import (
    BrowserDeliveryServiceError,
    BrowserPublishRequest,
    BrowserPublishResult,
)
from f1_replay_pipeline.generation_identity import validate_generation_id
from f1_replay_pipeline.generation_publication import GenerationPublicationError
from f1_replay_pipeline.track_assets_generator import (
    TrackAssetsGenerationError,
    generate_track_assets,
)


Reader = Callable[[Path], CanonicalGenerationSnapshot]
AssetGenerator = Callable[[CanonicalGenerationSnapshot], Mapping[str, object]]
Builder = Callable[[CanonicalGenerationSnapshot, Mapping[str, object]], BrowserDeliveryBuild]
Publisher = Callable[..., PublishedBrowserDelivery]


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
) -> BrowserPublishResult:
    """Resolve, derive, and publish one browser generation without network access."""
    _validate_request(request)
    try:
        snapshot = dependencies.reader(request.canonical_parent)
        assets = dependencies.asset_generator(snapshot)
        delivery = dependencies.builder(snapshot, assets)
        publication = dependencies.publisher(
            browser_parent=request.browser_parent,
            delivery_version=request.delivery_version,
            delivery=delivery,
            schema_root=request.schema_root,
        )
    except (BrowserDeliveryReadError, BrowserDeliveryBuildError, TrackAssetsGenerationError, BrowserDeliveryPublicationError) as error:
        raise BrowserDeliveryServiceError(str(error)) from error
    return BrowserPublishResult(request, publication.delivery_version, publication)


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
    "publish_browser_delivery_from_canonical",
]
