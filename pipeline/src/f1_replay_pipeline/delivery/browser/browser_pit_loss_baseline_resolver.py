"""Pure generation-time resolution of curated pit-loss baselines.

This module deliberately has no dependency on race observations.  The catalog
is the source of truth; gap differences remain useful only to the diagnostic
observation pipeline and cannot change a resolved production value here.

Resolution is per status: code ``1`` selects the Green baseline, code ``4``
selects the Safety Car baseline, and codes ``6``/``7`` select the Virtual
Safety Car baseline.  The selected status's own provenance, evidence count,
and confidence are preserved on the resolution; the catalog-only
``sourceStatus`` kind never leaves the catalog and is never part of the
resolver's sidecar-facing ``as_dict`` payload.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, cast

from f1_replay_pipeline.delivery.browser.browser_pit_loss_baseline_catalog import (
    Confidence,
    DEFAULT_PIT_LOSS_BASELINE_CATALOG,
    PitLossBaselineCatalog,
    PitLossBaselineEntry,
    PitLossBaselineProvenance,
    PitLossStatusBaseline,
    PitLossStatusKey,
    validate_catalog,
)
from f1_replay_pipeline.delivery.browser.browser_pit_loss_track_identity import (
    TrackIdentityLookupError,
    resolve_binding_identity,
)


PitLossBaselineStatus = Literal["green", "sc", "vsc", "unavailable"]
StatusCodeInput = int | Iterable[int | None] | None

GREEN_STATUS: PitLossBaselineStatus = "green"
SAFETY_CAR_STATUS: PitLossBaselineStatus = "sc"
VIRTUAL_SAFETY_CAR_STATUS: PitLossBaselineStatus = "vsc"
UNAVAILABLE_STATUS: PitLossBaselineStatus = "unavailable"

_STATUS_CODE_TO_BASELINE = MappingProxyType({
    1: GREEN_STATUS,
    4: SAFETY_CAR_STATUS,
    6: VIRTUAL_SAFETY_CAR_STATUS,
    7: VIRTUAL_SAFETY_CAR_STATUS,
})
_STATUS_CLASS = MappingProxyType({
    GREEN_STATUS: "normal",
    SAFETY_CAR_STATUS: "safety_car",
    VIRTUAL_SAFETY_CAR_STATUS: "virtual_safety_car",
})


@dataclass(frozen=True, slots=True)
class PitLossBaselineResolution:
    """Immutable result of resolving one fixture/track/status combination.

    ``status="unavailable"`` is an intentional fail-closed result.  It is
    used for an unknown catalog binding, unsupported status, or an ambiguous
    status interval rather than inventing a legacy global estimate.

    ``provenance``, ``evidence_count``, and ``confidence`` describe the
    *selected* status baseline (Green, SC, or VSC), not the entry-level Green
    defaults.  The catalog-only ``sourceStatus`` kind is intentionally never
    carried by this object or serialized by :meth:`as_dict`.
    """

    fixture_id: str | None
    track_id: str
    status_code: int | tuple[int | None, ...] | None
    status: PitLossBaselineStatus
    estimated_loss_ms: int | None
    catalog_version: str
    provenance: PitLossBaselineProvenance | None
    evidence_count: int | None
    confidence: Confidence | None
    available: bool
    error: str | None = None
    entry: PitLossBaselineEntry | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.catalog_version, str) or not self.catalog_version:
            raise ValueError("catalog_version must be a non-empty string")
        if self.status == UNAVAILABLE_STATUS:
            if self.available or self.estimated_loss_ms is not None:
                raise ValueError("unavailable resolution cannot contain an estimate")
            if self.error is None or not self.error.strip():
                raise ValueError("unavailable resolution must contain an error")
            if any(value is not None for value in (self.provenance, self.evidence_count, self.confidence)):
                raise ValueError("unavailable resolution cannot contain catalog entry metadata")
            if self.entry is not None:
                raise ValueError("unavailable resolution cannot contain a catalog entry")
            return
        if self.status not in _STATUS_CLASS:
            raise ValueError("pit-loss baseline status is invalid")
        if not self.available or type(self.estimated_loss_ms) is not int or self.estimated_loss_ms < 0:
            raise ValueError("available resolution must contain a non-negative estimate")
        if self.provenance is None or self.evidence_count is None or self.confidence is None:
            raise ValueError("available resolution must preserve catalog metadata")
        if self.error is not None:
            raise ValueError("available resolution cannot contain an error")
        if self.entry is None:
            raise ValueError("available resolution must contain its catalog entry")

    @property
    def loss_ms(self) -> int | None:
        """Compatibility alias for consumers that call the value a loss."""
        return self.estimated_loss_ms

    @property
    def value_ms(self) -> int | None:
        """Return the resolved status-specific baseline in milliseconds."""
        return self.estimated_loss_ms

    @property
    def resolved_ms(self) -> int | None:
        """Compatibility alias for generation-time callers."""
        return self.estimated_loss_ms

    @property
    def calibration_count(self) -> int | None:
        """Compatibility name for the curated evidence count."""
        return self.evidence_count

    @property
    def status_class(self) -> str | None:
        """Return the legacy observation status class when one is available."""
        return _STATUS_CLASS.get(self.status)

    @property
    def status_label(self) -> str:
        return {
            GREEN_STATUS: "Green",
            SAFETY_CAR_STATUS: "SC",
            VIRTUAL_SAFETY_CAR_STATUS: "VSC",
            UNAVAILABLE_STATUS: "Unavailable",
        }[self.status]

    def as_dict(self) -> dict[str, object]:
        """Serialize the result without dropping selected-status metadata.

        The wire payload intentionally never contains the catalog-only
        ``sourceStatus`` kind: provenance, evidence count, and confidence are
        the permitted metadata for the resolved status.
        """
        value: dict[str, object] = {
            "fixtureId": self.fixture_id,
            "trackId": self.track_id,
            "statusCode": _status_code_as_dict_value(self.status_code),
            "status": self.status,
            "available": self.available,
            "estimatedLossMs": self.estimated_loss_ms,
            "catalogVersion": self.catalog_version,
        }
        if self.provenance is not None:
            value["provenance"] = self.provenance.as_dict()
        if self.evidence_count is not None:
            value["evidenceCount"] = self.evidence_count
        if self.confidence is not None:
            value["confidence"] = self.confidence
        if self.error is not None:
            value["error"] = self.error
        return value


def resolve_pit_loss_baseline(
    fixture_id: str | None,
    track_id: str,
    status_code: StatusCodeInput,
    *,
    catalog: PitLossBaselineCatalog | Mapping[str, object] = DEFAULT_PIT_LOSS_BASELINE_CATALOG,
    track_name: str | None = None,
) -> PitLossBaselineResolution:
    """Resolve a catalog baseline without consulting race observations.

    A scalar status code resolves one point.  An iterable is accepted for a
    complete stop interval and is available only when every code proves the
    same class; mixed, yellow, red, null, and unknown intervals fail closed.
    Catalog validation is performed at this boundary so discount arithmetic
    and the ``SC <= VSC <= Green`` invariant cannot be bypassed.

    ``track_name`` optionally supplies the track asset's display name so the
    generator's actual binding (for example fixture ``2026-01-race`` with track
    asset ``2026-01-race-telemetry-layout-v1``) can still resolve its physical
    circuit through the identity registry.  For the production default catalog
    the fixture, track, and name are validated together; a bogus or missing
    name fails closed instead of guessing.
    """
    _validate_binding(fixture_id, track_id, track_name)
    validated_catalog = validate_catalog(catalog)
    codes, status_error = _normalise_status_codes(status_code)
    if status_error is not None:
        return _unavailable(fixture_id, track_id, validated_catalog, None, status_error)

    resolved_status, classification_error = _classify_status(codes)
    status_value = cast(int | tuple[int | None, ...] | None, _status_code_value(status_code, codes))
    if classification_error is not None or resolved_status is None:
        return _unavailable(
            fixture_id,
            track_id,
            validated_catalog,
            status_value,
            classification_error or "status is unavailable",
        )

    if fixture_id is not None and _is_default_catalog(validated_catalog):
        try:
            resolve_binding_identity(
                fixture_id=fixture_id, track_id=track_id, track_name=track_name,
            )
        except TrackIdentityLookupError:
            # The shipped catalog is identity-bound: a malformed or unknown
            # fixture can never reuse a known fixture-less circuit entry, and a
            # fixture that resolves to a different physical circuit is
            # ambiguous.  Fail closed with the same unavailable contract as an
            # unmatched binding so no legacy 22-second estimate can surface.
            return _unavailable(
                fixture_id,
                track_id,
                validated_catalog,
                status_value,
                "no curated pit-loss baseline is available for this fixture and track",
            )

    try:
        entry = validated_catalog.entry_for(fixture_id, track_id, track_name=track_name)
    except ValueError as error:
        return _unavailable(fixture_id, track_id, validated_catalog, status_value, str(error))
    if entry is None:
        return _unavailable(
            fixture_id,
            track_id,
            validated_catalog,
            status_value,
            "no curated pit-loss baseline is available for this fixture and track",
        )
    if not (entry.sc_ms <= entry.vsc_ms <= entry.green_ms):
        # Catalog validation already enforces this invariant; the explicit
        # boundary guard keeps a malformed or bypassed entry fail-closed rather
        # than emitting an inverted status ordering.
        return _unavailable(
            fixture_id,
            track_id,
            validated_catalog,
            status_value,
            "catalog entry violates the SC <= VSC <= Green monotonic invariant",
        )

    # Select the resolved status's own baseline so the value and its metadata
    # travel together.  ``sourceStatus`` stays on the catalog entry and is not
    # copied onto the resolution.
    status_baseline = entry.statuses[cast(PitLossStatusKey, resolved_status)]
    return PitLossBaselineResolution(
        fixture_id=fixture_id,
        track_id=track_id,
        status_code=status_value,
        status=resolved_status,
        estimated_loss_ms=status_baseline.value_ms,
        catalog_version=validated_catalog.catalog_version,
        provenance=cast(PitLossBaselineProvenance, status_baseline.provenance),
        evidence_count=status_baseline.evidence_count,
        confidence=status_baseline.confidence,
        available=True,
        entry=entry,
    )


def resolve_baseline(
    fixture_id: str | None,
    track_id: str,
    status_code: StatusCodeInput,
    *,
    catalog: PitLossBaselineCatalog | Mapping[str, object] = DEFAULT_PIT_LOSS_BASELINE_CATALOG,
    track_name: str | None = None,
) -> PitLossBaselineResolution:
    """Short alias for callers that already operate within the catalog domain."""
    return resolve_pit_loss_baseline(
        fixture_id, track_id, status_code, catalog=catalog, track_name=track_name,
    )


def _unavailable(
    fixture_id: str | None,
    track_id: str,
    catalog: PitLossBaselineCatalog,
    status_code: int | tuple[int | None, ...] | None,
    error: str,
) -> PitLossBaselineResolution:
    return PitLossBaselineResolution(
        fixture_id=fixture_id,
        track_id=track_id,
        status_code=status_code,
        status=UNAVAILABLE_STATUS,
        estimated_loss_ms=None,
        catalog_version=catalog.catalog_version,
        provenance=None,
        evidence_count=None,
        confidence=None,
        available=False,
        error=error,
    )


def _is_default_catalog(catalog: PitLossBaselineCatalog) -> bool:
    """Return whether ``catalog`` is the shipped production default.

    Only the production catalog is identity-bound: fixture and track must
    resolve together through the physical identity registry before matching, so
    a malformed or unknown fixture can never reuse a known fixture-less
    circuit's baseline.  Custom synthetic catalogs and legacy APIs that
    intentionally use arbitrary identifiers remain exempt because they are not
    the production default.
    """
    if catalog is DEFAULT_PIT_LOSS_BASELINE_CATALOG:
        return True
    return catalog.as_dict() == DEFAULT_PIT_LOSS_BASELINE_CATALOG.as_dict()


def _validate_binding(
    fixture_id: str | None,
    track_id: str,
    track_name: str | None = None,
) -> None:
    if fixture_id is not None and (not isinstance(fixture_id, str) or not fixture_id):
        raise ValueError("fixture_id must be a non-empty string or None")
    if not isinstance(track_id, str) or not track_id:
        raise ValueError("track_id must be a non-empty string")
    if track_name is not None and (not isinstance(track_name, str) or not track_name):
        raise ValueError("track_name must be a non-empty string or None")


def _normalise_status_codes(
    status_code: StatusCodeInput,
) -> tuple[tuple[int | None, ...], str | None]:
    if status_code is None:
        return (), "track status is unavailable"
    if type(status_code) is int:
        return (status_code,), None
    try:
        codes = tuple(cast(Iterable[int | None], status_code))
    except TypeError:
        return (), "track status must be an integer or an iterable of status codes"
    return codes, None


def _classify_status(
    codes: tuple[int | None, ...],
) -> tuple[PitLossBaselineStatus | None, str | None]:
    if not codes:
        return None, "track status interval is empty or unavailable"
    if any(type(code) is not int for code in codes):
        return None, "track status interval contains an unavailable or invalid code"
    statuses = tuple(
        _STATUS_CODE_TO_BASELINE.get(cast(int, code))
        for code in codes
    )
    if any(status is None for status in statuses):
        return None, "track status is unsupported; baseline resolution is unavailable"
    if len(set(statuses)) != 1:
        return None, "track status interval is ambiguous or mixed"
    return cast(PitLossBaselineStatus, statuses[0]), None


def _status_code_value(
    original: StatusCodeInput,
    codes: tuple[int | None, ...],
) -> int | tuple[int | None, ...] | None:
    if type(original) is int:
        return original
    return codes


def _status_code_as_dict_value(
    status_code: int | tuple[int | None, ...] | None,
) -> int | list[int | None] | None:
    if isinstance(status_code, tuple):
        return list(status_code)
    return status_code


__all__ = [
    "GREEN_STATUS",
    "PitLossBaselineResolution",
    "PitLossBaselineStatus",
    "SAFETY_CAR_STATUS",
    "StatusCodeInput",
    "UNAVAILABLE_STATUS",
    "VIRTUAL_SAFETY_CAR_STATUS",
    "resolve_baseline",
    "resolve_pit_loss_baseline",
]
