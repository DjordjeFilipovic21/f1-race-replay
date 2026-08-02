"""Versioned, session-indexed season catalog contract."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import PurePosixPath
import re
from types import MappingProxyType
from collections.abc import Mapping, Sequence
from typing import cast

from f1_replay_pipeline.domain.canonical_contract import CANONICAL_PARQUET_V1, CANONICAL_PARQUET_V2
from f1_replay_pipeline.domain.generation_identity import build_v2_generation_id, validate_generation_id


CATALOG_SCHEMA_VERSION = 2
CANONICAL_POINTER_FORMAT = CANONICAL_PARQUET_V2
BROWSER_POINTER_FORMAT = "browser-delivery-v2"
HISTORICAL_CANONICAL_POINTER_FORMAT = CANONICAL_PARQUET_V1
HISTORICAL_BROWSER_POINTER_FORMAT = "browser-delivery-v1"
REQUIRED_V2_CUTOVER_RACE_COUNT = 4
V1_DEPRECATION_METADATA: Mapping[str, Mapping[str, object]] = MappingProxyType({
    "canonical": MappingProxyType({
        "format_version": HISTORICAL_CANONICAL_POINTER_FORMAT,
        "active": False,
        "deprecated": True,
        "replacement": CANONICAL_POINTER_FORMAT,
    }),
    "browser": MappingProxyType({
        "format_version": HISTORICAL_BROWSER_POINTER_FORMAT,
        "active": False,
        "deprecated": True,
        "replacement": BROWSER_POINTER_FORMAT,
    }),
})
HISTORICAL_V1_ARTIFACT_METADATA: Mapping[str, Mapping[str, object]] = MappingProxyType({
    "canonical_manifest": MappingProxyType({
        "format_version": HISTORICAL_CANONICAL_POINTER_FORMAT,
        "schema_token": f"{HISTORICAL_CANONICAL_POINTER_FORMAT}:manifest",
        "active": False,
        "deprecated": True,
    }),
    "canonical_schema": MappingProxyType({
        "format_version": HISTORICAL_CANONICAL_POINTER_FORMAT,
        "active": False,
        "deprecated": True,
    }),
    "browser_manifest": MappingProxyType({
        "format_version": HISTORICAL_BROWSER_POINTER_FORMAT,
        "schema_token": "urn:f1-cache-replay:schema:replay-data:v1:manifest",
        "active": False,
        "deprecated": True,
    }),
    "browser_schema": MappingProxyType({
        "format_version": HISTORICAL_BROWSER_POINTER_FORMAT,
        "active": False,
        "deprecated": True,
    }),
    "historical_fixtures": MappingProxyType({
        "format_version": "v1",
        "count": 4,
        "active": False,
        "deprecated": True,
    }),
})
_SUFFIX = re.compile(r"-(?:force|browser)-[0-9]+$")
_RACE_ID = re.compile(r"^(?P<year>[0-9]{4})-round-(?P<round>[0-9]+)(?:-[A-Za-z0-9._-]+)?$")
_GENERATION_ID = re.compile(r"^(?P<year>[0-9]{4})-round-(?P<round>[0-9]+)-(?P<session>.+)$")
_V2_GENERATION_ID = re.compile(
    r"^(?P<year>[0-9]{4})-round-(?P<round>[0-9]+)-session-"
    r"(?P<identity>[A-Za-z0-9.-]+)-mode-"
    r"(?P<mode>practice|qualifying|race|sprint|sprint-qualifying|sprint-shootout|testing)$"
)
_V2_SESSION_CODES = {
    "practice-1": "fp1",
    "practice-2": "fp2",
    "practice-3": "fp3",
    "qualifying": "q",
    "race": "r",
    "sprint": "s",
    "sprint-qualifying": "sq",
    "sprint-shootout": "ss",
    "testing": "testing",
}
_SAFE_RELATIVE_POSIX_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*$")


def serialize_catalog_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"


@dataclass(frozen=True)
class DeprecatedV1Reference:
    """Metadata for an immutable v1 artifact retained only for history."""

    artifact: str
    path: str
    active: bool = False
    deprecated: bool = True

    def __post_init__(self) -> None:
        _require_text(self.artifact, "artifact")
        _require_pointer(self.path, "path")
        if self.active:
            raise ValueError("v1 historical references must be inactive")
        if not self.deprecated:
            raise ValueError("v1 historical references must be deprecated")

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact": self.artifact,
            "path": self.path,
            "active": self.active,
            "deprecated": self.deprecated,
        }


def validate_active_catalog(value: object) -> "CatalogV2Payload":
    """Parse the only catalog shape eligible for active discovery."""
    if not isinstance(value, Mapping):
        raise ValueError("active catalog must be an object")
    if value.get("schemaVersion") != CATALOG_SCHEMA_VERSION:
        raise ValueError("active catalog requires schemaVersion 2; v1 catalogs are deprecated")
    if set(value) - {"schemaVersion", "year", "atomicAcrossRaces", "races"}:
        raise ValueError("active catalog contains unsupported or deprecated fields")
    raw_races = value.get("races")
    if not isinstance(raw_races, list):
        raise ValueError("active catalog races must be an array")
    races = tuple(_race_from_dict(item) for item in raw_races)
    for race in races:
        for session in race.sessions:
            if session.generation_id is None:
                continue
            try:
                expected_code = session_code_from_generation_id(session.generation_id, race.race_id)
            except ValueError as error:
                raise ValueError(
                    f"active catalog session {race.race_id}/{session.session_code} has mixed-version identity"
                ) from error
            if expected_code != session.session_code:
                raise ValueError(
                    f"active catalog session {race.race_id}/{session.session_code} disagrees with generation_id"
                )
    return CatalogV2Payload(
        cast(int, value.get("year")),
        races,
        atomic_across_races=cast(bool, value.get("atomicAcrossRaces")),
    )


def validate_v2_cutover_contract(
    value: object,
    required_race_ids: Sequence[str],
) -> "CatalogV2Payload":
    """Require every republished race before the active catalog is switched."""
    try:
        requested = tuple(required_race_ids)
    except TypeError as error:
        raise ValueError("v2 cutover race IDs must be a collection") from error
    for race_id in requested:
        try:
            validate_generation_id(race_id)
        except (TypeError, ValueError) as error:
            raise ValueError("v2 cutover race IDs must be safe identifiers") from error
    required = tuple(dict.fromkeys(requested))
    if len(required) != REQUIRED_V2_CUTOVER_RACE_COUNT:
        raise ValueError("v2 catalog cutover requires exactly four republished races")
    catalog = validate_active_catalog(value)
    records = {race.race_id: race for race in catalog.races}
    missing = [race_id for race_id in required if race_id not in records]
    if missing:
        raise ValueError(f"v2 catalog cutover is incomplete; missing republished races: {', '.join(missing)}")
    for race_id in required:
        if not any(session.validated for session in records[race_id].sessions):
            raise ValueError(f"v2 catalog cutover race {race_id} has no validated v2 session")
    return catalog


def _race_from_dict(value: object) -> "CatalogV2RaceRecord":
    if not isinstance(value, Mapping):
        raise ValueError("active catalog race must be an object")
    raw_sessions = value.get("sessions")
    if not isinstance(raw_sessions, list):
        raise ValueError("active catalog race sessions must be an array")
    visual = value.get("visual")
    visual_values = visual if isinstance(visual, Mapping) else {}
    if visual is not None and not isinstance(visual, Mapping):
        raise ValueError("active catalog race visual metadata is malformed")
    return CatalogV2RaceRecord(
        cast(str, value.get("race_id")), cast(int, value.get("round_number")), cast(str, value.get("event_name")),
        tuple(_session_from_dict(item) for item in raw_sessions),
        value.get("country"), value.get("location"), value.get("event_date"),
        visual_values.get("latitude"), visual_values.get("longitude"), visual_values.get("circuitPreview"),
    )


def _session_from_dict(value: object) -> "CatalogV2SessionRecord":
    if not isinstance(value, Mapping):
        raise ValueError("active catalog session must be an object")
    if set(value) != {
        "session_code", "session_name", "generation_id", "delivery_version", "outcome",
        "validated", "canonical_pointer", "browser_pointer",
    }:
        raise ValueError("active catalog session has deprecated or mixed-version fields")
    return CatalogV2SessionRecord(
        cast(str, value["session_code"]), cast(str, value["session_name"]),
        cast(str | None, value["generation_id"]), cast(str | None, value["delivery_version"]),
        cast(str, value["outcome"]), cast(bool, value["validated"]),
        cast(str | None, value["canonical_pointer"]), cast(str | None, value["browser_pointer"]),
    )


def session_code_from_generation_id(generation_id: str, race_id: str) -> str:
    """Extract a session token while accepting readable race-folder slugs."""
    validate_generation_id(generation_id)
    validate_generation_id(race_id)
    base = strip_generation_suffix(generation_id)
    race_match = _RACE_ID.fullmatch(race_id)
    generation_match = _GENERATION_ID.fullmatch(base)
    if race_match is None or generation_match is None:
        raise ValueError("race_id and generation_id must identify a year and round")
    if (race_match["year"], int(race_match["round"])) != (
        generation_match["year"], int(generation_match["round"]),
    ):
        raise ValueError("generation_id belongs to a different year or round")
    v2_match = _V2_GENERATION_ID.fullmatch(base)
    if v2_match is not None:
        if (race_match["year"], int(race_match["round"])) != (
            v2_match["year"], int(v2_match["round"]),
        ):
            raise ValueError("generation_id belongs to a different year or round")
        identity = v2_match["identity"]
        try:
            expected = build_v2_generation_id(
                int(v2_match["year"]), int(v2_match["round"]), identity,
            )
        except ValueError as error:
            raise ValueError("generation_id has an invalid v2 session identity") from error
        if expected != base or identity not in _V2_SESSION_CODES:
            raise ValueError("generation_id has an invalid v2 session identity")
        return _V2_SESSION_CODES[identity]
    return validate_generation_id(generation_match["session"]).casefold()


def strip_generation_suffix(generation_id: str) -> str:
    validate_generation_id(generation_id)
    value = generation_id
    while True:
        stripped = _SUFFIX.sub("", value)
        if stripped == value:
            return value
        value = stripped


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-blank")
    return value.strip()


def _require_safe_text(value: object, label: str) -> str:
    text = _require_text(value, label)
    try:
        return validate_generation_id(text)
    except ValueError as error:
        raise ValueError(f"{label} must be a safe identifier") from error


def _require_pointer(value: object, label: str) -> str:
    text = _require_text(value, label)
    path = PurePosixPath(text)
    if (
        _SAFE_RELATIVE_POSIX_PATH.fullmatch(text) is None
        or path.is_absolute()
        or "\\" in text
        or any(part in {"", ".", ".."} for part in text.split("/"))
    ):
        raise ValueError(f"{label} must be a safe relative POSIX path")
    return text


def _require_coordinate(value: object, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    try:
        coordinate = float(value)
    except (OverflowError, ValueError) as error:
        raise ValueError(f"{label} must be a finite number") from error
    if not math.isfinite(coordinate) or not minimum <= coordinate <= maximum:
        raise ValueError(f"{label} must be between {minimum:g} and {maximum:g}")
    return coordinate


def _require_visual_pointer(value: object, label: str) -> str:
    text = _require_text(value, label)
    if _SAFE_RELATIVE_POSIX_PATH.fullmatch(text) is None:
        raise ValueError(f"{label} must be a safe relative POSIX path")
    return text


@dataclass(frozen=True)
class CatalogV2SessionRecord:
    session_code: str
    session_name: str
    generation_id: str | None
    delivery_version: str | None
    outcome: str
    validated: bool
    canonical_pointer: str | None
    browser_pointer: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_code", _require_safe_text(self.session_code, "session_code").casefold())
        object.__setattr__(self, "session_name", _require_text(self.session_name, "session_name"))
        object.__setattr__(self, "outcome", _require_safe_text(self.outcome, "outcome"))
        if type(self.validated) is not bool:
            raise ValueError("validated must be a boolean")
        for value, label in ((self.generation_id, "generation_id"), (self.delivery_version, "delivery_version")):
            if value is not None:
                _require_safe_text(value, label)
        pointers = (self.canonical_pointer, self.browser_pointer)
        if any(pointer is not None for pointer in pointers):
            if self.generation_id is None or self.delivery_version is None:
                raise ValueError("pointers require generation_id and delivery_version")
            if self.canonical_pointer is not None:
                _require_pointer(self.canonical_pointer, "canonical_pointer")
            if self.browser_pointer is not None:
                _require_pointer(self.browser_pointer, "browser_pointer")
            if not self.validated:
                raise ValueError("unvalidated sessions must not claim pointer paths")
        if self.validated and (
            self.generation_id is None or self.delivery_version is None
            or self.browser_pointer is None
        ):
            raise ValueError("validated sessions require a complete browser artifact reference")

    def to_dict(self) -> dict[str, object]:
        return {
            "session_code": self.session_code,
            "session_name": self.session_name,
            "generation_id": self.generation_id,
            "delivery_version": self.delivery_version,
            "outcome": self.outcome,
            "validated": self.validated,
            "canonical_pointer": self.canonical_pointer,
            "browser_pointer": self.browser_pointer,
        }


@dataclass(frozen=True)
class CatalogV2RaceRecord:
    race_id: str
    round_number: int
    event_name: str
    sessions: tuple[CatalogV2SessionRecord, ...]
    country: str | None = None
    location: str | None = None
    event_date: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    circuit_preview: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "race_id", _require_safe_text(self.race_id, "race_id"))
        if type(self.round_number) is not int or self.round_number < 1:
            raise ValueError("round_number must be a positive integer")
        object.__setattr__(self, "event_name", _require_text(self.event_name, "event_name"))
        try:
            sessions = tuple(self.sessions)
        except TypeError as error:
            raise ValueError("sessions must be a collection") from error
        if any(not isinstance(item, CatalogV2SessionRecord) for item in sessions):
            raise ValueError("sessions must contain CatalogV2SessionRecord values")
        codes = [item.session_code for item in sessions]
        if len(codes) != len(set(codes)):
            raise ValueError("race sessions must not contain duplicate session_code values")
        object.__setattr__(self, "sessions", tuple(sorted(sessions, key=lambda item: item.session_code)))
        for value, label in ((self.country, "country"), (self.location, "location"), (self.event_date, "event_date")):
            if value is not None:
                _require_text(value, label)
        coordinates = (self.latitude, self.longitude)
        if any(value is not None for value in coordinates):
            if not all(value is not None for value in coordinates):
                raise ValueError("latitude and longitude must be provided together")
            object.__setattr__(self, "latitude", _require_coordinate(self.latitude, "latitude", -90, 90))
            object.__setattr__(self, "longitude", _require_coordinate(self.longitude, "longitude", -180, 180))
        elif self.circuit_preview is not None:
            raise ValueError("circuit_preview requires latitude and longitude")
        if self.circuit_preview is not None:
            object.__setattr__(self, "circuit_preview", _require_visual_pointer(self.circuit_preview, "circuit_preview"))

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "race_id": self.race_id,
            "round_number": self.round_number,
            "event_name": self.event_name,
            "sessions": [session.to_dict() for session in self.sessions],
        }
        for name, item in (("country", self.country), ("location", self.location), ("event_date", self.event_date)):
            if item is not None:
                value[name] = item
        if self.latitude is not None:
            visual: dict[str, object] = {
                "latitude": self.latitude,
                "longitude": self.longitude,
            }
            if self.circuit_preview is not None:
                visual["circuitPreview"] = self.circuit_preview
            value["visual"] = visual
        return value


@dataclass(frozen=True)
class CatalogV2Payload:
    year: int
    races: tuple[CatalogV2RaceRecord, ...]
    atomic_across_races: bool = False
    schema_version: int = CATALOG_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != CATALOG_SCHEMA_VERSION:
            raise ValueError("catalog schema_version must be exactly 2")
        if type(self.year) is not int or self.year < 1:
            raise ValueError("year must be a positive integer")
        if type(self.atomic_across_races) is not bool:
            raise ValueError("atomic_across_races must be a boolean")
        try:
            races = tuple(self.races)
        except TypeError as error:
            raise ValueError("races must be a collection") from error
        if any(not isinstance(item, CatalogV2RaceRecord) for item in races):
            raise ValueError("races must contain CatalogV2RaceRecord values")
        identities = [race.race_id for race in races]
        if len(identities) != len(set(identities)):
            raise ValueError("catalog races must not contain duplicate race_id values")
        object.__setattr__(self, "races", tuple(sorted(races, key=lambda item: item.race_id)))

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "year": self.year,
            "atomicAcrossRaces": self.atomic_across_races,
            "races": [race.to_dict() for race in self.races],
        }

    def to_json_bytes(self) -> bytes:
        return serialize_catalog_json(self.to_dict())


__all__ = [
    "BROWSER_POINTER_FORMAT", "CANONICAL_POINTER_FORMAT", "CATALOG_SCHEMA_VERSION",
    "DeprecatedV1Reference", "HISTORICAL_BROWSER_POINTER_FORMAT",
    "HISTORICAL_CANONICAL_POINTER_FORMAT", "REQUIRED_V2_CUTOVER_RACE_COUNT",
    "HISTORICAL_V1_ARTIFACT_METADATA", "V1_DEPRECATION_METADATA",
    "validate_active_catalog", "validate_v2_cutover_contract",
    "CatalogV2Payload", "CatalogV2RaceRecord", "CatalogV2SessionRecord",
    "serialize_catalog_json", "session_code_from_generation_id", "strip_generation_suffix",
]
