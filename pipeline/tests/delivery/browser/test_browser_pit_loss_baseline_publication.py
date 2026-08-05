"""Focused publication coverage for the curated pit-loss baseline sidecar.

The curated sidecar is the catalog-backed, status-complete artifact built with
``build_curated_pit_loss_estimate_sidecar``.  These tests cover the resolved
sidecar schema, fixtureId/trackId binding, SHA-256 digest publication, known
values emitted even when SC/VSC never occurred in the race, and the explicit
fail-closed behavior for malformed or unknown track bindings.  Catalog audit
metadata remains internal to the catalog and is never part of this artifact.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
import polars as pl

from f1_replay_pipeline.delivery.browser.browser_chunk_builder import (
    BrowserChunk,
    BrowserEvent,
    BrowserOverlap,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    BrowserDriverFields,
    BrowserManifest,
    CanonicalGenerationSnapshot,
    CURATED_BASELINE_METHOD,
    LEGACY_PIT_LOSS_ESTIMATE_METHOD,
    PIT_LOSS_ESTIMATE_SIDECAR_FILENAME,
    PIT_LOSS_ESTIMATE_SIDECAR_SCHEMA_ID,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_orchestration import (
    BrowserDeliveryBuild,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_publication import (
    BrowserDeliveryPublicationError,
    BrowserValidationProgress,
    _validate_pit_loss_estimate_timeline,
    publish_browser_delivery,
    validate_complete_browser_delivery,
)
from f1_replay_pipeline.delivery.browser.browser_pit_loss_baseline_catalog import (
    AUSTRALIA_FIXTURE_ID,
    AUSTRALIA_TRACK_ASSET_ID,
    AUSTRALIA_TRACK_ID,
    BARCELONA_BASELINE,
    CATALOG_VERSION,
    MADRING_BASELINE,
    MONZA_BASELINE,
    resolve_binding_identity,
)
from f1_replay_pipeline.delivery.browser.browser_pit_loss_baseline_resolver import (
    StatusCodeInput,
    UNAVAILABLE_STATUS,
    resolve_pit_loss_baseline,
)
from f1_replay_pipeline.delivery.browser.browser_pit_loss_model import (
    CuratedPitLossBaselineUnavailableError,
    build_curated_pit_loss_estimate_sidecar,
)
from f1_replay_pipeline.delivery.browser.browser_pit_loss_sidecar import (
    BrowserPitLossEstimateSidecar,
    BrowserPitLossEstimateTimeline,
    BrowserPitLossEstimateUnavailable,
)
from f1_replay_pipeline.domain.canonical_schema import CANONICAL_TABLE_SCHEMAS_V2


SCHEMA_ROOT = Path(__file__).resolve().parents[4] / "contracts" / "replay-data" / "v2" / "schemas"
def _snapshot() -> CanonicalGenerationSnapshot:
    session_metadata = pl.DataFrame([{
        "session_id": "race-one", "year": 2026, "round_number": 1,
        "event_name": "Race", "session_name": "Race", "session_type": "R",
        "session_mode": "race", "session_start_time_utc": None,
    }], schema=dict(CANONICAL_TABLE_SCHEMAS_V2["session_metadata"]), strict=True)
    return CanonicalGenerationSnapshot(
        "canonical-one", "a" * 64, {"session_metadata": session_metadata},
    )


def _chunk(track_status: tuple[int, ...] = (1, 1)) -> BrowserChunk:
    fields = BrowserDriverFields(
        "HAM", (0, 1000), (1.0, 2.0), (3.0, 4.0), (5.0, 6.0), (7.0, 8.0),
        (0, 1), (None, 7), (None, 1), ("OnTrack", "OnTrack"), (1, 1), ("SOFT", "SOFT"),
        (False, False), (None, None), (None, None), (None, None),
    )
    return BrowserChunk(
        "chunk-001", 1, 0, 2000, BrowserOverlap("none", None, None, None, None),
        (0, 1000), 0, {"HAM": fields}, (("HAM",), ("HAM",)), track_status,
        ("clear", "clear"), (BrowserEvent(1000, "notice", "green flag"),),
    )


def _assets(
    *,
    fixture_id: str = "race-one",
    track_id: str = "track-one",
    track_name: str = "Albert Park Circuit",
) -> dict[str, object]:
    point = {"x": 0.0, "y": 0.0}
    polyline = (point, {"x": 1.0, "y": 0.0}, {"x": 1.0, "y": 1.0}, {"x": 0.0, "y": 1.0})
    return {
        "contractVersion": "v2", "fixtureId": fixture_id, "trackId": track_id,
        "trackName": track_name, "coordinateSpace": {"units": "meters", "origin": "test"},
        "circuitLengthMeters": 1.0, "rotationDegrees": 0.0,
        "startFinish": {"center": point, "inner": point, "outer": point},
        "centerLine": polyline, "innerBoundary": polyline, "outerBoundary": polyline,
    }


def _delivery(
    *,
    fixture_id: str = "race-one",
    track_id: str = "track-one",
    track_status: tuple[int, ...] = (1, 1),
    pit_loss_estimate_sidecar: BrowserPitLossEstimateSidecar | None = None,
    track_assets: Mapping[str, object] | None = None,
) -> BrowserDeliveryBuild:
    manifest = BrowserManifest(fixture_id, "Race One", ({
        "id": "HAM", "displayName": "Hamilton", "teamName": "Team",
        "colorHex": "#000000", "carNumber": "44",
    },), session_mode="race")
    return BrowserDeliveryBuild(
        _snapshot(), manifest,
        track_assets if track_assets is not None else _assets(fixture_id=fixture_id, track_id=track_id),
        (_chunk(track_status),), pit_loss_estimate_sidecar=pit_loss_estimate_sidecar,
    )


def _curated_sidecar(*, replay_start_ms: int = 0) -> BrowserPitLossEstimateSidecar:
    return build_curated_pit_loss_estimate_sidecar(
        replay_start_ms,
        fixture_id=AUSTRALIA_FIXTURE_ID,
        track_id=AUSTRALIA_TRACK_ID,
    )


def _curated_delivery(*, replay_start_ms: int = 0) -> BrowserDeliveryBuild:
    return _delivery(
        fixture_id=AUSTRALIA_FIXTURE_ID,
        track_id=AUSTRALIA_TRACK_ID,
        pit_loss_estimate_sidecar=_curated_sidecar(replay_start_ms=replay_start_ms),
    )


def _curated(**changes: object) -> BrowserPitLossEstimateSidecar:
    values: dict[str, object] = {
        "fixture_id": AUSTRALIA_FIXTURE_ID,
        "track_id": AUSTRALIA_TRACK_ID,
        "method": CURATED_BASELINE_METHOD,
        "race": BrowserPitLossEstimateTimeline((0,), (19_300,)),
        "safety_car": BrowserPitLossEstimateTimeline((0,), (9_300,)),
        "virtual_safety_car": BrowserPitLossEstimateTimeline((0,), (12_300,)),
    }
    values.update(changes)
    return BrowserPitLossEstimateSidecar(**values)  # type: ignore[arg-type]


def _redigest_sidecar_with(
    browser: Path,
    *,
    delivery_version: str = "delivery-one",
    **changes: object,
) -> None:
    """Rewrite the stored sidecar with JSON mutations and re-digest its chain.

    Mirrors the secure validation mutation pattern: the sidecar bytes, the
    manifest reference digest, and the pointer manifest checksum are all
    updated consistently so validation reaches the payload schema check.
    """
    sidecar_path = (
        browser / "generations" / delivery_version / PIT_LOSS_ESTIMATE_SIDECAR_FILENAME
    )
    sidecar = json.loads(sidecar_path.read_bytes())
    sidecar.update(changes)
    sidecar_bytes = json.dumps(sidecar, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    sidecar_path.write_bytes(sidecar_bytes)

    manifest_path = browser / "generations" / delivery_version / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["pitLossEstimateSidecar"]["sha256"] = hashlib.sha256(sidecar_bytes).hexdigest()
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    manifest_path.write_bytes(manifest_bytes)

    pointer_path = browser / "browser-current.json"
    pointer = json.loads(pointer_path.read_bytes())
    pointer["manifestSha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")


def test_curated_sidecar_emits_known_values_when_sc_vsc_absent() -> None:
    # Arrange: the catalog path resolves every status independently, so the
    # current race's status timeline is not an input at all.
    sidecar = build_curated_pit_loss_estimate_sidecar(
        0, fixture_id=AUSTRALIA_FIXTURE_ID, track_id=AUSTRALIA_TRACK_ID,
    )

    # Act: serialize the resolved sidecar.
    payload = sidecar.as_dict()

    # Assert: Green/SC/VSC known values are all emitted as single replay-start
    # points even though no Safety Car or Virtual Safety Car ever occurred.
    assert payload == {
        "contractVersion": "v2",
        "fixtureId": AUSTRALIA_FIXTURE_ID,
        "trackId": AUSTRALIA_TRACK_ID,
        "method": CURATED_BASELINE_METHOD,
        "race": {"timeMs": [0], "estimatedLossMs": [19_300]},
        "safetyCar": {"timeMs": [0], "estimatedLossMs": [9_300]},
        "virtualSafetyCar": {"timeMs": [0], "estimatedLossMs": [12_300]},
    }
    assert "observedSampleCount" not in cast(Mapping[str, object], payload["race"])


# --- Generator fixture binding (2026-01-race) -------------------------------


def _generator_assets() -> dict[str, object]:
    """Return the actual generator track assets for the 2026 Australia race."""
    return _assets(
        fixture_id="2026-01-race",
        track_id="2026-01-race-telemetry-layout-v1",
        track_name="Australian Grand Prix",
    )


def _generator_sidecar() -> BrowserPitLossEstimateSidecar:
    return build_curated_pit_loss_estimate_sidecar(
        0,
        fixture_id="2026-01-race",
        track_id="2026-01-race-telemetry-layout-v1",
        track_name="Australian Grand Prix",
    )


def test_curated_sidecar_accepts_generator_binding_with_track_name() -> None:
    # Arrange / Act: build the curated sidecar with the actual generator
    # binding, whose fixture/asset ids are not themselves registered.
    sidecar = _generator_sidecar()

    # Assert: the sidecar preserves the delivery track id and emits Australia's
    # known Green/SC/VSC values as single replay-start points.
    assert sidecar.fixture_id == "2026-01-race"
    assert sidecar.track_id == "2026-01-race-telemetry-layout-v1"
    assert sidecar.method == CURATED_BASELINE_METHOD
    race = cast(BrowserPitLossEstimateTimeline, sidecar.race)
    safety_car = cast(BrowserPitLossEstimateTimeline, sidecar.safety_car)
    virtual_safety_car = cast(BrowserPitLossEstimateTimeline, sidecar.virtual_safety_car)
    assert race.estimated_loss_ms == (19_300,)
    assert safety_car.estimated_loss_ms == (9_300,)
    assert virtual_safety_car.estimated_loss_ms == (12_300,)


def test_generator_binding_sidecar_publishes_and_validates(tmp_path: Path) -> None:
    # Arrange: a delivery bound to the actual generator fixture and track
    # assets, with a curated sidecar resolved through the track name.
    delivery = _delivery(
        fixture_id="2026-01-race",
        track_id="2026-01-race-telemetry-layout-v1",
        track_assets=_generator_assets(),
        pit_loss_estimate_sidecar=_generator_sidecar(),
    )

    # Act: publish the delivery and read the stored sidecar artifact.
    result = publish_browser_delivery(
        browser_parent=tmp_path / "browser", delivery_version="delivery-one",
        delivery=delivery, schema_root=SCHEMA_ROOT,
    )
    payload = json.loads(
        (result.generation_path / PIT_LOSS_ESTIMATE_SIDECAR_FILENAME).read_bytes(),
    )

    # Assert: the curated Australia values publish under the generator binding
    # and the complete delivery passes secure validation.
    assert payload["fixtureId"] == "2026-01-race"
    assert payload["trackId"] == "2026-01-race-telemetry-layout-v1"
    assert payload["race"]["estimatedLossMs"] == [19_300]
    assert payload["safetyCar"]["estimatedLossMs"] == [9_300]
    assert payload["virtualSafetyCar"]["estimatedLossMs"] == [12_300]
    validate_complete_browser_delivery(
        tmp_path / "browser", expected_generation_id="canonical-one",
        expected_manifest_sha256="a" * 64, schema_root=SCHEMA_ROOT,
    )


def test_publication_rejects_curated_sidecar_without_resolvable_track_name(
    tmp_path: Path,
) -> None:
    # Arrange: a curated sidecar resolved through the track name, published
    # against track assets whose display name cannot establish the circuit.
    delivery = _delivery(
        fixture_id="2026-01-race",
        track_id="2026-01-race-telemetry-layout-v1",
        track_assets=_assets(
            fixture_id="2026-01-race",
            track_id="2026-01-race-telemetry-layout-v1",
            track_name="Bogus Circuit",
        ),
        pit_loss_estimate_sidecar=_generator_sidecar(),
    )

    # Act / Assert: the identity validation fails closed at publication.
    with pytest.raises(
        BrowserDeliveryPublicationError,
        match="identity-consistent",
    ):
        publish_browser_delivery(
            browser_parent=tmp_path / "browser", delivery_version="delivery-one",
            delivery=delivery, schema_root=SCHEMA_ROOT,
        )


@pytest.mark.parametrize(
    (
        "status_code", "status", "status_label", "expected_loss_ms",
        "expected_evidence_count", "expected_confidence",
    ),
    [
        (1, "green", "Green", 19_300, 1, "high"),
        (4, "sc", "SC", 9_300, 1, "medium"),
        (6, "vsc", "VSC", 12_300, 1, "medium"),
    ],
)
def test_resolver_returns_known_catalog_values_for_australia(
    status_code: int,
    status: str,
    status_label: str,
    expected_loss_ms: int,
    expected_evidence_count: int,
    expected_confidence: str,
) -> None:
    # Act: resolve the catalog for the checked-in Australia baseline.
    resolution = resolve_pit_loss_baseline(
        AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, status_code,
    )

    # Assert: the resolution preserves the selected status's catalog value
    # and its own per-status metadata, never the Green entry-level defaults.
    assert resolution.available
    assert resolution.status == status
    assert resolution.status_label == status_label
    assert resolution.estimated_loss_ms == expected_loss_ms
    assert resolution.catalog_version == CATALOG_VERSION
    assert resolution.evidence_count == expected_evidence_count
    assert resolution.confidence == expected_confidence
    assert resolution.provenance is not None
    serialized = resolution.as_dict()
    assert serialized["confidence"] == expected_confidence
    assert serialized["evidenceCount"] == expected_evidence_count
    # sourceStatus is catalog-only metadata and never serialized.
    assert "sourceStatus" not in serialized


def test_resolver_binds_track_asset_id_to_catalog_entry() -> None:
    # Act: resolve through the fixture's telemetry layout asset identifier.
    resolution = resolve_pit_loss_baseline(
        AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ASSET_ID, 1,
    )

    # Assert: the alternate track binding still resolves the same known value.
    assert resolution.available
    assert resolution.estimated_loss_ms == 19_300
    assert resolution.track_id == AUSTRALIA_TRACK_ASSET_ID


def test_curated_sidecar_publication_binds_digest_and_passes_full_schema(tmp_path: Path) -> None:
    # Arrange: a delivery whose curated sidecar is bound to the manifest and assets.
    delivery = _curated_delivery()

    # Act: publish the delivery and read back the sidecar artifact and reference.
    result = publish_browser_delivery(
        browser_parent=tmp_path / "browser", delivery_version="delivery-one",
        delivery=delivery, schema_root=SCHEMA_ROOT,
    )
    sidecar_path = result.generation_path / PIT_LOSS_ESTIMATE_SIDECAR_FILENAME
    manifest = json.loads(result.manifest_path.read_bytes())

    # Assert: the artifact digest and manifest reference agree.
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


def test_curated_sidecar_payload_contains_values_only(tmp_path: Path) -> None:
    # Act: publish the curated delivery and parse the stored sidecar payload.
    result = publish_browser_delivery(
        browser_parent=tmp_path / "browser", delivery_version="delivery-one",
        delivery=_curated_delivery(), schema_root=SCHEMA_ROOT,
    )
    sidecar_path = result.generation_path / PIT_LOSS_ESTIMATE_SIDECAR_FILENAME
    manifest = json.loads(result.manifest_path.read_bytes())
    payload = json.loads(sidecar_path.read_bytes())

    # Assert: the payload is bound to the manifest and contains values only.
    assert payload["fixtureId"] == manifest["fixtureId"] == AUSTRALIA_FIXTURE_ID
    assert payload["trackId"] == AUSTRALIA_TRACK_ID
    assert payload["race"] == {"timeMs": [0], "estimatedLossMs": [19_300]}
    assert payload["safetyCar"] == {"timeMs": [0], "estimatedLossMs": [9_300]}
    assert payload["virtualSafetyCar"] == {"timeMs": [0], "estimatedLossMs": [12_300]}
    for key in (
        "sourceStatus", "metricDefinition", "provenance", "evidenceCount",
        "confidence", "catalogVersion", "statusMetadata", "derivation",
    ):
        assert key not in payload


def test_publication_semantic_validation_rejects_observed_sample_count_in_curated_timelines() -> None:
    # Arrange: a curated-style timeline that smuggles the current-race
    # observation array past the JSON schema guard must still fail the
    # explicit curated publication rule, even when the curated path permits
    # the array to be omitted entirely.
    manifest = {"chunks": [{"startMs": 0, "endMs": 2_000}]}

    # Act / Assert: allow_missing_observed_sample_count permits omission but
    # never presence of the array for a curated timeline.
    with pytest.raises(
        BrowserDeliveryPublicationError,
        match="curated pit loss timeline cannot carry observedSampleCount",
    ):
        _validate_pit_loss_estimate_timeline(
            {
                "timeMs": [0],
                "estimatedLossMs": [19_300],
                "observedSampleCount": [0],
            },
            manifest,
            "race",
            allow_missing_observed_sample_count=True,
            require_single_point=True,
        )


def test_complete_validation_reports_curated_sidecar_schema_stage(tmp_path: Path) -> None:
    # Arrange: publish a curated delivery, then run the complete validator.
    publish_browser_delivery(
        browser_parent=tmp_path / "browser", delivery_version="delivery-one",
        delivery=_curated_delivery(), schema_root=SCHEMA_ROOT,
    )
    progress: list[str | BrowserValidationProgress] = []

    # Act: validate the pointer-selected delivery with progress.
    validate_complete_browser_delivery(
        tmp_path / "browser", expected_generation_id="canonical-one",
        expected_manifest_sha256="a" * 64, schema_root=SCHEMA_ROOT,
        progress=progress.append,
    )

    # Assert: the stored curated sidecar passed the real JSON schema.
    details = [update.detail for update in progress if isinstance(update, BrowserValidationProgress)]
    assert "pit loss estimate sidecar schema" in details


def test_delivery_build_rejects_unbound_curated_sidecar() -> None:
    # Arrange: the curated sidecar's identity must match the delivery manifest
    # and track assets.
    sidecar = _curated_sidecar()

    # Act / Assert: mismatched fixture or track bindings fail closed at build time.
    with pytest.raises(ValueError, match="fixture_id disagrees"):
        _delivery(
            fixture_id=AUSTRALIA_FIXTURE_ID, track_id=AUSTRALIA_TRACK_ID,
            pit_loss_estimate_sidecar=replace(sidecar, fixture_id="race-one"),
        )
    with pytest.raises(ValueError, match="track_id disagrees"):
        _delivery(
            fixture_id=AUSTRALIA_FIXTURE_ID, track_id=AUSTRALIA_TRACK_ID,
            pit_loss_estimate_sidecar=replace(sidecar, track_id="silverstone"),
        )
    with pytest.raises(TypeError, match="BrowserPitLossEstimateSidecar"):
        _delivery(
            fixture_id=AUSTRALIA_FIXTURE_ID, track_id=AUSTRALIA_TRACK_ID,
            pit_loss_estimate_sidecar=object(),  # type: ignore[arg-type]
        )


def test_unknown_track_resolution_fails_closed() -> None:
    # Act: resolve a fixture/track absent from the 26-circuit catalog union.
    resolution = resolve_pit_loss_baseline("some-fixture", "pau", 1)

    # Assert: the resolver returns an explicit unavailable result, never a value.
    assert not resolution.available
    assert resolution.status == UNAVAILABLE_STATUS
    assert resolution.status_label == "Unavailable"
    assert resolution.estimated_loss_ms is None
    assert resolution.provenance is None
    assert resolution.error == "no curated pit-loss baseline is available for this fixture and track"


def test_unknown_track_curated_sidecar_raises() -> None:
    # Act / Assert: building a curated sidecar for an unknown track is a
    # generation error rather than a fallback to the legacy estimate.
    with pytest.raises(
        CuratedPitLossBaselineUnavailableError,
        match="no curated pit-loss baseline",
    ):
        build_curated_pit_loss_estimate_sidecar(
            0, fixture_id="some-fixture", track_id="pau",
        )


@pytest.mark.parametrize("status_code", [3, 99, None, (1, 4), (2,), ()])
def test_resolution_fails_closed_for_unsupported_or_mixed_status(
    status_code: StatusCodeInput,
) -> None:
    # Act: resolve Australia under a status the catalog cannot classify.
    resolution = resolve_pit_loss_baseline(
        AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, status_code,
    )

    # Assert: every ambiguous or unsupported interval fails closed.
    assert not resolution.available
    assert resolution.status == UNAVAILABLE_STATUS
    assert resolution.estimated_loss_ms is None
    assert resolution.error


def test_malformed_catalog_is_rejected_at_the_resolver_boundary() -> None:
    # Act / Assert: an empty catalog cannot satisfy a resolution.
    with pytest.raises(ValueError, match="at least one entry"):
        resolve_pit_loss_baseline(
            AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, 1,
            catalog={"catalogVersion": "v1", "entries": []},
        )


def test_curated_sidecar_requires_all_three_status_values() -> None:
    # Act / Assert: a curated sidecar without every status value is rejected.
    with pytest.raises(ValueError, match="must resolve Green, SC, and VSC values"):
        _curated(safety_car=None)
    with pytest.raises(ValueError, match="must resolve Green, SC, and VSC values"):
        _curated(virtual_safety_car=None)


def test_curated_sidecar_rejects_multi_point_timelines() -> None:
    # Act / Assert: curated values are replay-start points, never causal timelines.
    with pytest.raises(ValueError, match="replay-start points"):
        _curated(
            safety_car=BrowserPitLossEstimateTimeline((0, 500), (9_300, 9_200), (0, 1)),
        )
    with pytest.raises(ValueError, match="replay-start point"):
        _curated(
            race=BrowserPitLossEstimateTimeline((0, 500), (19_300, 19_200), (0, 1)),
        )


def test_curated_sidecar_rejects_instantiated_timeline_with_observed_sample_count() -> None:
    # Act / Assert: even a replay-start single-point timeline carrying the
    # current-race observation array is not a valid curated shape; catalog
    # evidence must never masquerade as a race observation count.
    with pytest.raises(ValueError, match="cannot carry current-race observedSampleCount"):
        _curated(
            race=BrowserPitLossEstimateTimeline((0,), (19_300,), (0,)),
        )


def test_curated_sidecar_rejects_unavailable_status_value() -> None:
    # Arrange / Act / Assert: an unavailable status value is a valid legacy
    # shape but never a valid curated shape; the model fails closed at
    # construction before publication can even see the payload.
    with pytest.raises(
        ValueError,
        match="curated pit loss status values must be available replay-start timelines",
    ):
        _curated(safety_car=BrowserPitLossEstimateUnavailable())
    with pytest.raises(
        ValueError,
        match="curated pit loss status values must be available replay-start timelines",
    ):
        _curated(virtual_safety_car=BrowserPitLossEstimateUnavailable())


def test_legacy_sidecar_unavailable_status_remains_accepted_and_publishable(
    tmp_path: Path,
) -> None:
    # Arrange: a legacy sidecar whose occurring Safety Car status has no
    # eligible sample is represented as an explicit unavailable value.
    sidecar = BrowserPitLossEstimateSidecar(
        fixture_id="race-one",
        track_id="track-one",
        method=LEGACY_PIT_LOSS_ESTIMATE_METHOD,
        race=BrowserPitLossEstimateTimeline((0,), (22_000,), (0,)),
        safety_car=BrowserPitLossEstimateUnavailable(),
    )

    # Act: publish the legacy delivery with a chunk that proves SC occurred.
    result = publish_browser_delivery(
        browser_parent=tmp_path / "browser",
        delivery_version="delivery-one",
        delivery=_delivery(
            fixture_id="race-one",
            track_id="track-one",
            track_status=(4, 4),
            pit_loss_estimate_sidecar=sidecar,
        ),
        schema_root=SCHEMA_ROOT,
    )
    payload = json.loads(
        (result.generation_path / PIT_LOSS_ESTIMATE_SIDECAR_FILENAME).read_bytes(),
    )

    # Assert: the legacy unavailable status round-trips and validates.
    assert payload["safetyCar"] == {"status": "unavailable"}
    validate_complete_browser_delivery(
        tmp_path / "browser", expected_generation_id="canonical-one",
        expected_manifest_sha256="a" * 64, schema_root=SCHEMA_ROOT,
    )


# --- Multi-season known-track generation -------------------------------------


def test_multi_season_fixtureless_track_reuses_one_catalog_entry() -> None:
    # Arrange: Monza is a fixture-less union entry, so every calendar season of
    # the same physical circuit must reuse the single stable baseline.
    expected = {
        "green": MONZA_BASELINE.green_ms,
        "sc": MONZA_BASELINE.sc_ms,
        "vsc": MONZA_BASELINE.vsc_ms,
    }
    season_fixtures = (
        "2024-round-16-italian-grand-prix",
        "2025-round-16-italian-grand-prix",
        "2026-round-16-italian-grand-prix",
    )

    # Act: build the curated sidecar for each season's fixture of the circuit.
    sidecars = [
        build_curated_pit_loss_estimate_sidecar(
            0, fixture_id=fixture_id, track_id="monza", catalog_track_id="monza",
        )
        for fixture_id in season_fixtures
    ]

    # Assert: every season resolves the identical stable per-circuit baseline
    # and value payloads, while each sidecar keeps its own delivery fixtureId.
    for sidecar, fixture_id in zip(sidecars, season_fixtures, strict=True):
        race = cast(BrowserPitLossEstimateTimeline, sidecar.race)
        safety_car = cast(BrowserPitLossEstimateTimeline, sidecar.safety_car)
        virtual_safety_car = cast(BrowserPitLossEstimateTimeline, sidecar.virtual_safety_car)
        assert sidecar.method == CURATED_BASELINE_METHOD
        assert sidecar.fixture_id == fixture_id
        assert sidecar.track_id == "monza"
        assert race.estimated_loss_ms == (expected["green"],)
        assert safety_car.estimated_loss_ms == (expected["sc"],)
        assert virtual_safety_car.estimated_loss_ms == (expected["vsc"],)
    first = sidecars[0].as_dict()
    for sidecar in sidecars[1:]:
        payload = sidecar.as_dict()
        assert payload["race"] == first["race"]
        assert payload["safetyCar"] == first["safetyCar"]
        assert payload["virtualSafetyCar"] == first["virtualSafetyCar"]
        assert set(payload) == set(first)
        for key in ("sourceStatus", "provenance", "evidenceCount", "confidence", "catalogVersion", "statusMetadata"):
            assert key not in payload


def test_barcelona_and_madrid_resolve_to_distinct_circuit_baselines() -> None:
    # Arrange: Barcelona (2024/2025) and Madring (2026) are separate physical
    # venues that share the ambiguous Spanish GP event name.  Each resolves
    # through its unambiguous track binding to a distinct catalog entry.
    barcelona_identity = resolve_binding_identity(track_id="barcelona-catalunya")
    madrid_identity = resolve_binding_identity(track_id="madring")
    assert barcelona_identity.track_id == "barcelona-catalunya"
    assert madrid_identity.track_id == "madring"

    # Act: resolve each distinct venue's baseline at generation time for its
    # own calendar season's Spanish GP fixture.
    barcelona = build_curated_pit_loss_estimate_sidecar(
        0,
        fixture_id="2024-round-16-spanish-grand-prix",
        track_id="barcelona-catalunya",
        catalog_track_id=barcelona_identity.track_id,
    )
    madrid = build_curated_pit_loss_estimate_sidecar(
        0,
        fixture_id="2026-round-16-spanish-grand-prix",
        track_id="madring",
        catalog_track_id=madrid_identity.track_id,
    )

    # Assert: distinct physical venues keep distinct baseline values even
    # though both fixtures share the ambiguous Spanish GP event name.
    barcelona_race = cast(BrowserPitLossEstimateTimeline, barcelona.race)
    madrid_race = cast(BrowserPitLossEstimateTimeline, madrid.race)
    assert barcelona_race.estimated_loss_ms == (BARCELONA_BASELINE.green_ms,)
    assert madrid_race.estimated_loss_ms == (MADRING_BASELINE.green_ms,)
    assert BARCELONA_BASELINE.green_ms != MADRING_BASELINE.green_ms


# --- sourceStatus stays catalog-only -----------------------------------------


def test_curated_sidecar_payload_and_manifest_never_serialize_source_status(
    tmp_path: Path,
) -> None:
    # Arrange / Act: publish the curated delivery and read every JSON artifact.
    result = publish_browser_delivery(
        browser_parent=tmp_path / "browser", delivery_version="delivery-one",
        delivery=_curated_delivery(), schema_root=SCHEMA_ROOT,
    )
    sidecar_text = (
        result.generation_path / PIT_LOSS_ESTIMATE_SIDECAR_FILENAME
    ).read_text(encoding="utf-8")
    manifest_text = result.manifest_path.read_text(encoding="utf-8")

    # Assert: the catalog-only sourceStatus kind never reaches the browser.
    assert "sourceStatus" not in sidecar_text
    assert "sourceStatus" not in manifest_text


def test_schema_rejects_curated_sidecar_with_source_status(tmp_path: Path) -> None:
    # Arrange: publish a valid curated delivery, then re-digest a mutated copy
    # that injects the catalog-only sourceStatus into the sidecar payload.
    publish_browser_delivery(
        browser_parent=tmp_path / "browser", delivery_version="delivery-one",
        delivery=_curated_delivery(), schema_root=SCHEMA_ROOT,
    )
    _redigest_sidecar_with(tmp_path / "browser", sourceStatus="direct")

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


# --- Canonical/chunk/template immutability -----------------------------------


def test_curated_sidecar_does_not_change_chunk_shapes_or_touch_templates(
    tmp_path: Path,
) -> None:
    # Arrange: a bare delivery and the identical chunks with a curated sidecar.
    bare = _delivery(fixture_id=AUSTRALIA_FIXTURE_ID, track_id=AUSTRALIA_TRACK_ID)
    enriched = _curated_delivery()

    # Act: publish both to independent browser roots.
    bare_result = publish_browser_delivery(
        browser_parent=tmp_path / "browser-one", delivery_version="delivery-one",
        delivery=bare, schema_root=SCHEMA_ROOT,
    )
    enriched_result = publish_browser_delivery(
        browser_parent=tmp_path / "browser-two", delivery_version="delivery-one",
        delivery=enriched, schema_root=SCHEMA_ROOT,
    )

    # Assert: chunk bytes and chunk references are unchanged by the additive
    # curated sidecar, and no templates/ or R2 path leaks into the manifest.
    assert [path.read_bytes() for path in bare_result.chunk_paths] == [
        path.read_bytes() for path in enriched_result.chunk_paths
    ]
    assert json.loads(bare_result.manifest_path.read_bytes())["chunks"] == json.loads(
        enriched_result.manifest_path.read_bytes(),
    )["chunks"]
    manifest_text = enriched_result.manifest_path.read_text(encoding="utf-8")
    assert "templates" not in manifest_text
    assert "r2" not in manifest_text
