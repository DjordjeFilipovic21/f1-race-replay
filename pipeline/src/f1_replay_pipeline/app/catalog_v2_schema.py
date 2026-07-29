"""Versioned, session-indexed season catalog contract."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import PurePosixPath
import re

from f1_replay_pipeline.domain.generation_identity import validate_generation_id


CATALOG_SCHEMA_VERSION = 2
CANONICAL_POINTER_FORMAT = "canonical-parquet-v1"
BROWSER_POINTER_FORMAT = "browser-delivery-v1"
_SUFFIX = re.compile(r"-(?:force|browser)-[0-9]+$")
_RACE_ID = re.compile(r"^(?P<year>[0-9]{4})-round-(?P<round>[0-9]+)(?:-[A-Za-z0-9._-]+)?$")
_GENERATION_ID = re.compile(r"^(?P<year>[0-9]{4})-round-(?P<round>[0-9]+)-(?P<session>.+)$")


def serialize_catalog_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"


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
    if path.is_absolute() or "\\" in text or any(part in {"", ".", ".."} for part in path.parts):
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
            if not all(pointer is not None for pointer in pointers):
                raise ValueError("canonical_pointer and browser_pointer must be provided together")
            if self.generation_id is None or self.delivery_version is None:
                raise ValueError("pointers require generation_id and delivery_version")
            _require_pointer(self.canonical_pointer, "canonical_pointer")
            _require_pointer(self.browser_pointer, "browser_pointer")
            if not self.validated:
                raise ValueError("unvalidated sessions must not claim pointer paths")
        if self.validated and (
            self.generation_id is None or self.delivery_version is None
            or self.canonical_pointer is None or self.browser_pointer is None
        ):
            raise ValueError("validated sessions require complete artifact references")

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
    "CatalogV2Payload", "CatalogV2RaceRecord", "CatalogV2SessionRecord",
    "serialize_catalog_json", "session_code_from_generation_id", "strip_generation_suffix",
]
