"""Immutable values exposed by the canonical-to-browser reader boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
import re
from types import MappingProxyType
from typing import Literal, cast

import polars as pl


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_DRIVER_ID = re.compile(r"[A-Z0-9]{2,4}\Z")
_FIXTURE_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
MAX_INT64 = (1 << 63) - 1
FASTF1_POSITION_UNITS_PER_METER = 10.0
TIMELINE_SUMMARY_SCHEMA_ID = "urn:f1-cache-replay:schema:replay-data:v1:timeline-summary"
BROWSER_LAP_SECTOR_SIDECAR_SCHEMA_ID = "urn:f1-cache-replay:schema:replay-data:v1:browser-lap-sector-sidecar"
PENALTY_SIDECAR_SCHEMA_ID = "urn:f1-cache-replay:schema:replay-data:v1:penalty-sidecar"
BROWSER_PENALTY_SIDECAR_SCHEMA_ID = PENALTY_SIDECAR_SCHEMA_ID
STINT_SUMMARY_SCHEMA_ID = "urn:f1-cache-replay:schema:replay-data:v1:stint-summary"
PIT_LOSS_MODEL_SCHEMA_ID = "urn:f1-cache-replay:schema:replay-data:v1:pit-loss-model"
WEATHER_SIDECAR_SCHEMA_ID = "urn:f1-cache-replay:schema:replay-data:v1:weather-sidecar"
BROWSER_WEATHER_SIDECAR_SCHEMA_ID = WEATHER_SIDECAR_SCHEMA_ID
TimelineSummaryKind = Literal["yellow", "sc", "red", "vsc"]


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
    track_distance_meters: tuple[float | None, ...]
    gap_to_leader_ms: tuple[float | None, ...]
    position: tuple[int | None, ...]
    rpm: tuple[float | None, ...] = ()
    is_finished: tuple[bool | None, ...] = ()
    tyre_age: tuple[int | None, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.driver_id, str) or not self.driver_id:
            raise ValueError("driver_id must be a non-empty string")
        if tuple(sorted(set(self.time_ms))) != self.time_ms:
            raise ValueError("time_ms must be sorted unique integer milliseconds")
        if not all(type(value) is int and 0 <= value <= MAX_INT64 for value in self.time_ms):
            raise TypeError("time_ms must contain non-negative signed Int64 milliseconds")
        size = len(self.time_ms)
        if self.rpm == ():
            object.__setattr__(self, "rpm", (None,) * size)
        if self.is_finished == ():
            object.__setattr__(self, "is_finished", (None,) * size)
        if self.tyre_age == ():
            object.__setattr__(self, "tyre_age", (None,) * size)
        fields = (
            self.x, self.y, self.speed, self.throttle, self.brake, self.gear,
            self.drs, self.status, self.lap, self.tyre_compound,
            self.is_in_pit_lane, self.track_distance_meters,
            self.gap_to_leader_ms, self.position,
            self.rpm, self.is_finished, self.tyre_age,
        )
        if any(not isinstance(field, tuple) or len(field) != size for field in fields):
            raise ValueError("every browser field must be a tuple aligned to time_ms")
        if any(value is not None and (type(value) is not float or not math.isfinite(value)) for field in (self.x, self.y, self.speed, self.throttle, self.rpm) for value in field):
            raise TypeError("continuous driver fields must contain finite floats or null")
        if any(value not in (None, 0, 1) for value in self.brake):
            raise ValueError("brake must contain 0, 1, or null")
        if any(value is not None and type(value) is not int for field in (self.gear, self.drs, self.lap, self.tyre_age) for value in field):
            raise TypeError("discrete driver fields must contain integers or null")
        if any(value is not None and value < 0 for value in self.tyre_age):
            raise ValueError("tyre age must contain non-negative integers or null")
        if any(value is not None and not isinstance(value, str) for field in (self.status, self.tyre_compound) for value in field):
            raise TypeError("categorical driver fields must contain strings or null")
        if any(value is not None and type(value) is not bool for value in self.is_in_pit_lane):
            raise TypeError("pit state must contain booleans or null")
        if any(value is not None and type(value) is not bool for value in self.is_finished):
            raise TypeError("finished state must contain booleans or null")
        if any(value is not None and (type(value) is not float or not math.isfinite(value) or value < 0) for field in (self.track_distance_meters, self.gap_to_leader_ms) for value in field):
            raise ValueError("derived continuous fields must contain non-negative finite floats or null")
        if any(value is not None and (type(value) is not int or value < 1) for value in self.position):
            raise ValueError("position must contain positive integers or null")


@dataclass(frozen=True)
class BrowserDriverLapSector:
    """Columnar completed lap and sector timings for one driver.

    Completed laps are required to have both ``lap_start_ms`` and ``lap_end_ms``;
    sector durations and completion timestamps remain nullable and propagate from
    the canonical source.
    """

    lap_number: tuple[int, ...]
    lap_start_ms: tuple[int, ...]
    lap_end_ms: tuple[int, ...]
    lap_duration_ms: tuple[int | None, ...]
    sector_1_duration_ms: tuple[int | None, ...]
    sector_2_duration_ms: tuple[int | None, ...]
    sector_3_duration_ms: tuple[int | None, ...]
    sector_1_session_time_ms: tuple[int | None, ...]
    sector_2_session_time_ms: tuple[int | None, ...]
    sector_3_session_time_ms: tuple[int | None, ...]

    def __post_init__(self) -> None:
        if not all(isinstance(field, tuple) for field in (
            self.lap_number, self.lap_start_ms, self.lap_end_ms, self.lap_duration_ms,
            self.sector_1_duration_ms, self.sector_2_duration_ms, self.sector_3_duration_ms,
            self.sector_1_session_time_ms, self.sector_2_session_time_ms, self.sector_3_session_time_ms,
        )):
            raise TypeError("every driver lap sector field must be a tuple")
        size = len(self.lap_number)
        fields = (
            self.lap_start_ms, self.lap_end_ms, self.lap_duration_ms,
            self.sector_1_duration_ms, self.sector_2_duration_ms, self.sector_3_duration_ms,
            self.sector_1_session_time_ms, self.sector_2_session_time_ms, self.sector_3_session_time_ms,
        )
        if any(len(field) != size for field in fields):
            raise ValueError("every driver lap sector field must be aligned to lap_number")
        if any(type(value) is not int or value < 1 for value in self.lap_number):
            raise TypeError("lap_number must contain positive integers")
        for field in (self.lap_start_ms, self.lap_end_ms):
            if any(type(value) is not int or not 0 <= value <= MAX_INT64 for value in field):
                raise TypeError("lap start/end times must contain non-negative signed Int64 integers")
        for field in (self.lap_duration_ms,
                      self.sector_1_duration_ms, self.sector_2_duration_ms, self.sector_3_duration_ms,
                      self.sector_1_session_time_ms, self.sector_2_session_time_ms, self.sector_3_session_time_ms):
            if any(value is not None and (type(value) is not int or not 0 <= value <= MAX_INT64) for value in field):
                raise TypeError("nullable timing fields must contain non-negative signed Int64 integers or null")

    def as_dict(self) -> dict[str, object]:
        return {
            "lapNumber": list(self.lap_number),
            "lapStartMs": [value for value in self.lap_start_ms],
            "lapEndMs": [value for value in self.lap_end_ms],
            "lapDurationMs": [value for value in self.lap_duration_ms],
            "sector1DurationMs": [value for value in self.sector_1_duration_ms],
            "sector2DurationMs": [value for value in self.sector_2_duration_ms],
            "sector3DurationMs": [value for value in self.sector_3_duration_ms],
            "sector1SessionTimeMs": [value for value in self.sector_1_session_time_ms],
            "sector2SessionTimeMs": [value for value in self.sector_2_session_time_ms],
            "sector3SessionTimeMs": [value for value in self.sector_3_session_time_ms],
        }


@dataclass(frozen=True)
class BrowserLapSectorSidecar:
    """Compact optional columnar lap and sector data for one browser replay."""

    fixture_id: str
    drivers: Mapping[str, BrowserDriverLapSector]

    def __post_init__(self) -> None:
        if not isinstance(self.fixture_id, str) or not self.fixture_id:
            raise ValueError("lap sector sidecar fixture_id must be a non-empty string")
        if not isinstance(self.drivers, Mapping) or not self.drivers:
            raise ValueError("drivers must be a non-empty mapping")
        if any(not isinstance(key, str) or not _DRIVER_ID.fullmatch(key) for key in self.drivers):
            raise ValueError("driver IDs must match the canonical identifier pattern")
        if len(set(self.drivers)) != len(self.drivers):
            raise ValueError("driver IDs must be unique")
        if any(not isinstance(value, BrowserDriverLapSector) for value in self.drivers.values()):
            raise TypeError("drivers must map to BrowserDriverLapSector values")
        object.__setattr__(self, "drivers", MappingProxyType(dict(sorted(self.drivers.items()))))

    def as_dict(self) -> dict[str, object]:
        return {
            "contractVersion": "v1",
            "fixtureId": self.fixture_id,
            "drivers": {driver_id: driver.as_dict() for driver_id, driver in self.drivers.items()},
        }


@dataclass(frozen=True)
class BrowserPenaltyIssuance:
    """One immutable race-control penalty issuance.

    This model intentionally has no served/active field.  Race-control data
    establishes that a penalty was issued, but does not provide an authoritative
    lifecycle state for it.
    """

    driver_id: str
    session_time_ms: int
    penalty_type: str
    reason: str
    raw_message: str
    lap_number: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.driver_id, str) or not _DRIVER_ID.fullmatch(self.driver_id):
            raise ValueError("penalty issuance driver_id must match the canonical identifier pattern")
        if type(self.session_time_ms) is not int or not 0 <= self.session_time_ms <= MAX_INT64:
            raise ValueError("penalty issuance session_time_ms must be a non-negative signed Int64 integer")
        for value, label in (
            (self.penalty_type, "penalty_type"),
            (self.reason, "reason"),
            (self.raw_message, "raw_message"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"penalty issuance {label} must be a non-empty string")
        if self.lap_number is not None and (type(self.lap_number) is not int or self.lap_number < 1):
            raise ValueError("penalty issuance lap_number must be a positive integer or null")

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "driverId": self.driver_id,
            "sessionTimeMs": self.session_time_ms,
            "penaltyType": self.penalty_type,
            "reason": self.reason,
            "rawMessage": self.raw_message,
        }
        if self.lap_number is not None:
            value["lapNumber"] = self.lap_number
        return value


@dataclass(frozen=True)
class BrowserPenaltySidecar:
    """Optional issued-penalty data for one browser replay."""

    fixture_id: str
    penalty_issuances: tuple[BrowserPenaltyIssuance, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.fixture_id, str) or not self.fixture_id:
            raise ValueError("penalty sidecar fixture_id must be a non-empty string")
        issuances = tuple(self.penalty_issuances)
        if any(not isinstance(value, BrowserPenaltyIssuance) for value in issuances):
            raise TypeError("penalty_issuances must contain BrowserPenaltyIssuance values")
        object.__setattr__(self, "penalty_issuances", issuances)

    def as_dict(self) -> dict[str, object]:
        return {
            "contractVersion": "v1",
            "fixtureId": self.fixture_id,
            "penaltyIssuances": [issuance.as_dict() for issuance in self.penalty_issuances],
        }


@dataclass(frozen=True)
class BrowserDriverStintSummary:
    """Null-preserving columnar tyre stints and pit transitions for one driver."""

    stint_number: tuple[int, ...]
    compound: tuple[str | None, ...]
    start_lap: tuple[int, ...]
    end_lap: tuple[int | None, ...]
    start_time_ms: tuple[int | None, ...]
    end_time_ms: tuple[int | None, ...]
    tyre_life_at_start: tuple[int | None, ...]
    is_fresh_tyre: tuple[bool | None, ...]
    pit_in_time_ms: tuple[int | None, ...]
    pit_out_time_ms: tuple[int | None, ...]

    def __post_init__(self) -> None:
        fields = (
            self.stint_number, self.compound, self.start_lap, self.end_lap,
            self.start_time_ms, self.end_time_ms, self.tyre_life_at_start,
            self.is_fresh_tyre, self.pit_in_time_ms, self.pit_out_time_ms,
        )
        if not all(isinstance(field, tuple) for field in fields):
            raise TypeError("every driver stint summary field must be a tuple")
        size = len(self.stint_number)
        if any(len(field) != size for field in fields[1:]):
            raise ValueError("every driver stint summary field must be aligned to stint_number")
        if any(type(value) is not int or value < 1 for value in self.stint_number):
            raise ValueError("stint_number must contain positive integers")
        if any(
            following <= current
            for current, following in zip(self.stint_number, self.stint_number[1:], strict=False)
        ):
            raise ValueError("stint_number must be strictly increasing")
        if any(type(value) is not int or value < 1 for value in self.start_lap):
            raise ValueError("start_lap must contain positive integers")
        if any(value is not None and (type(value) is not int or value < 1) for value in self.end_lap):
            raise ValueError("end_lap must contain positive integers or null")
        if any(
            end_lap is not None and end_lap < start_lap
            for start_lap, end_lap in zip(self.start_lap, self.end_lap, strict=False)
        ):
            raise ValueError("end_lap must not precede start_lap")
        if any(value is not None and not isinstance(value, str) for value in self.compound):
            raise TypeError("compound must contain strings or null")
        timing_fields = (
            self.start_time_ms, self.end_time_ms, self.pit_in_time_ms, self.pit_out_time_ms,
        )
        if any(
            value is not None and (type(value) is not int or not 0 <= value <= MAX_INT64)
            for field in timing_fields
            for value in field
        ):
            raise TypeError("timing fields must contain non-negative signed Int64 integers or null")
        if any(
            value is not None and (type(value) is not int or value < 0)
            for value in self.tyre_life_at_start
        ):
            raise ValueError("tyre_life_at_start must contain non-negative integers or null")
        if any(value is not None and type(value) is not bool for value in self.is_fresh_tyre):
            raise TypeError("is_fresh_tyre must contain booleans or null")

    def as_dict(self) -> dict[str, object]:
        return {
            "stintNumber": list(self.stint_number),
            "compound": list(self.compound),
            "startLap": list(self.start_lap),
            "endLap": list(self.end_lap),
            "startTimeMs": list(self.start_time_ms),
            "endTimeMs": list(self.end_time_ms),
            "tyreLifeAtStart": list(self.tyre_life_at_start),
            "isFreshTyre": list(self.is_fresh_tyre),
            "pitInTimeMs": list(self.pit_in_time_ms),
            "pitOutTimeMs": list(self.pit_out_time_ms),
        }


@dataclass(frozen=True)
class BrowserStintSummary:
    """Compact optional columnar stint data for one browser replay."""

    fixture_id: str
    drivers: Mapping[str, BrowserDriverStintSummary]

    def __post_init__(self) -> None:
        if not isinstance(self.fixture_id, str) or not self.fixture_id:
            raise ValueError("stint summary fixture_id must be a non-empty string")
        if not isinstance(self.drivers, Mapping) or not self.drivers:
            raise ValueError("drivers must be a non-empty mapping")
        if any(not isinstance(key, str) or not _DRIVER_ID.fullmatch(key) for key in self.drivers):
            raise ValueError("driver IDs must match the canonical identifier pattern")
        if len(set(self.drivers)) != len(self.drivers):
            raise ValueError("driver IDs must be unique")
        if any(not isinstance(value, BrowserDriverStintSummary) for value in self.drivers.values()):
            raise TypeError("drivers must map to BrowserDriverStintSummary values")
        object.__setattr__(self, "drivers", MappingProxyType(dict(sorted(self.drivers.items()))))

    def as_dict(self) -> dict[str, object]:
        return {
            "contractVersion": "v1",
            "fixtureId": self.fixture_id,
            "drivers": {driver_id: driver.as_dict() for driver_id, driver in self.drivers.items()},
        }


@dataclass(frozen=True)
class BrowserLapStart:
    """One immutable leader-lap navigation marker in absolute session time."""

    lap: int
    start_ms: int

    def __post_init__(self) -> None:
        if type(self.lap) is not int or self.lap < 1:
            raise ValueError("lap start lap must be a positive integer")
        if type(self.start_ms) is not int or not 0 <= self.start_ms <= MAX_INT64:
            raise ValueError("lap start time must be a non-negative signed Int64 integer")

    def as_dict(self) -> dict[str, int]:
        return {"lap": self.lap, "startMs": self.start_ms}


@dataclass(frozen=True)
class BrowserTimelineInterval:
    """One half-open, absolute-time status interval in the compact summary."""

    kind: TimelineSummaryKind
    start_ms: int
    end_ms: int

    def __post_init__(self) -> None:
        if self.kind not in {"yellow", "sc", "red", "vsc"}:
            raise ValueError("timeline interval kind is invalid")
        if any(type(value) is not int or not 0 <= value <= MAX_INT64 for value in (self.start_ms, self.end_ms)):
            raise ValueError("timeline interval times must be non-negative signed Int64 integers")
        if self.start_ms >= self.end_ms:
            raise ValueError("timeline intervals must be half-open and non-empty")

    def as_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "startMs": self.start_ms, "endMs": self.end_ms}


@dataclass(frozen=True)
class BrowserDnfMarker:
    """One final-result DNF marker at an absolute replay time."""

    driver_id: str
    time_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.driver_id, str) or not self.driver_id:
            raise ValueError("DNF marker driver_id must be a non-empty string")
        if type(self.time_ms) is not int or not 0 <= self.time_ms <= MAX_INT64:
            raise ValueError("DNF marker time must be a non-negative signed Int64 integer")

    def as_dict(self) -> dict[str, object]:
        return {"driverId": self.driver_id, "timeMs": self.time_ms}


@dataclass(frozen=True)
class BrowserTimelineSummary:
    """Immutable, deterministic status and DNF data for one browser replay."""

    fixture_id: str
    start_ms: int
    end_ms: int
    intervals: tuple[BrowserTimelineInterval, ...] = ()
    dnf_markers: tuple[BrowserDnfMarker, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.fixture_id, str) or not self.fixture_id:
            raise ValueError("timeline summary fixture_id must be a non-empty string")
        if any(type(value) is not int or not 0 <= value <= MAX_INT64 for value in (self.start_ms, self.end_ms)):
            raise ValueError("timeline summary bounds must be non-negative signed Int64 integers")
        if self.start_ms >= self.end_ms:
            raise ValueError("timeline summary bounds must be a non-empty interval")
        intervals = tuple(self.intervals)
        markers = tuple(self.dnf_markers)
        if any(not isinstance(interval, BrowserTimelineInterval) for interval in intervals):
            raise TypeError("timeline intervals must contain BrowserTimelineInterval values")
        if any(not isinstance(marker, BrowserDnfMarker) for marker in markers):
            raise TypeError("DNF markers must contain BrowserDnfMarker values")
        if any(
            interval.start_ms < self.start_ms or interval.end_ms > self.end_ms
            for interval in intervals
        ):
            raise ValueError("timeline intervals must be within replay bounds")
        if any(
            marker.time_ms < self.start_ms or marker.time_ms >= self.end_ms
            for marker in markers
        ):
            raise ValueError("DNF markers must be within replay bounds")
        if intervals != tuple(sorted(intervals, key=lambda value: (value.start_ms, value.end_ms, value.kind))):
            raise ValueError("timeline intervals must be deterministically ordered")
        if markers != tuple(sorted(markers, key=lambda value: (value.time_ms, value.driver_id))):
            raise ValueError("DNF markers must be deterministically ordered")
        if len({marker.driver_id for marker in markers}) != len(markers):
            raise ValueError("DNF markers must contain one marker per driver")
        object.__setattr__(self, "intervals", intervals)
        object.__setattr__(self, "dnf_markers", markers)

    def as_dict(self) -> dict[str, object]:
        return {
            "contractVersion": "v1",
            "fixtureId": self.fixture_id,
            "startMs": self.start_ms,
            "endMs": self.end_ms,
            "intervals": [interval.as_dict() for interval in self.intervals],
            "dnfMarkers": [marker.as_dict() for marker in self.dnf_markers],
        }


def _validate_signed_int64(value: object, label: str, *, minimum: int | None = None) -> None:
    """Validate an exact Python integer within the signed Int64 range."""
    if type(value) is not int or not -(1 << 63) <= value <= MAX_INT64:
        raise TypeError(f"{label} must be a signed Int64 integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} must be at least {minimum}")


def _validate_weather_float_values(
    values: tuple[object, ...],
    label: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive_minimum: bool = False,
) -> None:
    """Validate nullable finite float measurements at the sidecar boundary."""
    for value in values:
        if value is None:
            continue
        if type(value) is not float or not math.isfinite(value):
            raise TypeError(f"{label} must contain finite floats or null")
        if minimum is not None and (value <= minimum if exclusive_minimum else value < minimum):
            comparison = "greater than" if exclusive_minimum else "at least"
            raise ValueError(f"{label} must be {comparison} {minimum}")
        if maximum is not None and value > maximum:
            raise ValueError(f"{label} must be at most {maximum}")


@dataclass(frozen=True)
class BrowserPitLossModel:
    """Immutable causal pit-loss estimates sampled over replay time."""

    fixture_id: str
    method: str
    baseline_ms: int
    prior_weight: int
    time_ms: tuple[int, ...]
    estimated_loss_ms: tuple[int, ...]
    observed_sample_count: tuple[int, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.fixture_id, str) or not self.fixture_id:
            raise ValueError("pit loss model fixture_id must be a non-empty string")
        if self.method != "global-prior-weighted-mean-v1":
            raise ValueError("pit loss model method is invalid")
        _validate_signed_int64(self.baseline_ms, "baseline_ms", minimum=1)
        _validate_signed_int64(self.prior_weight, "prior_weight", minimum=1)

        time_ms = tuple(self.time_ms)
        estimated_loss_ms = tuple(self.estimated_loss_ms)
        observed_sample_count = tuple(self.observed_sample_count)
        arrays = (time_ms, estimated_loss_ms, observed_sample_count)
        if not time_ms:
            raise ValueError("pit loss model arrays must be non-empty")
        if any(len(values) != len(time_ms) for values in arrays):
            raise ValueError("pit loss model arrays must be aligned")
        for value in time_ms:
            _validate_signed_int64(value, "time_ms", minimum=0)
        for value in estimated_loss_ms:
            _validate_signed_int64(value, "estimated_loss_ms", minimum=0)
        for value in observed_sample_count:
            _validate_signed_int64(value, "observed_sample_count", minimum=0)
        if any(following <= current for current, following in zip(time_ms, time_ms[1:], strict=False)):
            raise ValueError("time_ms must be strictly increasing")
        if estimated_loss_ms[0] != self.baseline_ms:
            raise ValueError("first estimated_loss_ms must equal baseline_ms")
        if observed_sample_count[0] != 0:
            raise ValueError("first observed_sample_count must be zero")
        if any(
            following <= current
            for current, following in zip(observed_sample_count, observed_sample_count[1:], strict=False)
        ):
            raise ValueError("observed_sample_count must strictly increase after the initial sample")
        object.__setattr__(self, "time_ms", time_ms)
        object.__setattr__(self, "estimated_loss_ms", estimated_loss_ms)
        object.__setattr__(self, "observed_sample_count", observed_sample_count)

    def as_dict(self) -> dict[str, object]:
        return {
            "contractVersion": "v1",
            "fixtureId": self.fixture_id,
            "method": self.method,
            "baselineMs": self.baseline_ms,
            "priorWeight": self.prior_weight,
            "timeMs": list(self.time_ms),
            "estimatedLossMs": list(self.estimated_loss_ms),
            "observedSampleCount": list(self.observed_sample_count),
        }


@dataclass(frozen=True)
class BrowserArtifactReference:
    """Immutable manifest reference for a digested browser artifact."""

    path: str
    schema_id: str
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path:
            raise ValueError("artifact path must be a non-empty string")
        if not isinstance(self.schema_id, str) or not self.schema_id:
            raise ValueError("artifact schema_id must be a non-empty string")
        if not isinstance(self.sha256, str) or not _SHA256.fullmatch(self.sha256):
            raise ValueError("artifact sha256 must be a SHA-256 hexadecimal digest")

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "schemaId": self.schema_id, "sha256": self.sha256}


@dataclass(frozen=True)
class BrowserTimelineSummaryReference(BrowserArtifactReference):
    """Immutable manifest reference for the optional compact timeline artifact."""

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.path != "timeline-summary.json":
            raise ValueError("timeline summary path must be timeline-summary.json")
        if self.schema_id != TIMELINE_SUMMARY_SCHEMA_ID:
            raise ValueError("timeline summary schema_id is invalid")


@dataclass(frozen=True)
class BrowserLapSectorSidecarReference(BrowserArtifactReference):
    """Immutable manifest reference for the optional compact lap sector sidecar."""

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.path != "lap-sector-sidecar.json":
            raise ValueError("lap sector sidecar path must be lap-sector-sidecar.json")
        if self.schema_id != BROWSER_LAP_SECTOR_SIDECAR_SCHEMA_ID:
            raise ValueError("lap sector sidecar schema_id is invalid")


@dataclass(frozen=True)
class BrowserPenaltySidecarReference(BrowserArtifactReference):
    """Immutable manifest reference for the optional issued-penalty sidecar."""

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.path != "penalty-sidecar.json":
            raise ValueError("penalty sidecar path must be penalty-sidecar.json")
        if self.schema_id != PENALTY_SIDECAR_SCHEMA_ID:
            raise ValueError("penalty sidecar schema_id is invalid")


@dataclass(frozen=True)
class BrowserStintSummaryReference(BrowserArtifactReference):
    """Immutable manifest reference for the optional compact stint summary."""

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.path != "stint-summary.json":
            raise ValueError("stint summary path must be stint-summary.json")
        if self.schema_id != STINT_SUMMARY_SCHEMA_ID:
            raise ValueError("stint summary schema_id is invalid")


@dataclass(frozen=True)
class BrowserPitLossModelReference(BrowserArtifactReference):
    """Immutable manifest reference for the optional pit-loss model."""

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.path != "pit-loss-model.json":
            raise ValueError("pit loss model path must be pit-loss-model.json")
        if self.schema_id != PIT_LOSS_MODEL_SCHEMA_ID:
            raise ValueError("pit loss model schema_id is invalid")


@dataclass(frozen=True)
class BrowserWeatherSidecar:
    """Immutable native-cadence weather observations for one browser replay."""

    fixture_id: str
    time_ms: tuple[int, ...]
    air_temp_c: tuple[float | None, ...]
    humidity_pct: tuple[float | None, ...]
    pressure_mbar: tuple[float | None, ...]
    rainfall: tuple[bool | None, ...]
    track_temp_c: tuple[float | None, ...]
    wind_direction_deg: tuple[int | None, ...]
    wind_speed_mps: tuple[float | None, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.fixture_id, str) or not _FIXTURE_ID.fullmatch(self.fixture_id):
            raise ValueError("weather sidecar fixture_id must match the canonical identifier pattern")

        arrays = (
            tuple(self.time_ms),
            tuple(self.air_temp_c),
            tuple(self.humidity_pct),
            tuple(self.pressure_mbar),
            tuple(self.rainfall),
            tuple(self.track_temp_c),
            tuple(self.wind_direction_deg),
            tuple(self.wind_speed_mps),
        )
        if not arrays[0]:
            raise ValueError("weather sidecar arrays must be non-empty")
        if any(len(values) != len(arrays[0]) for values in arrays[1:]):
            raise ValueError("weather sidecar arrays must be equal length")

        for value in arrays[0]:
            _validate_signed_int64(value, "weather time_ms", minimum=0)
        if any(following <= current for current, following in zip(arrays[0], arrays[0][1:], strict=False)):
            raise ValueError("weather time_ms must be strictly increasing")

        _validate_weather_float_values(arrays[1], "air_temp_c", minimum=0.0, exclusive_minimum=True)
        _validate_weather_float_values(arrays[2], "humidity_pct", minimum=0.0, maximum=100.0)
        _validate_weather_float_values(arrays[3], "pressure_mbar", minimum=0.0, exclusive_minimum=True)
        if any(value is not None and type(value) is not bool for value in arrays[4]):
            raise TypeError("rainfall must contain booleans or null")
        _validate_weather_float_values(arrays[5], "track_temp_c", minimum=0.0, exclusive_minimum=True)
        if any(
            value is not None and (type(value) is not int or not 0 <= value <= 359)
            for value in arrays[6]
        ):
            raise ValueError("wind_direction_deg must contain integers from 0 through 359 or null")
        _validate_weather_float_values(arrays[7], "wind_speed_mps", minimum=0.0)

        for field, values in zip(
            ("time_ms", "air_temp_c", "humidity_pct", "pressure_mbar", "rainfall",
             "track_temp_c", "wind_direction_deg", "wind_speed_mps"),
            arrays,
            strict=True,
        ):
            object.__setattr__(self, field, values)

    def as_dict(self) -> dict[str, object]:
        """Return the contract-shaped payload in its frozen field order."""
        return {
            "contractVersion": "v1",
            "fixtureId": self.fixture_id,
            "timeMs": list(self.time_ms),
            "airTempC": list(self.air_temp_c),
            "humidityPct": list(self.humidity_pct),
            "pressureMbar": list(self.pressure_mbar),
            "rainfall": list(self.rainfall),
            "trackTempC": list(self.track_temp_c),
            "windDirectionDeg": list(self.wind_direction_deg),
            "windSpeedMps": list(self.wind_speed_mps),
        }


@dataclass(frozen=True)
class BrowserWeatherSidecarReference(BrowserArtifactReference):
    """Immutable manifest reference for the optional weather sidecar."""

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.path != "weather-sidecar.json":
            raise ValueError("weather sidecar path must be weather-sidecar.json")
        if self.schema_id != WEATHER_SIDECAR_SCHEMA_ID:
            raise ValueError("weather sidecar schema_id is invalid")


@dataclass(frozen=True)
class BrowserManifest:
    """Immutable contract metadata derived from one canonical snapshot."""

    fixture_id: str
    fixture_name: str
    drivers: tuple[Mapping[str, object], ...]
    lap_starts: tuple[BrowserLapStart, ...] = ()
    timeline_summary: BrowserArtifactReference | Mapping[str, object] | None = None
    lap_sector_sidecar: BrowserArtifactReference | Mapping[str, object] | None = None
    stint_summary: BrowserArtifactReference | Mapping[str, object] | None = None
    pit_loss_model: BrowserArtifactReference | Mapping[str, object] | None = None
    penalty_sidecar: BrowserArtifactReference | Mapping[str, object] | None = None
    season_metadata: Mapping[str, object] | None = None
    telemetry_capabilities: Mapping[str, object] | None = None
    weather_sidecar: BrowserArtifactReference | Mapping[str, object] | None = None

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
        season_metadata = _freeze_season_metadata(self.season_metadata)
        telemetry_capabilities = _freeze_telemetry_capabilities(self.telemetry_capabilities)
        lap_starts = tuple(self.lap_starts)
        if any(not isinstance(marker, BrowserLapStart) for marker in lap_starts):
            raise TypeError("lap_starts must contain BrowserLapStart values")
        if any(
            following.lap <= current.lap or following.start_ms < current.start_ms
            for current, following in zip(lap_starts, lap_starts[1:], strict=False)
        ):
            raise ValueError("lap starts must have increasing laps and nondecreasing timestamps")
        timeline_summary = self.timeline_summary
        if isinstance(timeline_summary, Mapping):
            required = {"path", "schemaId", "sha256"}
            if set(timeline_summary) != required:
                raise ValueError("timeline_summary must contain path, schemaId, and sha256")
            timeline_summary = BrowserTimelineSummaryReference(
                cast(str, timeline_summary["path"]),
                cast(str, timeline_summary["schemaId"]),
                cast(str, timeline_summary["sha256"]),
            )
        elif isinstance(timeline_summary, BrowserArtifactReference):
            timeline_summary = BrowserTimelineSummaryReference(
                timeline_summary.path, timeline_summary.schema_id, timeline_summary.sha256,
            )
        elif timeline_summary is not None:
            raise TypeError("timeline_summary must be a BrowserArtifactReference or mapping")
        lap_sector_sidecar = self.lap_sector_sidecar
        if isinstance(lap_sector_sidecar, Mapping):
            required = {"path", "schemaId", "sha256"}
            if set(lap_sector_sidecar) != required:
                raise ValueError("lap_sector_sidecar must contain path, schemaId, and sha256")
            lap_sector_sidecar = BrowserLapSectorSidecarReference(
                cast(str, lap_sector_sidecar["path"]),
                cast(str, lap_sector_sidecar["schemaId"]),
                cast(str, lap_sector_sidecar["sha256"]),
            )
        elif isinstance(lap_sector_sidecar, BrowserArtifactReference):
            lap_sector_sidecar = BrowserLapSectorSidecarReference(
                lap_sector_sidecar.path, lap_sector_sidecar.schema_id, lap_sector_sidecar.sha256,
            )
        elif lap_sector_sidecar is not None:
            raise TypeError("lap_sector_sidecar must be a BrowserArtifactReference or mapping")
        stint_summary = self.stint_summary
        if isinstance(stint_summary, Mapping):
            required = {"path", "schemaId", "sha256"}
            if set(stint_summary) != required:
                raise ValueError("stint_summary must contain path, schemaId, and sha256")
            stint_summary = BrowserStintSummaryReference(
                cast(str, stint_summary["path"]),
                cast(str, stint_summary["schemaId"]),
                cast(str, stint_summary["sha256"]),
            )
        elif isinstance(stint_summary, BrowserArtifactReference):
            stint_summary = BrowserStintSummaryReference(
                stint_summary.path, stint_summary.schema_id, stint_summary.sha256,
            )
        elif stint_summary is not None:
            raise TypeError("stint_summary must be a BrowserArtifactReference or mapping")
        pit_loss_model = self.pit_loss_model
        if isinstance(pit_loss_model, Mapping):
            required = {"path", "schemaId", "sha256"}
            if set(pit_loss_model) != required:
                raise ValueError("pit_loss_model must contain path, schemaId, and sha256")
            pit_loss_model = BrowserPitLossModelReference(
                cast(str, pit_loss_model["path"]),
                cast(str, pit_loss_model["schemaId"]),
                cast(str, pit_loss_model["sha256"]),
            )
        elif isinstance(pit_loss_model, BrowserArtifactReference):
            pit_loss_model = BrowserPitLossModelReference(
                pit_loss_model.path, pit_loss_model.schema_id, pit_loss_model.sha256,
            )
        elif pit_loss_model is not None:
            raise TypeError("pit_loss_model must be a BrowserArtifactReference or mapping")
        penalty_sidecar = self.penalty_sidecar
        if isinstance(penalty_sidecar, Mapping):
            required = {"path", "schemaId", "sha256"}
            if set(penalty_sidecar) != required:
                raise ValueError("penalty_sidecar must contain path, schemaId, and sha256")
            penalty_sidecar = BrowserPenaltySidecarReference(
                cast(str, penalty_sidecar["path"]),
                cast(str, penalty_sidecar["schemaId"]),
                cast(str, penalty_sidecar["sha256"]),
            )
        elif isinstance(penalty_sidecar, BrowserArtifactReference):
            penalty_sidecar = BrowserPenaltySidecarReference(
                penalty_sidecar.path, penalty_sidecar.schema_id, penalty_sidecar.sha256,
            )
        elif penalty_sidecar is not None:
            raise TypeError("penalty_sidecar must be a BrowserArtifactReference or mapping")
        weather_sidecar = self.weather_sidecar
        if isinstance(weather_sidecar, Mapping):
            required = {"path", "schemaId", "sha256"}
            if set(weather_sidecar) != required:
                raise ValueError("weather_sidecar must contain path, schemaId, and sha256")
            weather_sidecar = BrowserWeatherSidecarReference(
                cast(str, weather_sidecar["path"]),
                cast(str, weather_sidecar["schemaId"]),
                cast(str, weather_sidecar["sha256"]),
            )
        elif isinstance(weather_sidecar, BrowserArtifactReference):
            weather_sidecar = BrowserWeatherSidecarReference(
                weather_sidecar.path, weather_sidecar.schema_id, weather_sidecar.sha256,
            )
        elif weather_sidecar is not None:
            raise TypeError("weather_sidecar must be a BrowserArtifactReference or mapping")
        object.__setattr__(self, "drivers", frozen_drivers)
        object.__setattr__(self, "lap_starts", lap_starts)
        object.__setattr__(self, "timeline_summary", timeline_summary)
        object.__setattr__(self, "lap_sector_sidecar", lap_sector_sidecar)
        object.__setattr__(self, "stint_summary", stint_summary)
        object.__setattr__(self, "pit_loss_model", pit_loss_model)
        object.__setattr__(self, "penalty_sidecar", penalty_sidecar)
        object.__setattr__(self, "season_metadata", season_metadata)
        object.__setattr__(self, "telemetry_capabilities", telemetry_capabilities)
        object.__setattr__(self, "weather_sidecar", weather_sidecar)

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
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
        if self.season_metadata is not None:
            value["seasonMetadata"] = dict(self.season_metadata)
        if self.telemetry_capabilities is not None:
            value["telemetryCapabilities"] = dict(self.telemetry_capabilities)
        if self.lap_starts:
            value["lapStarts"] = [marker.as_dict() for marker in self.lap_starts]
        if self.timeline_summary is not None:
            value["timelineSummary"] = cast(
                BrowserArtifactReference, self.timeline_summary,
            ).as_dict()
        if self.lap_sector_sidecar is not None:
            value["lapSectorSidecar"] = cast(
                BrowserArtifactReference, self.lap_sector_sidecar,
            ).as_dict()
        if self.stint_summary is not None:
            value["stintSummary"] = cast(
                BrowserArtifactReference, self.stint_summary,
            ).as_dict()
        if self.pit_loss_model is not None:
            value["pitLossModel"] = cast(
                BrowserArtifactReference, self.pit_loss_model,
            ).as_dict()
        if self.penalty_sidecar is not None:
            value["penaltySidecar"] = cast(
                BrowserArtifactReference, self.penalty_sidecar,
            ).as_dict()
        if self.weather_sidecar is not None:
            value["weatherSidecar"] = cast(
                BrowserArtifactReference, self.weather_sidecar,
            ).as_dict()
        return value


def _freeze_season_metadata(value: Mapping[str, object] | None) -> Mapping[str, object] | None:
    if value is None:
        return None
    frozen = deep_freeze_json(value)
    if not isinstance(frozen, Mapping) or set(frozen) != {"year"}:
        raise ValueError("season_metadata must contain only year")
    year = frozen["year"]
    if type(year) is not int or not 1 <= year <= 9999:
        raise ValueError("season_metadata year must be an integer from 1 to 9999")
    return cast(Mapping[str, object], frozen)


def _freeze_telemetry_capabilities(value: Mapping[str, object] | None) -> Mapping[str, object] | None:
    if value is None:
        return None
    frozen = deep_freeze_json(value)
    required = {"drs", "overtakeMode", "activeAero", "ersReplacement"}
    if not isinstance(frozen, Mapping) or set(frozen) != required:
        raise ValueError("telemetry_capabilities must contain the four capability values")
    valid = {"available", "not-published"}
    if any(not isinstance(item, str) or item not in valid for item in frozen.values()):
        raise ValueError("telemetry capability values must be available or not-published")
    return cast(Mapping[str, object], frozen)


__all__ = [
    "BrowserArtifactReference", "BrowserDnfMarker", "BrowserDriverFields",
    "BrowserDriverLapSector", "BrowserDriverStintSummary", "BrowserLapSectorSidecar",
    "BrowserLapSectorSidecarReference",
    "BrowserLapStart", "BrowserManifest", "BrowserPenaltyIssuance", "BrowserPenaltySidecar",
    "BrowserPenaltySidecarReference",
    "BrowserTimelineInterval", "BrowserTimelineSummary",
    "BrowserWeatherSidecar", "BrowserWeatherSidecarReference",
    "BrowserPitLossModel", "BrowserPitLossModelReference", "BrowserTimelineSummaryReference",
    "BrowserStintSummary", "BrowserStintSummaryReference",
    "CanonicalGenerationSnapshot",
    "BROWSER_LAP_SECTOR_SIDECAR_SCHEMA_ID", "FASTF1_POSITION_UNITS_PER_METER", "MAX_INT64",
    "BROWSER_PENALTY_SIDECAR_SCHEMA_ID", "PENALTY_SIDECAR_SCHEMA_ID", "PIT_LOSS_MODEL_SCHEMA_ID",
    "STINT_SUMMARY_SCHEMA_ID", "BROWSER_WEATHER_SIDECAR_SCHEMA_ID",
    "WEATHER_SIDECAR_SCHEMA_ID",
    "TIMELINE_SUMMARY_SCHEMA_ID",
    "TimelineSummaryKind",
    "deep_freeze_json",
]
