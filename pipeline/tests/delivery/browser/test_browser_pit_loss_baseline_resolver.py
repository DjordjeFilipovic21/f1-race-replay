"""Tests for pure generation-time pit-loss baseline resolution.

Covers status code mapping (1/4/6/7), unambiguous status intervals,
replay-start resolution of the (1, 4, 6) triple without race observations,
explicit fail-closed behavior instead of a legacy 22,000 ms estimate,
unknown/ambiguous catalog bindings, per-status provenance/evidence/confidence
selection, multi-season fixture-less track reuse, distinct-venue resolution
(Bahrain/Sakhir versus Sepang and Barcelona-Catalunya versus Madring), catalog
validation at the resolver boundary, ``PitLossBaselineResolution`` invariants
and serialization, and proofs that resolution is deterministic, preserves the
catalog-only ``sourceStatus`` kind internally, and performs no network I/O and
no race-gap derived calculation.
"""

from __future__ import annotations

import http.client
import importlib
import inspect
import socket
import urllib.request
from collections.abc import Mapping
from dataclasses import replace
from typing import cast

import pytest

from f1_replay_pipeline.delivery.browser.browser_pit_loss_baseline_catalog import (
    AUSTRALIA_BASELINE,
    AUSTRALIA_FIXTURE_ID,
    AUSTRALIA_TRACK_ASSET_ID,
    AUSTRALIA_TRACK_ID,
    BAHRAIN_BASELINE,
    BARCELONA_BASELINE,
    CATALOG_VERSION,
    DEFAULT_SAFETY_CAR_DISCOUNT_MS,
    DEFAULT_VSC_DISCOUNT_MS,
    DERIVE_GREEN_DISCOUNT_METHOD,
    MADRING_BASELINE,
    MONZA_BASELINE,
    MONZA_STRATEGY_GUIDE_SOURCE_URL,
    PIT_LOSS_BASELINE_CATALOG,
    PitLossBaselineCatalog,
    PitLossBaselineEntry,
    PitLossBaselineProvenance,
    PitLossDerivationRecord,
    PitLossStatusBaseline,
    PitLossStatusKey,
    SEPANG_BASELINE,
)
from f1_replay_pipeline.delivery.browser.browser_pit_loss_baseline_resolver import (
    GREEN_STATUS,
    SAFETY_CAR_STATUS,
    UNAVAILABLE_STATUS,
    VIRTUAL_SAFETY_CAR_STATUS,
    PitLossBaselineResolution,
    resolve_baseline,
    resolve_pit_loss_baseline,
)

# The legacy observation model defaulted to this global estimate; the curated
# catalog path must never fall back to it.
LEGACY_GLOBAL_BASELINE_MS = 22_000

_UNION_ENTRIES = cast(
    tuple[PitLossBaselineEntry, ...],
    PIT_LOSS_BASELINE_CATALOG.entries,
)


def _provenance() -> PitLossBaselineProvenance:
    return PitLossBaselineProvenance(
        source_url="https://example.com/source",
        captured_date="2026-08-04",
        evidence="curated source evidence",
    )


def _entry(
    track_id: str = "alpha",
    *,
    fixture_id: str | None = None,
    track_asset_id: str | None = None,
    green_ms: int = 19_300,
    vsc_ms: int = 12_300,
    sc_ms: int = 9_300,
) -> PitLossBaselineEntry:
    provenance = _provenance()

    def _derived(value_ms: int, discount_ms: int) -> PitLossStatusBaseline:
        return PitLossStatusBaseline(
            value_ms=value_ms,
            source_status="derived",
            metric_definition="f1-com-lane-plus-stationary",
            provenance=provenance,
            evidence_count=1,
            confidence="medium",
            derivation=PitLossDerivationRecord(
                method=DERIVE_GREEN_DISCOUNT_METHOD,
                base_status="green",
                discount_ms=discount_ms,
                notes="derived from Green",
            ),
        )

    return PitLossBaselineEntry(
        track_id=track_id,
        track_name="Alpha Circuit",
        statuses={
            "green": PitLossStatusBaseline(
                value_ms=green_ms,
                source_status="direct",
                metric_definition="f1-com-lane-plus-stationary",
                provenance=provenance,
                evidence_count=1,
                confidence="high",
            ),
            "vsc": _derived(vsc_ms, green_ms - vsc_ms),
            "sc": _derived(sc_ms, green_ms - sc_ms),
        },
        fixture_id=fixture_id,
        track_asset_id=track_asset_id,
    )


def _available_resolution() -> PitLossBaselineResolution:
    green = AUSTRALIA_BASELINE.statuses["green"]
    return PitLossBaselineResolution(
        fixture_id=AUSTRALIA_FIXTURE_ID,
        track_id=AUSTRALIA_TRACK_ID,
        status_code=1,
        status=GREEN_STATUS,
        estimated_loss_ms=green.value_ms,
        catalog_version=CATALOG_VERSION,
        provenance=cast(PitLossBaselineProvenance, green.provenance),
        evidence_count=green.evidence_count,
        confidence=green.confidence,
        available=True,
        entry=AUSTRALIA_BASELINE,
    )


def _error_contains(resolution: PitLossBaselineResolution, fragment: str) -> bool:
    """Return whether the fail-closed error mentions ``fragment``."""
    return resolution.error is not None and fragment in resolution.error


def _unavailable_resolution() -> PitLossBaselineResolution:
    return PitLossBaselineResolution(
        fixture_id=None,
        track_id="unknown",
        status_code=None,
        status=UNAVAILABLE_STATUS,
        estimated_loss_ms=None,
        catalog_version=CATALOG_VERSION,
        provenance=None,
        evidence_count=None,
        confidence=None,
        available=False,
        error="no curated pit-loss baseline is available for this fixture and track",
    )


# --- Status code mapping ----------------------------------------------------


@pytest.mark.parametrize(
    ("status_code", "status", "expected_ms"),
    [
        (1, GREEN_STATUS, AUSTRALIA_BASELINE.green_ms),
        (4, SAFETY_CAR_STATUS, AUSTRALIA_BASELINE.sc_ms),
        (6, VIRTUAL_SAFETY_CAR_STATUS, AUSTRALIA_BASELINE.vsc_ms),
        (7, VIRTUAL_SAFETY_CAR_STATUS, AUSTRALIA_BASELINE.vsc_ms),
    ],
)
def test_resolve_maps_status_code_to_catalog_baseline(
    status_code: int, status: str, expected_ms: int,
) -> None:
    resolution = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, status_code)

    assert resolution.available
    assert resolution.status == status
    assert resolution.estimated_loss_ms == expected_ms
    assert resolution.status_code == status_code


def test_resolve_maps_all_status_codes_without_observations() -> None:
    # Arrange: every status is resolved purely from the curated catalog.
    for code in (1, 4, 6, 7):
        resolution = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, code)
        assert resolution.available
        assert resolution.status_code == code


@pytest.mark.parametrize(
    ("codes", "status", "expected_ms"),
    [
        ((1, 1, 1), GREEN_STATUS, AUSTRALIA_BASELINE.green_ms),
        ((4, 4), SAFETY_CAR_STATUS, AUSTRALIA_BASELINE.sc_ms),
        ((6, 6, 6), VIRTUAL_SAFETY_CAR_STATUS, AUSTRALIA_BASELINE.vsc_ms),
        ((7, 7), VIRTUAL_SAFETY_CAR_STATUS, AUSTRALIA_BASELINE.vsc_ms),
        ((6, 7), VIRTUAL_SAFETY_CAR_STATUS, AUSTRALIA_BASELINE.vsc_ms),
        ((7, 6, 6), VIRTUAL_SAFETY_CAR_STATUS, AUSTRALIA_BASELINE.vsc_ms),
    ],
)
def test_resolve_accepts_unambiguous_status_intervals(
    codes: tuple[int, ...], status: str, expected_ms: int,
) -> None:
    resolution = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, codes)

    assert resolution.available
    assert resolution.status == status
    assert resolution.estimated_loss_ms == expected_ms


def test_resolve_accepts_list_and_generator_status_inputs() -> None:
    from_list = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, [4, 4])
    from_generator = resolve_pit_loss_baseline(
        AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, (code for code in (6, 6)),
    )

    assert from_list.status == SAFETY_CAR_STATUS
    assert from_list.estimated_loss_ms == AUSTRALIA_BASELINE.sc_ms
    assert from_generator.status == VIRTUAL_SAFETY_CAR_STATUS
    assert from_generator.estimated_loss_ms == AUSTRALIA_BASELINE.vsc_ms


def test_resolve_baseline_alias_matches_long_form() -> None:
    fixture_id, track_id = AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID

    assert resolve_baseline(fixture_id, track_id, 1) == resolve_pit_loss_baseline(fixture_id, track_id, 1)
    assert resolve_baseline(fixture_id, track_id, 4) == resolve_pit_loss_baseline(fixture_id, track_id, 4)


# --- Replay-start resolution ------------------------------------------------


def test_replay_start_resolution_resolves_all_statuses_without_race_observations() -> None:
    # Arrange: the curated replay-start path resolves statuses (1, 4, 6) with
    # no observation or gap input; this test invokes that exact call pattern.
    statuses = (1, 4, 6)

    # Act
    resolutions = tuple(
        resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, status)
        for status in statuses
    )

    # Assert: every status is available at replay start from the catalog alone.
    assert tuple(resolution.available for resolution in resolutions) == (True, True, True)
    assert tuple(resolution.status for resolution in resolutions) == (GREEN_STATUS, SAFETY_CAR_STATUS, VIRTUAL_SAFETY_CAR_STATUS)
    assert tuple(resolution.estimated_loss_ms for resolution in resolutions) == (
        AUSTRALIA_BASELINE.green_ms,
        AUSTRALIA_BASELINE.sc_ms,
        AUSTRALIA_BASELINE.vsc_ms,
    )


def test_replay_start_resolution_values_are_monotonic_with_default_discounts() -> None:
    green = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, 1)
    sc = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, 4)
    vsc = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, 6)

    assert sc.estimated_loss_ms is not None
    assert vsc.estimated_loss_ms is not None
    assert green.estimated_loss_ms is not None
    assert sc.estimated_loss_ms <= vsc.estimated_loss_ms <= green.estimated_loss_ms
    assert green.estimated_loss_ms - vsc.estimated_loss_ms == DEFAULT_VSC_DISCOUNT_MS
    assert green.estimated_loss_ms - sc.estimated_loss_ms == DEFAULT_SAFETY_CAR_DISCOUNT_MS


def test_all_union_circuits_are_available_at_replay_start_without_observations() -> None:
    # Arrange / Act: resolve every shipped circuit for every supported status.
    for entry in _UNION_ENTRIES:
        fixture = entry.fixture_id
        for status_code in (1, 4, 6, 7):
            resolution = resolve_pit_loss_baseline(fixture, entry.track_id, status_code)

            # Assert: catalog-backed values exist at replay start for the whole
            # 26-circuit union even when SC/VSC never occurred in the race.
            assert resolution.available
            assert resolution.estimated_loss_ms is not None


# --- Explicit no-22000-ms behavior ------------------------------------------


@pytest.mark.parametrize(
    ("status_code", "expected_ms"),
    [
        (1, AUSTRALIA_BASELINE.green_ms),
        (4, AUSTRALIA_BASELINE.sc_ms),
        (6, AUSTRALIA_BASELINE.vsc_ms),
        (7, AUSTRALIA_BASELINE.vsc_ms),
    ],
)
def test_resolved_estimate_never_falls_back_to_legacy_22000(status_code: int, expected_ms: int) -> None:
    resolution = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, status_code)

    assert resolution.estimated_loss_ms == expected_ms
    assert resolution.estimated_loss_ms != LEGACY_GLOBAL_BASELINE_MS


def test_unavailable_resolution_never_invents_legacy_22000_estimate() -> None:
    resolution = resolve_pit_loss_baseline(None, "no-such-track", 1)

    assert not resolution.available
    assert resolution.estimated_loss_ms is None
    assert resolution.estimated_loss_ms != LEGACY_GLOBAL_BASELINE_MS


def test_no_shipped_catalog_status_silently_equals_legacy_22000() -> None:
    # Arrange / Act: resolve every curated status across the 26-circuit union.
    for entry in _UNION_ENTRIES:
        fixture = entry.fixture_id
        for status_code, key in ((1, "green"), (4, "sc"), (6, "vsc")):
            resolution = resolve_pit_loss_baseline(fixture, entry.track_id, status_code)

            # Assert: the value is exactly the curated per-status baseline and
            # is never a hidden fallback to the legacy 22-second estimate.
            assert resolution.available
            assert resolution.estimated_loss_ms == entry.statuses[cast(PitLossStatusKey, key)].value_ms
            assert resolution.estimated_loss_ms != LEGACY_GLOBAL_BASELINE_MS


# --- Unknown and ambiguous catalog bindings ---------------------------------


def test_unknown_track_resolution_fails_closed() -> None:
    resolution = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, "pau", 1)

    assert not resolution.available
    assert resolution.status == UNAVAILABLE_STATUS
    assert resolution.estimated_loss_ms is None
    assert resolution.error == "no curated pit-loss baseline is available for this fixture and track"
    assert resolution.status_code == 1
    assert resolution.catalog_version == CATALOG_VERSION


def test_fixture_mismatch_resolution_fails_closed() -> None:
    resolution = resolve_pit_loss_baseline("other-fixture", AUSTRALIA_TRACK_ID, 1)

    assert not resolution.available
    assert resolution.error == "no curated pit-loss baseline is available for this fixture and track"


def test_none_fixture_does_not_match_fixture_bound_entry() -> None:
    resolution = resolve_pit_loss_baseline(None, AUSTRALIA_TRACK_ID, 1)

    assert not resolution.available
    assert resolution.estimated_loss_ms is None


def test_track_asset_id_binding_resolves_to_entry() -> None:
    resolution = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ASSET_ID, 4)

    assert resolution.available
    assert resolution.estimated_loss_ms == AUSTRALIA_BASELINE.sc_ms


def test_ambiguous_catalog_binding_fails_closed() -> None:
    # Arrange: a catalog whose fixture-bound entry overlaps a fixtureless entry.
    catalog = PitLossBaselineCatalog(
        "v2",
        (
            _entry(track_id="alpha", fixture_id=None),
            _entry(track_id="alpha", fixture_id="fixture-x"),
        ),
    )

    # Act
    resolution = resolve_pit_loss_baseline("fixture-x", "alpha", 1, catalog=catalog)

    # Assert: ambiguity is a fail-closed unavailable result.
    assert not resolution.available
    assert resolution.status == UNAVAILABLE_STATUS
    assert resolution.estimated_loss_ms is None
    assert _error_contains(resolution, "ambiguous")


def test_known_track_with_bogus_fixture_fails_closed_against_default_catalog() -> None:
    # Arrange: Monza is a fixture-less union entry, so a bogus fixture would
    # previously reuse the known track's baseline through the production
    # catalog even though it never belonged to that circuit.
    resolution = resolve_pit_loss_baseline("bogus-fixture", "monza", 1)

    # Assert: the default catalog validates fixture + track together through
    # the physical identity registry before matching; the bogus fixture fails
    # closed and never surfaces the Monza baseline or a 22-second estimate.
    assert not resolution.available
    assert resolution.status == UNAVAILABLE_STATUS
    assert resolution.estimated_loss_ms is None
    assert resolution.estimated_loss_ms != LEGACY_GLOBAL_BASELINE_MS
    assert _error_contains(resolution, "no curated pit-loss baseline")


def test_malformed_fixture_fails_closed_against_default_catalog() -> None:
    # Arrange / Act: a malformed fixture identifier cannot normalize through
    # the physical identity registry even when the track is known.
    resolution = resolve_pit_loss_baseline("!!!", "monza", 1)

    # Assert: malformed fixtures fail closed instead of reusing the fixture-less
    # Monza entry, and never fall back to the legacy 22-second estimate.
    assert not resolution.available
    assert resolution.status == UNAVAILABLE_STATUS
    assert resolution.estimated_loss_ms is None
    assert resolution.estimated_loss_ms != LEGACY_GLOBAL_BASELINE_MS


def test_known_track_with_other_circuit_fixture_fails_closed() -> None:
    # Arrange: the Italian GP fixture resolves to Monza, a different physical
    # circuit than Australia, so the binding is ambiguous.
    resolution = resolve_pit_loss_baseline(
        "2024-round-16-italian-grand-prix", AUSTRALIA_TRACK_ID, 1,
    )

    # Assert: fixture and track are validated together; a mismatched pair never
    # resolves to the Australia baseline.
    assert not resolution.available
    assert resolution.estimated_loss_ms is None
    assert resolution.estimated_loss_ms != LEGACY_GLOBAL_BASELINE_MS


def test_default_catalog_mapping_form_fails_closed_for_bogus_fixture() -> None:
    # Arrange / Act: the shipped catalog passed as its exact wire mapping must
    # keep the same identity-bound fail-closed behavior as the object form.
    resolution = resolve_pit_loss_baseline(
        "bogus-fixture", "monza", 1, catalog=PIT_LOSS_BASELINE_CATALOG.as_dict(),
    )

    # Assert: the mapping boundary is recognized as the production default and
    # rejects the bogus fixture instead of matching the fixture-less Monza entry.
    assert not resolution.available
    assert resolution.estimated_loss_ms is None
    assert resolution.estimated_loss_ms != LEGACY_GLOBAL_BASELINE_MS


def test_custom_synthetic_catalog_accepts_arbitrary_fixture_ids() -> None:
    # Arrange: a custom synthetic catalog is not the production default, so its
    # fixture-less entries intentionally match arbitrary fixture identifiers.
    catalog = PitLossBaselineCatalog("v2", (_entry(track_id="alpha"),))

    # Act
    resolution = resolve_pit_loss_baseline("arbitrary-fixture", "alpha", 1, catalog=catalog)

    # Assert: synthetic catalogs and legacy APIs that use arbitrary IDs remain
    # exempt from the production identity-binding check.
    assert resolution.available
    assert resolution.estimated_loss_ms == 19_300
    assert resolution.fixture_id == "arbitrary-fixture"


# --- Generator fixture binding (2026-01-race) -------------------------------


def test_generator_fixture_resolves_all_australia_statuses_with_track_name() -> None:
    # Arrange: the actual generator binding for the 2026 Australia delivery.
    fixture_id, track_id, track_name = (
        "2026-01-race", "2026-01-race-telemetry-layout-v1", "Australian Grand Prix",
    )

    # Act: resolve every status at replay start from the catalog alone.
    green = resolve_pit_loss_baseline(fixture_id, track_id, 1, track_name=track_name)
    safety_car = resolve_pit_loss_baseline(fixture_id, track_id, 4, track_name=track_name)
    vsc = resolve_pit_loss_baseline(fixture_id, track_id, 6, track_name=track_name)

    # Assert: all three Australia statuses resolve with the exact values and
    # the resolution preserves the delivery's own binding.
    assert all(resolution.available for resolution in (green, safety_car, vsc))
    assert green.estimated_loss_ms == 19_300
    assert vsc.estimated_loss_ms == 12_300
    assert safety_car.estimated_loss_ms == 9_300
    assert green.fixture_id == fixture_id
    assert green.track_id == track_id
    assert green.estimated_loss_ms != LEGACY_GLOBAL_BASELINE_MS


def test_resolve_baseline_alias_forwards_track_name() -> None:
    # Arrange / Act: both entry points with the generator binding.
    fixture_id = "2026-01-race"
    track_id = "2026-01-race-telemetry-layout-v1"
    track_name = "Australian Grand Prix"

    # Assert: the alias forwards the track name and matches the long form.
    assert resolve_baseline(fixture_id, track_id, 1, track_name=track_name) == (
        resolve_pit_loss_baseline(fixture_id, track_id, 1, track_name=track_name)
    )
    assert resolve_pit_loss_baseline(fixture_id, track_id, 1, track_name=track_name).available


def test_event_form_alias_with_agreeing_track_name_resolves() -> None:
    # Arrange / Act: the existing event-form Australia alias plus an agreeing
    # display name must keep resolving through the production catalog.
    resolution = resolve_pit_loss_baseline(
        AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, 1,
        track_name="Australian Grand Prix",
    )

    # Assert: the alias path is unchanged by the added name binding.
    assert resolution.available
    assert resolution.estimated_loss_ms == AUSTRALIA_BASELINE.green_ms


def test_generator_fixture_without_track_name_fails_closed() -> None:
    # Arrange / Act: the generator binding without the establishing name.
    resolution = resolve_pit_loss_baseline(
        "2026-01-race", "2026-01-race-telemetry-layout-v1", 1,
    )

    # Assert: the missing name fails closed and never fabricates a value.
    assert not resolution.available
    assert resolution.status == UNAVAILABLE_STATUS
    assert resolution.estimated_loss_ms is None
    assert resolution.estimated_loss_ms != LEGACY_GLOBAL_BASELINE_MS


def test_generator_fixture_with_unknown_track_name_fails_closed() -> None:
    # Arrange / Act: a bogus track name cannot establish the generator fixture.
    resolution = resolve_pit_loss_baseline(
        "2026-01-race", "2026-01-race-telemetry-layout-v1", 1,
        track_name="Bogus Circuit",
    )

    # Assert: the unknown name fails closed.
    assert not resolution.available
    assert resolution.estimated_loss_ms is None
    assert resolution.estimated_loss_ms != LEGACY_GLOBAL_BASELINE_MS


def test_conflicting_track_name_fails_closed() -> None:
    # Arrange / Act: the event-form Australia binding (which resolves through
    # the fixture and track) paired with a track name that resolves to a
    # different physical circuit.
    resolution = resolve_pit_loss_baseline(
        AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, 1,
        track_name="Bahrain Grand Prix",
    )

    # Assert: the conflicting name fails closed instead of guessing.
    assert not resolution.available
    assert resolution.estimated_loss_ms is None
    assert resolution.estimated_loss_ms != LEGACY_GLOBAL_BASELINE_MS


def test_generator_fixture_with_track_name_establishing_other_circuit_resolves() -> None:
    # Arrange / Act: the generator fixture is a generic per-season id, so a
    # resolvable track name legitimately establishes the circuit it names (the
    # same shape a future Bahrain generator binding would use).
    resolution = resolve_pit_loss_baseline(
        "2026-01-race", "2026-01-race-telemetry-layout-v1", 1,
        track_name="Bahrain Grand Prix",
    )

    # Assert: the name is the only resolvable evidence and establishes Bahrain.
    assert resolution.available
    assert resolution.estimated_loss_ms == BAHRAIN_BASELINE.green_ms
    assert resolution.fixture_id == "2026-01-race"
    assert resolution.track_id == "2026-01-race-telemetry-layout-v1"


def test_synthetic_catalog_keeps_raw_matching_with_track_name() -> None:
    # Arrange: a custom synthetic catalog is exempt from identity binding even
    # when a track name is supplied because its entries cannot resolve.
    catalog = PitLossBaselineCatalog("v2", (_entry(track_id="alpha"),))

    # Act
    resolution = resolve_pit_loss_baseline(
        "arbitrary-fixture", "alpha", 1, catalog=catalog,
        track_name="Australian Grand Prix",
    )

    # Assert: the synthetic entry keeps its raw binding match.
    assert resolution.available
    assert resolution.estimated_loss_ms == 19_300
    assert resolution.fixture_id == "arbitrary-fixture"


# --- Status input normalization and classification ---------------------------


def test_none_status_code_fails_closed() -> None:
    resolution = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, None)

    assert not resolution.available
    assert resolution.error == "track status is unavailable"
    assert resolution.status_code is None


@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [
        (1.5, "track status must be an integer or an iterable of status codes"),
        (object(), "track status must be an integer or an iterable of status codes"),
        ("4", "track status interval contains an unavailable or invalid code"),
    ],
)
def test_non_scalar_non_iterable_status_input_fails_closed(
    status_code: object, expected_error: str,
) -> None:
    resolution = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, status_code)  # type: ignore[arg-type]

    assert not resolution.available
    assert _error_contains(resolution, expected_error)


def test_empty_status_interval_fails_closed() -> None:
    resolution = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, ())

    assert not resolution.available
    assert _error_contains(resolution, "empty or unavailable")


@pytest.mark.parametrize(
    "codes",
    [
        (None,),
        (1, None),
        (1, "4"),
        (1.0,),
        (True,),
    ],
)
def test_status_interval_with_invalid_or_unavailable_code_fails_closed(
    codes: tuple[object, ...],
) -> None:
    resolution = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, codes)  # type: ignore[arg-type]

    assert not resolution.available
    assert _error_contains(resolution, "unavailable or invalid code")


@pytest.mark.parametrize(
    "codes",
    [
        (1, 4),
        (4, 6),
        (1, 6),
        (1, 4, 6),
        (4, 1),
    ],
)
def test_mixed_status_interval_fails_closed(codes: tuple[int, ...]) -> None:
    resolution = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, codes)

    assert not resolution.available
    assert resolution.error == "track status interval is ambiguous or mixed"
    assert resolution.status_code == codes
    assert resolution.estimated_loss_ms is None


@pytest.mark.parametrize("code", [0, 2, 3, 5, 8, -1])
def test_unsupported_status_code_fails_closed(code: int) -> None:
    resolution = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, code)

    assert not resolution.available
    assert _error_contains(resolution, "unsupported")
    assert resolution.estimated_loss_ms is None


@pytest.mark.parametrize("codes", [(2,), (3,), (5,), (8,), (2, 2)])
def test_unsupported_status_interval_fails_closed(codes: tuple[int, ...]) -> None:
    resolution = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, codes)

    assert not resolution.available
    assert _error_contains(resolution, "unsupported")
    assert resolution.estimated_loss_ms is None


# --- Binding and catalog validation at the boundary -------------------------


def test_empty_fixture_id_fails_closed_as_value_error() -> None:
    with pytest.raises(ValueError, match="fixture_id must be a non-empty string or None"):
        resolve_pit_loss_baseline("", AUSTRALIA_TRACK_ID, 1)


def test_non_string_fixture_id_raises() -> None:
    with pytest.raises(ValueError, match="fixture_id must be a non-empty string or None"):
        resolve_pit_loss_baseline(42, AUSTRALIA_TRACK_ID, 1)  # type: ignore[arg-type]


def test_empty_track_id_raises() -> None:
    with pytest.raises(ValueError, match="track_id must be a non-empty string"):
        resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, "", 1)


def test_resolver_validates_catalog_and_propagates_catalog_errors() -> None:
    with pytest.raises(ValueError, match="catalog_version must be v2"):
        resolve_pit_loss_baseline(
            AUSTRALIA_FIXTURE_ID,
            AUSTRALIA_TRACK_ID,
            1,
            catalog={"catalogVersion": "v1", "entries": []},
        )


def test_resolver_accepts_catalog_mapping_and_resolves_identically() -> None:
    # Arrange: the shipped catalog as its exact wire mapping.
    catalog_mapping = PIT_LOSS_BASELINE_CATALOG.as_dict()

    # Act: resolve through the object and through the mapping boundary.
    direct = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, 1)
    mapped = resolve_pit_loss_baseline(
        AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, 1, catalog=catalog_mapping,
    )

    # Assert: both paths produce the identical immutable result.
    assert mapped == direct
    assert mapped.available
    assert mapped.estimated_loss_ms == AUSTRALIA_BASELINE.green_ms


# --- Resolution invariants --------------------------------------------------


def test_available_resolution_preserves_catalog_metadata() -> None:
    resolution = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, 6)
    vsc = AUSTRALIA_BASELINE.statuses["vsc"]

    # The resolution carries the selected VSC status's own metadata, never the
    # entry-level Green defaults.
    assert resolution.catalog_version == CATALOG_VERSION
    assert resolution.provenance == vsc.provenance
    assert resolution.evidence_count == vsc.evidence_count
    assert resolution.confidence == vsc.confidence
    assert resolution.entry == AUSTRALIA_BASELINE
    assert resolution.calibration_count == vsc.evidence_count


@pytest.mark.parametrize(
    ("status_code", "key"),
    [(1, "green"), (4, "sc"), (6, "vsc")],
)
def test_available_resolution_preserves_selected_status_metadata_for_each_status(
    status_code: int, key: PitLossStatusKey,
) -> None:
    resolution = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, status_code)
    baseline = AUSTRALIA_BASELINE.statuses[key]

    assert resolution.estimated_loss_ms == baseline.value_ms
    assert resolution.provenance == baseline.provenance
    assert resolution.evidence_count == baseline.evidence_count
    assert resolution.confidence == baseline.confidence


def test_resolution_preserves_direct_and_derived_status_provenance() -> None:
    # Monza mixes source kinds per status: Green and SC are direct, VSC is
    # derived; each resolution must carry that status's own provenance.
    green = resolve_pit_loss_baseline(None, "monza", 1)
    safety_car = resolve_pit_loss_baseline(None, "monza", 4)
    vsc = resolve_pit_loss_baseline(None, "monza", 6)

    assert green.provenance == MONZA_BASELINE.statuses["green"].provenance
    assert green.confidence == MONZA_BASELINE.statuses["green"].confidence == "high"
    assert safety_car.provenance == MONZA_BASELINE.statuses["sc"].provenance
    assert cast(
        PitLossBaselineProvenance, safety_car.provenance,
    ).source_url == MONZA_STRATEGY_GUIDE_SOURCE_URL
    assert safety_car.evidence_count == MONZA_BASELINE.statuses["sc"].evidence_count
    assert safety_car.confidence == MONZA_BASELINE.statuses["sc"].confidence == "medium"
    assert vsc.provenance == MONZA_BASELINE.statuses["vsc"].provenance
    assert vsc.evidence_count == MONZA_BASELINE.statuses["vsc"].evidence_count
    assert vsc.confidence == MONZA_BASELINE.statuses["vsc"].confidence == "medium"


def test_available_resolution_aliases_and_status_classification() -> None:
    resolution = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, 6)

    assert resolution.loss_ms == resolution.estimated_loss_ms
    assert resolution.value_ms == resolution.estimated_loss_ms
    assert resolution.resolved_ms == resolution.estimated_loss_ms
    assert resolution.status_class == "virtual_safety_car"
    assert resolution.status_label == "VSC"

    sc = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, 4)
    assert sc.status_class == "safety_car"
    assert sc.status_label == "SC"

    green = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, 1)
    assert green.status_class == "normal"
    assert green.status_label == "Green"


def test_available_resolution_as_dict_uses_wire_names() -> None:
    resolution = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, 1)
    green = AUSTRALIA_BASELINE.statuses["green"]

    assert resolution.as_dict() == {
        "fixtureId": AUSTRALIA_FIXTURE_ID,
        "trackId": AUSTRALIA_TRACK_ID,
        "statusCode": 1,
        "status": "green",
        "available": True,
        "estimatedLossMs": AUSTRALIA_BASELINE.green_ms,
        "catalogVersion": "v2",
        "provenance": cast(PitLossBaselineProvenance, green.provenance).as_dict(),
        "evidenceCount": 1,
        "confidence": "high",
    }


def test_unavailable_resolution_serializes_status_code_as_list() -> None:
    resolution = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, (1, 4))

    payload = resolution.as_dict()
    assert payload["statusCode"] == [1, 4]
    assert payload["status"] == "unavailable"
    assert payload["estimatedLossMs"] is None
    assert "error" in payload
    assert "provenance" not in payload
    assert "evidenceCount" not in payload
    assert "confidence" not in payload


def test_unavailable_resolution_aliases_and_serialization() -> None:
    resolution = _unavailable_resolution()

    assert resolution.status == UNAVAILABLE_STATUS
    assert resolution.status_class is None
    assert resolution.status_label == "Unavailable"
    assert resolution.loss_ms is None
    assert resolution.value_ms is None
    assert resolution.resolved_ms is None
    assert resolution.calibration_count is None
    assert resolution.as_dict()["error"] == (
        "no curated pit-loss baseline is available for this fixture and track"
    )


# --- PitLossBaselineResolution construction guards --------------------------


@pytest.mark.parametrize(
    "changes",
    [
        {"estimated_loss_ms": None},
        {"available": False},
        {"provenance": None},
        {"evidence_count": None},
        {"confidence": None},
        {"error": "boom"},
        {"entry": None},
        {"status": "yellow"},
        {"catalog_version": ""},
    ],
)
def test_available_resolution_rejects_invalid_construction(changes: Mapping[str, object]) -> None:
    with pytest.raises(ValueError):
        replace(_available_resolution(), **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"estimated_loss_ms": 5_000},
        {"available": True},
        {"error": None},
        {"error": "   "},
        {"provenance": _provenance()},
        {"evidence_count": 1},
        {"confidence": "high"},
        {"entry": AUSTRALIA_BASELINE},
        {"catalog_version": ""},
    ],
)
def test_unavailable_resolution_rejects_invalid_construction(changes: Mapping[str, object]) -> None:
    with pytest.raises(ValueError):
        replace(_unavailable_resolution(), **changes)


# --- Determinism and dependency isolation ------------------------------------


def test_resolution_is_deterministic_across_repeated_calls() -> None:
    fixture_id, track_id = AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID
    statuses = (1, 4, 6, 7)

    # Act: resolve every status twice, interleaved.
    first = tuple(resolve_pit_loss_baseline(fixture_id, track_id, code).as_dict() for code in statuses)
    second = tuple(resolve_pit_loss_baseline(fixture_id, track_id, code).as_dict() for code in statuses)

    # Assert: byte-for-byte identical wire payloads.
    assert second == first


def test_resolution_is_independent_of_status_iterable_order() -> None:
    left = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, (6, 7))
    right = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, (7, 6))

    # Assert: identical classification and estimate; the preserved status code
    # tuple is the only difference and is order-insensitive as a set.
    assert left.status == right.status == VIRTUAL_SAFETY_CAR_STATUS
    assert left.estimated_loss_ms == right.estimated_loss_ms
    assert isinstance(left.status_code, tuple)
    assert isinstance(right.status_code, tuple)
    assert set(left.status_code) == set(right.status_code) == {6, 7}


def test_resolution_values_are_exact_catalog_values_not_gap_derived() -> None:
    # Arrange / Act: resolve each status; the expected value is the catalog
    # entry field itself, never a function of any race gap input.
    for status_code, attribute in ((1, "green_ms"), (4, "sc_ms"), (6, "vsc_ms")):
        resolution = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, status_code)

        # Assert: the resolved estimate is exactly the curated catalog value.
        assert resolution.estimated_loss_ms == getattr(AUSTRALIA_BASELINE, attribute)


def test_resolver_public_api_accepts_no_race_gap_inputs() -> None:
    # Act: inspect the public signature.
    parameters = tuple(inspect.signature(resolve_pit_loss_baseline).parameters)

    # Assert: only binding, status, and catalog inputs exist; the optional
    # track-name display binding is not a race-gap input and there is no
    # parameter through which race-derived gap data could influence a result.
    assert parameters == ("fixture_id", "track_id", "status_code", "catalog", "track_name")


def test_resolver_module_imports_no_network_capable_libraries() -> None:
    # Arrange: inspect the resolver module's own namespace.
    module = importlib.import_module(
        "f1_replay_pipeline.delivery.browser.browser_pit_loss_baseline_resolver",
    )
    imported = {name.split(".")[0] for name in vars(module)}

    # Assert: no network-capable stdlib or third-party library is imported.
    assert imported.isdisjoint(
        {"socket", "urllib", "http", "ssl", "ftplib", "xmlrpc", "requests", "aiohttp", "fastf1"},
    )


def test_resolution_does_not_perform_network_io(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: deny every plausible network entry point.
    def deny(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network I/O attempted during baseline resolution")

    monkeypatch.setattr(socket, "socket", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(urllib.request, "urlopen", deny)
    monkeypatch.setattr(http.client, "HTTPConnection", deny)

    # Act: resolve both an available and a fail-closed path.
    available = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, 4)
    unavailable = resolve_pit_loss_baseline(None, "no-such-track", 1)

    # Assert: resolution completes with no network I/O.
    assert available.available
    assert available.estimated_loss_ms == AUSTRALIA_BASELINE.sc_ms
    assert not unavailable.available
    assert unavailable.estimated_loss_ms is None


# --- Multi-season fixture-less reuse and distinct venues ---------------------


def test_multi_season_fixtureless_track_reuses_one_catalog_baseline() -> None:
    # Arrange: Monza is a fixture-less union entry, so every calendar season of
    # the same physical circuit must reuse the single stable baseline.
    fixtures = (
        "2024-round-16-italian-grand-prix",
        "2025-round-16-italian-grand-prix",
        "2026-round-16-italian-grand-prix",
    )

    # Act: resolve the circuit for each season's fixture.
    resolved = [resolve_pit_loss_baseline(fixture, "monza", 1) for fixture in fixtures]

    # Assert: every season resolves the identical stable per-circuit baseline
    # while each resolution keeps its own delivery fixtureId.
    assert all(resolution.available for resolution in resolved)
    assert all(
        resolution.estimated_loss_ms == MONZA_BASELINE.green_ms
        for resolution in resolved
    )
    assert [resolution.fixture_id for resolution in resolved] == list(fixtures)
    assert len({resolution.status for resolution in resolved}) == 1
    assert len({resolution.provenance for resolution in resolved}) == 1
    assert len({resolution.catalog_version for resolution in resolved}) == 1
    assert len({resolution.confidence for resolution in resolved}) == 1
    assert len({resolution.evidence_count for resolution in resolved}) == 1


def test_bahrain_and_sepang_resolve_to_distinct_baselines() -> None:
    # Act: resolve each distinct Malaysian-adjacent venue.
    bahrain = resolve_pit_loss_baseline(None, "bahrain", 1)
    sepang = resolve_pit_loss_baseline(None, "sepang", 1)

    # Assert: separate physical circuits keep separate curated values and
    # neither silently falls back to the legacy 22-second estimate.
    assert bahrain.available
    assert sepang.available
    assert bahrain.estimated_loss_ms == BAHRAIN_BASELINE.green_ms
    assert sepang.estimated_loss_ms == SEPANG_BASELINE.green_ms
    assert bahrain.estimated_loss_ms != sepang.estimated_loss_ms
    assert bahrain.estimated_loss_ms != LEGACY_GLOBAL_BASELINE_MS
    assert sepang.estimated_loss_ms != LEGACY_GLOBAL_BASELINE_MS


def test_barcelona_and_madring_resolve_to_distinct_baselines() -> None:
    # Act: resolve Barcelona (2024/2025) and Madring (2026) through their own
    # season-qualified Spanish GP fixtures.
    barcelona = resolve_pit_loss_baseline(
        "2024-round-16-spanish-grand-prix", "barcelona-catalunya", 1,
    )
    madrid = resolve_pit_loss_baseline(
        "2026-round-16-spanish-grand-prix", "madring", 1,
    )

    # Assert: distinct physical venues resolve to distinct curated values even
    # though both fixtures share the ambiguous Spanish GP event name.
    assert barcelona.available
    assert madrid.available
    assert barcelona.estimated_loss_ms == BARCELONA_BASELINE.green_ms
    assert madrid.estimated_loss_ms == MADRING_BASELINE.green_ms
    assert barcelona.estimated_loss_ms != madrid.estimated_loss_ms


# --- sourceStatus stays catalog-only ----------------------------------------


def test_resolution_drops_catalog_only_source_status_for_derived_status() -> None:
    # Arrange: the shipped Australia VSC status is derived and its catalog wire
    # form carries the catalog-only sourceStatus kind.
    vsc = AUSTRALIA_BASELINE.statuses["vsc"]
    assert vsc.source_status == "derived"
    assert "sourceStatus" in vsc.as_dict()

    # Act: resolve the derived VSC status through the sidecar-facing resolver.
    resolution = resolve_pit_loss_baseline(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID, 6)

    # Assert: the catalog-only sourceStatus kind never leaves the catalog.
    assert resolution.available
    assert "sourceStatus" not in resolution.as_dict()


@pytest.mark.parametrize(
    ("track_id", "fixture_id"),
    [
        (AUSTRALIA_TRACK_ID, AUSTRALIA_FIXTURE_ID),
        ("monza", None),
        ("bahrain", None),
        ("madring", None),
        ("sepang", None),
    ],
)
def test_source_status_is_never_serialized_by_any_resolution(
    track_id: str, fixture_id: str | None,
) -> None:
    # Act: resolve every supported status for each representative track.
    for status_code in (1, 4, 6):
        resolution = resolve_pit_loss_baseline(fixture_id, track_id, status_code)

        # Assert: no resolver payload serializes the catalog-only sourceStatus.
        assert resolution.available
        assert "sourceStatus" not in resolution.as_dict()
