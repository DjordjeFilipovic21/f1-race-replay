"""Immutable values exposed by the canonical-to-browser reader boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
import re
from types import MappingProxyType
from typing import cast

import polars as pl


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
MAX_INT64 = (1 << 63) - 1


def deep_freeze_json(value: object) -> object:
    """Return an immutable, finite, signed-Int64-safe JSON-like value."""
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        return MappingProxyType({key: deep_freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(deep_freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, bool)):
        return value
    if type(value) is int:
        if not -(1 << 63) <= value <= MAX_INT64:
            raise ValueError("JSON integers must fit signed Int64")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("value must contain only finite numbers")
        return value
    raise TypeError("value must be finite JSON-compatible data")


@dataclass(frozen=True)
class CanonicalGenerationSnapshot:
    """One completely validated, pointer-selected canonical generation."""

    generation_id: str
    manifest_sha256: str
    frames: Mapping[str, pl.DataFrame]

    def __post_init__(self) -> None:
        if not isinstance(self.generation_id, str) or not self.generation_id:
            raise ValueError("generation_id must be a non-empty string")
        if not isinstance(self.manifest_sha256, str) or not _SHA256.fullmatch(self.manifest_sha256):
            raise ValueError("manifest_sha256 must be a SHA-256 hexadecimal digest")
        if not isinstance(self.frames, Mapping):
            raise TypeError("frames must be a mapping")
        if not all(isinstance(name, str) and isinstance(frame, pl.DataFrame) for name, frame in self.frames.items()):
            raise TypeError("frames must map table names to Polars DataFrames")
        object.__setattr__(self, "frames", MappingProxyType(dict(self.frames)))


@dataclass(frozen=True)
class BrowserDriverFields:
    """Exact-time, null-preserving browser fields for one driver."""

    driver_id: str
    time_ms: tuple[int, ...]
    x: tuple[float | None, ...]
    y: tuple[float | None, ...]
    speed: tuple[float | None, ...]
    throttle: tuple[float | None, ...]
    brake: tuple[int | None, ...]
    gear: tuple[int | None, ...]
    drs: tuple[int | None, ...]
    status: tuple[str | None, ...]
    lap: tuple[int | None, ...]
    tyre_compound: tuple[str | None, ...]
    is_in_pit_lane: tuple[bool | None, ...]
    track_distance_meters: tuple[None, ...]
    gap_to_leader_ms: tuple[None, ...]
    position: tuple[None, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.driver_id, str) or not self.driver_id:
            raise ValueError("driver_id must be a non-empty string")
        if tuple(sorted(set(self.time_ms))) != self.time_ms:
            raise ValueError("time_ms must be sorted unique integer milliseconds")
        if not all(type(value) is int and 0 <= value <= MAX_INT64 for value in self.time_ms):
            raise TypeError("time_ms must contain non-negative signed Int64 milliseconds")
        size = len(self.time_ms)
        fields = (
            self.x, self.y, self.speed, self.throttle, self.brake, self.gear,
            self.drs, self.status, self.lap, self.tyre_compound,
            self.is_in_pit_lane, self.track_distance_meters,
            self.gap_to_leader_ms, self.position,
        )
        if any(not isinstance(field, tuple) or len(field) != size for field in fields):
            raise ValueError("every browser field must be a tuple aligned to time_ms")
        if any(value is not None and (type(value) is not float or not math.isfinite(value)) for field in (self.x, self.y, self.speed, self.throttle) for value in field):
            raise TypeError("continuous driver fields must contain finite floats or null")
        if any(value not in (None, 0, 1) for value in self.brake):
            raise ValueError("brake must contain 0, 1, or null")
        if any(value is not None and type(value) is not int for field in (self.gear, self.drs, self.lap) for value in field):
            raise TypeError("discrete driver fields must contain integers or null")
        if any(value is not None and not isinstance(value, str) for field in (self.status, self.tyre_compound) for value in field):
            raise TypeError("categorical driver fields must contain strings or null")
        if any(value is not None and type(value) is not bool for value in self.is_in_pit_lane):
            raise TypeError("pit state must contain booleans or null")
        if any(value is not None for field in (self.track_distance_meters, self.gap_to_leader_ms, self.position) for value in field):
            raise ValueError("unsupported v1 fields must remain null")


@dataclass(frozen=True)
class BrowserManifest:
    """Immutable contract metadata derived from one canonical snapshot."""

    fixture_id: str
    fixture_name: str
    drivers: tuple[Mapping[str, object], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.fixture_id, str) or not self.fixture_id:
            raise ValueError("fixture_id must be a non-empty string")
        if not isinstance(self.fixture_name, str) or not self.fixture_name:
            raise ValueError("fixture_name must be a non-empty string")
        frozen_drivers = tuple(
            cast(Mapping[str, object], deep_freeze_json(driver)) for driver in self.drivers
        )
        required = {"id", "displayName", "teamName", "colorHex", "carNumber"}
        if not frozen_drivers or any(set(driver) != required for driver in frozen_drivers):
            raise ValueError("drivers must contain immutable driver metadata")
        if len({driver["id"] for driver in frozen_drivers}) != len(frozen_drivers):
            raise ValueError("driver metadata IDs must be unique")
        object.__setattr__(self, "drivers", frozen_drivers)

    def as_dict(self) -> dict[str, object]:
        return {
            "contractVersion": "v1",
            "fixtureId": self.fixture_id,
            "fixtureName": self.fixture_name,
            "schemas": {
                "manifest": "urn:f1-cache-replay:schema:replay-data:v1:manifest",
                "chunk": "urn:f1-cache-replay:schema:replay-data:v1:chunk",
                "trackAssets": "urn:f1-cache-replay:schema:replay-data:v1:track-assets",
            },
            "drivers": [dict(driver) for driver in self.drivers],
        }


__all__ = [
    "BrowserDriverFields", "BrowserManifest", "CanonicalGenerationSnapshot",
    "MAX_INT64", "deep_freeze_json",
]
