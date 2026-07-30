"""Pure, offline helpers for optional race-library visual metadata.

The registry is deliberately curated rather than geocoded at publication time.
Unknown schedule values therefore fail closed and never become guessed map data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import math
import re
import unicodedata
from types import MappingProxyType
from typing import NamedTuple, cast


class VenueCoordinates(NamedTuple):
    """Immutable latitude/longitude pair in the order used by catalog v2."""

    latitude: float
    longitude: float


def _normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(character for character in decomposed if not unicodedata.combining(character))
    return " ".join(re.findall(r"[a-z0-9]+", without_marks))


_VENUES: dict[tuple[str, str], VenueCoordinates] = {}


def _add_venue(
    country: str,
    location: str,
    latitude: float,
    longitude: float,
    *aliases: str,
) -> None:
    coordinates = VenueCoordinates(latitude, longitude)
    for venue_location in (location, *aliases):
        _VENUES[(_normalize_text(country), _normalize_text(venue_location))] = coordinates


# Coordinates are circuit-area anchors, not claims about a particular property
# boundary. Values seed the read-only calendar template and current-calendar
# aliases commonly emitted by FastF1.
_add_venue("Bahrain", "Sakhir", 26.0325, 50.5105556, "Bahrain International Circuit")
_add_venue("Saudi Arabia", "Jeddah", 21.6319444, 39.1044444, "Jeddah Corniche Circuit")
_add_venue("Australia", "Melbourne", -37.8497222, 144.9683333, "Albert Park")
_add_venue("Italy", "Imola", 44.3411111, 11.7133333, "Imola Circuit")
_add_venue("Italy", "Monza", 45.6205556, 9.2894444, "Autodromo Nazionale Monza")
_add_venue("United States", "Miami", 25.9580556, -80.2388889, "Miami Gardens")
_add_venue("Spain", "Barcelona", 41.57, 2.2611111, "Montmelo", "Montmeló")
_add_venue("Monaco", "Monaco", 43.7347222, 7.4205556, "Monte Carlo", "Monte-Carlo")
_add_venue("Azerbaijan", "Baku", 40.3725, 49.8533333, "Baku City Circuit")
_add_venue("Canada", "Montreal", 45.5005556, -73.5225, "Montréal", "Circuit Gilles Villeneuve")
_add_venue("United Kingdom", "Silverstone", 52.0786111, -1.0169444, "Silverstone Circuit")
_add_venue("Austria", "Spielberg", 47.2197222, 14.7647222, "Red Bull Ring")
_add_venue("France", "Le Castellet", 43.2505556, 5.7916667, "Paul Ricard")
_add_venue("Hungary", "Budapest", 47.5822222, 19.2511111, "Hungaroring")
_add_venue("Belgium", "Spa", 50.4372222, 5.9713889, "Spa-Francorchamps", "Francorchamps")
_add_venue("Netherlands", "Zandvoort", 52.3888194, 4.5409222, "Circuit Zandvoort")
_add_venue("Singapore", "Singapore", 1.2915306, 103.86385, "Marina Bay")
_add_venue("Japan", "Suzuka", 34.8430556, 136.5405556, "Suzuka Circuit")
_add_venue("United States", "Austin", 30.1327778, -97.6411111, "Circuit of the Americas", "COTA")
_add_venue("Mexico", "Mexico City", 19.4061111, -99.0925, "Mexico")
_add_venue("Brazil", "Sao Paulo", -23.7011111, -46.6972222, "São Paulo", "Interlagos")
_add_venue("United Arab Emirates", "Abu Dhabi", 24.4672222, 54.6030556, "Yas Marina")

# Additional clearly named modern-calendar venues found in recent schedules.
_add_venue("Qatar", "Lusail", 25.490, 51.454, "Losail")
_add_venue("United States", "Las Vegas", 36.1147, -115.1728)
_add_venue("Portugal", "Portimao", 37.227, -8.626, "Portimão")
_add_venue("Turkey", "Istanbul", 40.9517, 29.405, "Istanbul Park")
_add_venue("China", "Shanghai", 31.3389, 121.2197, "Shanghai International Circuit")
_add_venue("Malaysia", "Sepang", 2.7608, 101.738, "Sepang International Circuit")
_add_venue("Russia", "Sochi", 43.4057, 39.9578, "Sochi Autodrom")
_add_venue("Vietnam", "Hanoi", 21.0167, 105.7667, "Hanoi Circuit")
_add_venue("Germany", "Hockenheim", 49.3275, 8.5658, "Hockenheimring")
_add_venue("Italy", "Mugello", 43.9975, 11.3719, "Mugello Circuit")

# Schedule providers and historical calendar data use both country spellings.
for _country_alias in ("USA", "US", "United States of America"):
    for _location in ("Miami", "Miami Gardens"):
        _VENUES[(_normalize_text(_country_alias), _normalize_text(_location))] = _VENUES[
            (_normalize_text("United States"), _normalize_text(_location))
        ]
    for _location in ("Austin", "Circuit of the Americas", "COTA", "Las Vegas"):
        _VENUES[(_normalize_text(_country_alias), _normalize_text(_location))] = _VENUES[
            (_normalize_text("United States"), _normalize_text(_location))
        ]
_VENUES[(_normalize_text("UAE"), _normalize_text("Abu Dhabi"))] = _VENUES[
    (_normalize_text("United Arab Emirates"), _normalize_text("Abu Dhabi"))
]
_VENUES[(_normalize_text("UAE"), _normalize_text("Yas Marina"))] = _VENUES[
    (_normalize_text("United Arab Emirates"), _normalize_text("Yas Marina"))
]


VENUE_REGISTRY: Mapping[tuple[str, str], VenueCoordinates] = MappingProxyType(dict(_VENUES))
# Descriptive alias for callers that prefer the catalog terminology.
CURATED_VENUE_REGISTRY = VENUE_REGISTRY
del _VENUES


def resolve_venue_coordinates(country: str | None, location: str | None) -> VenueCoordinates | None:
    """Resolve exact normalized schedule metadata through the curated registry."""
    if not isinstance(country, str) or not isinstance(location, str):
        return None
    key = (_normalize_text(country), _normalize_text(location))
    if not all(key):
        return None
    return VENUE_REGISTRY.get(key)


def resolve_coordinates(country: str | None, location: str | None) -> VenueCoordinates | None:
    """Compatibility alias for the catalog publication call site."""
    return resolve_venue_coordinates(country, location)


def create_circuit_preview(
    track_assets: object,
    *,
    max_points: int = 256,
) -> dict[str, str] | None:
    """Create the small JSON-only circuit preview contract from centerLine.

    The source is treated as untrusted despite browser publication validation:
    only finite point objects are accepted, and no source text is copied into
    the SVG path. Downsampling preserves source order and both endpoints.
    """
    if not isinstance(max_points, int) or isinstance(max_points, bool) or max_points < 4:
        return None
    if not isinstance(track_assets, Mapping):
        return None
    source = track_assets.get("centerLine")
    if not isinstance(source, Sequence) or isinstance(source, (str, bytes, bytearray)):
        return None
    rotation_degrees = track_assets.get("rotationDegrees")
    if not _is_finite_number(rotation_degrees):
        return None
    points = _validated_points(source)
    if points is None or len(points) < 4:
        return None
    sampled = _to_workspace_map_points(
        _downsample(points, max_points),
        float(cast(float, rotation_degrees)),
    )
    bounds = _bounds(sampled)
    if bounds is None:
        return None
    path_data = " ".join(
        [f"M {_format_number(sampled[0][0])} {_format_number(sampled[0][1])}"]
        + [f"L {_format_number(x)} {_format_number(y)}" for x, y in sampled[1:]]
        + ["Z"]
    )
    return {
        "pathData": path_data,
        "viewBox": " ".join(_format_number(value) for value in bounds),
    }


def build_circuit_preview_payload(track_assets: object, *, max_points: int = 256) -> dict[str, str] | None:
    """Explicitly named alias for callers constructing a publication payload."""
    return create_circuit_preview(track_assets, max_points=max_points)


def serialize_circuit_preview(payload: Mapping[str, str]) -> bytes:
    """Serialize a validated preview payload deterministically for atomic writes."""
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"


def _validated_points(source: Sequence[object]) -> tuple[tuple[float, float], ...] | None:
    points: list[tuple[float, float]] = []
    for value in source:
        if not isinstance(value, Mapping) or set(value) != {"x", "y"}:
            return None
        x, y = value.get("x"), value.get("y")
        if not _is_finite_number(x) or not _is_finite_number(y):
            return None
        points.append((float(cast(float, x)), float(cast(float, y))))
    return tuple(points)


def _is_finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


def _downsample(points: tuple[tuple[float, float], ...], max_points: int) -> tuple[tuple[float, float], ...]:
    if len(points) <= max_points:
        return points
    last = len(points) - 1
    return tuple(points[round(index * last / (max_points - 1))] for index in range(max_points))


def _to_workspace_map_points(
    points: tuple[tuple[float, float], ...],
    rotation_degrees: float,
) -> tuple[tuple[float, float], ...]:
    """Mirror LiveTrackMap.toMapPoint without changing source traversal order."""
    radians = math.radians(rotation_degrees)
    cosine, sine = math.cos(radians), math.sin(radians)
    return tuple(
        (
            _normalize_coordinate(x * cosine + y * sine),
            _normalize_coordinate(x * sine - y * cosine),
        )
        for x, y in points
    )


def _normalize_coordinate(value: float) -> float:
    return 0.0 if abs(value) < 1e-12 else value


def _bounds(points: tuple[tuple[float, float], ...]) -> tuple[float, float, float, float] | None:
    x_values = [point[0] for point in points]
    y_values = [point[1] for point in points]
    min_x, max_x = min(x_values), max(x_values)
    min_y, max_y = min(y_values), max(y_values)
    width, height = max_x - min_x, max_y - min_y
    if not all(math.isfinite(value) for value in (min_x, min_y, width, height)):
        return None
    return min_x - (0.5 if width == 0 else 0), min_y - (0.5 if height == 0 else 0), max(width, 1.0), max(height, 1.0)


def _format_number(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError("preview geometry must be finite")
    if value == 0:
        return "0"
    if value.is_integer():
        return str(int(value))
    return format(value, ".15g")


__all__ = [
    "CURATED_VENUE_REGISTRY", "VENUE_REGISTRY", "VenueCoordinates",
    "build_circuit_preview_payload", "create_circuit_preview", "resolve_coordinates",
    "resolve_venue_coordinates", "serialize_circuit_preview",
]
