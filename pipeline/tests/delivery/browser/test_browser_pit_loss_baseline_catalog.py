"""Tests for the immutable repository-local pit-loss baseline catalog.

Covers the multi-season 26-circuit union (2024/2025/2026), stable identity
reuse for one physical circuit across seasons, Bahrain/Sakhir versus Sepang and
Barcelona-Catalunya versus Madring venue separation, the Australia
Green/VSC/SC fixture values (19 300 / 12 300 / 9 300 ms), per-status
direct/derived/proxy metadata with provenance/evidence/confidence, the
monotonic ``SC <= VSC <= Green`` invariant, unbounded discounts outside the
legacy 5-10 second window, malformed metadata and values, duplicate and unknown
track identities, catalog validation at the mapping boundary, ``entry_for``
binding resolution, and the exact deterministic wire payload of the shipped
catalog.

``sourceStatus`` is asserted to remain catalog-only metadata: it exists on the
catalog status objects (and the catalog wire form) but the resolver and sidecar
tests prove it never reaches the browser.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from datetime import date
from typing import cast

import pytest

from f1_replay_pipeline.delivery.browser.browser_pit_loss_baseline_catalog import (
    AUSTRALIA_BASELINE,
    AUSTRALIA_FIXTURE_ID,
    AUSTRALIA_SOURCE_URL,
    AUSTRALIA_TRACK_ASSET_ID,
    AUSTRALIA_TRACK_ID,
    BAHRAIN_BASELINE,
    BAKU_BASELINE,
    BARCELONA_BASELINE,
    BASELINE_CATALOG,
    CATALOG_SCHEMA_ID,
    CATALOG_VERSION,
    COMPARABLE_CIRCUIT_PROXY_METHOD,
    CURATED_BASELINE_METHOD,
    DEFAULT_PIT_LOSS_BASELINE_CATALOG,
    DEFAULT_SAFETY_CAR_DISCOUNT_MS,
    DEFAULT_SC_DISCOUNT_MS,
    DEFAULT_VSC_DISCOUNT_MS,
    DERIVE_GREEN_DISCOUNT_METHOD,
    IMOLA_BASELINE,
    MADRING_BASELINE,
    MARINA_BAY_BASELINE,
    MAX_DISCOUNT_MS,
    MAX_INT64,
    MIN_DISCOUNT_MS,
    MONZA_BASELINE,
    PIT_LOSS_BASELINE_CATALOG,
    PIT_LOSS_TRACK_IDENTITIES,
    SEPANG_BASELINE,
    Confidence,
    PitLossBaselineCatalog,
    PitLossBaselineEntry,
    PitLossBaselineProvenance,
    PitLossDerivationRecord,
    PitLossDiscountConfiguration,
    PitLossMetric,
    PitLossStatusBaseline,
    PitLossStatusKey,
    StatusSourceKind,
    TrackIdentityLookupError,
    catalog_entry_identity_binding,
    resolve_binding_identity,
    resolve_catalog_entry_identity,
    resolve_track_identity,
    validate_baseline_catalog,
    validate_catalog,
    validate_catalog_identities,
)
from f1_replay_pipeline.delivery.browser.browser_pit_loss_track_identity import (
    UNION_CIRCUIT_COUNT,
)

_KNOWN_METRIC_FAMILIES = frozenset({
    "f1-com-lane-plus-stationary",
    "measured-total-cost",
    "fia-stop-duration",
})
_UNION_ENTRIES = cast(
    tuple[PitLossBaselineEntry, ...],
    PIT_LOSS_BASELINE_CATALOG.entries,
)


def _provenance(
    source_url: str = "https://example.com/source",
    captured_date: str = "2026-08-04",
    evidence: str = "curated source evidence",
    method: str = CURATED_BASELINE_METHOD,
) -> PitLossBaselineProvenance:
    return PitLossBaselineProvenance(
        source_url=source_url,
        captured_date=captured_date,
        evidence=evidence,
        method=method,
    )


def _status(
    value_ms: int,
    *,
    source_status: StatusSourceKind = "direct",
    metric_definition: PitLossMetric = "f1-com-lane-plus-stationary",
    provenance: PitLossBaselineProvenance | Mapping[str, object] | None = None,
    evidence_count: int = 1,
    confidence: Confidence = "high",
    derivation: PitLossDerivationRecord | Mapping[str, object] | None = None,
) -> PitLossStatusBaseline:
    return PitLossStatusBaseline(
        value_ms=value_ms,
        source_status=source_status,
        metric_definition=metric_definition,
        provenance=provenance if provenance is not None else _provenance(),
        evidence_count=evidence_count,
        confidence=confidence,
        derivation=derivation,
    )


def _derived_status(
    value_ms: int,
    green_ms: int,
    key: str,
    *,
    confidence: Confidence = "medium",
    compute_discounts: bool = True,
) -> PitLossStatusBaseline:
    """Return a derived status baseline with an explicit Green discount record."""
    return _status(
        value_ms,
        source_status="derived",
        provenance=_provenance(evidence=f"derived {key} evidence"),
        confidence=confidence,
        derivation=PitLossDerivationRecord(
            method=DERIVE_GREEN_DISCOUNT_METHOD,
            base_status="green",
            discount_ms=green_ms - value_ms if compute_discounts else None,
            notes=f"{key} derived from the Green baseline",
        ),
    )


def _statuses(
    green_ms: int = 19_300,
    vsc_ms: int = 12_300,
    sc_ms: int = 9_300,
    *,
    compute_discounts: bool = True,
) -> dict[PitLossStatusKey, PitLossStatusBaseline]:
    return {
        "green": _status(green_ms),
        "vsc": _derived_status(vsc_ms, green_ms, "vsc", compute_discounts=compute_discounts),
        "sc": _derived_status(sc_ms, green_ms, "sc", compute_discounts=compute_discounts),
    }


def _entry(
    track_id: str = "alpha",
    track_name: str = "Alpha Circuit",
    *,
    fixture_id: str | None = None,
    track_asset_id: str | None = None,
    green_ms: int = 19_300,
    vsc_ms: int = 12_300,
    sc_ms: int = 9_300,
    discounts: PitLossDiscountConfiguration | Mapping[str, object] | None = None,
    provenance: PitLossBaselineProvenance | Mapping[str, object] | None = None,
    green_evidence_count: int = 1,
    green_confidence: Confidence = "high",
    compute_discounts: bool = True,
    season: int | None = None,
) -> PitLossBaselineEntry:
    statuses = _statuses(green_ms, vsc_ms, sc_ms, compute_discounts=compute_discounts)
    # Always rebuild Green from the explicit parameters so malformed
    # evidence/confidence values (for example ``True``) are validated.
    statuses["green"] = _status(
        green_ms,
        provenance=provenance if provenance is not None else _provenance(),
        evidence_count=green_evidence_count,
        confidence=green_confidence,
    )
    return PitLossBaselineEntry(
        track_id=track_id,
        track_name=track_name,
        statuses=statuses,
        discounts=discounts,
        fixture_id=fixture_id,
        track_asset_id=track_asset_id,
        season=season,
    )


# --- Australia fixture values ----------------------------------------------


def test_australia_baseline_has_curated_green_vsc_and_sc_values() -> None:
    # Arrange / Act: the shipped Australia baseline entry.
    entry = AUSTRALIA_BASELINE

    # Assert: curated per-status values and the season-opener binding.
    assert entry.green_ms == 19_300
    assert entry.vsc_ms == 12_300
    assert entry.sc_ms == 9_300
    assert entry.track_id == AUSTRALIA_TRACK_ID
    assert entry.track_name == "Albert Park Circuit"
    assert entry.fixture_id == AUSTRALIA_FIXTURE_ID
    assert entry.track_asset_id == AUSTRALIA_TRACK_ASSET_ID
    assert entry.season == 2026


def test_australia_default_discounts_are_derived_from_green() -> None:
    # Arrange / Act: the shipped Australia entry and the default discounts.
    entry = AUSTRALIA_BASELINE

    # Assert: VSC and SC discounts exactly span the baseline.
    assert entry.green_ms - entry.vsc_ms == DEFAULT_VSC_DISCOUNT_MS
    assert entry.green_ms - entry.sc_ms == DEFAULT_SAFETY_CAR_DISCOUNT_MS
    assert entry.vsc_discount_ms == DEFAULT_VSC_DISCOUNT_MS
    assert entry.safety_car_discount_ms == DEFAULT_SAFETY_CAR_DISCOUNT_MS


def test_australia_provenance_confidence_and_evidence_count() -> None:
    # Arrange / Act: the shipped Australia entry (entry-level aliases point at
    # the Green status baseline).
    entry = AUSTRALIA_BASELINE

    # Assert: provenance metadata is present and complete.
    assert isinstance(entry.provenance, PitLossBaselineProvenance)
    provenance = entry.provenance
    assert provenance.source_url == AUSTRALIA_SOURCE_URL
    assert provenance.captured_date == "2026-08-04"
    assert provenance.evidence == (
        "Formula1.com lists Australia pit stop time loss as 19.30 seconds."
    )
    assert provenance.method == CURATED_BASELINE_METHOD
    assert provenance.as_dict() == {
        "sourceUrl": AUSTRALIA_SOURCE_URL,
        "capturedDate": "2026-08-04",
        "evidence": "Formula1.com lists Australia pit stop time loss as 19.30 seconds.",
        "method": CURATED_BASELINE_METHOD,
    }
    assert entry.evidence_count == 1
    assert entry.confidence == "high"
    assert entry.calibration_count == 1


def test_australia_statuses_carry_per_status_source_metadata() -> None:
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
    assert isinstance(green.provenance, PitLossBaselineProvenance)
    assert vsc.source_status == "derived"
    assert vsc.confidence == "medium"
    assert isinstance(vsc.derivation, PitLossDerivationRecord)
    assert vsc.derivation.discount_ms == DEFAULT_VSC_DISCOUNT_MS
    assert sc.source_status == "derived"
    assert sc.confidence == "medium"
    assert isinstance(sc.derivation, PitLossDerivationRecord)
    assert sc.derivation.discount_ms == DEFAULT_SAFETY_CAR_DISCOUNT_MS

    # Assert: each status names its own provenance, distinct from Green's.
    assert isinstance(vsc.provenance, PitLossBaselineProvenance)
    assert isinstance(sc.provenance, PitLossBaselineProvenance)
    assert vsc.provenance.evidence != green.provenance.evidence
    assert sc.provenance.evidence != green.provenance.evidence


def test_australia_monotonic_ordering_is_sc_less_than_vsc_less_than_green() -> None:
    assert AUSTRALIA_BASELINE.sc_ms <= AUSTRALIA_BASELINE.vsc_ms <= AUSTRALIA_BASELINE.green_ms


def test_default_catalog_contains_the_full_26_circuit_union() -> None:
    catalog = PIT_LOSS_BASELINE_CATALOG

    assert catalog.catalog_version == CATALOG_VERSION
    assert len(catalog.entries) == UNION_CIRCUIT_COUNT == 26
    assert AUSTRALIA_BASELINE in catalog.entries
    assert BASELINE_CATALOG is PIT_LOSS_BASELINE_CATALOG
    assert DEFAULT_PIT_LOSS_BASELINE_CATALOG is PIT_LOSS_BASELINE_CATALOG


def test_catalog_constants_are_stable() -> None:
    assert CATALOG_VERSION == "v1"
    assert CATALOG_SCHEMA_ID == (
        "urn:f1-cache-replay:schema:replay-data:v1:pit-loss-baseline-catalog"
    )
    assert CURATED_BASELINE_METHOD == "curated-track-baseline-v1"
    assert DEFAULT_VSC_DISCOUNT_MS == 7_000
    assert DEFAULT_SAFETY_CAR_DISCOUNT_MS == 10_000
    assert DEFAULT_SC_DISCOUNT_MS == DEFAULT_SAFETY_CAR_DISCOUNT_MS
    assert MIN_DISCOUNT_MS == 5_000
    assert MAX_DISCOUNT_MS == 10_000
    assert MAX_INT64 == (1 << 63) - 1


def test_default_discount_configuration_matches_catalog_constants() -> None:
    discounts = PitLossDiscountConfiguration()

    assert discounts.vsc_ms == DEFAULT_VSC_DISCOUNT_MS
    assert discounts.safety_car_ms == DEFAULT_SAFETY_CAR_DISCOUNT_MS
    assert discounts.sc_ms == DEFAULT_SAFETY_CAR_DISCOUNT_MS
    assert discounts.as_dict() == {
        "vscMs": DEFAULT_VSC_DISCOUNT_MS,
        "safetyCarMs": DEFAULT_SAFETY_CAR_DISCOUNT_MS,
    }


def test_australia_entry_as_dict_emits_exact_wire_payload() -> None:
    # Arrange: the expected deterministic wire form of the shipped entry.
    expected = {
        "trackId": "australia",
        "trackName": "Albert Park Circuit",
        "statuses": {
            "green": {
                "valueMs": 19_300,
                "sourceStatus": "direct",
                "metricDefinition": "f1-com-lane-plus-stationary",
                "provenance": {
                    "sourceUrl": AUSTRALIA_SOURCE_URL,
                    "capturedDate": "2026-08-04",
                    "evidence": "Formula1.com lists Australia pit stop time loss as 19.30 seconds.",
                    "method": CURATED_BASELINE_METHOD,
                },
                "evidenceCount": 1,
                "confidence": "high",
            },
            "vsc": {
                "valueMs": 12_300,
                "sourceStatus": "derived",
                "metricDefinition": "f1-com-lane-plus-stationary",
                "provenance": {
                    "sourceUrl": AUSTRALIA_SOURCE_URL,
                    "capturedDate": "2026-08-04",
                    "evidence": (
                        "Virtual Safety Car baseline derived from the Formula1.com "
                        "Australia Green value with a 7 000 ms discount."
                    ),
                    "method": CURATED_BASELINE_METHOD,
                },
                "evidenceCount": 1,
                "confidence": "medium",
                "derivation": {
                    "method": DERIVE_GREEN_DISCOUNT_METHOD,
                    "baseStatus": "green",
                    "discountMs": 7_000,
                    "notes": (
                        "Legacy default VSC discount consistent with the "
                        "Formula1.com Green value."
                    ),
                },
            },
            "sc": {
                "valueMs": 9_300,
                "sourceStatus": "derived",
                "metricDefinition": "f1-com-lane-plus-stationary",
                "provenance": {
                    "sourceUrl": AUSTRALIA_SOURCE_URL,
                    "capturedDate": "2026-08-04",
                    "evidence": (
                        "Safety Car baseline derived from the Formula1.com "
                        "Australia Green value with a 10 000 ms discount."
                    ),
                    "method": CURATED_BASELINE_METHOD,
                },
                "evidenceCount": 1,
                "confidence": "medium",
                "derivation": {
                    "method": DERIVE_GREEN_DISCOUNT_METHOD,
                    "baseStatus": "green",
                    "discountMs": 10_000,
                    "notes": (
                        "Legacy default SC discount consistent with the "
                        "Formula1.com Green value."
                    ),
                },
            },
        },
        "discounts": {"vscMs": 7_000, "safetyCarMs": 10_000},
        "fixtureId": AUSTRALIA_FIXTURE_ID,
        "trackAssetId": AUSTRALIA_TRACK_ASSET_ID,
        "season": 2026,
    }

    # Act / Assert: the shipped entry serializes to the exact payload and the
    # repeated call is identical.
    assert AUSTRALIA_BASELINE.as_dict() == expected
    assert AUSTRALIA_BASELINE.as_dict() == expected


def test_catalog_as_dict_round_trips_and_is_deterministic() -> None:
    # Arrange / Act: serialize the shipped 26-circuit catalog.
    catalog = PIT_LOSS_BASELINE_CATALOG
    payload = catalog.as_dict()

    # Assert: repeated calls are identical and the exact wire mapping validates
    # back into an equal immutable catalog.
    assert payload == catalog.as_dict()
    assert payload["catalogVersion"] == "v1"
    assert len(cast(list[object], payload["entries"])) == 26
    assert validate_catalog(payload) == catalog


def test_catalog_and_entries_are_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        PIT_LOSS_BASELINE_CATALOG.entries = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        AUSTRALIA_BASELINE.track_id = "other"  # type: ignore[misc]


# --- Discount bounds (unbounded, not the legacy 5-10 s window) --------------


@pytest.mark.parametrize(
    ("vsc_ms", "safety_car_ms"),
    [
        (0, 0),
        (4_999, 10_000),
        (10_001, 10_001),
        (12_000, 12_000),
        (5_000, 5_000),
        (8_000, 10_000),
    ],
)
def test_discount_configuration_accepts_unbounded_non_negative_values(
    vsc_ms: int, safety_car_ms: int,
) -> None:
    discounts = PitLossDiscountConfiguration(vsc_ms, safety_car_ms)

    assert discounts.vsc_ms == vsc_ms
    assert discounts.safety_car_ms == safety_car_ms


@pytest.mark.parametrize(
    ("vsc_ms", "safety_car_ms"),
    [
        (-1, 10_000),
        (7_000, -1),
        (7_000.0, 10_000),
        (True, 10_000),
    ],
)
def test_discount_configuration_rejects_negative_or_non_integer_values(
    vsc_ms: object, safety_car_ms: object,
) -> None:
    with pytest.raises(TypeError, match="non-negative signed Int64"):
        PitLossDiscountConfiguration(vsc_ms, safety_car_ms)  # type: ignore[arg-type]


def test_discount_configuration_requires_safety_car_at_least_vsc() -> None:
    with pytest.raises(ValueError, match="safety_car_ms must be greater than or equal to vsc_ms"):
        PitLossDiscountConfiguration(9_000, 8_000)


def test_discount_bounds_constants_are_documented_not_enforced() -> None:
    # The legacy 5000-10000 ms window remains a documented constant but the
    # current model accepts any non-negative discount with provenance support.
    discounts = PitLossDiscountConfiguration(4_000, 12_000)

    assert MIN_DISCOUNT_MS == 5_000
    assert MAX_DISCOUNT_MS == 10_000
    assert discounts.as_dict() == {"vscMs": 4_000, "safetyCarMs": 12_000}


def test_shipped_catalog_has_discounts_outside_the_legacy_window() -> None:
    # Baku's direct SC value (8.5 s) implies an 11 200 ms Green discount, and
    # Sepang/Marina Bay/Imola derived discounts all exceed the legacy 10 s.
    assert BAKU_BASELINE.safety_car_discount_ms == 11_200
    assert BAKU_BASELINE.safety_car_discount_ms > MAX_DISCOUNT_MS
    assert SEPANG_BASELINE.safety_car_discount_ms == 11_000
    assert SEPANG_BASELINE.safety_car_discount_ms > MAX_DISCOUNT_MS
    assert MARINA_BAY_BASELINE.safety_car_discount_ms == 11_000
    assert IMOLA_BASELINE.vsc_discount_ms == 10_100
    assert IMOLA_BASELINE.safety_car_discount_ms == 12_100


# --- Derivation records -----------------------------------------------------


def test_derivation_record_accepts_discount_outside_legacy_window() -> None:
    record = PitLossDerivationRecord(
        method=DERIVE_GREEN_DISCOUNT_METHOD,
        base_status="green",
        discount_ms=11_200,
        notes="Direct status value implies an 11 200 ms discount.",
    )

    assert record.discount_ms == 11_200
    assert record.as_dict() == {
        "method": DERIVE_GREEN_DISCOUNT_METHOD,
        "baseStatus": "green",
        "discountMs": 11_200,
        "notes": "Direct status value implies an 11 200 ms discount.",
    }


@pytest.mark.parametrize("discount_ms", [-1, 1.5, True])
def test_derivation_record_rejects_malformed_discount(discount_ms: object) -> None:
    with pytest.raises(TypeError, match="discount_ms must be a non-negative signed Int64"):
        PitLossDerivationRecord(discount_ms=discount_ms)  # type: ignore[arg-type]


def test_derivation_record_rejects_invalid_base_status() -> None:
    with pytest.raises(ValueError, match="base_status must be green, vsc, or sc"):
        PitLossDerivationRecord(base_status="yellow")  # type: ignore[arg-type]


@pytest.mark.parametrize("method", ["", "   "])
def test_derivation_record_rejects_empty_method(method: str) -> None:
    with pytest.raises(ValueError, match="method must be a non-empty string"):
        PitLossDerivationRecord(method=method)


# --- Status baseline metadata ------------------------------------------------


@pytest.mark.parametrize("value_ms", [-1, 1.5, True, None])
def test_status_baseline_rejects_invalid_value(value_ms: object) -> None:
    with pytest.raises(TypeError, match="non-negative signed Int64"):
        _status(value_ms)  # type: ignore[arg-type]


@pytest.mark.parametrize("source_status", ["official", "measured", "game", ""])
def test_status_baseline_rejects_unknown_source_status(source_status: str) -> None:
    with pytest.raises(ValueError, match="source_status must be direct, derived, or proxy"):
        _status(19_300, source_status=source_status)  # type: ignore[arg-type]


@pytest.mark.parametrize("metric", ["pit-stop-time", "", 1])
def test_status_baseline_rejects_unknown_metric_definition(metric: object) -> None:
    with pytest.raises(ValueError, match="metric_definition is not a known metric family"):
        _status(19_300, metric_definition=metric)  # type: ignore[arg-type]


@pytest.mark.parametrize("evidence_count", [-1, 1.5, True])
def test_status_baseline_rejects_malformed_evidence_count(evidence_count: object) -> None:
    with pytest.raises(TypeError, match="evidence_count must be a non-negative integer"):
        _status(19_300, evidence_count=evidence_count)  # type: ignore[arg-type]


@pytest.mark.parametrize("confidence", ["HIGH", "", "medium ", "high ", 1])
def test_status_baseline_rejects_malformed_confidence(confidence: object) -> None:
    with pytest.raises(ValueError, match="confidence must be high, medium, or low"):
        _status(19_300, confidence=confidence)  # type: ignore[arg-type]


def test_derived_and_proxy_statuses_require_a_derivation_record() -> None:
    with pytest.raises(
        ValueError,
        match="derived and proxy statuses must carry a derivation record",
    ):
        _status(12_300, source_status="derived")
    with pytest.raises(
        ValueError,
        match="derived and proxy statuses must carry a derivation record",
    ):
        _status(19_000, source_status="proxy")


def test_direct_status_cannot_carry_a_derivation_record() -> None:
    with pytest.raises(ValueError, match="direct statuses cannot carry a derivation record"):
        _status(
            19_300,
            derivation=PitLossDerivationRecord(method=DERIVE_GREEN_DISCOUNT_METHOD),
        )


def test_status_baseline_calibration_count_aliases_evidence_count() -> None:
    baseline = _status(19_300, evidence_count=3)

    assert baseline.calibration_count == 3


# --- Malformed provenance metadata ------------------------------------------


def test_provenance_accepts_https_source_and_iso_date() -> None:
    provenance = _provenance()

    assert date.fromisoformat(provenance.captured_date) == date(2026, 8, 4)
    assert provenance.method == CURATED_BASELINE_METHOD


@pytest.mark.parametrize(
    "source_url",
    ["http://example.com/source", "ftp://example.com/source", "not-a-url", ""],
)
def test_provenance_rejects_non_https_source_url(source_url: str) -> None:
    with pytest.raises(ValueError, match="source_url must be an HTTPS URL"):
        _provenance(source_url=source_url)


@pytest.mark.parametrize(
    "captured_date",
    ["2026-13-45", "2026/08/04", "tomorrow", ""],
)
def test_provenance_rejects_malformed_iso_date(captured_date: str) -> None:
    with pytest.raises(ValueError, match="captured_date must be an ISO date"):
        _provenance(captured_date=captured_date)


def test_provenance_rejects_non_string_captured_date() -> None:
    with pytest.raises(TypeError, match="captured_date must be an ISO date"):
        _provenance(captured_date=20260804)  # type: ignore[arg-type]


@pytest.mark.parametrize("evidence", ["", "   ", 7])
def test_provenance_rejects_empty_or_non_string_evidence(evidence: object) -> None:
    with pytest.raises(ValueError, match="evidence must be a non-empty string"):
        _provenance(evidence=evidence)  # type: ignore[arg-type]


@pytest.mark.parametrize("method", ["", "   ", 1])
def test_provenance_rejects_empty_or_non_string_method(method: object) -> None:
    with pytest.raises(ValueError, match="method must be a non-empty string"):
        _provenance(method=method)  # type: ignore[arg-type]


# --- Malformed entry values -------------------------------------------------


@pytest.mark.parametrize(
    "track_id",
    ["Australia", "australia_2026", "-australia", "australia--2026", "", "au stralia"],
)
def test_entry_rejects_invalid_track_identifiers(track_id: str) -> None:
    with pytest.raises(ValueError, match="lowercase kebab-case identifier"):
        _entry(track_id=track_id)


@pytest.mark.parametrize("track_name", ["", "   "])
def test_entry_rejects_empty_track_name(track_name: str) -> None:
    with pytest.raises(ValueError, match="track_name must be a non-empty string"):
        _entry(track_name=track_name)


@pytest.mark.parametrize(
    ("green_ms", "vsc_ms", "sc_ms"),
    [
        (-1, 12_300, 9_300),
        (19_300, -1, 9_300),
        (19_300, 12_300, -1),
        (19_300.5, 12_300, 9_300),
        (None, 12_300, 9_300),
    ],
)
def test_entry_rejects_negative_or_non_integer_baselines(
    green_ms: object, vsc_ms: object, sc_ms: object,
) -> None:
    with pytest.raises(TypeError, match="non-negative signed Int64"):
        _entry(green_ms=green_ms, vsc_ms=vsc_ms, sc_ms=sc_ms)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("green_ms", "vsc_ms", "sc_ms"),
    [
        (19_300, 19_400, 9_300),  # vsc above green
        (19_300, 12_300, 12_400),  # sc above vsc
        (19_300, 9_300, 12_300),  # sc above vsc
    ],
)
def test_entry_rejects_non_monotonic_ordering(
    green_ms: int, vsc_ms: int, sc_ms: int,
) -> None:
    with pytest.raises(ValueError, match="sc_ms <= vsc_ms <= green_ms"):
        _entry(green_ms=green_ms, vsc_ms=vsc_ms, sc_ms=sc_ms, compute_discounts=False)


@pytest.mark.parametrize("evidence_count", [-1, 1.5, True])
def test_entry_rejects_malformed_evidence_count(evidence_count: object) -> None:
    with pytest.raises(TypeError, match="evidence_count must be a non-negative integer"):
        _entry(green_evidence_count=evidence_count)  # type: ignore[arg-type]


@pytest.mark.parametrize("confidence", ["HIGH", "", "medium ", "high ", 1])
def test_entry_rejects_malformed_confidence(confidence: object) -> None:
    with pytest.raises(ValueError, match="confidence must be high, medium, or low"):
        _entry(green_confidence=confidence)  # type: ignore[arg-type]


@pytest.mark.parametrize("fixture_id", ["Round-01", "2026_round", "", "2026-round-01 "])
def test_entry_rejects_invalid_fixture_identifiers(fixture_id: str) -> None:
    with pytest.raises(ValueError, match="lowercase kebab-case identifier"):
        _entry(fixture_id=fixture_id)


@pytest.mark.parametrize("track_asset_id", ["Telemetry-Layout", "", "telemetry_layout"])
def test_entry_rejects_invalid_track_asset_identifiers(track_asset_id: str) -> None:
    with pytest.raises(ValueError, match="lowercase kebab-case identifier"):
        _entry(track_asset_id=track_asset_id)


@pytest.mark.parametrize("season", [0, -1, 2026.0, "2026"])
def test_entry_rejects_invalid_season(season: object) -> None:
    with pytest.raises(ValueError, match="season must be a positive integer or None"):
        _entry(season=season)  # type: ignore[arg-type]


def test_entry_accepts_none_season_and_optional_identifiers() -> None:
    entry = _entry(fixture_id=None, track_asset_id=None, season=None)

    assert entry.fixture_id is None
    assert entry.track_asset_id is None
    assert entry.season is None


# --- Statuses and discount coercion -----------------------------------------


def test_entry_rejects_missing_or_extra_status_keys() -> None:
    statuses = _statuses()
    with pytest.raises(ValueError, match="statuses must define green, vsc, and sc baselines"):
        PitLossBaselineEntry(
            "alpha",
            "Alpha Circuit",
            statuses={key: value for key, value in statuses.items() if key != "sc"},
        )
    with pytest.raises(ValueError, match="statuses must define green, vsc, and sc baselines"):
        PitLossBaselineEntry(
            "alpha",
            "Alpha Circuit",
            statuses={**statuses, "yellow": _status(9_000)},  # type: ignore[arg-type]
        )


def test_entry_rejects_non_mapping_statuses() -> None:
    with pytest.raises(TypeError, match="statuses must be a mapping of status baselines"):
        PitLossBaselineEntry("alpha", "Alpha Circuit", statuses=42)  # type: ignore[arg-type]


def test_entry_accepts_statuses_as_wire_mappings() -> None:
    # Arrange: the entry built from mapping statuses exactly as the wire form.
    entry = PitLossBaselineEntry(
        "alpha",
        "Alpha Circuit",
        statuses={
            "green": _status(19_300).as_dict(),
            "vsc": _status(
                12_300,
                source_status="derived",
                confidence="medium",
                derivation=PitLossDerivationRecord(
                    method=DERIVE_GREEN_DISCOUNT_METHOD,
                    base_status="green",
                    discount_ms=7_000,
                ),
            ).as_dict(),
            "sc": _status(
                9_300,
                source_status="derived",
                confidence="medium",
                derivation=PitLossDerivationRecord(
                    method=DERIVE_GREEN_DISCOUNT_METHOD,
                    base_status="green",
                    discount_ms=10_000,
                ),
            ).as_dict(),
        },  # type: ignore[arg-type]
    )

    # Assert: every status coerces to an immutable status baseline object.
    assert all(
        isinstance(entry.statuses[key], PitLossStatusBaseline) for key in ("green", "vsc", "sc")
    )
    assert entry.green_ms == 19_300
    assert entry.vsc_ms == 12_300
    assert entry.sc_ms == 9_300


def test_entry_accepts_matching_discount_mapping() -> None:
    entry = _entry(
        green_ms=18_000,
        vsc_ms=12_000,
        sc_ms=10_000,
        discounts={"vscMs": 6_000, "safetyCarMs": 8_000},
    )

    assert isinstance(entry.discounts, PitLossDiscountConfiguration)
    assert entry.discounts.vsc_ms == 6_000
    assert entry.discounts.safety_car_ms == 8_000
    assert entry.vsc_discount_ms == 6_000
    assert entry.safety_car_discount_ms == 8_000


@pytest.mark.parametrize(
    "discounts",
    [
        {"vscMs": 6_000},
        {"vscMs": 6_000, "safetyCarMs": 8_000, "extra": 1},
        42,
    ],
)
def test_entry_rejects_malformed_discount_mappings(discounts: object) -> None:
    with pytest.raises(ValueError, match="discounts must contain vscMs and safetyCarMs"):
        _entry(discounts=discounts)  # type: ignore[arg-type]


def test_entry_accepts_none_discounts_as_optional_metadata() -> None:
    entry = _entry(discounts=None)

    assert entry.discounts is None
    # Without a configuration, the derived statuses' own discounts are exposed.
    assert entry.vsc_discount_ms == 19_300 - 12_300
    assert entry.safety_car_discount_ms == 19_300 - 9_300


def test_entry_accepts_discounts_independent_of_status_gaps() -> None:
    entry = _entry(
        green_ms=19_300,
        vsc_ms=12_300,
        sc_ms=9_300,
        discounts={"vscMs": 6_000, "safetyCarMs": 8_000},
    )

    # The configured discount metadata is independent of the raw status gaps;
    # the legacy equality check no longer exists.
    assert entry.vsc_discount_ms == 6_000
    assert entry.safety_car_discount_ms == 8_000
    assert entry.green_ms - entry.vsc_ms == 7_000
    assert entry.green_ms - entry.sc_ms == 10_000


def test_entry_accepts_provenance_mapping_with_default_method() -> None:
    entry = _entry(
        provenance={"sourceUrl": "https://example.com/source", "capturedDate": "2026-08-04", "evidence": "x"},
    )

    green = entry.statuses["green"]
    assert isinstance(green.provenance, PitLossBaselineProvenance)
    assert green.provenance.method == CURATED_BASELINE_METHOD


def test_entry_rejects_non_mapping_non_provenance_input() -> None:
    with pytest.raises(TypeError, match="provenance must be PitLossBaselineProvenance or a mapping"):
        _entry(provenance=42)  # type: ignore[arg-type]


# --- Entry matching ---------------------------------------------------------


def test_matches_accepts_fixture_and_track_binding() -> None:
    assert AUSTRALIA_BASELINE.matches(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID)


def test_matches_accepts_track_asset_id_binding() -> None:
    assert AUSTRALIA_BASELINE.matches(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ASSET_ID)


@pytest.mark.parametrize(
    ("fixture_id", "track_id"),
    [
        (None, AUSTRALIA_TRACK_ID),
        ("other-fixture", AUSTRALIA_TRACK_ID),
        (AUSTRALIA_FIXTURE_ID, "monza"),
    ],
)
def test_matches_rejects_wrong_fixture_or_track(fixture_id: str | None, track_id: str) -> None:
    assert not AUSTRALIA_BASELINE.matches(fixture_id, track_id)


def test_fixtureless_entry_matches_any_fixture() -> None:
    entry = _entry()

    assert entry.matches("any-fixture", "alpha")
    assert entry.matches(None, "alpha")


# --- Catalog validation -----------------------------------------------------


def test_catalog_accepts_unique_entry_identities() -> None:
    catalog = PitLossBaselineCatalog(
        "v1",
        (_entry(track_id="alpha", fixture_id=None), _entry(track_id="bravo", fixture_id="fixture-y")),
    )

    assert len(catalog.entries) == 2


def test_catalog_rejects_duplicate_track_and_fixture_identities() -> None:
    with pytest.raises(ValueError, match="unique track and fixture identities"):
        PitLossBaselineCatalog("v1", (AUSTRALIA_BASELINE, AUSTRALIA_BASELINE))


def test_catalog_rejects_duplicate_fixtureless_track_identities() -> None:
    with pytest.raises(ValueError, match="unique track and fixture identities"):
        PitLossBaselineCatalog("v1", (_entry(track_id="alpha"), _entry(track_id="alpha")))


def test_catalog_rejects_empty_entries() -> None:
    with pytest.raises(ValueError, match="at least one entry"):
        PitLossBaselineCatalog("v1", ())


def test_catalog_rejects_wrong_version() -> None:
    with pytest.raises(ValueError, match="catalog_version must be v1"):
        PitLossBaselineCatalog("v2", (AUSTRALIA_BASELINE,))


def test_validate_catalog_returns_catalog_instance_unchanged() -> None:
    assert validate_catalog(PIT_LOSS_BASELINE_CATALOG) is PIT_LOSS_BASELINE_CATALOG
    assert validate_baseline_catalog(PIT_LOSS_BASELINE_CATALOG) is PIT_LOSS_BASELINE_CATALOG
    assert validate_baseline_catalog is validate_catalog


def test_validate_catalog_accepts_exact_wire_mapping() -> None:
    catalog = validate_catalog(PIT_LOSS_BASELINE_CATALOG.as_dict())

    assert catalog.catalog_version == CATALOG_VERSION
    assert len(catalog.entries) == 26
    assert catalog.entry_for(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID) == AUSTRALIA_BASELINE


@pytest.mark.parametrize(
    "catalog",
    [
        {"catalogVersion": "v1"},
        {"catalogVersion": "v1", "entries": [], "extra": 1},
        {},
    ],
)
def test_validate_catalog_rejects_mappings_with_wrong_keys(catalog: object) -> None:
    with pytest.raises(ValueError, match="catalog must contain catalogVersion and entries"):
        validate_catalog(catalog)  # type: ignore[arg-type]


@pytest.mark.parametrize("catalog", [42, "v1", ("v1", ())])
def test_validate_catalog_rejects_non_catalog_values(catalog: object) -> None:
    with pytest.raises(TypeError, match="catalog must be a PitLossBaselineCatalog"):
        validate_catalog(catalog)  # type: ignore[arg-type]


# --- entry_for binding resolution -------------------------------------------


def test_entry_for_resolves_fixture_and_track_binding() -> None:
    assert PIT_LOSS_BASELINE_CATALOG.entry_for(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ID) == AUSTRALIA_BASELINE


def test_entry_for_resolves_track_asset_id_binding() -> None:
    assert PIT_LOSS_BASELINE_CATALOG.entry_for(AUSTRALIA_FIXTURE_ID, AUSTRALIA_TRACK_ASSET_ID) == AUSTRALIA_BASELINE


@pytest.mark.parametrize(
    ("fixture_id", "track_id"),
    [
        (None, AUSTRALIA_TRACK_ID),
        ("other-fixture", AUSTRALIA_TRACK_ID),
        (AUSTRALIA_FIXTURE_ID, "pau"),
    ],
)
def test_entry_for_returns_none_for_unbound_fixture_or_track(
    fixture_id: str | None, track_id: str,
) -> None:
    assert PIT_LOSS_BASELINE_CATALOG.entry_for(fixture_id, track_id) is None


def test_entry_for_resolves_fixtureless_entry_by_track_only() -> None:
    catalog = PitLossBaselineCatalog("v1", (_entry(track_id="alpha"),))

    assert catalog.entry_for(None, "alpha") is not None
    assert catalog.entry_for("any-fixture", "alpha") is not None


def test_entry_for_raises_on_ambiguous_binding() -> None:
    catalog = PitLossBaselineCatalog(
        "v1",
        (
            _entry(track_id="alpha", fixture_id=None),
            _entry(track_id="alpha", fixture_id="fixture-x"),
        ),
    )

    with pytest.raises(ValueError, match="ambiguous"):
        catalog.entry_for("fixture-x", "alpha")


# --- Multi-season union identities and season reuse -------------------------


def test_catalog_covers_the_26_circuit_union() -> None:
    # Arrange / Act: the shipped catalog and the curated physical-circuit union.
    catalog_track_ids = {entry.track_id for entry in _UNION_ENTRIES}
    identity_track_ids = {identity.track_id for identity in PIT_LOSS_TRACK_IDENTITIES}

    # Assert: every registered physical circuit has exactly one catalog entry.
    assert len(PIT_LOSS_BASELINE_CATALOG.entries) == UNION_CIRCUIT_COUNT == 26
    assert len(catalog_track_ids) == 26
    assert catalog_track_ids == identity_track_ids


def test_every_catalog_entry_resolves_to_a_registered_physical_circuit() -> None:
    # Arrange / Act: run the opt-in identity validation over the shipped union.
    validated = validate_catalog_identities(PIT_LOSS_BASELINE_CATALOG)

    # Assert: every entry binds exactly one registered physical circuit and the
    # identity-aware validation accepts the full union unchanged.
    assert validated is PIT_LOSS_BASELINE_CATALOG
    known = {identity.track_id for identity in PIT_LOSS_TRACK_IDENTITIES}
    for entry in _UNION_ENTRIES:
        identity = resolve_catalog_entry_identity(entry)
        assert identity.track_id == entry.track_id
        assert entry.track_id in known
        assert catalog_entry_identity_binding(entry).track_id == entry.track_id


def test_only_australia_is_fixture_bound_in_the_union() -> None:
    for entry in _UNION_ENTRIES:
        if entry is AUSTRALIA_BASELINE:
            assert entry.fixture_id == AUSTRALIA_FIXTURE_ID
            assert entry.track_asset_id == AUSTRALIA_TRACK_ASSET_ID
            assert entry.season == 2026
        else:
            assert entry.fixture_id is None
            assert entry.track_asset_id is None
            assert entry.season is None


def test_fixtureless_union_entries_reuse_one_stable_entry_across_seasons() -> None:
    # Arrange: one catalog entry per physical circuit; the same circuit that
    # appears in 2024, 2025, and 2026 reuses that single stable baseline.
    catalog = PIT_LOSS_BASELINE_CATALOG
    entry_by_track = {entry.track_id: entry for entry in _UNION_ENTRIES}

    # Act / Assert: every season's fixture for a circuit resolves to the one
    # fixture-less entry, and the circuit is never duplicated in the union.
    for identity in PIT_LOSS_TRACK_IDENTITIES:
        entry = entry_by_track[identity.track_id]
        if entry is AUSTRALIA_BASELINE:
            continue
        assert entry.fixture_id is None
        assert entry.track_asset_id is None
        for season in identity.seasons:
            fixture = f"{season}-round-01-{identity.names[0]}"
            assert catalog.entry_for(fixture, identity.track_id) is entry


def test_bahrain_sakhir_and_sepang_are_distinct_circuits() -> None:
    # Arrange / Act: resolve Sakhir and Sepang through the identity layer and
    # read each venue's own catalog entry.
    catalog = PIT_LOSS_BASELINE_CATALOG
    sakhir = resolve_binding_identity(track_id="bahrain")
    sepang = resolve_binding_identity(track_id="sepang")

    # Assert: the two Malaysian-adjacent venues are separate physical circuits
    # with separate entries and values, never merged under one identity.
    assert BAHRAIN_BASELINE.track_id == "bahrain"
    assert SEPANG_BASELINE.track_id == "sepang"
    assert sakhir.track_id == "bahrain"
    assert sepang.track_id == "sepang"
    assert resolve_track_identity("sakhir").track_id == "bahrain"
    assert BAHRAIN_BASELINE.green_ms != SEPANG_BASELINE.green_ms
    assert catalog.entry_for(None, "bahrain") is BAHRAIN_BASELINE
    assert catalog.entry_for(None, "sepang") is SEPANG_BASELINE


def test_barcelona_and_madring_are_distinct_circuits() -> None:
    # Arrange: Barcelona (2024/2025) and Madring (2026) share the ambiguous
    # Spanish GP event name but are separate physical venues.
    catalog = PIT_LOSS_BASELINE_CATALOG

    # Act: resolve each venue by its unambiguous track binding and the
    # season-qualified Spanish GP fixture names.
    assert BARCELONA_BASELINE.track_id == "barcelona-catalunya"
    assert MADRING_BASELINE.track_id == "madring"
    assert resolve_track_identity("2024-spanish-grand-prix").track_id == "barcelona-catalunya"
    assert resolve_track_identity("2026-spanish-grand-prix").track_id == "madring"
    assert catalog.entry_for(
        "2024-round-16-spanish-grand-prix", "barcelona-catalunya",
    ) is BARCELONA_BASELINE
    assert catalog.entry_for("2026-round-16-spanish-grand-prix", "madring") is MADRING_BASELINE

    # Assert: the venues keep distinct baselines and the bare ambiguous name
    # fails closed instead of guessing.
    assert BARCELONA_BASELINE.green_ms != MADRING_BASELINE.green_ms
    with pytest.raises(TrackIdentityLookupError):
        resolve_track_identity("spanish-grand-prix")


# --- Generator fixture binding (2026-01-race) -------------------------------


def test_resolve_binding_identity_resolves_generator_fixture_with_track_name() -> None:
    # Arrange / Act: the actual generator binding for the 2026 Australia
    # delivery: neither the fixture nor the track asset id is itself a
    # registered identity, but the track asset's display name establishes the
    # physical circuit.
    identity = resolve_binding_identity(
        fixture_id="2026-01-race",
        track_id="2026-01-race-telemetry-layout-v1",
        track_name="Australian Grand Prix",
    )

    # Assert: the binding resolves to the Australia physical circuit.
    assert identity.track_id == AUSTRALIA_TRACK_ID == "australia"
    assert identity.track_name == "Albert Park Circuit"


def test_resolve_binding_identity_requires_track_name_for_generator_fixture() -> None:
    # Arrange / Act: the same generator fixture/asset without the track name.
    # Assert: the missing name fails closed instead of guessing Australia.
    with pytest.raises(TrackIdentityLookupError):
        resolve_binding_identity(
            fixture_id="2026-01-race",
            track_id="2026-01-race-telemetry-layout-v1",
        )


def test_resolve_binding_identity_rejects_unknown_track_name() -> None:
    # Arrange / Act: a bogus track name cannot establish the generator fixture.
    # Assert: the unknown name fails closed.
    with pytest.raises(TrackIdentityLookupError):
        resolve_binding_identity(
            fixture_id="2026-01-race",
            track_id="2026-01-race-telemetry-layout-v1",
            track_name="Bogus Circuit",
        )


def test_resolve_binding_identity_rejects_conflicting_track_name() -> None:
    # Arrange / Act: the fixture/track bindings resolve to Australia while the
    # track name resolves to Bahrain — an internally inconsistent delivery.
    # Assert: the conflicting name is ambiguous and fails closed.
    with pytest.raises(TrackIdentityLookupError, match="ambiguous"):
        resolve_binding_identity(
            fixture_id=AUSTRALIA_FIXTURE_ID,
            track_id=AUSTRALIA_TRACK_ID,
            track_name="Bahrain Grand Prix",
        )


def test_resolve_binding_identity_accepts_event_form_alias_with_track_name() -> None:
    # Arrange / Act: the event-form Australia alias with an agreeing display
    # name (the existing identity path plus the new name binding).
    identity = resolve_binding_identity(
        fixture_id=AUSTRALIA_FIXTURE_ID,
        track_id=AUSTRALIA_TRACK_ASSET_ID,
        track_name="Australian Grand Prix",
    )

    # Assert: the alias and the name agree on the Australia circuit.
    assert identity.track_id == "australia"


def test_entry_for_matches_fixture_bound_entry_by_resolved_identity() -> None:
    # Arrange / Act: the generator binding matches the Australia entry through
    # the resolved identity even though the entry is fixture-bound to the
    # event-form fixture and the generator fixture is not registered.
    entry = PIT_LOSS_BASELINE_CATALOG.entry_for(
        "2026-01-race",
        "2026-01-race-telemetry-layout-v1",
        track_name="Australian Grand Prix",
    )

    # Assert: the identity-aware match returns the single Australia entry.
    assert entry is AUSTRALIA_BASELINE


def test_entry_for_keeps_raw_matching_for_synthetic_catalog_with_track_name() -> None:
    # Arrange: a synthetic catalog whose track id is not a registered physical
    # circuit, so identity resolution is unavailable for its entries.
    catalog = PitLossBaselineCatalog("v1", (_entry(track_id="alpha"),))

    # Act: match with a track name supplied.
    entry = catalog.entry_for("any-fixture", "alpha", track_name="Australian Grand Prix")

    # Assert: the synthetic entry keeps the raw binding match.
    assert entry is not None
    assert entry.track_id == "alpha"


def test_validate_catalog_identities_rejects_unknown_track() -> None:
    catalog = PitLossBaselineCatalog("v1", (_entry(track_id="pau"),))

    # Plain validation accepts the well-formed entry; the opt-in identity
    # boundary rejects a track that is not a registered physical circuit.
    assert validate_catalog(catalog) is catalog
    with pytest.raises(TrackIdentityLookupError):
        validate_catalog_identities(catalog)


def test_validate_catalog_identities_rejects_conflicting_fixture_track_binding() -> None:
    # Arrange: the fixture binding resolves to Australia while the track
    # binding resolves to Monza — an internally inconsistent catalog entry.
    catalog = PitLossBaselineCatalog(
        "v1",
        (_entry(track_id="monza", fixture_id=AUSTRALIA_FIXTURE_ID),),
    )

    # Act / Assert: the binding cross-check rejects the mismatch instead of
    # trusting either identifier alone.
    with pytest.raises(TrackIdentityLookupError, match="ambiguous"):
        validate_catalog_identities(catalog)


def test_validate_catalog_identities_rejects_asset_binding_conflict() -> None:
    # Arrange: the track asset resolves to Australia while the track id
    # resolves to Monza — a malformed asset binding.
    catalog = PitLossBaselineCatalog(
        "v1",
        (
            _entry(
                track_id="monza",
                fixture_id=AUSTRALIA_FIXTURE_ID,
                track_asset_id=AUSTRALIA_TRACK_ASSET_ID,
            ),
        ),
    )

    # Act / Assert: the binding cross-check fails closed.
    with pytest.raises(TrackIdentityLookupError, match="ambiguous"):
        validate_catalog_identities(catalog)


def test_validate_catalog_identities_rejects_season_outside_resolved_identity() -> None:
    # Arrange: Madring only raced in 2026; a catalog entry claiming a 2024
    # season for it is physically inconsistent even though the track id
    # resolves.
    catalog = PitLossBaselineCatalog(
        "v1",
        (_entry(track_id="madring", season=2024),),
    )

    # Act / Assert: the explicit entry season must belong to the resolved
    # physical identity's calendar seasons.
    with pytest.raises(
        ValueError,
        match="season 2024 is not part of the resolved physical identity 'madring'",
    ):
        validate_catalog_identities(catalog)


def test_validate_catalog_identities_accepts_season_inside_resolved_identity() -> None:
    # Arrange: Australia raced in 2024/2025/2026, so a 2024 season entry for
    # the same physical circuit is consistent with the identity.
    catalog = PitLossBaselineCatalog(
        "v1",
        (_entry(track_id="australia", season=2024),),
    )

    # Act / Assert: the explicit season belongs to the resolved identity.
    assert validate_catalog_identities(catalog) is catalog


def test_default_catalog_is_validated_against_identity_bindings() -> None:
    # Arrange / Act: the shipped 26-circuit union is validated at construction
    # (module import would have failed otherwise); run the same boundary over
    # every entry to prove fixture/track/asset bindings and seasons agree.
    validated = validate_catalog_identities(PIT_LOSS_BASELINE_CATALOG)

    # Assert: the default catalog passes binding and season validation.
    assert validated is PIT_LOSS_BASELINE_CATALOG
    for entry in _UNION_ENTRIES:
        identity = catalog_entry_identity_binding(entry)
        assert identity.track_id == entry.track_id
        if entry.season is not None:
            assert entry.season in identity.seasons


def test_entry_statuses_mapping_rejects_mutation() -> None:
    # Arrange / Act / Assert: the frozen entry already blocks attribute
    # rebinding; the status mapping itself must also reject item mutation so a
    # shipped entry cannot be altered through its dict.
    with pytest.raises(TypeError, match="does not support item assignment"):
        AUSTRALIA_BASELINE.statuses["green"] = AUSTRALIA_BASELINE.statuses["sc"]  # type: ignore[index]
    with pytest.raises(TypeError, match="does not support item deletion"):
        del AUSTRALIA_BASELINE.statuses["green"]  # type: ignore[misc]


# --- Per-status metadata invariants over the whole union --------------------


def test_shipped_catalog_uses_direct_derived_and_proxy_source_kinds() -> None:
    kinds = {
        baseline.source_status
        for entry in _UNION_ENTRIES
        for baseline in entry.statuses.values()
    }

    assert kinds == {"direct", "derived", "proxy"}


def test_every_derived_and_proxy_status_carries_derivation_and_direct_does_not() -> None:
    for entry in _UNION_ENTRIES:
        for key in ("green", "vsc", "sc"):
            baseline = entry.statuses[key]
            if baseline.source_status in {"derived", "proxy"}:
                assert isinstance(baseline.derivation, PitLossDerivationRecord)
                assert baseline.derivation.method.strip()
            else:
                assert baseline.derivation is None


def test_every_status_has_complete_provenance_evidence_and_confidence() -> None:
    for entry in _UNION_ENTRIES:
        for key in ("green", "vsc", "sc"):
            baseline = entry.statuses[key]
            assert baseline.metric_definition in _KNOWN_METRIC_FAMILIES
            assert type(baseline.evidence_count) is int and baseline.evidence_count >= 0
            assert baseline.confidence in {"high", "medium", "low"}
            assert isinstance(baseline.provenance, PitLossBaselineProvenance)
            assert baseline.provenance.source_url.startswith("https://")
            date.fromisoformat(baseline.provenance.captured_date)
            assert baseline.provenance.evidence.strip()


def test_shipped_catalog_uses_explicit_metric_families() -> None:
    used = {
        baseline.metric_definition
        for entry in _UNION_ENTRIES
        for baseline in entry.statuses.values()
    }

    assert used <= _KNOWN_METRIC_FAMILIES
    assert "f1-com-lane-plus-stationary" in used
    assert "measured-total-cost" in used


def test_every_shipped_entry_satisfies_monotonic_invariant() -> None:
    for entry in _UNION_ENTRIES:
        assert entry.sc_ms <= entry.vsc_ms <= entry.green_ms
        assert entry.statuses["green"].value_ms == entry.green_ms
        assert entry.statuses["vsc"].value_ms == entry.vsc_ms
        assert entry.statuses["sc"].value_ms == entry.sc_ms


def test_low_evidence_union_entries_are_explicit_proxies_not_a_hidden_baseline() -> None:
    # The 2024/2025-only and 2026-only venues without direct evidence must be
    # explicit derived/proxy estimates with full provenance, never a silent
    # 22-second global baseline.
    for entry in (BAHRAIN_BASELINE, MADRING_BASELINE, SEPANG_BASELINE):
        green = entry.statuses["green"]
        assert green.source_status == "proxy"
        assert green.confidence == "low"
        assert isinstance(green.derivation, PitLossDerivationRecord)
        assert green.derivation.method == COMPARABLE_CIRCUIT_PROXY_METHOD
        assert green.value_ms != 22_000
        assert entry.sc_ms <= entry.vsc_ms <= entry.green_ms


def test_source_status_remains_catalog_only_metadata() -> None:
    # The sourceStatus kind lives on catalog objects and the catalog wire form
    # only; resolver and sidecar tests prove it never reaches the browser.
    for entry in _UNION_ENTRIES:
        for key in ("green", "vsc", "sc"):
            baseline = entry.statuses[key]
            assert baseline.source_status in {"direct", "derived", "proxy"}
            assert "sourceStatus" in baseline.as_dict()
