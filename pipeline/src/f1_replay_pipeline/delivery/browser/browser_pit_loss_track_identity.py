"""Stable physical-circuit identity for the 2024-2026 pit-loss catalog union.

The curated pit-loss catalog is per physical circuit, not per race fixture: the
same circuit that appears in 2024, 2025, and 2026 reuses one stable
``track_id``.  This module defines that deterministic identity layer:

* one :class:`PitLossTrackIdentity` per physical circuit in the 2024/2025/2026
  calendar union (26 circuits);
* a repository-local, immutable alias registry that maps every plausible
  fixture name, track-asset name, location, and circuit alias to exactly one
  identity;
* deterministic lookup helpers that reject empty, malformed, unknown, and
  ambiguous bindings instead of guessing.

The registry is data-only and fully offline.  It performs no network access,
never reads canonical Parquet, and never changes browser chunk shapes.  It
also contains no Green/VSC/SC baseline values: this module exists so later
catalog subtasks can bind one stable entry per physical circuit.

Distinct-venue rules required by the catalog:

* ``bahrain`` (Sakhir) and ``sepang`` are separate physical circuits;
* ``barcelona-catalunya`` and ``madring`` (Madrid) are separate physical
  circuits.  The shared ``spanish-grand-prix`` / ``spain-grand-prix`` names are
  ambiguous across the 2024/2025 (Barcelona) and 2026 (Madrid) calendars, so
  those bare names are deliberately not registered; only the season-qualified
  forms (``2024-spanish-grand-prix`` vs ``2026-spanish-grand-prix``) resolve.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
import unicodedata
from types import MappingProxyType

_IDENTIFIER = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TOKEN = re.compile(r"[a-z0-9]+")
_ROUND_SEGMENT = re.compile(r"-round-\d+")
# Track-asset identifiers are deterministic: ``{fixture}-telemetry-layout-v1``.
_ASSET_SUFFIX = "-telemetry-layout-v1"


def _validate_identifier(value: object, label: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase kebab-case identifier")

IDENTITY_SCHEMA_ID = (
    "urn:f1-cache-replay:schema:replay-data:v1:pit-loss-track-identity"
)
IDENTITY_UNION_VERSION = "2024-2026-v1"
SUPPORTED_SEASONS = (2024, 2025, 2026)
_SUPPORTED_SEASONS = frozenset(SUPPORTED_SEASONS)
# Number of unique physical circuits in the 2024/2025/2026 calendar union.
UNION_CIRCUIT_COUNT = 26


class TrackIdentityLookupError(ValueError):
    """Raised when a track identity cannot be resolved deterministically.

    ``ValueError`` keeps fail-closed callers (the catalog, the resolver, and
    publication) compatible while the specific type remains testable.
    """


@dataclass(frozen=True, slots=True)
class PitLossTrackIdentity:
    """One stable physical circuit reused across calendar seasons.

    ``seasons`` records which calendars contain the circuit; ``names`` holds
    grand-prix/event name tokens (already normalized kebab-case) used to derive
    deterministic fixture and track-asset aliases.  No pit-loss baseline value
    lives here: values belong to the catalog, keyed by ``track_id``.
    """

    track_id: str
    track_name: str
    seasons: frozenset[int]
    names: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_identifier(self.track_id, "track_id")
        if not isinstance(self.track_name, str) or not self.track_name.strip():
            raise ValueError("track_name must be a non-empty string")
        seasons = frozenset(self.seasons)
        if not seasons or not seasons.issubset(_SUPPORTED_SEASONS):
            raise ValueError("seasons must be a non-empty subset of 2024, 2025, and 2026")
        names = tuple(dict.fromkeys(self.names))
        if not names or any(_IDENTIFIER.fullmatch(name) is None for name in names):
            raise ValueError("names must be non-empty normalized kebab-case tokens")
        object.__setattr__(self, "seasons", seasons)
        object.__setattr__(self, "names", names)

    @property
    def aliases(self) -> tuple[str, ...]:
        """Return the deterministic registry keys registered for this identity."""
        return _alias_keys_for(self, _AMBIGUOUS_BARE_NAMES)

    def as_dict(self) -> dict[str, object]:
        return {
            "trackId": self.track_id,
            "trackName": self.track_name,
            "seasons": sorted(self.seasons),
            "names": list(self.names),
        }


# Declarative union table.  The same physical circuit that appears in multiple
# seasons is declared once; ``names`` is a curated alias set for real fixture
# names, FastF1-style locations, and circuit names.  ``seasons`` restricts the
# derived ``{season}-{name}`` fixture aliases so a fixture cannot resolve to a
# circuit that did not race in that season.
_TRACK_IDENTITIES: tuple[PitLossTrackIdentity, ...] = (
    PitLossTrackIdentity(
        "australia", "Albert Park Circuit", frozenset({2024, 2025, 2026}),
        ("australian-grand-prix", "albert-park-circuit", "albert-park", "melbourne"),
    ),
    PitLossTrackIdentity(
        "bahrain", "Bahrain International Circuit", frozenset({2024, 2025, 2026}),
        ("bahrain-grand-prix", "bahrain-international-circuit", "sakhir"),
    ),
    PitLossTrackIdentity(
        "jeddah", "Jeddah Corniche Circuit", frozenset({2024, 2025, 2026}),
        ("saudi-arabian-grand-prix", "saudi-arabia-grand-prix", "jeddah-corniche-circuit", "jeddah"),
    ),
    PitLossTrackIdentity(
        "shanghai", "Shanghai International Circuit", frozenset({2024, 2025, 2026}),
        ("chinese-grand-prix", "china-grand-prix", "shanghai-international-circuit", "shanghai"),
    ),
    PitLossTrackIdentity(
        "suzuka", "Suzuka International Racing Course", frozenset({2024, 2025, 2026}),
        ("japanese-grand-prix", "japan-grand-prix", "suzuka-international-racing-course", "suzuka"),
    ),
    PitLossTrackIdentity(
        "miami", "Miami International Autodrome", frozenset({2024, 2025, 2026}),
        ("miami-grand-prix", "miami-international-autodrome", "miami-gardens"),
    ),
    PitLossTrackIdentity(
        "imola", "Autodromo Enzo e Dino Ferrari", frozenset({2024, 2025}),
        ("emilia-romagna-grand-prix", "imola-grand-prix", "autodromo-enzo-e-dino-ferrari", "imola"),
    ),
    PitLossTrackIdentity(
        "monaco", "Circuit de Monaco", frozenset({2024, 2025, 2026}),
        ("monaco-grand-prix", "circuit-de-monaco", "monte-carlo", "montecarlo"),
    ),
    PitLossTrackIdentity(
        "barcelona-catalunya", "Circuit de Barcelona-Catalunya", frozenset({2024, 2025}),
        (
            "spanish-grand-prix", "spain-grand-prix", "circuit-de-barcelona-catalunya",
            "barcelona-catalunya", "barcelona", "catalunya", "montmelo",
        ),
    ),
    PitLossTrackIdentity(
        "madring", "Madrid Circuit (Madring)", frozenset({2026}),
        ("spanish-grand-prix", "spain-grand-prix", "madrid-grand-prix", "madring", "madrid-circuit", "madrid"),
    ),
    PitLossTrackIdentity(
        "montreal", "Circuit Gilles Villeneuve", frozenset({2024, 2025, 2026}),
        ("canadian-grand-prix", "canada-grand-prix", "circuit-gilles-villeneuve", "gilles-villeneuve", "montreal"),
    ),
    PitLossTrackIdentity(
        "red-bull-ring", "Red Bull Ring", frozenset({2024, 2025, 2026}),
        ("austrian-grand-prix", "austria-grand-prix", "red-bull-ring", "spielberg"),
    ),
    PitLossTrackIdentity(
        "silverstone", "Silverstone Circuit", frozenset({2024, 2025, 2026}),
        ("british-grand-prix", "great-britain-grand-prix", "britain-grand-prix", "silverstone-circuit", "silverstone"),
    ),
    PitLossTrackIdentity(
        "hungaroring", "Hungaroring", frozenset({2024, 2025, 2026}),
        ("hungarian-grand-prix", "hungary-grand-prix", "hungaroring", "budapest"),
    ),
    PitLossTrackIdentity(
        "spa-francorchamps", "Circuit de Spa-Francorchamps", frozenset({2024, 2025, 2026}),
        ("belgian-grand-prix", "belgium-grand-prix", "circuit-de-spa-francorchamps", "spa-francorchamps", "francorchamps"),
    ),
    PitLossTrackIdentity(
        "zandvoort", "Circuit Zandvoort", frozenset({2024, 2025, 2026}),
        ("dutch-grand-prix", "netherlands-grand-prix", "circuit-zandvoort", "zandvoort"),
    ),
    PitLossTrackIdentity(
        "monza", "Autodromo Nazionale Monza", frozenset({2024, 2025, 2026}),
        ("italian-grand-prix", "italy-grand-prix", "autodromo-nazionale-monza", "monza"),
    ),
    PitLossTrackIdentity(
        "baku", "Baku City Circuit", frozenset({2024, 2025, 2026}),
        ("azerbaijan-grand-prix", "azerbaijani-grand-prix", "baku-city-circuit", "baku"),
    ),
    PitLossTrackIdentity(
        "marina-bay", "Marina Bay Street Circuit", frozenset({2024, 2025, 2026}),
        ("singapore-grand-prix", "singapore-street-circuit", "marina-bay-street-circuit", "marina-bay"),
    ),
    PitLossTrackIdentity(
        "cota", "Circuit of the Americas", frozenset({2024, 2025, 2026}),
        ("united-states-grand-prix", "usa-grand-prix", "circuit-of-the-americas", "cota", "austin"),
    ),
    PitLossTrackIdentity(
        "mexico-city", "Autodromo Hermanos Rodriguez", frozenset({2024, 2025, 2026}),
        ("mexico-city-grand-prix", "mexican-grand-prix", "mexico-grand-prix", "autodromo-hermanos-rodriguez", "hermanos-rodriguez", "mexico-city"),
    ),
    PitLossTrackIdentity(
        "interlagos", "Autodromo Jose Carlos Pace", frozenset({2024, 2025, 2026}),
        ("brazilian-grand-prix", "brazil-grand-prix", "sao-paulo-grand-prix", "autodromo-jose-carlos-pace", "interlagos"),
    ),
    PitLossTrackIdentity(
        "las-vegas", "Las Vegas Strip Circuit", frozenset({2024, 2025, 2026}),
        ("las-vegas-grand-prix", "las-vegas-strip-circuit", "las-vegas"),
    ),
    PitLossTrackIdentity(
        "lusail", "Lusail International Circuit", frozenset({2024, 2025, 2026}),
        ("qatar-grand-prix", "qatari-grand-prix", "lusail-international-circuit", "lusail", "losail"),
    ),
    PitLossTrackIdentity(
        "yas-marina", "Yas Marina Circuit", frozenset({2024, 2025, 2026}),
        ("abu-dhabi-grand-prix", "abu-dhabi-circuit", "yas-marina-circuit", "yas-marina"),
    ),
    PitLossTrackIdentity(
        "sepang", "Sepang International Circuit", frozenset({2026}),
        ("malaysian-grand-prix", "malaysia-grand-prix", "sepang-international-circuit", "sepang"),
    ),
)

def _name_counts(identities: Sequence[PitLossTrackIdentity]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for identity in identities:
        for name in identity.names:
            counts[name] = counts.get(name, 0) + 1
    return counts


# Bare names shared by more than one circuit (for example
# ``spanish-grand-prix`` used by both Barcelona 2024/2025 and Madrid 2026) are
# ambiguous and must not resolve without a season qualifier.
_AMBIGUOUS_BARE_NAMES = frozenset(
    name
    for name, count in _name_counts(_TRACK_IDENTITIES).items()
    if count > 1
)


def _alias_keys_for(
    identity: PitLossTrackIdentity,
    ambiguous_bare_names: frozenset[str],
) -> tuple[str, ...]:
    """Return deterministic registry keys for one identity.

    Every key is either the canonical ``track_id``, a bare name (when the name
    is unambiguous), or a season-qualified ``{season}-{name}`` /
    ``{name}-{season}`` fixture key.  Track-asset identifiers are handled by
    :func:`canonicalize_identity_key`, which strips the ``-telemetry-layout-v1``
    suffix before lookup, so no asset-suffixed keys are stored here.
    """
    names = (identity.track_id,) + identity.names
    keys: list[str] = [identity.track_id]
    for name in names:
        is_ambiguous_bare_name = name != identity.track_id and name in ambiguous_bare_names
        if not is_ambiguous_bare_name:
            keys.append(name)
        for season in SUPPORTED_SEASONS:
            if season not in identity.seasons:
                continue
            keys.append(f"{season}-{name}")
            keys.append(f"{name}-{season}")
    return tuple(dict.fromkeys(keys))


def _build_registry(
    identities: Sequence[PitLossTrackIdentity],
) -> Mapping[str, PitLossTrackIdentity]:
    registry: dict[str, PitLossTrackIdentity] = {}
    for identity in identities:
        for key in _alias_keys_for(identity, _AMBIGUOUS_BARE_NAMES):
            if key in registry:
                raise ValueError(
                    f"track identity alias {key!r} is ambiguous: "
                    f"already registered for {registry[key].track_id!r}"
                )
            registry[key] = identity
    return MappingProxyType(registry)


PIT_LOSS_TRACK_IDENTITIES: tuple[PitLossTrackIdentity, ...] = tuple(
    sorted(_TRACK_IDENTITIES, key=lambda identity: identity.track_id),
)
PIT_LOSS_TRACK_IDENTITY_REGISTRY: Mapping[str, PitLossTrackIdentity] = _build_registry(
    PIT_LOSS_TRACK_IDENTITIES,
)

if len(PIT_LOSS_TRACK_IDENTITIES) != UNION_CIRCUIT_COUNT:
    raise RuntimeError(
        "track identity union must contain "
        f"{UNION_CIRCUIT_COUNT} physical circuits; found {len(PIT_LOSS_TRACK_IDENTITIES)}"
    )


def normalize_identity_key(value: object) -> str:
    """Return the deterministic kebab-case key for a raw identity input.

    Raises :class:`TrackIdentityLookupError` for non-string, empty, and
    malformed inputs (for example ``"!!!"``) instead of guessing.
    """
    if not isinstance(value, str):
        raise TrackIdentityLookupError("track identity must be a string")
    stripped = value.strip()
    if not stripped:
        raise TrackIdentityLookupError("track identity must not be empty")
    decomposed = unicodedata.normalize("NFKD", stripped.casefold())
    flattened = "".join(character for character in decomposed if not unicodedata.combining(character))
    tokens = tuple(_TOKEN.findall(flattened))
    if not tokens:
        raise TrackIdentityLookupError("track identity must contain a recognizable name")
    return "-".join(tokens)


def canonicalize_identity_key(value: object) -> str:
    """Return the fixture/asset-aware registry key for an identity input.

    Strips the deterministic ``-telemetry-layout-v1`` track-asset suffix and
    ``-round-<n>`` fixture segments so real fixture identifiers such as
    ``2026-round-01-australian-grand-prix`` and their track assets resolve onto
    the physical-circuit registry.
    """
    key = normalize_identity_key(value)
    if key.endswith(_ASSET_SUFFIX):
        key = key[: -len(_ASSET_SUFFIX)]
    key = _ROUND_SEGMENT.sub("", key)
    if not key:
        raise TrackIdentityLookupError("track identity is empty after fixture normalization")
    return key


def resolve_track_identity(value: object) -> PitLossTrackIdentity:
    """Resolve one physical circuit from any registered alias or identity.

    Accepts an already-resolved :class:`PitLossTrackIdentity` as a pass-through
    for callers that hold the object.  Unknown, empty, malformed, and ambiguous
    inputs raise :class:`TrackIdentityLookupError`.
    """
    if isinstance(value, PitLossTrackIdentity):
        return value
    identity = PIT_LOSS_TRACK_IDENTITY_REGISTRY.get(canonicalize_identity_key(value))
    if identity is None:
        raise TrackIdentityLookupError(f"unknown track identity {value!r}")
    return identity


def resolve_track_identity_or_none(value: object) -> PitLossTrackIdentity | None:
    """Return the identity for a well-formed name, or ``None`` when unknown.

    Empty, non-string, and malformed inputs still raise
    :class:`TrackIdentityLookupError`; only a well-formed but unregistered name
    returns ``None``.
    """
    if isinstance(value, PitLossTrackIdentity):
        return value
    return PIT_LOSS_TRACK_IDENTITY_REGISTRY.get(canonicalize_identity_key(value))


def is_known_track_identity(value: object) -> bool:
    """Return whether the input resolves to a registered physical circuit."""
    try:
        resolve_track_identity(value)
    except TrackIdentityLookupError:
        return False
    return True


def resolve_binding_identity(
    *,
    fixture_id: str | None = None,
    track_id: str | None = None,
    track_asset_id: str | None = None,
    track_name: str | None = None,
) -> PitLossTrackIdentity:
    """Resolve one physical circuit from a delivery binding without guessing.

    Every provided identifier must resolve; identifiers that resolve to
    different circuits are ambiguous and raise.  An all-empty binding raises.

    When ``track_name`` is supplied the resolution is identity-tolerant for
    identifiers that are not themselves registered (for example the actual
    generator fixture ``2026-01-race`` and its ``-telemetry-layout-v1`` track
    asset): those values are skipped and the track name establishes the
    circuit.  A malformed/unknown track name with no other resolvable binding,
    an entirely unresolvable binding, and any pair of bindings that resolve to
    different circuits still fail closed.
    """
    candidates: list[tuple[str, PitLossTrackIdentity]] = []
    for label, value in (
        ("fixture_id", fixture_id),
        ("track_id", track_id),
        ("track_asset_id", track_asset_id),
        ("track_name", track_name),
    ):
        if value is None:
            continue
        try:
            identity = resolve_track_identity(value)
        except TrackIdentityLookupError:
            if track_name is None:
                raise
            # With an explicit track-name binding an unregistered identifier
            # (the generator's ``2026-01-race`` fixture or its asset id) is
            # tolerated; the remaining bindings must still agree on one circuit.
            continue
        candidates.append((label, identity))
    if not candidates:
        raise TrackIdentityLookupError("track identity binding is empty")
    first_label, first = candidates[0]
    for label, identity in candidates[1:]:
        if identity.track_id != first.track_id:
            raise TrackIdentityLookupError(
                "track identity binding is ambiguous: "
                f"{first_label}={first.track_id!r} conflicts with {label}={identity.track_id!r}"
            )
    return first


def track_identities_for_season(season: int) -> tuple[PitLossTrackIdentity, ...]:
    """Return the union circuits that appear in one calendar season."""
    if season not in _SUPPORTED_SEASONS:
        raise ValueError("season must be 2024, 2025, or 2026")
    return tuple(
        identity for identity in PIT_LOSS_TRACK_IDENTITIES if season in identity.seasons
    )


__all__ = [
    "IDENTITY_SCHEMA_ID",
    "IDENTITY_UNION_VERSION",
    "PIT_LOSS_TRACK_IDENTITIES",
    "PIT_LOSS_TRACK_IDENTITY_REGISTRY",
    "PitLossTrackIdentity",
    "SUPPORTED_SEASONS",
    "TrackIdentityLookupError",
    "UNION_CIRCUIT_COUNT",
    "canonicalize_identity_key",
    "is_known_track_identity",
    "normalize_identity_key",
    "resolve_binding_identity",
    "resolve_track_identity",
    "resolve_track_identity_or_none",
    "track_identities_for_season",
]
