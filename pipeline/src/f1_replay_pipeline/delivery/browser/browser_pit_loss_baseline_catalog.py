"""Immutable, repository-local pit-loss baseline catalog.

The catalog is deliberately data-only.  It is checked into the repository so
generation is deterministic and never needs to fetch a circuit statistic at
runtime.  Race observations are not part of this model; ``evidence_count``
describes the curated source evidence instead.

Every available status (Green, VSC, SC) is represented independently and must
carry its own source status (``direct``/``derived``/``proxy``), metric
definition, provenance, evidence count, and confidence.  ``sourceStatus`` is
catalog-only metadata: it is never serialized into the browser sidecar or the
frontend delivery data.  Derived and proxy statuses must explain themselves
with an explicit derivation record, whose optional discount is intentionally
not bounded by the legacy 5-10 second window; the monotonic
``SC <= VSC <= Green`` invariant and integer/non-negative validation are
always enforced.

The shipped catalog covers the full 2024-2026 physical-circuit union (26
circuits): one fixture-less entry per physical circuit.  Source-backed
circuits use the official Formula1.com Green value and any direct status
value supported by the research context; low-evidence circuits (Bahrain,
Jeddah, Imola, Madrid, Sepang, Zandvoort, and Marina Bay) carry explicit
derived or proxy estimates with full provenance instead of a hidden
22-second baseline.  Australia remains bound to the 2026 season opener.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
import re
from types import MappingProxyType
from typing import Literal, cast

from f1_replay_pipeline.delivery.browser.browser_pit_loss_track_identity import (
    IDENTITY_SCHEMA_ID,
    PIT_LOSS_TRACK_IDENTITIES,
    PIT_LOSS_TRACK_IDENTITY_REGISTRY,
    PitLossTrackIdentity,
    TrackIdentityLookupError,
    canonicalize_identity_key,
    normalize_identity_key,
    resolve_binding_identity,
    resolve_track_identity,
    resolve_track_identity_or_none,
)


MAX_INT64 = (1 << 63) - 1
# Legacy universal discount bounds, retained as documented constants only.
# The current model accepts any non-negative integer discount and no longer
# enforces the historical 5000-10000 ms window; provenance and derivation
# records justify every value outside that range.
MIN_DISCOUNT_MS = 5_000
MAX_DISCOUNT_MS = 10_000
DEFAULT_VSC_DISCOUNT_MS = 7_000
DEFAULT_SAFETY_CAR_DISCOUNT_MS = 10_000
# ``SC`` is the wire/status abbreviation used by downstream delivery code.
DEFAULT_SC_DISCOUNT_MS = DEFAULT_SAFETY_CAR_DISCOUNT_MS
CATALOG_VERSION = "v2"
CATALOG_SCHEMA_ID = "urn:f1-cache-replay:schema:replay-data:v2:pit-loss-baseline-catalog"
CURATED_BASELINE_METHOD = "curated-track-baseline-v1"
DERIVE_GREEN_DISCOUNT_METHOD = "green-minus-discount-v1"
# Derivation methods for low-evidence derived/proxy values.  These name the
# policy that produced an estimate so downstream review can audit it without
# re-reading the catalog source.  ``comparable-circuit-proxy-v1`` marks a
# Green estimate calibrated against comparable measured circuits when no
# direct baseline exists; ``source-adjusted-green-v1`` marks a Green value
# adjusted from a conflicting or stale source figure.
COMPARABLE_CIRCUIT_PROXY_METHOD = "comparable-circuit-proxy-v1"
SOURCE_ADJUSTED_GREEN_METHOD = "source-adjusted-green-v1"

AUSTRALIA_FIXTURE_ID = "2026-round-01-australian-grand-prix"
AUSTRALIA_TRACK_ID = "australia"
AUSTRALIA_TRACK_ASSET_ID = f"{AUSTRALIA_FIXTURE_ID}-telemetry-layout-v1"
AUSTRALIA_SOURCE_URL = (
    "https://www.formula1.com/en/latest/article/need-to-know-the-most-important-"
    "facts-stats-and-trivia-ahead-of-the-2026.7gyyqNLcwuCPZdXvgGwhCM"
)
# Official Formula1.com ``Need to Know`` pit stop time loss sources for the
# source-backed union circuits.  Values were verified against these pages on
# 2026-08-04 and are referenced per status in each entry's provenance.
CHINA_SOURCE_URL = (
    "https://www.formula1.com/en/latest/article/need-to-know-the-most-important-"
    "facts-stats-and-trivia-ahead-of-the-2026-chinese-grand-prix.2KbEu5okNlIMtVgklEpGJT"
)
JAPAN_SOURCE_URL = (
    "https://www.formula1.com/en/latest/article/need-to-know-the-most-important-"
    "facts-stats-and-trivia-ahead-of-the-2026-japanese-grand-prix.59xoCL07qjNCWPVzBJOmK2"
)
MIAMI_SOURCE_URL = (
    "https://www.formula1.com/en/latest/article/need-to-know-the-most-important-"
    "facts-stats-and-trivia-ahead-of-the-2026-miami-grand-prix.6CaXXUBmgXIxzTWv26Qc6b"
)
MONACO_SOURCE_URL = (
    "https://www.formula1.com/en/latest/article/need-to-know-the-most-important-"
    "facts-stats-and-trivia-ahead-of-the-2026-monaco-grand-prix.1dphiN7qz6fiI4sm4MaC4V"
)
BARCELONA_SOURCE_URL = (
    "https://www.formula1.com/en/latest/article/need-to-know-the-most-important-"
    "facts-stats-and-trivia-ahead-of-the-2026-barcelona-catalunya-grand-prix."
    "51RmTAVS0jveoGWnBr9Ul"
)
CANADA_SOURCE_URL = (
    "https://www.formula1.com/en/latest/article/need-to-know-the-most-important-"
    "facts-stats-and-trivia-ahead-of-the-2026-canadian-grand-prix.5mxhs5HB0dFrvjaz7sbBGR"
)
# Pirelli Canada race-day status value corroboration (technical analysis).
CANADA_PIRELLI_SOURCE_URL = "https://www.f1technical.net/news/24759"
AUSTRIA_SOURCE_URL = (
    "https://www.formula1.com/en/latest/article/need-to-know-the-most-important-"
    "facts-stats-and-trivia-ahead-of-the-2026-austrian-grand-prix.5c3It3dAheBaaNuzedrXPx"
)
BRITAIN_SOURCE_URL = (
    "https://www.formula1.com/en/latest/article/need-to-know-the-most-important-"
    "facts-stats-and-trivia-ahead-of-the-2026-british-grand-prix.6zB1eKpKSqJjdYtLxye7eG"
)
BELGIUM_SOURCE_URL = (
    "https://www.formula1.com/en/latest/article/need-to-know-the-most-important-"
    "facts-stats-and-trivia-ahead-of-the-2026-belgian-grand-prix.79EYKMXkm1MLXnciMgrPyY"
)
HUNGARY_SOURCE_URL = (
    "https://www.formula1.com/en/latest/article/need-to-know-the-most-important-"
    "facts-stats-and-trivia-ahead-of-the-2026-hungarian-grand-prix.5Zhle8vMBDwzt2OSoCgk4d"
)
MONZA_SOURCE_URL = (
    "https://www.formula1.com/en/latest/article/need-to-know-the-most-important-"
    "facts-stats-and-trivia-ahead-of-the-2025-italian-grand-prix.4HiHxhtZYqq5rxAql7JwPW"
)
# Formula1.com strategy guide with Monza's direct SC/VSC status value.
MONZA_STRATEGY_GUIDE_SOURCE_URL = (
    "https://www.formula1.com/en/latest/article/strategy-guide-what-are-the-possible-"
    "race-strategies-for-the-italian-grand.5WHbApgWXXFo9ExiU1oSvw"
)
BAKU_SOURCE_URL = (
    "https://www.formula1.com/en/latest/article/need-to-know-the-most-important-"
    "facts-stats-and-trivia-ahead-of-the-2025-azerbaijan.46ZhDHANIrQQY5cbcIWy6Y"
)
COTA_SOURCE_URL = (
    "https://www.formula1.com/en/latest/article/need-to-know-the-most-important-"
    "facts-stats-and-trivia-ahead-of-the-2025-united-states-grand-prix."
    "1jmVcZ1nggWJpoUO6OLkjA"
)
# Secondary technical analysis reporting COTA's approximate 6 s neutralised saving.
COTA_TECHNICAL_SOURCE_URL = "https://www.f1technical.net/news/27898"
MEXICO_SOURCE_URL = (
    "https://www.formula1.com/en/latest/article/need-to-know-the-most-important-"
    "facts-stats-and-trivia-ahead-of-the-2025-mexico-city-grand-prix."
    "25jpn16FhpRZvIpC4ULU5w"
)
BRAZIL_SOURCE_URL = (
    "https://www.formula1.com/en/latest/article/need-to-know-the-most-important-"
    "facts-stats-and-trivia-ahead-of-the-2025-sao.4q3WPe5DWYcZPJ3ThYZole"
)
LAS_VEGAS_SOURCE_URL = (
    "https://www.formula1.com/en/latest/article/need-to-know-the-most-important-"
    "facts-stats-and-trivia-ahead-of-the-2025-las.2I0688JAOAFKCPbDIZGPZo"
)
QATAR_SOURCE_URL = (
    "https://www.formula1.com/en/latest/article/need-to-know-the-most-important-"
    "facts-stats-and-trivia-ahead-of-the-2025-qatar-grand-prix.6DxCdCUowmWJJQMOTOPZI7"
)
# Pirelli press material reporting Qatar's approximate 10 s SC advantage.
PIRELLI_QATAR_SOURCE_URL = (
    "https://press.pirelli.com/tyre-strategy-at-the-forefront-of-the-action-in-qatar/"
)
ABU_DHABI_SOURCE_URL = (
    "https://www.formula1.com/en/latest/article/need-to-know-the-most-important-"
    "facts-stats-and-trivia-ahead-of-the-2025-abu.5pGXiI5uO3um15txovxKwc"
)
# Corroborating and low-evidence sources for the derived/proxy entries.
# F1 Chronicle is the measured-dataset reference used to calibrate proxy
# values for circuits without a direct published baseline.  Game-balance
# values (F1 Manager) are explicitly rejected as production evidence.
F1_CHRONICLE_SOURCE_URL = "https://f1chronicle.com/f1-pit-stop-time-loss-data/"
# Zandvoort speed-limit analysis supporting the 60 to 80 km/h pit-lane change.
ZANDVOORT_SPEED_LIMIT_ANALYSIS_SOURCE_URL = (
    "https://coffeecornermotorsport.com/dutch-gp-2025-strategy-preview-tyres-"
    "pit-lane-speed-and-pirelli/"
)
# Sepang 2017 FIA pit-stop summary: raw stop durations only, used as a rough
# magnitude reference and never as a pit-loss baseline.
SEPANG_PIT_STOP_SUMMARY_SOURCE_URL = (
    "https://www.formula1.com/en/results/2017/races/973/malaysia/pit-stop-summary"
)
SINGAPORE_SOURCE_URL = (
    "https://www.formula1.com/en/latest/article/need-to-know-the-most-important-"
    "facts-stats-and-trivia-ahead-of-the-2025-singapore.468A8YSelm8nsKywLuGTf"
)

_IDENTIFIER = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
Confidence = Literal["high", "medium", "low"]
# Catalog-only source status for one status value.  ``direct`` covers values
# published by an official or measured source; ``derived`` and ``proxy`` values
# must carry an explicit derivation record.
StatusSourceKind = Literal["direct", "derived", "proxy"]
# Canonical metric families from the research context.  Mixing metric families
# silently is rejected; each status names the family it was measured or derived
# under.
PitLossMetric = Literal[
    "f1-com-lane-plus-stationary",
    "measured-total-cost",
    "fia-stop-duration",
]
PitLossStatusKey = Literal["green", "vsc", "sc"]

_STATUS_KEYS = frozenset({"green", "vsc", "sc"})
_METRICS = frozenset({
    "f1-com-lane-plus-stationary",
    "measured-total-cost",
    "fia-stop-duration",
})
_SOURCE_STATUS_KINDS = frozenset({"direct", "derived", "proxy"})


@dataclass(frozen=True, slots=True)
class PitLossDerivationRecord:
    """Optional record explaining how a derived or proxy value was produced.

    ``discount_ms`` is optional and intentionally unbounded by the legacy
    5000-10000 ms window; provenance decides whether a discount is plausible.
    """

    method: str = DERIVE_GREEN_DISCOUNT_METHOD
    base_status: PitLossStatusKey = "green"
    discount_ms: int | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.method, str) or not self.method.strip():
            raise ValueError("method must be a non-empty string")
        if self.base_status not in _STATUS_KEYS:
            raise ValueError("base_status must be green, vsc, or sc")
        if self.discount_ms is not None:
            _validate_unsigned_int(self.discount_ms, "discount_ms")
        if self.notes is not None and (not isinstance(self.notes, str) or not self.notes.strip()):
            raise ValueError("notes must be a non-empty string or None")

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {"method": self.method, "baseStatus": self.base_status}
        if self.discount_ms is not None:
            value["discountMs"] = self.discount_ms
        if self.notes is not None:
            value["notes"] = self.notes
        return value


@dataclass(frozen=True, slots=True)
class PitLossStatusBaseline:
    """One status-specific baseline with required source metadata.

    ``source_status`` is catalog-only: downstream sidecar and frontend data
    never receive it.  Derived and proxy statuses must carry a derivation
    record; direct statuses must not.
    """

    value_ms: int
    source_status: StatusSourceKind
    metric_definition: PitLossMetric
    provenance: PitLossBaselineProvenance | Mapping[str, object]
    evidence_count: int
    confidence: Confidence
    derivation: PitLossDerivationRecord | Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        _validate_unsigned_int(self.value_ms, "value_ms")
        if self.source_status not in _SOURCE_STATUS_KINDS:
            raise ValueError("source_status must be direct, derived, or proxy")
        if self.metric_definition not in _METRICS:
            raise ValueError("metric_definition is not a known metric family")
        provenance = _coerce_provenance(self.provenance)
        if type(self.evidence_count) is not int or not 0 <= self.evidence_count <= MAX_INT64:
            raise TypeError("evidence_count must be a non-negative integer")
        if not isinstance(self.confidence, str) or self.confidence not in {"high", "medium", "low"}:
            raise ValueError("confidence must be high, medium, or low")
        derivation = _coerce_derivation(self.derivation)
        if self.source_status in {"derived", "proxy"} and derivation is None:
            raise ValueError("derived and proxy statuses must carry a derivation record")
        if self.source_status == "direct" and derivation is not None:
            raise ValueError("direct statuses cannot carry a derivation record")
        object.__setattr__(self, "provenance", provenance)
        object.__setattr__(self, "derivation", derivation)

    @property
    def calibration_count(self) -> int:
        """Compatibility name for the curated evidence count."""
        return self.evidence_count

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "valueMs": self.value_ms,
            "sourceStatus": self.source_status,
            "metricDefinition": self.metric_definition,
            "provenance": cast(PitLossBaselineProvenance, self.provenance).as_dict(),
            "evidenceCount": self.evidence_count,
            "confidence": self.confidence,
        }
        if self.derivation is not None:
            value["derivation"] = cast(PitLossDerivationRecord, self.derivation).as_dict()
        return value


@dataclass(frozen=True, slots=True)
class PitLossDiscountConfiguration:
    """Optional status discounts applied to a track's Green baseline.

    Discounts are unbounded, non-negative metadata used to explain derived
    values.  They are never required to equal ``green - vsc`` or
    ``green - sc``: direct track-specific status values may imply any discount,
    and the legacy 5000-10000 ms window is not enforced here.
    """

    vsc_ms: int = DEFAULT_VSC_DISCOUNT_MS
    safety_car_ms: int = DEFAULT_SAFETY_CAR_DISCOUNT_MS

    def __post_init__(self) -> None:
        _validate_unsigned_int(self.vsc_ms, "vsc_ms")
        _validate_unsigned_int(self.safety_car_ms, "safety_car_ms")
        if self.safety_car_ms < self.vsc_ms:
            raise ValueError("safety_car_ms must be greater than or equal to vsc_ms")

    @property
    def sc_ms(self) -> int:
        """Compatibility alias for callers that use the status abbreviation."""
        return self.safety_car_ms

    def as_dict(self) -> dict[str, int]:
        return {"vscMs": self.vsc_ms, "safetyCarMs": self.safety_car_ms}


@dataclass(frozen=True, slots=True)
class PitLossBaselineProvenance:
    """Source metadata for a curated baseline, independent of race telemetry."""

    source_url: str
    captured_date: str
    evidence: str
    method: str = CURATED_BASELINE_METHOD

    def __post_init__(self) -> None:
        if not isinstance(self.source_url, str) or not self.source_url.startswith("https://"):
            raise ValueError("source_url must be an HTTPS URL")
        if not isinstance(self.captured_date, str):
            raise TypeError("captured_date must be an ISO date")
        try:
            date.fromisoformat(self.captured_date)
        except ValueError as error:
            raise ValueError("captured_date must be an ISO date") from error
        for value, label in ((self.evidence, "evidence"), (self.method, "method")):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must be a non-empty string")

    def as_dict(self) -> dict[str, str]:
        return {
            "sourceUrl": self.source_url,
            "capturedDate": self.captured_date,
            "evidence": self.evidence,
            "method": self.method,
        }


@dataclass(frozen=True, slots=True)
class PitLossBaselineEntry:
    """One immutable baseline bound to a circuit and, when known, a fixture.

    Green, VSC, and SC are represented independently through ``statuses``, so a
    catalog can mix direct, derived, and proxy values per status.  The entry
    exposes compatibility aliases (``green_ms``, ``vsc_ms``, ``sc_ms``,
    ``provenance``, ``evidence_count``, ``confidence``, ``vsc_discount_ms``,
    ``safety_car_discount_ms``) for downstream code that predates per-status
    metadata.
    """

    track_id: str
    track_name: str
    statuses: Mapping[PitLossStatusKey, PitLossStatusBaseline]
    discounts: PitLossDiscountConfiguration | Mapping[str, object] | None = None
    fixture_id: str | None = None
    track_asset_id: str | None = None
    season: int | None = None

    def __post_init__(self) -> None:
        _validate_identifier(self.track_id, "track_id")
        if not isinstance(self.track_name, str) or not self.track_name.strip():
            raise ValueError("track_name must be a non-empty string")
        statuses = _coerce_statuses(self.statuses)
        green = cast(PitLossStatusBaseline, statuses["green"])
        vsc = cast(PitLossStatusBaseline, statuses["vsc"])
        sc = cast(PitLossStatusBaseline, statuses["sc"])
        if not sc.value_ms <= vsc.value_ms <= green.value_ms:
            raise ValueError("pit-loss baselines must satisfy sc_ms <= vsc_ms <= green_ms")
        discounts = _coerce_discounts(self.discounts)
        for value, label in ((self.fixture_id, "fixture_id"), (self.track_asset_id, "track_asset_id")):
            if value is not None:
                _validate_identifier(value, label)
        if self.season is not None and (type(self.season) is not int or self.season < 1):
            raise ValueError("season must be a positive integer or None")
        object.__setattr__(self, "statuses", statuses)
        object.__setattr__(self, "discounts", discounts)

    @property
    def green_ms(self) -> int:
        """Compatibility alias for the Green status value."""
        return cast(PitLossStatusBaseline, self.statuses["green"]).value_ms

    @property
    def vsc_ms(self) -> int:
        """Compatibility alias for the VSC status value."""
        return cast(PitLossStatusBaseline, self.statuses["vsc"]).value_ms

    @property
    def sc_ms(self) -> int:
        """Compatibility alias for the SC status value."""
        return cast(PitLossStatusBaseline, self.statuses["sc"]).value_ms

    @property
    def provenance(self) -> PitLossBaselineProvenance:
        """Green-status provenance, kept as the entry-level alias."""
        return cast(PitLossBaselineProvenance, self.statuses["green"].provenance)

    @property
    def evidence_count(self) -> int:
        """Green-status evidence count, kept as the entry-level alias."""
        return cast(PitLossStatusBaseline, self.statuses["green"]).evidence_count

    @property
    def confidence(self) -> Confidence:
        """Green-status confidence, kept as the entry-level alias."""
        return cast(PitLossStatusBaseline, self.statuses["green"]).confidence

    @property
    def calibration_count(self) -> int:
        """Compatibility name for the curated evidence count."""
        return self.evidence_count

    @property
    def vsc_discount_ms(self) -> int:
        """Return the configured, derived, or reported VSC discount."""
        discounts = self.discounts
        if discounts is not None:
            return cast(PitLossDiscountConfiguration, discounts).vsc_ms
        derivation = cast(
            PitLossDerivationRecord | None,
            cast(PitLossStatusBaseline, self.statuses["vsc"]).derivation,
        )
        if derivation is not None and derivation.discount_ms is not None:
            return derivation.discount_ms
        return self.green_ms - self.vsc_ms

    @property
    def safety_car_discount_ms(self) -> int:
        """Return the configured, derived, or reported SC discount."""
        discounts = self.discounts
        if discounts is not None:
            return cast(PitLossDiscountConfiguration, discounts).safety_car_ms
        derivation = cast(
            PitLossDerivationRecord | None,
            cast(PitLossStatusBaseline, self.statuses["sc"]).derivation,
        )
        if derivation is not None and derivation.discount_ms is not None:
            return derivation.discount_ms
        return self.green_ms - self.sc_ms

    def matches(self, fixture_id: str | None, track_id: str) -> bool:
        """Return whether a delivery's fixture and track binding match."""
        if track_id not in {self.track_id, self.track_asset_id}:
            return False
        return self.fixture_id is None or fixture_id == self.fixture_id

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "trackId": self.track_id,
            "trackName": self.track_name,
            "statuses": {
                key: cast(PitLossStatusBaseline, baseline).as_dict()
                for key, baseline in self.statuses.items()
            },
        }
        if self.discounts is not None:
            value["discounts"] = cast(PitLossDiscountConfiguration, self.discounts).as_dict()
        for key, field_value in (
            ("fixtureId", self.fixture_id),
            ("trackAssetId", self.track_asset_id),
            ("season", self.season),
        ):
            if field_value is not None:
                value[key] = field_value
        return value


@dataclass(frozen=True, slots=True)
class PitLossBaselineCatalog:
    """Versioned immutable collection of curated per-track baselines."""

    catalog_version: str
    entries: Sequence[PitLossBaselineEntry | Mapping[str, object]]

    def __post_init__(self) -> None:
        if self.catalog_version != CATALOG_VERSION:
            raise ValueError(f"catalog_version must be {CATALOG_VERSION}")
        entries = tuple(_coerce_entry(entry) for entry in self.entries)
        if not entries:
            raise ValueError("catalog must contain at least one entry")
        identities = tuple((entry.track_id, entry.fixture_id) for entry in entries)
        if len(set(identities)) != len(identities):
            raise ValueError("catalog entries must have unique track and fixture identities")
        object.__setattr__(self, "entries", entries)

    def as_dict(self) -> dict[str, object]:
        entry_values = cast(tuple[PitLossBaselineEntry, ...], self.entries)
        return {
            "catalogVersion": self.catalog_version,
            "entries": [entry.as_dict() for entry in entry_values],
        }

    def entry_for(
        self,
        fixture_id: str | None,
        track_id: str,
        *,
        track_name: str | None = None,
    ) -> PitLossBaselineEntry | None:
        """Return the uniquely matching entry without applying a status.

        With a ``track_name`` binding the match is identity-aware: a delivery
        whose fixture/track/asset identifiers resolve to one registered
        physical circuit matches the single stable entry for that circuit,
        including the fixture-less union entries and the fixture-bound
        Australia entry.  Raw fixture/track matching is kept for synthetic
        catalogs and legacy calls where identity resolution is unavailable.
        """
        entries = cast(tuple[PitLossBaselineEntry, ...], self.entries)
        if track_name is None:
            matches = tuple(entry for entry in entries if entry.matches(fixture_id, track_id))
        else:
            try:
                delivery_identity = resolve_binding_identity(
                    fixture_id=fixture_id, track_id=track_id, track_name=track_name,
                )
            except TrackIdentityLookupError as error:
                if "ambiguous" in str(error):
                    raise
                # The delivery binding cannot resolve through the identity
                # registry at all (for example a synthetic/legacy binding with
                # an unresolvable track id): keep the raw binding match.
                matches = tuple(entry for entry in entries if entry.matches(fixture_id, track_id))
            else:
                matches = tuple(
                    entry
                    for entry in entries
                    if _entry_matches_resolved_identity(
                        entry, fixture_id, track_id, delivery_identity,
                    )
                )
        if len(matches) > 1:
            raise ValueError("catalog track binding is ambiguous")
        return matches[0] if matches else None


def _entry_matches_resolved_identity(
    entry: PitLossBaselineEntry,
    fixture_id: str | None,
    track_id: str,
    delivery_identity: PitLossTrackIdentity,
) -> bool:
    """Return whether one entry binds the delivery's resolved physical circuit.

    Registered circuit entries match when their own track identity equals the
    delivery identity.  Synthetic/legacy entries that cannot resolve through
    the identity registry keep the raw fixture/track binding match so explicit
    synthetic catalogs continue to work even when a track name is supplied.
    """
    try:
        entry_identity = resolve_track_identity(entry.track_id)
    except TrackIdentityLookupError:
        return entry.matches(fixture_id, track_id)
    return entry_identity.track_id == delivery_identity.track_id


def validate_catalog(
    catalog: PitLossBaselineCatalog | Mapping[str, object],
) -> PitLossBaselineCatalog:
    """Validate and return an immutable catalog for explicit boundary checks."""
    if isinstance(catalog, Mapping):
        if set(catalog) != {"catalogVersion", "entries"}:
            raise ValueError("catalog must contain catalogVersion and entries")
        catalog = PitLossBaselineCatalog(
            catalog_version=cast(str, catalog["catalogVersion"]),
            entries=cast(Sequence[Mapping[str, object]], catalog["entries"]),
        )
    if not isinstance(catalog, PitLossBaselineCatalog):
        raise TypeError("catalog must be a PitLossBaselineCatalog")
    return catalog


validate_baseline_catalog = validate_catalog


def resolve_catalog_entry_identity(
    entry: PitLossBaselineEntry | Mapping[str, object],
) -> PitLossTrackIdentity:
    """Resolve an entry's physical circuit through the curated identity map.

    This is the identity-aware boundary for catalog population: an entry whose
    ``track_id`` is not a registered physical circuit raises
    :class:`TrackIdentityLookupError` instead of being silently accepted.
    """
    coerced = entry if isinstance(entry, PitLossBaselineEntry) else _coerce_entry(entry)
    return resolve_track_identity(coerced.track_id)


def catalog_entry_identity_binding(
    entry: PitLossBaselineEntry | Mapping[str, object],
) -> PitLossTrackIdentity:
    """Cross-check an entry's fixture and track-asset bindings against one circuit.

    Deterministically resolves every non-null binding (``fixture_id``,
    ``track_id``, ``track_asset_id``) and rejects bindings that point at
    different physical circuits as ambiguous.
    """
    coerced = entry if isinstance(entry, PitLossBaselineEntry) else _coerce_entry(entry)
    return resolve_binding_identity(
        fixture_id=coerced.fixture_id,
        track_id=coerced.track_id,
        track_asset_id=coerced.track_asset_id,
    )


def validate_catalog_identities(
    catalog: PitLossBaselineCatalog | Mapping[str, object],
) -> PitLossBaselineCatalog:
    """Validate that every catalog entry binds one known physical circuit.

    Opt-in boundary used by catalog population before a wider catalog ships:
    synthetic test catalogs and existing deliveries remain valid without it.
    Every non-null fixture/track/asset binding must resolve to the same
    registered physical circuit (through :func:`catalog_entry_identity_binding`),
    and an explicit entry ``season`` must belong to that identity's calendar
    seasons.
    """
    validated = validate_catalog(catalog)
    entries = cast(tuple[PitLossBaselineEntry, ...], validated.entries)
    for entry in entries:
        identity = resolve_catalog_entry_identity(entry)
        catalog_entry_identity_binding(entry)
        if entry.season is not None and entry.season not in identity.seasons:
            raise ValueError(
                f"catalog entry {entry.track_id!r} season {entry.season} "
                f"is not part of the resolved physical identity "
                f"{identity.track_id!r} seasons {sorted(identity.seasons)}",
            )
    return validated


def _coerce_derivation(
    value: PitLossDerivationRecord | Mapping[str, object] | None,
) -> PitLossDerivationRecord | None:
    if value is None:
        return None
    if isinstance(value, PitLossDerivationRecord):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("derivation must be PitLossDerivationRecord, a mapping, or None")
    return PitLossDerivationRecord(
        method=cast(str, value.get("method", DERIVE_GREEN_DISCOUNT_METHOD)),
        base_status=cast(PitLossStatusKey, value.get("baseStatus", "green")),
        discount_ms=cast(int | None, value.get("discountMs")),
        notes=cast(str | None, value.get("notes")),
    )


def _coerce_status_baseline(
    value: PitLossStatusBaseline | Mapping[str, object], key: str,
) -> PitLossStatusBaseline:
    if isinstance(value, PitLossStatusBaseline):
        return value
    if not isinstance(value, Mapping):
        raise TypeError(f"statuses.{key} must be a status baseline mapping")
    return PitLossStatusBaseline(
        value_ms=cast(int, value.get("valueMs")),
        source_status=cast(StatusSourceKind, value.get("sourceStatus")),
        metric_definition=cast(PitLossMetric, value.get("metricDefinition")),
        provenance=cast(Mapping[str, object], value.get("provenance")),
        evidence_count=cast(int, value.get("evidenceCount")),
        confidence=cast(Confidence, value.get("confidence")),
        derivation=cast(
            PitLossDerivationRecord | Mapping[str, object] | None,
            value.get("derivation"),
        ),
    )


def _coerce_statuses(
    value: Mapping[PitLossStatusKey, PitLossStatusBaseline],
) -> Mapping[PitLossStatusKey, PitLossStatusBaseline]:
    if not isinstance(value, Mapping):
        raise TypeError("statuses must be a mapping of status baselines")
    if set(value) != _STATUS_KEYS:
        raise ValueError("statuses must define green, vsc, and sc baselines")
    # ``MappingProxyType`` makes the status mapping truly immutable: the frozen
    # dataclass already blocks attribute rebinding, but a plain dict would
    # still allow ``entry.statuses["green"] = ...`` to mutate a shipped entry.
    return MappingProxyType({
        cast(PitLossStatusKey, key): _coerce_status_baseline(value[key], key)
        for key in ("green", "vsc", "sc")
    })


def _coerce_discounts(
    value: PitLossDiscountConfiguration | Mapping[str, object] | None,
) -> PitLossDiscountConfiguration | None:
    if value is None:
        return None
    if isinstance(value, PitLossDiscountConfiguration):
        return value
    if not isinstance(value, Mapping) or set(value) != {"vscMs", "safetyCarMs"}:
        raise ValueError("discounts must contain vscMs and safetyCarMs")
    return PitLossDiscountConfiguration(
        cast(int, value["vscMs"]), cast(int, value["safetyCarMs"]),
    )


def _coerce_provenance(
    value: PitLossBaselineProvenance | Mapping[str, object],
) -> PitLossBaselineProvenance:
    if isinstance(value, PitLossBaselineProvenance):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("provenance must be PitLossBaselineProvenance or a mapping")
    return PitLossBaselineProvenance(
        source_url=cast(str, value.get("sourceUrl")),
        captured_date=cast(str, value.get("capturedDate")),
        evidence=cast(str, value.get("evidence")),
        method=cast(str, value.get("method", CURATED_BASELINE_METHOD)),
    )


def _coerce_entry(value: PitLossBaselineEntry | Mapping[str, object]) -> PitLossBaselineEntry:
    if isinstance(value, PitLossBaselineEntry):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("catalog entries must be PitLossBaselineEntry values or mappings")
    return PitLossBaselineEntry(
        track_id=cast(str, value.get("trackId")),
        track_name=cast(str, value.get("trackName")),
        statuses=cast(Mapping[PitLossStatusKey, PitLossStatusBaseline], value.get("statuses")),
        discounts=cast(
            PitLossDiscountConfiguration | Mapping[str, object] | None,
            value.get("discounts"),
        ),
        fixture_id=cast(str | None, value.get("fixtureId")),
        track_asset_id=cast(str | None, value.get("trackAssetId")),
        season=cast(int | None, value.get("season")),
    )


def _validate_unsigned_int(value: object, label: str) -> None:
    if type(value) is not int or not 0 <= value <= MAX_INT64:
        raise TypeError(f"{label} must be a non-negative signed Int64 integer")


def _validate_identifier(value: object, label: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase kebab-case identifier")


AUSTRALIA_GREEN_PROVENANCE = PitLossBaselineProvenance(
    source_url=AUSTRALIA_SOURCE_URL,
    captured_date="2026-08-04",
    evidence="Formula1.com lists Australia pit stop time loss as 19.30 seconds.",
)

AUSTRALIA_BASELINE = PitLossBaselineEntry(
    track_id=AUSTRALIA_TRACK_ID,
    track_name="Albert Park Circuit",
    fixture_id=AUSTRALIA_FIXTURE_ID,
    track_asset_id=AUSTRALIA_TRACK_ASSET_ID,
    season=2026,
    discounts=PitLossDiscountConfiguration(),
    statuses={
        "green": PitLossStatusBaseline(
            value_ms=19_300,
            source_status="direct",
            metric_definition="f1-com-lane-plus-stationary",
            provenance=AUSTRALIA_GREEN_PROVENANCE,
            evidence_count=1,
            confidence="high",
        ),
        "vsc": PitLossStatusBaseline(
            value_ms=12_300,
            source_status="derived",
            metric_definition="f1-com-lane-plus-stationary",
            provenance=PitLossBaselineProvenance(
                source_url=AUSTRALIA_SOURCE_URL,
                captured_date="2026-08-04",
                evidence=(
                    "Virtual Safety Car baseline derived from the Formula1.com "
                    "Australia Green value with a 7 000 ms discount."
                ),
            ),
            evidence_count=1,
            confidence="medium",
            derivation=PitLossDerivationRecord(
                method=DERIVE_GREEN_DISCOUNT_METHOD,
                base_status="green",
                discount_ms=7_000,
                notes="Legacy default VSC discount consistent with the Formula1.com Green value.",
            ),
        ),
        "sc": PitLossStatusBaseline(
            value_ms=9_300,
            source_status="derived",
            metric_definition="f1-com-lane-plus-stationary",
            provenance=PitLossBaselineProvenance(
                source_url=AUSTRALIA_SOURCE_URL,
                captured_date="2026-08-04",
                evidence=(
                    "Safety Car baseline derived from the Formula1.com "
                    "Australia Green value with a 10 000 ms discount."
                ),
            ),
            evidence_count=1,
            confidence="medium",
            derivation=PitLossDerivationRecord(
                method=DERIVE_GREEN_DISCOUNT_METHOD,
                base_status="green",
                discount_ms=10_000,
                notes="Legacy default SC discount consistent with the Formula1.com Green value.",
            ),
        ),
    },
)

# --- Source-backed multi-season union entries --------------------------------
#
# Each circuit below carries one stable, fixture-less entry so the same
# physical track resolves for every calendar season.  Green uses the official
# Formula1.com ``Need to Know`` pit stop time loss figure (the existing catalog
# metric family) consistently; VSC and SC use direct track-specific status
# values where the research context supports them (Canada, Monaco, Monza, and
# Baku) and bounded derived estimates otherwise.  The remaining low-evidence
# circuits (Bahrain, Jeddah, Imola, Madring, Sepang, Zandvoort, and Marina Bay)
# are defined in the next section with explicit derived/proxy estimates.


def _ms_text(value_ms: int) -> str:
    """Format a millisecond discount as ``N NNN ms`` for provenance text."""
    return f"{value_ms // 1000} {value_ms % 1000:03d} ms"


def _provenance(
    source_url: str,
    evidence: str,
    *,
    captured_date: str = "2026-08-04",
) -> PitLossBaselineProvenance:
    """Return immutable curated provenance for one status value."""
    return PitLossBaselineProvenance(
        source_url=source_url,
        captured_date=captured_date,
        evidence=evidence,
    )


def _direct_status(
    value_ms: int,
    source_url: str,
    evidence: str,
    *,
    metric_definition: PitLossMetric = "f1-com-lane-plus-stationary",
    evidence_count: int = 1,
    confidence: Confidence = "high",
    captured_date: str = "2026-08-04",
) -> PitLossStatusBaseline:
    """Return a direct source-backed status baseline with required metadata."""
    return PitLossStatusBaseline(
        value_ms=value_ms,
        source_status="direct",
        metric_definition=metric_definition,
        provenance=_provenance(source_url, evidence, captured_date=captured_date),
        evidence_count=evidence_count,
        confidence=confidence,
    )


def _derived_status(
    value_ms: int,
    green_ms: int,
    source_url: str,
    evidence: str,
    notes: str,
    *,
    evidence_count: int = 1,
    confidence: Confidence = "medium",
    captured_date: str = "2026-08-04",
) -> PitLossStatusBaseline:
    """Return a derived status baseline with an explicit Green discount record.

    ``discount_ms`` is intentionally unbounded by the legacy 5-10 second
    window; the derivation notes cite the policy or evidence that justifies it.
    """
    return PitLossStatusBaseline(
        value_ms=value_ms,
        source_status="derived",
        metric_definition="f1-com-lane-plus-stationary",
        provenance=_provenance(source_url, evidence, captured_date=captured_date),
        evidence_count=evidence_count,
        confidence=confidence,
        derivation=PitLossDerivationRecord(
            method=DERIVE_GREEN_DISCOUNT_METHOD,
            base_status="green",
            discount_ms=green_ms - value_ms,
            notes=notes,
        ),
    )


def _estimated_green_status(
    value_ms: int,
    source_url: str,
    evidence: str,
    notes: str,
    *,
    source_status: StatusSourceKind,
    method: str,
    evidence_count: int,
    confidence: Confidence,
    captured_date: str = "2026-08-04",
) -> PitLossStatusBaseline:
    """Return a derived or proxy Green estimate with an explicit derivation.

    ``discount_ms`` is intentionally absent: the estimate is not produced by
    subtracting a discount from a measured baseline, so the derivation record
    carries the policy method and notes instead.
    """
    return PitLossStatusBaseline(
        value_ms=value_ms,
        source_status=source_status,
        metric_definition="f1-com-lane-plus-stationary",
        provenance=_provenance(source_url, evidence, captured_date=captured_date),
        evidence_count=evidence_count,
        confidence=confidence,
        derivation=PitLossDerivationRecord(
            method=method,
            base_status="green",
            notes=notes,
        ),
    )


def _f1_com_entry(
    track_id: str,
    track_name: str,
    green_ms: int,
    vsc_ms: int,
    sc_ms: int,
    source_url: str,
    *,
    green_evidence: str,
    green_evidence_count: int = 1,
    green_confidence: Confidence = "high",
    vsc_notes: str | None = None,
    sc_notes: str | None = None,
) -> PitLossBaselineEntry:
    """Build a fixture-less entry from one official F1.com source.

    Green is direct under the ``f1-com-lane-plus-stationary`` metric; VSC and
    SC are derived from Green with the bounded proportional policy from the
    research context.  The entry is deliberately fixture-less so the same
    physical circuit resolves for every calendar season.
    """
    vsc_notes = vsc_notes or (
        f"Proportional VSC policy (approximately 0.55-0.70 x Green) applied "
        f"to the Formula1.com {track_name} Green value."
    )
    sc_notes = sc_notes or (
        f"Proportional SC policy (approximately 0.45-0.55 x Green) applied "
        f"to the Formula1.com {track_name} Green value."
    )
    return PitLossBaselineEntry(
        track_id=track_id,
        track_name=track_name,
        statuses={
            "green": _direct_status(
                green_ms,
                source_url,
                green_evidence,
                evidence_count=green_evidence_count,
                confidence=green_confidence,
            ),
            "vsc": _derived_status(
                vsc_ms,
                green_ms,
                source_url,
                (
                    f"Virtual Safety Car baseline derived from the Formula1.com "
                    f"{track_name} Green value with a "
                    f"{_ms_text(green_ms - vsc_ms)} discount."
                ),
                vsc_notes,
            ),
            "sc": _derived_status(
                sc_ms,
                green_ms,
                source_url,
                (
                    f"Safety Car baseline derived from the Formula1.com "
                    f"{track_name} Green value with a "
                    f"{_ms_text(green_ms - sc_ms)} discount."
                ),
                sc_notes,
            ),
        },
    )


SHANGHAI_BASELINE = _f1_com_entry(
    "shanghai", "Shanghai International Circuit", 23_670, 14_500, 13_500,
    CHINA_SOURCE_URL,
    green_evidence="Formula1.com lists China pit stop time loss as 23.67 seconds.",
)

SUZUKA_BASELINE = _f1_com_entry(
    "suzuka", "Suzuka International Racing Course", 23_750, 14_000, 13_000,
    JAPAN_SOURCE_URL,
    green_evidence=(
        "Formula1.com lists Japan pit stop time loss as 23.75 seconds; "
        "Pirelli values agree within the definition spread."
    ),
    green_evidence_count=2,
)

MIAMI_BASELINE = _f1_com_entry(
    "miami", "Miami International Autodrome", 18_760, 12_000, 11_000,
    MIAMI_SOURCE_URL,
    green_evidence=(
        "Formula1.com lists Miami pit stop time loss as 18.76 seconds; the F1 "
        "Chronicle measured total cost of 19.7 s uses a different metric, so "
        "the official F1.com value is used for catalog consistency."
    ),
    green_evidence_count=2,
    green_confidence="medium",
)

BARCELONA_BASELINE = _f1_com_entry(
    "barcelona-catalunya", "Circuit de Barcelona-Catalunya", 22_960, 14_000, 13_000,
    BARCELONA_SOURCE_URL,
    green_evidence=(
        "Formula1.com lists Barcelona-Catalunya pit stop time loss as 22.96 "
        "seconds; the F1 Chronicle measured total cost of 23.8 s uses a "
        "different metric, so the official F1.com value is used for catalog "
        "consistency."
    ),
    green_evidence_count=2,
    green_confidence="medium",
)

RED_BULL_RING_BASELINE = _f1_com_entry(
    "red-bull-ring", "Red Bull Ring", 20_020, 13_000, 12_000,
    AUSTRIA_SOURCE_URL,
    green_evidence=(
        "Formula1.com lists Austria pit stop time loss as 20.02 seconds; the F1 "
        "Chronicle measured total cost of 21.5 s uses a different metric, so "
        "the official F1.com value is used for catalog consistency."
    ),
    green_evidence_count=2,
    green_confidence="medium",
)

SILVERSTONE_BASELINE = _f1_com_entry(
    "silverstone", "Silverstone Circuit", 20_000, 12_500, 11_500,
    BRITAIN_SOURCE_URL,
    green_evidence=(
        "Formula1.com lists Britain pit stop time loss as 20.0 seconds; the F1 "
        "Chronicle measured total cost of 20.9 s uses a different metric, so "
        "the official F1.com value is used for catalog consistency."
    ),
    green_evidence_count=2,
    green_confidence="medium",
)

SPA_BASELINE = _f1_com_entry(
    "spa-francorchamps", "Circuit de Spa-Francorchamps", 18_500, 11_000, 10_000,
    BELGIUM_SOURCE_URL,
    green_evidence=(
        "Formula1.com lists Belgium pit stop time loss as 18.5 seconds; the F1 "
        "Chronicle era measured value of 18.4 s uses a different metric, so "
        "the official F1.com value is used for catalog consistency."
    ),
    green_evidence_count=2,
    green_confidence="medium",
)

HUNGARORING_BASELINE = _f1_com_entry(
    "hungaroring", "Hungaroring", 20_560, 13_000, 12_000,
    HUNGARY_SOURCE_URL,
    green_evidence=(
        "Formula1.com lists Hungary pit stop time loss as 20.56 seconds; the F1 "
        "Chronicle measured total cost of 22.0 s uses a different metric, so "
        "the official F1.com value is used for catalog consistency."
    ),
    green_evidence_count=2,
    green_confidence="medium",
)

MONZA_BASELINE = PitLossBaselineEntry(
    track_id="monza",
    track_name="Autodromo Nazionale Monza",
    statuses={
        "green": _direct_status(
            24_300, MONZA_SOURCE_URL,
            "Formula1.com lists Italy pit stop time loss as 24.3 seconds.",
        ),
        "vsc": _derived_status(
            14_000,
            24_300,
            MONZA_SOURCE_URL,
            (
                "Virtual Safety Car baseline derived from the Formula1.com Italy "
                "Green value with a 10 300 ms discount."
            ),
            (
                "Proportional VSC policy (approximately 0.55-0.70 x Green) "
                "applied to the Formula1.com Italy Green value."
            ),
        ),
        "sc": _direct_status(
            13_000, MONZA_STRATEGY_GUIDE_SOURCE_URL,
            (
                "The Formula1.com strategy guide reports approximately 12-13 s "
                "loss under SC at Monza (about a 9 s reduction); the catalog "
                "uses the rounded 13.0 s value."
            ),
            confidence="medium",
        ),
    },
)

BAKU_BASELINE = PitLossBaselineEntry(
    track_id="baku",
    track_name="Baku City Circuit",
    statuses={
        "green": _direct_status(
            19_700, BAKU_SOURCE_URL,
            "Formula1.com lists Azerbaijan pit stop time loss as 19.7 seconds.",
        ),
        "vsc": _derived_status(
            9_500,
            19_700,
            BAKU_SOURCE_URL,
            (
                "Virtual Safety Car baseline for Baku set near the direct SC "
                "value with a 10 200 ms Green discount."
            ),
            (
                "Baku VSC is close to the direct SC value because the "
                "neutralised-stop regime dominates; the discount exceeds the "
                "legacy window with provenance support."
            ),
        ),
        "sc": _direct_status(
            8_500, BAKU_SOURCE_URL,
            (
                "A neutralised-stop observation at Baku reports approximately "
                "8.5 s loss, implying an 11 200 ms Green discount."
            ),
            metric_definition="measured-total-cost",
            confidence="medium",
        ),
    },
)

COTA_BASELINE = PitLossBaselineEntry(
    track_id="cota",
    track_name="Circuit of the Americas",
    statuses={
        "green": _direct_status(
            20_600, COTA_SOURCE_URL,
            "Formula1.com lists United States pit stop time loss as 20.6 seconds.",
        ),
        "vsc": _derived_status(
            15_000,
            20_600,
            COTA_SOURCE_URL,
            (
                "Virtual Safety Car baseline derived from the Formula1.com COTA "
                "Green value with a 5 600 ms discount."
            ),
            (
                "COTA neutralised stops save approximately 6 s rather than the "
                "legacy 10 s; the derived policy respects the secondary evidence."
            ),
        ),
        "sc": _derived_status(
            14_600,
            20_600,
            COTA_TECHNICAL_SOURCE_URL,
            (
                "Secondary technical analysis reports approximately 6 s saving "
                "under SC at COTA, giving 14.6 s from the Formula1.com Green value."
            ),
            (
                "COTA SC derived from the approximately 6 s saving evidence, "
                "not the legacy 10 s discount."
            ),
        ),
    },
)

MEXICO_CITY_BASELINE = _f1_com_entry(
    "mexico-city", "Autodromo Hermanos Rodriguez", 21_900, 13_000, 12_000,
    MEXICO_SOURCE_URL,
    green_evidence="Formula1.com lists Mexico City pit stop time loss as 21.9 seconds.",
)

INTERLAGOS_BASELINE = _f1_com_entry(
    "interlagos", "Autodromo Jose Carlos Pace", 20_800, 12_000, 11_000,
    BRAZIL_SOURCE_URL,
    green_evidence="Formula1.com lists Sao Paulo pit stop time loss as 20.8 seconds.",
)

LAS_VEGAS_BASELINE = _f1_com_entry(
    "las-vegas", "Las Vegas Strip Circuit", 20_000, 12_000, 11_000,
    LAS_VEGAS_SOURCE_URL,
    green_evidence="Formula1.com lists Las Vegas pit stop time loss as 20.0 seconds.",
)

LUSAIL_BASELINE = PitLossBaselineEntry(
    track_id="lusail",
    track_name="Lusail International Circuit",
    statuses={
        "green": _direct_status(
            26_300, QATAR_SOURCE_URL,
            "Formula1.com lists Qatar pit stop time loss as 26.3 seconds.",
        ),
        "vsc": _derived_status(
            18_000,
            26_300,
            QATAR_SOURCE_URL,
            (
                "Virtual Safety Car baseline derived from the Formula1.com Qatar "
                "Green value with a 8 300 ms discount."
            ),
            (
                "Proportional VSC policy (approximately 0.55-0.70 x Green) "
                "applied to the Formula1.com Qatar Green value."
            ),
        ),
        "sc": _derived_status(
            17_000,
            26_300,
            PIRELLI_QATAR_SOURCE_URL,
            (
                "Pirelli press material reports that pitting under SC at Qatar "
                "carries roughly a 10 s advantage versus Green, giving 17.0 s "
                "from the Formula1.com Green value."
            ),
            (
                "Qatar SC derived from the Pirelli approximately 10 s SC "
                "advantage evidence."
            ),
        ),
    },
)

YAS_MARINA_BASELINE = _f1_com_entry(
    "yas-marina", "Yas Marina Circuit", 21_000, 12_500, 12_000,
    ABU_DHABI_SOURCE_URL,
    green_evidence="Formula1.com lists Abu Dhabi pit stop time loss as 21.0 seconds.",
)

MONACO_BASELINE = PitLossBaselineEntry(
    track_id="monaco",
    track_name="Circuit de Monaco",
    statuses={
        "green": _direct_status(
            19_920, MONACO_SOURCE_URL,
            "Formula1.com lists Monaco pit stop time loss as 19.92 seconds.",
        ),
        "vsc": _direct_status(
            12_500, MONACO_SOURCE_URL,
            (
                "Pirelli/F1 race-day status data report approximately 12.5 s "
                "loss under VSC at Monaco."
            ),
            metric_definition="measured-total-cost",
            confidence="medium",
        ),
        "sc": _direct_status(
            12_500, MONACO_SOURCE_URL,
            (
                "Pirelli/F1 race-day status data report approximately 12.5 s "
                "loss under SC at Monaco."
            ),
            metric_definition="measured-total-cost",
            confidence="medium",
        ),
    },
)

MONTREAL_BASELINE = PitLossBaselineEntry(
    track_id="montreal",
    track_name="Circuit Gilles Villeneuve",
    statuses={
        "green": _direct_status(
            18_250, CANADA_SOURCE_URL,
            (
                "Formula1.com lists Canada pit stop time loss as 18.25 seconds; "
                "the Pirelli race-day Green figure of 18.4 s is documented as "
                "an alternative measurement."
            ),
            evidence_count=2,
        ),
        "vsc": _direct_status(
            9_500, CANADA_PIRELLI_SOURCE_URL,
            (
                "Pirelli race-day status data report approximately 9.5 s loss "
                "under SC/VSC at Canada."
            ),
            metric_definition="measured-total-cost",
            confidence="medium",
        ),
        "sc": _direct_status(
            9_500, CANADA_PIRELLI_SOURCE_URL,
            (
                "Pirelli race-day status data report approximately 9.5 s loss "
                "under SC/VSC at Canada."
            ),
            metric_definition="measured-total-cost",
            confidence="medium",
        ),
    },
)

# --- Low-evidence multi-season union entries ---------------------------------
#
# Bahrain, Jeddah, Imola, Madring, Sepang, Zandvoort, and Marina Bay complete
# the 26-circuit union.  They have no direct Formula1.com pit-loss baseline in
# the research context, so every status is derived or proxy with low or medium
# confidence, an explicit derivation record, and full provenance.  Game-balance
# values (F1 Manager) are rejected; FIA stop-duration summaries are treated as
# magnitude references only, never as a pit-loss baseline.


BAHRAIN_BASELINE = PitLossBaselineEntry(
    track_id="bahrain",
    track_name="Bahrain International Circuit",
    statuses={
        "green": _estimated_green_status(
            22_900,
            F1_CHRONICLE_SOURCE_URL,
            (
                "No direct Formula1.com pit-loss baseline is published for "
                "Bahrain in the research context; the 22.9 s Green value is a "
                "proxy estimate of the lane-plus-stationary family calibrated "
                "against comparable high pit-loss circuits (F1 Chronicle "
                "dataset). F1 Manager game values are rejected as production "
                "evidence."
            ),
            (
                "Proxy estimate for Sakhir; no source-backed direct value "
                "exists in the research context, so the value is calibrated "
                "from comparable high pit-loss circuits under the catalog "
                "lane-plus-stationary metric."
            ),
            source_status="proxy",
            method=COMPARABLE_CIRCUIT_PROXY_METHOD,
            evidence_count=1,
            confidence="low",
        ),
        "vsc": _derived_status(
            14_000,
            22_900,
            F1_CHRONICLE_SOURCE_URL,
            (
                "Virtual Safety Car baseline derived from the Bahrain Green "
                f"proxy with a {_ms_text(22_900 - 14_000)} discount."
            ),
            (
                "Proportional VSC policy (approximately 0.55-0.70 x Green) "
                "applied to the Bahrain Green proxy."
            ),
            confidence="low",
        ),
        "sc": _derived_status(
            13_000,
            22_900,
            F1_CHRONICLE_SOURCE_URL,
            (
                "Safety Car baseline derived from the Bahrain Green proxy "
                f"with a {_ms_text(22_900 - 13_000)} discount."
            ),
            (
                "Proportional SC policy (approximately 0.45-0.55 x Green) "
                "applied to the Bahrain Green proxy."
            ),
            confidence="low",
        ),
    },
)

JEDDAH_BASELINE = PitLossBaselineEntry(
    track_id="jeddah",
    track_name="Jeddah Corniche Circuit",
    statuses={
        "green": _estimated_green_status(
            19_000,
            F1_CHRONICLE_SOURCE_URL,
            (
                "No direct Formula1.com pit-loss baseline is published for "
                "Jeddah in the research context; the 19.0 s Green value is a "
                "proxy estimate of the lane-plus-stationary family calibrated "
                "against comparable fast street circuits (F1 Chronicle "
                "dataset). F1 Manager game values are rejected as production "
                "evidence."
            ),
            (
                "Proxy estimate for a fast street circuit with a "
                "medium-length pit lane; no source-backed direct value exists "
                "in the research context."
            ),
            source_status="proxy",
            method=COMPARABLE_CIRCUIT_PROXY_METHOD,
            evidence_count=1,
            confidence="low",
        ),
        "vsc": _derived_status(
            12_000,
            19_000,
            F1_CHRONICLE_SOURCE_URL,
            (
                "Virtual Safety Car baseline derived from the Jeddah Green "
                f"proxy with a {_ms_text(19_000 - 12_000)} discount."
            ),
            (
                "Proportional VSC policy (approximately 0.55-0.70 x Green) "
                "applied to the Jeddah Green proxy."
            ),
            confidence="low",
        ),
        "sc": _derived_status(
            10_000,
            19_000,
            F1_CHRONICLE_SOURCE_URL,
            (
                "Safety Car baseline derived from the Jeddah Green proxy "
                f"with a {_ms_text(19_000 - 10_000)} discount."
            ),
            (
                "Proportional SC policy (approximately 0.45-0.55 x Green) "
                "applied to the Jeddah Green proxy."
            ),
            confidence="low",
        ),
    },
)

IMOLA_BASELINE = PitLossBaselineEntry(
    track_id="imola",
    track_name="Autodromo Enzo e Dino Ferrari",
    statuses={
        "green": _estimated_green_status(
            28_100,
            F1_CHRONICLE_SOURCE_URL,
            (
                "No direct Formula1.com pit-loss baseline is published for "
                "Imola in the research context; the 28.1 s Green value is a "
                "proxy estimate of the lane-plus-stationary family reflecting "
                "Imola's long pit lane and slow pit entry, calibrated against "
                "comparable measured circuits (F1 Chronicle dataset). F1 "
                "Manager game values are rejected as production evidence."
            ),
            (
                "Proxy estimate; Imola's narrow layout and long pit lane "
                "place it among the highest-loss circuits in the union."
            ),
            source_status="proxy",
            method=COMPARABLE_CIRCUIT_PROXY_METHOD,
            evidence_count=1,
            confidence="low",
        ),
        "vsc": _derived_status(
            18_000,
            28_100,
            F1_CHRONICLE_SOURCE_URL,
            (
                "Virtual Safety Car baseline derived from the Imola Green "
                f"proxy with a {_ms_text(28_100 - 18_000)} discount."
            ),
            (
                "Proportional VSC policy (approximately 0.55-0.70 x Green) "
                "applied to the Imola Green proxy; the discount exceeds the "
                "legacy 10 s window and reflects the high-loss circuit profile."
            ),
            confidence="low",
        ),
        "sc": _derived_status(
            16_000,
            28_100,
            F1_CHRONICLE_SOURCE_URL,
            (
                "Safety Car baseline derived from the Imola Green proxy "
                f"with a {_ms_text(28_100 - 16_000)} discount."
            ),
            (
                "SC baseline derived just outside the nominal 0.45-0.55 x "
                "Green proportional band; the discount exceeds the legacy "
                "10 s window and is consistent with the high-loss circuit "
                "profile."
            ),
            confidence="low",
        ),
    },
)

MADRING_BASELINE = PitLossBaselineEntry(
    track_id="madring",
    track_name="Madrid Circuit (Madring)",
    statuses={
        "green": _estimated_green_status(
            19_000,
            F1_CHRONICLE_SOURCE_URL,
            (
                "Madring is a new 2026 circuit with no published pit-loss "
                "baseline; the research context notes that a Jeddah analogy "
                "is insufficient for a direct transfer. The 19.0 s Green "
                "value is a provisional proxy estimate of the "
                "lane-plus-stationary family based on comparable fast "
                "hybrid/street circuits."
            ),
            (
                "Provisional proxy for a brand-new venue with zero direct "
                "evidence; the Jeddah analogy is used only as a rough "
                "starting point and is documented as insufficient for a "
                "direct transfer."
            ),
            source_status="proxy",
            method=COMPARABLE_CIRCUIT_PROXY_METHOD,
            evidence_count=0,
            confidence="low",
        ),
        "vsc": _derived_status(
            12_000,
            19_000,
            F1_CHRONICLE_SOURCE_URL,
            (
                "Virtual Safety Car baseline derived from the provisional "
                f"Madring Green proxy with a {_ms_text(19_000 - 12_000)} "
                "discount."
            ),
            (
                "Proportional VSC policy applied to the provisional Madring "
                "Green proxy; placeholder until the 2026 race provides "
                "measurements."
            ),
            evidence_count=0,
            confidence="low",
        ),
        "sc": _derived_status(
            10_000,
            19_000,
            F1_CHRONICLE_SOURCE_URL,
            (
                "Safety Car baseline derived from the provisional Madring "
                f"Green proxy with a {_ms_text(19_000 - 10_000)} discount."
            ),
            (
                "Proportional SC policy applied to the provisional Madring "
                "Green proxy; placeholder until the 2026 race provides "
                "measurements."
            ),
            evidence_count=0,
            confidence="low",
        ),
    },
)

SEPANG_BASELINE = PitLossBaselineEntry(
    track_id="sepang",
    track_name="Sepang International Circuit",
    statuses={
        "green": _estimated_green_status(
            24_000,
            SEPANG_PIT_STOP_SUMMARY_SOURCE_URL,
            (
                "The 2017 Malaysian Grand Prix pit-stop summary lists only "
                "raw stop durations, which are not a comparable pit-loss "
                "metric and are used here as a rough magnitude reference "
                "only. The 24.0 s Green value is a proxy estimate of the "
                "lane-plus-stationary family reflecting Sepang's long pit "
                "lane."
            ),
            (
                "Proxy estimate; FIA stop-duration summaries are explicitly "
                "not treated as a loss baseline because the metric family is "
                "incompatible. The value is calibrated against comparable "
                "high pit-loss circuits and the proportional policy."
            ),
            source_status="proxy",
            method=COMPARABLE_CIRCUIT_PROXY_METHOD,
            evidence_count=1,
            confidence="low",
        ),
        "vsc": _derived_status(
            15_000,
            24_000,
            SEPANG_PIT_STOP_SUMMARY_SOURCE_URL,
            (
                "Virtual Safety Car baseline derived from the Sepang Green "
                f"proxy with a {_ms_text(24_000 - 15_000)} discount."
            ),
            (
                "Proportional VSC policy (approximately 0.55-0.70 x Green) "
                "applied to the Sepang Green proxy."
            ),
            confidence="low",
        ),
        "sc": _derived_status(
            13_000,
            24_000,
            SEPANG_PIT_STOP_SUMMARY_SOURCE_URL,
            (
                "Safety Car baseline derived from the Sepang Green proxy "
                f"with a {_ms_text(24_000 - 13_000)} discount."
            ),
            (
                "Proportional SC policy (approximately 0.45-0.55 x Green) "
                "applied to the Sepang Green proxy; the 11 000 ms discount "
                "exceeds the legacy 10 s window."
            ),
            confidence="low",
        ),
    },
)

ZANDVOORT_BASELINE = PitLossBaselineEntry(
    track_id="zandvoort",
    track_name="Circuit Zandvoort",
    statuses={
        "green": _estimated_green_status(
            19_500,
            ZANDVOORT_SPEED_LIMIT_ANALYSIS_SOURCE_URL,
            (
                "The official 2025 Formula1.com figure (approximately 23 s) "
                "conflicts with the 60 to 80 km/h pit-lane speed change; the "
                "speed-limit analysis in the research context supports a "
                "derived Green of approximately 19-21 s. The catalog uses "
                "19.5 s from that derived range."
            ),
            (
                "Derived Green; the conflicting official figure was adjusted "
                "down for the 60 to 80 km/h pit-lane speed limit increase."
            ),
            source_status="derived",
            method=SOURCE_ADJUSTED_GREEN_METHOD,
            evidence_count=2,
            confidence="medium",
        ),
        "vsc": _derived_status(
            11_500,
            19_500,
            ZANDVOORT_SPEED_LIMIT_ANALYSIS_SOURCE_URL,
            (
                "Virtual Safety Car baseline derived from the Zandvoort "
                f"Green value with a {_ms_text(19_500 - 11_500)} discount."
            ),
            (
                "Proportional VSC policy (approximately 0.55-0.70 x Green) "
                "applied to the derived Zandvoort Green value."
            ),
            confidence="medium",
        ),
        "sc": _derived_status(
            10_500,
            19_500,
            ZANDVOORT_SPEED_LIMIT_ANALYSIS_SOURCE_URL,
            (
                "Safety Car baseline derived from the Zandvoort Green value "
                f"with a {_ms_text(19_500 - 10_500)} discount."
            ),
            (
                "Proportional SC policy (approximately 0.45-0.55 x Green) "
                "applied to the derived Zandvoort Green value."
            ),
            confidence="medium",
        ),
    },
)

MARINA_BAY_BASELINE = PitLossBaselineEntry(
    track_id="marina-bay",
    track_name="Marina Bay Street Circuit",
    statuses={
        "green": _estimated_green_status(
            24_000,
            SINGAPORE_SOURCE_URL,
            (
                "The old Formula1.com Singapore pit-loss figure "
                "(approximately 29.1 s) is stale after the 60 to 80 km/h "
                "pit-lane speed change; the research context supports an "
                "adjusted Green of approximately 23-24 s. The catalog uses "
                "24.0 s."
            ),
            (
                "Derived Green; the stale official figure was adjusted down "
                "for the 60 to 80 km/h pit-lane speed limit increase."
            ),
            source_status="derived",
            method=SOURCE_ADJUSTED_GREEN_METHOD,
            evidence_count=2,
            confidence="medium",
        ),
        "vsc": _derived_status(
            14_000,
            24_000,
            SINGAPORE_SOURCE_URL,
            (
                "Virtual Safety Car baseline derived from the adjusted "
                f"Marina Bay Green value with a {_ms_text(24_000 - 14_000)} "
                "discount."
            ),
            (
                "Proportional VSC policy (approximately 0.55-0.70 x Green) "
                "applied to the adjusted Marina Bay Green value."
            ),
            confidence="medium",
        ),
        "sc": _derived_status(
            13_000,
            24_000,
            SINGAPORE_SOURCE_URL,
            (
                "Safety Car baseline derived from the adjusted Marina Bay "
                f"Green value with a {_ms_text(24_000 - 13_000)} discount."
            ),
            (
                "Proportional SC policy (approximately 0.45-0.55 x Green) "
                "applied to the adjusted Marina Bay Green value; the "
                "11 000 ms discount exceeds the legacy 10 s window."
            ),
            confidence="medium",
        ),
    },
)

PIT_LOSS_BASELINE_CATALOG = PitLossBaselineCatalog(
    catalog_version=CATALOG_VERSION,
    entries=(
        AUSTRALIA_BASELINE,
        BAHRAIN_BASELINE,
        BAKU_BASELINE,
        BARCELONA_BASELINE,
        COTA_BASELINE,
        HUNGARORING_BASELINE,
        IMOLA_BASELINE,
        INTERLAGOS_BASELINE,
        JEDDAH_BASELINE,
        LAS_VEGAS_BASELINE,
        LUSAIL_BASELINE,
        MADRING_BASELINE,
        MARINA_BAY_BASELINE,
        MEXICO_CITY_BASELINE,
        MIAMI_BASELINE,
        MONACO_BASELINE,
        MONTREAL_BASELINE,
        MONZA_BASELINE,
        RED_BULL_RING_BASELINE,
        SEPANG_BASELINE,
        SHANGHAI_BASELINE,
        SILVERSTONE_BASELINE,
        SPA_BASELINE,
        SUZUKA_BASELINE,
        YAS_MARINA_BASELINE,
        ZANDVOORT_BASELINE,
    ),
)
BASELINE_CATALOG = PIT_LOSS_BASELINE_CATALOG
DEFAULT_PIT_LOSS_BASELINE_CATALOG = PIT_LOSS_BASELINE_CATALOG
# The production default catalog is validated at construction: every entry must
# bind one registered physical circuit through fixture/track/asset identities
# and any explicit season must belong to that circuit.  Synthetic/legacy test
# catalogs are unaffected because plain ``PitLossBaselineCatalog`` construction
# does not run identity validation.
validate_catalog_identities(PIT_LOSS_BASELINE_CATALOG)


__all__ = [
    "ABU_DHABI_SOURCE_URL",
    "AUSTRALIA_BASELINE",
    "AUSTRALIA_FIXTURE_ID",
    "AUSTRALIA_GREEN_PROVENANCE",
    "AUSTRALIA_SOURCE_URL",
    "AUSTRALIA_TRACK_ASSET_ID",
    "AUSTRALIA_TRACK_ID",
    "AUSTRIA_SOURCE_URL",
    "BAHRAIN_BASELINE",
    "BAKU_BASELINE",
    "BAKU_SOURCE_URL",
    "BARCELONA_BASELINE",
    "BARCELONA_SOURCE_URL",
    "BASELINE_CATALOG",
    "BELGIUM_SOURCE_URL",
    "BRAZIL_SOURCE_URL",
    "BRITAIN_SOURCE_URL",
    "CANADA_PIRELLI_SOURCE_URL",
    "CANADA_SOURCE_URL",
    "CATALOG_SCHEMA_ID",
    "CATALOG_VERSION",
    "CHINA_SOURCE_URL",
    "COMPARABLE_CIRCUIT_PROXY_METHOD",
    "Confidence",
    "COTA_BASELINE",
    "COTA_SOURCE_URL",
    "COTA_TECHNICAL_SOURCE_URL",
    "CURATED_BASELINE_METHOD",
    "DEFAULT_PIT_LOSS_BASELINE_CATALOG",
    "DEFAULT_SC_DISCOUNT_MS",
    "DEFAULT_SAFETY_CAR_DISCOUNT_MS",
    "DEFAULT_VSC_DISCOUNT_MS",
    "DERIVE_GREEN_DISCOUNT_METHOD",
    "F1_CHRONICLE_SOURCE_URL",
    "HUNGARORING_BASELINE",
    "HUNGARY_SOURCE_URL",
    "IDENTITY_SCHEMA_ID",
    "IMOLA_BASELINE",
    "INTERLAGOS_BASELINE",
    "JAPAN_SOURCE_URL",
    "JEDDAH_BASELINE",
    "LAS_VEGAS_BASELINE",
    "LAS_VEGAS_SOURCE_URL",
    "LUSAIL_BASELINE",
    "MADRING_BASELINE",
    "MARINA_BAY_BASELINE",
    "MAX_DISCOUNT_MS",
    "MAX_INT64",
    "MEXICO_CITY_BASELINE",
    "MEXICO_SOURCE_URL",
    "MIAMI_BASELINE",
    "MIAMI_SOURCE_URL",
    "MIN_DISCOUNT_MS",
    "MONACO_BASELINE",
    "MONACO_SOURCE_URL",
    "MONTREAL_BASELINE",
    "MONZA_BASELINE",
    "MONZA_SOURCE_URL",
    "MONZA_STRATEGY_GUIDE_SOURCE_URL",
    "PIT_LOSS_BASELINE_CATALOG",
    "PIT_LOSS_TRACK_IDENTITIES",
    "PIT_LOSS_TRACK_IDENTITY_REGISTRY",
    "PIRELLI_QATAR_SOURCE_URL",
    "PitLossBaselineCatalog",
    "PitLossBaselineEntry",
    "PitLossBaselineProvenance",
    "PitLossDerivationRecord",
    "PitLossDiscountConfiguration",
    "PitLossMetric",
    "PitLossStatusBaseline",
    "PitLossStatusKey",
    "PitLossTrackIdentity",
    "QATAR_SOURCE_URL",
    "RED_BULL_RING_BASELINE",
    "SEPANG_BASELINE",
    "SEPANG_PIT_STOP_SUMMARY_SOURCE_URL",
    "SHANGHAI_BASELINE",
    "SILVERSTONE_BASELINE",
    "SINGAPORE_SOURCE_URL",
    "SOURCE_ADJUSTED_GREEN_METHOD",
    "SPA_BASELINE",
    "StatusSourceKind",
    "SUZUKA_BASELINE",
    "TrackIdentityLookupError",
    "YAS_MARINA_BASELINE",
    "ZANDVOORT_BASELINE",
    "ZANDVOORT_SPEED_LIMIT_ANALYSIS_SOURCE_URL",
    "canonicalize_identity_key",
    "catalog_entry_identity_binding",
    "normalize_identity_key",
    "resolve_binding_identity",
    "resolve_catalog_entry_identity",
    "resolve_track_identity",
    "resolve_track_identity_or_none",
    "validate_baseline_catalog",
    "validate_catalog",
    "validate_catalog_identities",
]
