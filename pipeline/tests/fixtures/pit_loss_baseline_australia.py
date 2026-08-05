"""Deterministic repository-local Australia pit-loss baseline fixture.

This fixture is the test oracle for the curated Australia baseline (Green
19 300 ms, VSC 12 300 ms, SC 9 300 ms).  The expected values are written here
as literals so tests can prove the production catalog entry, the pure resolver,
and the curated browser sidecar all agree with them.

The fixture is deliberately pure and in-memory:

* ``build_australia_sidecar`` resolves the repository-local catalog without any
  race observations, so Green, SC, and VSC are all available at replay start
  even when the input race never entered a Safety Car or VSC state.
* ``build_australia_delivery`` constructs a complete Australia-bound browser
  delivery whose chunk only ever proves All Clear (track status code 1), which
  demonstrates the curated sidecar does not depend on current-race status
  samples.

Neither builder accesses the network, reads or writes ``templates/``, or
invokes R2 publication.
"""

from __future__ import annotations

from f1_replay_pipeline.delivery.browser.browser_chunk_builder import (
    BrowserChunk,
    BrowserEvent,
    BrowserOverlap,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    BrowserDriverFields,
    BrowserManifest,
    CanonicalGenerationSnapshot,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_orchestration import (
    BrowserDeliveryBuild,
)
from f1_replay_pipeline.delivery.browser.browser_pit_loss_model import (
    build_curated_pit_loss_estimate_sidecar,
)
from f1_replay_pipeline.delivery.browser.browser_pit_loss_sidecar import (
    BrowserPitLossEstimateSidecar,
)

# Catalog-backed Australia values in milliseconds and their source metadata.
AUSTRALIA_GREEN_MS = 19_300
AUSTRALIA_VSC_MS = 12_300
AUSTRALIA_SC_MS = 9_300
AUSTRALIA_VSC_DISCOUNT_MS = 7_000
AUSTRALIA_SC_DISCOUNT_MS = 10_000
AUSTRALIA_SEASON = 2026
AUSTRALIA_EVIDENCE_COUNT = 1
AUSTRALIA_CONFIDENCE = "high"
AUSTRALIA_CAPTURED_DATE = "2026-08-04"
AUSTRALIA_EVIDENCE = "Formula1.com lists Australia pit stop time loss as 19.30 seconds."
AUSTRALIA_SOURCE_URL = (
    "https://www.formula1.com/en/latest/article/need-to-know-the-most-important-"
    "facts-stats-and-trivia-ahead-of-the-2026.7gyyqNLcwuCPZdXvgGwhCM"
)
AUSTRALIA_METHOD = "curated-track-baseline-v1"
AUSTRALIA_CATALOG_VERSION = "v1"

# Identity used by the delivery builders and asserted against the catalog.
AUSTRALIA_FIXTURE_ID = "2026-round-01-australian-grand-prix"
AUSTRALIA_TRACK_ID = "australia"
AUSTRALIA_TRACK_ASSET_ID = f"{AUSTRALIA_FIXTURE_ID}-telemetry-layout-v1"
AUSTRALIA_TRACK_NAME = "Albert Park Circuit"
AUSTRALIA_DRIVER_ID = "HAM"
AUSTRALIA_CANONICAL_GENERATION_ID = "canonical-australia"
AUSTRALIA_DELIVERY_VERSION = "delivery-australia"


def build_australia_sidecar(replay_start_ms: int = 0) -> BrowserPitLossEstimateSidecar:
    """Build the immutable catalog-backed Australia sidecar at replay start.

    The builder accepts no observations: Green, SC, and VSC values come from
    the repository-local catalog, so every resolved value is available from the
    first replay frame even when the current race never contained a Safety Car
    or VSC state.
    """
    return build_curated_pit_loss_estimate_sidecar(
        replay_start_ms,
        fixture_id=AUSTRALIA_FIXTURE_ID,
        track_id=AUSTRALIA_TRACK_ID,
    )


def build_australia_delivery(
    *,
    track_status: tuple[int, int] = (1, 1),
) -> BrowserDeliveryBuild:
    """Build a complete Australia delivery bound to the curated sidecar.

    The single chunk proves only All Clear (track status code 1), so publishing
    this delivery proves the curated SC/VSC values remain available even though
    the input race never entered those states.  The replay starts at ``0`` so
    the delivery is publication-compatible with the chunk bounds.
    """
    sidecar = build_australia_sidecar(0)
    manifest = BrowserManifest(
        AUSTRALIA_FIXTURE_ID,
        "2026 Australian Grand Prix Race",
        ({
            "id": AUSTRALIA_DRIVER_ID,
            "displayName": "Hamilton",
            "teamName": "Ferrari",
            "colorHex": "#000000",
            "carNumber": "44",
        },),
    )
    return BrowserDeliveryBuild(
        CanonicalGenerationSnapshot(AUSTRALIA_CANONICAL_GENERATION_ID, "a" * 64, {}),
        manifest,
        _australia_track_assets(),
        (_australia_chunk(track_status),),
        pit_loss_estimate_sidecar=sidecar,
    )


def _australia_chunk(track_status: tuple[int, int]) -> BrowserChunk:
    """Return the focused All Clear chunk for the Australia fixture."""
    fields = BrowserDriverFields(
        AUSTRALIA_DRIVER_ID,
        (0, 1000),
        (1.0, 2.0),
        (3.0, 4.0),
        (5.0, 6.0),
        (7.0, 8.0),
        (0, 1),
        (None, 7),
        (None, 1),
        ("OnTrack", "OnTrack"),
        (1, 1),
        ("SOFT", "SOFT"),
        (False, False),
        (None, None),
        (None, None),
        (None, None),
    )
    return BrowserChunk(
        "chunk-001",
        1,
        0,
        2000,
        BrowserOverlap("none", None, None, None, None),
        (0, 1000),
        0,
        {AUSTRALIA_DRIVER_ID: fields},
        ((AUSTRALIA_DRIVER_ID,), (AUSTRALIA_DRIVER_ID,)),
        track_status,
        ("clear", "clear"),
        (BrowserEvent(1000, "notice", "green flag"),),
    )


def _australia_track_assets() -> dict[str, object]:
    """Return minimal schema-valid track assets bound to the Australia fixture."""
    point = {"x": 0.0, "y": 0.0}
    polyline = (point, {"x": 1.0, "y": 0.0}, {"x": 1.0, "y": 1.0}, {"x": 0.0, "y": 1.0})
    return {
        "contractVersion": "v1",
        "fixtureId": AUSTRALIA_FIXTURE_ID,
        "trackId": AUSTRALIA_TRACK_ID,
        "trackName": AUSTRALIA_TRACK_NAME,
        "coordinateSpace": {"units": "meters", "origin": "test"},
        "circuitLengthMeters": 5278.0,
        "rotationDegrees": 0.0,
        "startFinish": {"center": point, "inner": point, "outer": point},
        "centerLine": polyline,
        "innerBoundary": polyline,
        "outerBoundary": polyline,
    }


__all__ = [
    "AUSTRALIA_CANONICAL_GENERATION_ID",
    "AUSTRALIA_CAPTURED_DATE",
    "AUSTRALIA_CATALOG_VERSION",
    "AUSTRALIA_CONFIDENCE",
    "AUSTRALIA_DELIVERY_VERSION",
    "AUSTRALIA_DRIVER_ID",
    "AUSTRALIA_EVIDENCE",
    "AUSTRALIA_EVIDENCE_COUNT",
    "AUSTRALIA_FIXTURE_ID",
    "AUSTRALIA_GREEN_MS",
    "AUSTRALIA_METHOD",
    "AUSTRALIA_SC_DISCOUNT_MS",
    "AUSTRALIA_SC_MS",
    "AUSTRALIA_SEASON",
    "AUSTRALIA_SOURCE_URL",
    "AUSTRALIA_TRACK_ASSET_ID",
    "AUSTRALIA_TRACK_ID",
    "AUSTRALIA_TRACK_NAME",
    "AUSTRALIA_VSC_DISCOUNT_MS",
    "AUSTRALIA_VSC_MS",
    "build_australia_delivery",
    "build_australia_sidecar",
]
