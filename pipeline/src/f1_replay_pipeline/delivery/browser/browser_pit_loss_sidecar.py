"""Immutable values for the status-aware browser pit-loss sidecar.

The curated method (``curated-track-baseline-v1``) serializes catalog-backed
replay-start values only.  Catalog audit metadata (including ``sourceStatus``,
provenance, evidence, confidence, and derivation details) stays in the
repository-local catalog and never enters this public payload.  Current-race
``observedSampleCount`` arrays are never part of a curated payload.  Legacy
``track-status-median-v1`` timelines keep their causal ``observedSampleCount``
arrays and remain fully readable.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import re
from typing import TYPE_CHECKING, Literal, cast

from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    MAX_INT64,
    PIT_LOSS_ESTIMATE_SIDECAR_FILENAME,
    PIT_LOSS_ESTIMATE_SIDECAR_SCHEMA_ID,
    PIT_LOSS_ESTIMATE_METHOD,
)
from f1_replay_pipeline.delivery.browser.browser_pit_loss_baseline_catalog import CURATED_BASELINE_METHOD

if TYPE_CHECKING:
    from f1_replay_pipeline.delivery.browser.browser_pit_loss_model import PitLossObservation


_IDENTIFIER = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_TIMELINE_FIELDS = {"timeMs", "estimatedLossMs", "observedSampleCount"}
_CURATED_TIMELINE_FIELDS = {"timeMs", "estimatedLossMs"}
# Keep the wire value explicit so a future default-method change cannot make
# existing track-status-median-v1 artifacts unreadable.
LEGACY_PIT_LOSS_ESTIMATE_METHOD = "track-status-median-v1"


@dataclass(frozen=True)
class BrowserPitLossEstimateTimeline:
    """A generation-time estimate timeline shared by race and status values.

    New sidecars contain one replay-start point.  Older multi-point causal
    timelines remain accepted so browser delivery can read legacy artifacts.
    """

    time_ms: tuple[int, ...]
    estimated_loss_ms: tuple[int, ...]
    observed_sample_count: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        time_ms = tuple(self.time_ms)
        estimated_loss_ms = tuple(self.estimated_loss_ms)
        observed_sample_count = (
            None if self.observed_sample_count is None else tuple(self.observed_sample_count)
        )
        arrays = (time_ms, estimated_loss_ms)
        if not time_ms:
            raise ValueError("pit loss estimate timeline arrays must be non-empty")
        if any(len(values) != len(time_ms) for values in arrays):
            raise ValueError("pit loss estimate timeline arrays must be aligned")
        for value in time_ms:
            _validate_unsigned_int64(value, "time_ms")
        for value in estimated_loss_ms:
            _validate_unsigned_int64(value, "estimated_loss_ms")
        if observed_sample_count is not None:
            if len(observed_sample_count) != len(time_ms):
                raise ValueError("pit loss estimate timeline arrays must be aligned")
            for value in observed_sample_count:
                _validate_unsigned_int64(value, "observed_sample_count")
        if any(
            following <= current
            for current, following in zip(time_ms, time_ms[1:], strict=False)
        ):
            raise ValueError("pit loss estimate time_ms must be strictly increasing")
        if observed_sample_count is not None and len(observed_sample_count) > 1:
            if observed_sample_count[0] != 0:
                raise ValueError("legacy pit loss estimate first observed_sample_count must be zero")
            if any(
                following <= current
                for current, following in zip(observed_sample_count, observed_sample_count[1:], strict=False)
            ):
                raise ValueError("pit loss estimate observed_sample_count must be strictly increasing")
        object.__setattr__(self, "time_ms", time_ms)
        object.__setattr__(self, "estimated_loss_ms", estimated_loss_ms)
        object.__setattr__(self, "observed_sample_count", observed_sample_count)

    def as_dict(self) -> dict[str, object]:
        """Serialize the timeline using the sidecar's wire names."""
        value: dict[str, object] = {
            "timeMs": list(self.time_ms),
            "estimatedLossMs": list(self.estimated_loss_ms),
        }
        if self.observed_sample_count is not None:
            value["observedSampleCount"] = list(self.observed_sample_count)
        return value


@dataclass(frozen=True)
class BrowserPitLossEstimateUnavailable:
    """Explicit status occurrence for which no eligible sample was available."""

    status: Literal["unavailable"] = "unavailable"

    def __post_init__(self) -> None:
        if self.status != "unavailable":
            raise ValueError("pit loss status estimate status must be unavailable")

    def as_dict(self) -> dict[str, str]:
        return {"status": self.status}


StatusEstimate = BrowserPitLossEstimateTimeline | BrowserPitLossEstimateUnavailable
@dataclass(frozen=True)
class BrowserPitLossEstimateSidecar:
    """One immutable, per-track status-aware pit-loss estimate artifact.

    The race timeline is the All Clear/Green baseline.  Safety Car and Virtual
    Safety Car values are kept in their dedicated status timelines instead of
    being mixed into that baseline.  Curated sidecars use a single replay-start
    point for every resolved value.  Catalog audit metadata remains in the
    internal catalog and is never copied into this public sidecar.  Legacy
    sidecars retain their causal timelines.

    ``None`` means that a status never occurred.  An unavailable value means the
    status did occur, but no eligible observation could be assigned to it.
    """

    fixture_id: str
    track_id: str
    method: str
    race: BrowserPitLossEstimateTimeline | Mapping[str, object]
    safety_car: StatusEstimate | Mapping[str, object] | None = None
    virtual_safety_car: StatusEstimate | Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        _validate_identifier(self.fixture_id, "fixture_id")
        _validate_identifier(self.track_id, "track_id")
        if self.method not in (LEGACY_PIT_LOSS_ESTIMATE_METHOD, CURATED_BASELINE_METHOD):
            raise ValueError("pit loss estimate method is invalid")
        is_curated = self.method == CURATED_BASELINE_METHOD
        race = _coerce_timeline(
            self.race, "race", allow_missing_observed_sample_count=is_curated,
        )
        safety_car = _coerce_status_estimate(
            self.safety_car, "safety_car",
            allow_missing_observed_sample_count=is_curated,
        )
        virtual_safety_car = _coerce_status_estimate(
            self.virtual_safety_car, "virtual_safety_car",
            allow_missing_observed_sample_count=is_curated,
        )
        if is_curated and any(
            isinstance(value, BrowserPitLossEstimateTimeline) and len(value.time_ms) != 1
            for value in (safety_car, virtual_safety_car)
        ):
            raise ValueError("curated pit loss status values must be replay-start points")
        if is_curated and (safety_car is None or virtual_safety_car is None):
            raise ValueError("curated pit loss sidecars must resolve Green, SC, and VSC values")
        if is_curated and any(
            isinstance(value, BrowserPitLossEstimateUnavailable)
            for value in (safety_car, virtual_safety_car)
        ):
            raise ValueError(
                "curated pit loss status values must be available replay-start timelines",
            )
        if is_curated and len(race.time_ms) != 1:
            raise ValueError("curated pit loss race value must be a replay-start point")
        if is_curated and any(
            isinstance(value, BrowserPitLossEstimateTimeline)
            and value.observed_sample_count is not None
            for value in (race, safety_car, virtual_safety_car)
        ):
            raise ValueError(
                "curated pit loss timelines cannot carry current-race observedSampleCount",
            )
        if is_curated and all(
            isinstance(value, BrowserPitLossEstimateTimeline)
            for value in (race, safety_car, virtual_safety_car)
        ):
            green_ms = cast(BrowserPitLossEstimateTimeline, race).estimated_loss_ms[0]
            sc_ms = cast(BrowserPitLossEstimateTimeline, safety_car).estimated_loss_ms[0]
            vsc_ms = cast(
                BrowserPitLossEstimateTimeline, virtual_safety_car,
            ).estimated_loss_ms[0]
            if not sc_ms <= vsc_ms <= green_ms:
                raise ValueError("curated pit loss values must satisfy SC <= VSC <= Green")
        object.__setattr__(self, "race", race)
        object.__setattr__(self, "safety_car", safety_car)
        object.__setattr__(self, "virtual_safety_car", virtual_safety_car)

    def as_dict(self) -> dict[str, object]:
        """Serialize the sidecar deterministically to its JSON contract."""
        value: dict[str, object] = {
            "contractVersion": "v2",
            "fixtureId": self.fixture_id,
            "trackId": self.track_id,
            "method": self.method,
            "race": cast(BrowserPitLossEstimateTimeline, self.race).as_dict(),
        }
        if self.safety_car is not None:
            value["safetyCar"] = cast(StatusEstimate, self.safety_car).as_dict()
        if self.virtual_safety_car is not None:
            value["virtualSafetyCar"] = cast(StatusEstimate, self.virtual_safety_car).as_dict()
        return value


def _coerce_timeline(
    value: BrowserPitLossEstimateTimeline | Mapping[str, object], label: str,
    *, allow_missing_observed_sample_count: bool = False,
) -> BrowserPitLossEstimateTimeline:
    if isinstance(value, BrowserPitLossEstimateTimeline):
        if value.observed_sample_count is None and not allow_missing_observed_sample_count:
            # A pre-instantiated legacy timeline must still carry the causal
            # current-race observation array; only curated replay-start values
            # may omit it.  The mapping path below enforces the same contract.
            raise ValueError(f"{label} must contain observedSampleCount")
        return value
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a timeline mapping")
    fields = set(value)
    if allow_missing_observed_sample_count and fields == _TIMELINE_FIELDS:
        # A curated replay-start value is catalog evidence, never a current-race
        # observation: reject an array that would mislabel it as such.
        raise ValueError(
            f"{label} must not contain observedSampleCount in a curated sidecar",
        )
    valid_fields = [_TIMELINE_FIELDS]
    if allow_missing_observed_sample_count:
        valid_fields.append(_CURATED_TIMELINE_FIELDS)
    if fields not in valid_fields:
        raise ValueError(f"{label} must contain timeMs and estimatedLossMs")
    if "observedSampleCount" in value and value["observedSampleCount"] is None:
        raise ValueError(f"{label}.observedSampleCount must be an array when present")
    if not allow_missing_observed_sample_count and value.get("observedSampleCount") is None:
        raise ValueError(f"{label} must contain observedSampleCount")
    return BrowserPitLossEstimateTimeline(
        cast(tuple[int, ...], value["timeMs"]),
        cast(tuple[int, ...], value["estimatedLossMs"]),
        cast(tuple[int, ...] | None, value.get("observedSampleCount")),
    )


def _coerce_status_estimate(
    value: StatusEstimate | Mapping[str, object] | None, label: str,
    *, allow_missing_observed_sample_count: bool = False,
) -> StatusEstimate | None:
    if value is None:
        return None
    if isinstance(value, BrowserPitLossEstimateTimeline):
        return _coerce_timeline(
            value, label, allow_missing_observed_sample_count=allow_missing_observed_sample_count,
        )
    if isinstance(value, BrowserPitLossEstimateUnavailable):
        return value
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a timeline, unavailable status, mapping, or None")
    if allow_missing_observed_sample_count and set(value) == _CURATED_TIMELINE_FIELDS:
        return _coerce_timeline(
            value, label, allow_missing_observed_sample_count=True,
        )
    if set(value) == _TIMELINE_FIELDS:
        if allow_missing_observed_sample_count:
            raise ValueError(
                f"{label} must not contain observedSampleCount in a curated sidecar",
            )
        return _coerce_timeline(
            value, label, allow_missing_observed_sample_count=False,
        )
    if set(value) == {"status"}:
        return BrowserPitLossEstimateUnavailable(cast(Literal["unavailable"], value["status"]))
    raise ValueError(f"{label} must contain a timeline or status unavailable")


def _validate_identifier(value: object, label: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase kebab-case identifier")


def _validate_unsigned_int64(value: object, label: str) -> None:
    if type(value) is not int or not 0 <= value <= MAX_INT64:
        raise TypeError(f"{label} must be a non-negative signed Int64 integer")


def build_pit_loss_estimate_sidecar(
    replay_start_ms: int,
    observations: Iterable[PitLossObservation],
    fixture_id: str = "unknown",
    track_id: str = "unknown",
    track_status_codes: Iterable[int | None] = (),
    *,
    baseline_ms: int = 22_000,
) -> BrowserPitLossEstimateSidecar:
    """Build the pure status-aware sidecar from existing observations.

    The calculation lives in the model module; this entry point keeps the
    sidecar's value types and its construction API discoverable together while
    avoiding a module import cycle.
    """
    from f1_replay_pipeline.delivery.browser.browser_pit_loss_model import (
        build_pit_loss_estimate_sidecar as _build_pit_loss_estimate_sidecar,
    )

    return _build_pit_loss_estimate_sidecar(
        replay_start_ms,
        observations,
        fixture_id,
        track_id,
        track_status_codes,
        baseline_ms=baseline_ms,
    )


# Short aliases keep the focused module convenient without changing the wire model.
PitLossEstimateTimeline = BrowserPitLossEstimateTimeline
PitLossEstimateUnavailable = BrowserPitLossEstimateUnavailable
PitLossEstimateSidecar = BrowserPitLossEstimateSidecar


__all__ = [
    "BrowserPitLossEstimateSidecar",
    "BrowserPitLossEstimateTimeline",
    "BrowserPitLossEstimateUnavailable",
    "build_pit_loss_estimate_sidecar",
    "PitLossEstimateSidecar",
    "PitLossEstimateTimeline",
    "PitLossEstimateUnavailable",
    "PIT_LOSS_ESTIMATE_METHOD",
    "PIT_LOSS_ESTIMATE_SIDECAR_FILENAME",
    "PIT_LOSS_ESTIMATE_SIDECAR_SCHEMA_ID",
    "CURATED_BASELINE_METHOD",
    "LEGACY_PIT_LOSS_ESTIMATE_METHOD",
    "StatusEstimate",
]
