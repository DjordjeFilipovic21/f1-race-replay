"""Focused deterministic coverage for the Australia pit-loss baseline fixture.

The fixture under test resolves the curated Australia baseline (Green 19 300 ms,
VSC 12 300 ms, SC 9 300 ms) with explicit provenance, confidence, and evidence
metadata.  These tests prove:

* the fixture values agree with the repository-local catalog entry;
* all three status values are available at replay start even when the input
  race only ever proved All Clear (no SC/VSC observations or status codes);
* publication emits the manifest sidecar reference and its SHA-256 digest and
  passes complete secure validation;
* the setup is deterministic, local-only, and never touches the network,
  ``templates/``, or R2 publication.

Every test follows Arrange-Act-Assert and is independent and deterministic.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    PIT_LOSS_ESTIMATE_SIDECAR_FILENAME,
    PIT_LOSS_ESTIMATE_SIDECAR_SCHEMA_ID,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_publication import (
    publish_browser_delivery,
    validate_complete_browser_delivery,
)
from f1_replay_pipeline.delivery.browser.browser_pit_loss_baseline_catalog import (
    AUSTRALIA_BASELINE,
    AUSTRALIA_SOURCE_URL,
    PitLossBaselineProvenance,
)
from f1_replay_pipeline.delivery.browser.browser_pit_loss_model import (
    CuratedPitLossBaselineUnavailableError,
    build_curated_pit_loss_estimate_sidecar,
)
from f1_replay_pipeline.delivery.browser.browser_pit_loss_sidecar import (
    BrowserPitLossEstimateTimeline,
)
from fixtures.pit_loss_baseline_australia import (
    AUSTRALIA_CANONICAL_GENERATION_ID,
    AUSTRALIA_CAPTURED_DATE,
    AUSTRALIA_CONFIDENCE,
    AUSTRALIA_DELIVERY_VERSION,
    AUSTRALIA_EVIDENCE,
    AUSTRALIA_EVIDENCE_COUNT,
    AUSTRALIA_FIXTURE_ID,
    AUSTRALIA_GREEN_MS,
    AUSTRALIA_METHOD,
    AUSTRALIA_SC_DISCOUNT_MS,
    AUSTRALIA_SC_MS,
    AUSTRALIA_SEASON,
    AUSTRALIA_TRACK_ASSET_ID,
    AUSTRALIA_TRACK_ID,
    AUSTRALIA_VSC_DISCOUNT_MS,
    AUSTRALIA_VSC_MS,
    build_australia_delivery,
    build_australia_sidecar,
)


REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMA_ROOT = REPO_ROOT / "contracts" / "replay-data" / "v1" / "schemas"
TEMPLATES_ROOT = REPO_ROOT / "templates"

_CANONICAL_MANIFEST_SHA256 = "a" * 64


def _publish_australia(browser: Path):
    """Publish the fixture Australia delivery into ``browser``."""
    return publish_browser_delivery(
        browser_parent=browser,
        delivery_version=AUSTRALIA_DELIVERY_VERSION,
        delivery=build_australia_delivery(),
        schema_root=SCHEMA_ROOT,
    )


def _validate_australia(browser: Path) -> None:
    validate_complete_browser_delivery(
        browser,
        expected_generation_id=AUSTRALIA_CANONICAL_GENERATION_ID,
        expected_manifest_sha256=_CANONICAL_MANIFEST_SHA256,
        schema_root=SCHEMA_ROOT,
    )


def _tree_snapshot(root: Path) -> tuple[tuple[str, str], ...]:
    """Return deterministic relative-path and SHA-256 digests for a tree."""
    if not root.exists():
        return ()
    snapshot = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            snapshot.append((relative, digest))
    return tuple(snapshot)


# --- Fixture values against the curated catalog -------------------------------


def test_australia_fixture_values_match_the_curated_catalog_entry() -> None:
    # Arrange: the canonical repository-local catalog entry for Australia.
    entry = AUSTRALIA_BASELINE

    # Act: the fixture exposes the resolved Australia values.
    # Assert: every fixture literal agrees with the immutable catalog entry.
    assert AUSTRALIA_GREEN_MS == entry.green_ms == 19_300
    assert AUSTRALIA_VSC_MS == entry.vsc_ms == 12_300
    assert AUSTRALIA_SC_MS == entry.sc_ms == 9_300
    assert AUSTRALIA_FIXTURE_ID == entry.fixture_id
    assert AUSTRALIA_TRACK_ID == entry.track_id
    assert AUSTRALIA_TRACK_ASSET_ID == entry.track_asset_id
    assert AUSTRALIA_SEASON == entry.season == 2026
    provenance = cast(PitLossBaselineProvenance, entry.provenance)
    assert AUSTRALIA_METHOD == provenance.method == "curated-track-baseline-v1"


def test_australia_provenance_confidence_and_evidence_are_explicit() -> None:
    # Arrange: the catalog entry is the immutable source of the fixture values.
    entry = AUSTRALIA_BASELINE
    provenance = cast(PitLossBaselineProvenance, entry.provenance)

    # Act: read the fixture's documented source metadata.
    # Assert: official HTTPS source, captured date, evidence text, and counts.
    assert provenance.source_url == AUSTRALIA_SOURCE_URL
    assert provenance.source_url.startswith("https://www.formula1.com/")
    assert provenance.captured_date == AUSTRALIA_CAPTURED_DATE == "2026-08-04"
    assert provenance.evidence == AUSTRALIA_EVIDENCE
    assert AUSTRALIA_EVIDENCE == "Formula1.com lists Australia pit stop time loss as 19.30 seconds."
    assert entry.evidence_count == AUSTRALIA_EVIDENCE_COUNT == 1
    assert entry.confidence == AUSTRALIA_CONFIDENCE == "high"


def test_australia_catalog_statuses_carry_per_status_source_metadata() -> None:
    # Arrange: the immutable Australia catalog entry.
    entry = AUSTRALIA_BASELINE

    # Act: read each status's own source status, confidence, and evidence.
    green = entry.statuses["green"]
    vsc = entry.statuses["vsc"]
    sc = entry.statuses["sc"]

    # Assert: Green is a direct official value while VSC and SC are derived,
    # each with its own per-status metadata rather than the Green defaults.
    assert green.source_status == "direct"
    assert green.confidence == "high"
    assert green.evidence_count == 1
    assert vsc.source_status == "derived"
    assert vsc.confidence == "medium"
    assert sc.source_status == "derived"
    assert sc.confidence == "medium"

    # Assert: the catalog-only sourceStatus kind is never serialized.
    payload = build_australia_sidecar(0).as_dict()
    assert "sourceStatus" not in json.dumps(payload)


def test_australia_baseline_satisfies_monotonic_invariant_and_discounts() -> None:
    # Arrange: the fixture's catalog-backed milliseconds values.
    # Act: verify the documented SC/VSC discounts against the Green baseline.
    # Assert: the monotonic invariant and the configured 7 s / 10 s discounts.
    assert AUSTRALIA_SC_MS <= AUSTRALIA_VSC_MS <= AUSTRALIA_GREEN_MS
    assert AUSTRALIA_GREEN_MS - AUSTRALIA_VSC_MS == AUSTRALIA_VSC_DISCOUNT_MS == 7_000
    assert AUSTRALIA_GREEN_MS - AUSTRALIA_SC_MS == AUSTRALIA_SC_DISCOUNT_MS == 10_000
    assert AUSTRALIA_SC_DISCOUNT_MS >= AUSTRALIA_VSC_DISCOUNT_MS


# --- Catalog-backed availability at replay start -----------------------------


def test_australia_sidecar_is_available_at_replay_start_with_all_values() -> None:
    # Arrange: a non-zero replay start proves values are generation-time static.
    replay_start_ms = 12_345

    # Act: build the curated sidecar without any race observations.
    sidecar = build_australia_sidecar(replay_start_ms)

    # Assert: Green, SC, and VSC are all replay-start points with catalog values.
    assert sidecar.method == AUSTRALIA_METHOD
    assert sidecar.fixture_id == AUSTRALIA_FIXTURE_ID
    assert sidecar.track_id == AUSTRALIA_TRACK_ID
    assert isinstance(sidecar.race, BrowserPitLossEstimateTimeline)
    assert sidecar.race.time_ms == (replay_start_ms,)
    assert sidecar.race.estimated_loss_ms == (AUSTRALIA_GREEN_MS,)
    assert isinstance(sidecar.safety_car, BrowserPitLossEstimateTimeline)
    assert sidecar.safety_car.time_ms == (replay_start_ms,)
    assert sidecar.safety_car.estimated_loss_ms == (AUSTRALIA_SC_MS,)
    assert isinstance(sidecar.virtual_safety_car, BrowserPitLossEstimateTimeline)
    assert sidecar.virtual_safety_car.time_ms == (replay_start_ms,)
    assert sidecar.virtual_safety_car.estimated_loss_ms == (AUSTRALIA_VSC_MS,)


def test_australia_sidecar_does_not_pretend_to_have_race_observations() -> None:
    # Arrange / Act: serialize the fixture sidecar to its wire contract.
    payload = build_australia_sidecar(0).as_dict()

    # Assert: curated timelines carry no fabricated observedSampleCount.
    assert payload["contractVersion"] == "v1"
    assert payload["method"] == AUSTRALIA_METHOD
    for key in (
        "sourceStatus", "metricDefinition", "provenance", "evidenceCount",
        "confidence", "catalogVersion", "statusMetadata", "derivation",
    ):
        assert key not in payload
    for name in ("race", "safetyCar", "virtualSafetyCar"):
        timeline = cast(Mapping[str, object], payload[name])
        assert "observedSampleCount" not in timeline
        assert timeline["timeMs"] == [0]
        assert len(cast(list[object], timeline["estimatedLossMs"])) == 1


def test_australia_sidecar_provides_sc_and_vsc_when_input_race_has_only_all_clear() -> None:
    # Arrange: the fixture delivery's chunk only ever proves All Clear.
    delivery = build_australia_delivery()
    assert delivery.chunks[0].track_status_code == (1, 1)

    # Act: read the curated sidecar bound to that All Clear delivery.
    sidecar = delivery.pit_loss_estimate_sidecar

    # Assert: SC and VSC remain available without any SC/VSC in the input race.
    assert sidecar is not None
    assert isinstance(sidecar.safety_car, BrowserPitLossEstimateTimeline)
    assert isinstance(sidecar.virtual_safety_car, BrowserPitLossEstimateTimeline)
    safety_car = cast(BrowserPitLossEstimateTimeline, sidecar.safety_car)
    virtual_safety_car = cast(BrowserPitLossEstimateTimeline, sidecar.virtual_safety_car)
    race = cast(BrowserPitLossEstimateTimeline, sidecar.race)
    assert safety_car.estimated_loss_ms == (AUSTRALIA_SC_MS,)
    assert virtual_safety_car.estimated_loss_ms == (AUSTRALIA_VSC_MS,)
    assert race.estimated_loss_ms == (AUSTRALIA_GREEN_MS,)


def test_australia_fixture_builds_deterministically_without_io() -> None:
    # Arrange / Act: build the same fixture twice with identical inputs.
    first = build_australia_sidecar(0)
    second = build_australia_sidecar(0)

    # Assert: results are equal objects and identical wire payloads.
    assert first == second
    assert first.as_dict() == second.as_dict()
    first_race = cast(BrowserPitLossEstimateTimeline, first.race)
    second_race = cast(BrowserPitLossEstimateTimeline, second.race)
    assert first_race.estimated_loss_ms == second_race.estimated_loss_ms


# --- Publication: manifest reference, digest, and secure validation ----------


def test_australia_publication_emits_manifest_reference_with_digest_and_validates(
    tmp_path: Path,
) -> None:
    # Arrange: a fresh local publication target owned by the test.
    browser = tmp_path / "browser"

    # Act: publish the fixture Australia delivery and read the sidecar artifact.
    result = _publish_australia(browser)
    sidecar_path = result.generation_path / PIT_LOSS_ESTIMATE_SIDECAR_FILENAME
    manifest = json.loads(result.manifest_path.read_bytes())

    # Assert: the manifest references the sidecar by path, schema, and digest.
    assert result.pit_loss_estimate_sidecar_path == sidecar_path
    assert manifest["pitLossEstimateSidecar"] == {
        "path": PIT_LOSS_ESTIMATE_SIDECAR_FILENAME,
        "schemaId": PIT_LOSS_ESTIMATE_SIDECAR_SCHEMA_ID,
        "sha256": hashlib.sha256(sidecar_path.read_bytes()).hexdigest(),
    }
    assert (
        result.artifact_digests[PIT_LOSS_ESTIMATE_SIDECAR_FILENAME]
        == manifest["pitLossEstimateSidecar"]["sha256"]
    )
    assert json.loads(sidecar_path.read_bytes()) == build_australia_sidecar(0).as_dict()
    _validate_australia(browser)


def test_australia_publication_never_exposes_source_status(tmp_path: Path) -> None:
    # Arrange / Act: publish the fixture Australia delivery.
    browser = tmp_path / "browser"
    result = _publish_australia(browser)

    # Assert: catalog-only sourceStatus appears neither in the sidecar payload
    # nor in the manifest output.
    sidecar_text = result.generation_path.joinpath(
        PIT_LOSS_ESTIMATE_SIDECAR_FILENAME,
    ).read_text(encoding="utf-8")
    manifest_text = result.manifest_path.read_text(encoding="utf-8")
    assert "sourceStatus" not in sidecar_text
    assert "sourceStatus" not in manifest_text


def test_australia_publication_succeeds_when_sc_and_vsc_absent_from_input_race(
    tmp_path: Path,
) -> None:
    # Arrange: the delivery chunk never contains Safety Car or VSC status codes.
    delivery = build_australia_delivery()
    assert delivery.chunks[0].track_status_code == (1, 1)

    # Act: publish the All Clear delivery with the curated sidecar.
    result = publish_browser_delivery(
        browser_parent=tmp_path / "browser",
        delivery_version=AUSTRALIA_DELIVERY_VERSION,
        delivery=delivery,
        schema_root=SCHEMA_ROOT,
    )
    sidecar = json.loads(
        result.generation_path.joinpath(PIT_LOSS_ESTIMATE_SIDECAR_FILENAME).read_bytes()
    )

    # Assert: SC/VSC values are present and the complete delivery validates.
    assert sidecar["race"]["estimatedLossMs"] == [AUSTRALIA_GREEN_MS]
    assert sidecar["safetyCar"]["estimatedLossMs"] == [AUSTRALIA_SC_MS]
    assert sidecar["virtualSafetyCar"]["estimatedLossMs"] == [AUSTRALIA_VSC_MS]
    _validate_australia(tmp_path / "browser")


def test_australia_publication_is_byte_identical_across_targets(tmp_path: Path) -> None:
    # Arrange: one immutable fixture delivery published to two local targets.
    delivery = build_australia_delivery()

    # Act: publish the same delivery twice.
    first = publish_browser_delivery(
        browser_parent=tmp_path / "browser-one",
        delivery_version=AUSTRALIA_DELIVERY_VERSION,
        delivery=delivery,
        schema_root=SCHEMA_ROOT,
    )
    second = publish_browser_delivery(
        browser_parent=tmp_path / "browser-two",
        delivery_version=AUSTRALIA_DELIVERY_VERSION,
        delivery=delivery,
        schema_root=SCHEMA_ROOT,
    )

    # Assert: every artifact, including the sidecar, is byte identical.
    first_paths = (
        first.manifest_path,
        first.track_assets_path,
        first.generation_path / PIT_LOSS_ESTIMATE_SIDECAR_FILENAME,
        *first.chunk_paths,
    )
    second_paths = (
        second.manifest_path,
        second.track_assets_path,
        second.generation_path / PIT_LOSS_ESTIMATE_SIDECAR_FILENAME,
        *second.chunk_paths,
    )
    assert [path.read_bytes() for path in first_paths] == [
        path.read_bytes() for path in second_paths
    ]


# --- Local-only, no network, no templates, no R2 ------------------------------


def test_australia_publication_is_local_only_and_never_touches_r2(tmp_path: Path) -> None:
    # Arrange / Act: publish into a test-owned local directory.
    browser = tmp_path / "browser"
    result = _publish_australia(browser)

    # Assert: every published path lives under the local browser root only.
    published = [
        path
        for path in (
            result.manifest_path,
            result.track_assets_path,
            result.pointer_path,
            result.pit_loss_estimate_sidecar_path,
            *result.chunk_paths,
        )
        if path is not None
    ]
    assert all(path.is_relative_to(browser) for path in published)
    assert all(not path.is_relative_to(TEMPLATES_ROOT) for path in published)
    pointer = json.loads(result.pointer_path.read_bytes())
    assert pointer["manifestPath"].startswith("generations/")
    assert pointer["deliveryVersion"] == AUSTRALIA_DELIVERY_VERSION


def test_australia_publication_does_not_modify_templates(tmp_path: Path) -> None:
    # Arrange: snapshot the untouched templates tree before the fixture runs.
    before = _tree_snapshot(TEMPLATES_ROOT)

    # Act: publish the fixture delivery into a test-owned local directory.
    _publish_australia(tmp_path / "browser")

    # Assert: the templates tree is byte-for-byte unchanged.
    assert _tree_snapshot(TEMPLATES_ROOT) == before


# --- Fail-closed behavior -----------------------------------------------------


def test_curated_australia_sidecar_requires_all_three_status_values() -> None:
    # Arrange: a valid curated Australia sidecar.
    sidecar = build_australia_sidecar(0)

    # Act / Assert: dropping either status value is rejected by the model.
    with pytest.raises(ValueError, match="must resolve Green, SC, and VSC"):
        replace(sidecar, safety_car=None)  # type: ignore[arg-type]


def test_unknown_track_fails_closed_without_legacy_22_second_fallback() -> None:
    # Arrange: a track with no curated catalog entry.

    # Act / Assert: generation raises explicitly instead of a 22 s baseline.
    with pytest.raises(
        CuratedPitLossBaselineUnavailableError,
        match="no curated pit-loss baseline",
    ):
        build_curated_pit_loss_estimate_sidecar(
            0,
            fixture_id="2026-unknown-race",
            track_id="unknown-track",
        )
