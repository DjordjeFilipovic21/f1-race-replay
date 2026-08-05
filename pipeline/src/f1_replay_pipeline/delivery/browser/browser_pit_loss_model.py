"""Pure diagnostic and catalog-backed browser pit-loss derivation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
import math
from typing import Iterable, Literal, cast

from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    BrowserPitLossModel,
    PIT_LOSS_ESTIMATE_METHOD,
)
from f1_replay_pipeline.delivery.browser.browser_pit_loss_baseline_catalog import (
    DEFAULT_PIT_LOSS_BASELINE_CATALOG,
    CURATED_BASELINE_METHOD,
    PitLossBaselineCatalog,
)
from f1_replay_pipeline.delivery.browser.browser_pit_loss_baseline_resolver import resolve_pit_loss_baseline
from f1_replay_pipeline.delivery.browser.browser_pit_loss_sidecar import (
    BrowserPitLossEstimateSidecar,
    BrowserPitLossEstimateTimeline,
    BrowserPitLossEstimateUnavailable,
)


BASELINE_MS = 22_000
PRIOR_WEIGHT = 2
MODEL_METHOD = "global-prior-weighted-mean-v1"
PitLossStatus = Literal["normal", "safety_car", "virtual_safety_car"]
NORMAL_PIT_LOSS_STATUS: PitLossStatus = "normal"
SAFETY_CAR_PIT_LOSS_STATUS: PitLossStatus = "safety_car"
VIRTUAL_SAFETY_CAR_PIT_LOSS_STATUS: PitLossStatus = "virtual_safety_car"


class CuratedPitLossBaselineUnavailableError(ValueError):
    """Raised when a delivery has no catalog-backed baseline binding."""


@dataclass(frozen=True)
class PitStopCandidate:
    """A proposed, mapped pit transition for one driver."""

    driver_id: str
    pit_in_time_ms: int
    pit_out_time_ms: int
    quality_gate_passed: bool
    mapping_complete_and_unique: bool


@dataclass(frozen=True)
class GapSample:
    """One driver's gap and state observation at a session timestamp."""

    driver_id: str
    time_ms: int
    gap_to_leader_ms: int | float | None
    status: str | None
    is_in_pit_lane: bool | None
    is_on_track: bool | None
    is_finished: bool | None


@dataclass(frozen=True)
class LeaderSnapshot:
    """Leader identity and pit-lane observations around one stop."""

    before_leader_id: str | None
    after_leader_id: str | None
    before_is_in_pit_lane: bool | None
    after_is_in_pit_lane: bool | None
    interval_is_in_pit_lane: tuple[bool | None, ...]


@dataclass(frozen=True)
class StopIntervalStatus:
    """Track-status observations across a stop interval."""

    track_status_codes: tuple[int | None, ...]

    def __post_init__(self) -> None:
        """Freeze callers' iterables without changing their source arrays."""
        object.__setattr__(self, "track_status_codes", tuple(self.track_status_codes))

    @property
    def classification(self) -> PitLossStatus | None:
        """Return the one status class proven by the complete interval."""
        return classify_stop_interval_status(self)


@dataclass(frozen=True)
class PitLossObservation:
    """All pure inputs needed to validate and price one pit stop."""

    candidate: PitStopCandidate
    before_gap: GapSample
    after_gap: GapSample
    leader_snapshot: LeaderSnapshot
    stop_interval_status: StopIntervalStatus
    status: PitLossStatus | None = None

    def __post_init__(self) -> None:
        """Attach deterministic status metadata derived from immutable interval data."""
        classification = classify_stop_interval_status(self.stop_interval_status)
        if self.status is not None and self.status != classification:
            raise ValueError("pit-loss observation status does not match its interval")
        object.__setattr__(self, "status", classification)


def is_eligible_observation(observation: PitLossObservation) -> bool:
    """Return whether one stop can causally refine the estimate."""
    if not isinstance(observation, PitLossObservation):
        return False
    if not _valid_parts(observation):
        return False
    candidate = observation.candidate
    if not _valid_candidate(candidate):
        return False
    if not _valid_gap_sample(candidate, observation.before_gap, is_after=False):
        return False
    if not _valid_gap_sample(candidate, observation.after_gap, is_after=True):
        return False
    if not _valid_leader_snapshot(observation.leader_snapshot):
        return False
    if classify_stop_interval_status(observation.stop_interval_status) is None:
        return False
    if not _leader_is_not_in_pit(observation.leader_snapshot):
        return False
    return _observed_loss_ms(observation.before_gap, observation.after_gap) > 0


def refine_prior_weighted_estimate(
    baseline_ms: int,
    prior_weight: int,
    cumulative_observed_losses: Iterable[int],
) -> int:
    """Return the exact integer prior-weighted mean, rounded half up."""
    _require_nonnegative_integer(baseline_ms, "baseline_ms", positive=True)
    _require_nonnegative_integer(prior_weight, "prior_weight", positive=True)
    losses = tuple(cumulative_observed_losses)
    if any(not _is_nonnegative_integer(loss) for loss in losses):
        raise ValueError("cumulative observed losses must be non-negative integers")
    numerator = baseline_ms * prior_weight + sum(losses)
    denominator = prior_weight + len(losses)
    return (2 * numerator + denominator) // (2 * denominator)


def build_pit_loss_timeline(
    replay_start_ms: int,
    observations: Iterable[PitLossObservation],
    fixture_id: str = "unknown",
    *,
    baseline_ms: int = BASELINE_MS,
    prior_weight: int = PRIOR_WEIGHT,
) -> BrowserPitLossModel:
    """Build the legacy all-clear placeholder-plus-refinement model."""
    _require_nonnegative_integer(replay_start_ms, "replay_start_ms")
    _require_nonnegative_integer(baseline_ms, "baseline_ms", positive=True)
    _require_nonnegative_integer(prior_weight, "prior_weight", positive=True)

    grouped_losses: dict[int, list[int]] = {}
    for observation in sorted(tuple(observations), key=_observation_sort_key):
        if (
            not isinstance(observation, PitLossObservation)
            or observation.status != NORMAL_PIT_LOSS_STATUS
            or not is_eligible_observation(observation)
        ):
            continue
        pit_out_time_ms = observation.candidate.pit_out_time_ms
        loss_ms = _observed_loss_ms(observation.before_gap, observation.after_gap)
        if pit_out_time_ms <= replay_start_ms or loss_ms <= 0:
            continue
        grouped_losses.setdefault(pit_out_time_ms, []).append(loss_ms)

    cumulative_losses: list[int] = []
    time_ms = [replay_start_ms]
    estimated_loss_ms = [baseline_ms]
    observed_sample_count = [0]
    for pit_out_time_ms in sorted(grouped_losses):
        cumulative_losses.extend(grouped_losses[pit_out_time_ms])
        time_ms.append(pit_out_time_ms)
        estimated_loss_ms.append(
            refine_prior_weighted_estimate(baseline_ms, prior_weight, cumulative_losses),
        )
        observed_sample_count.append(len(cumulative_losses))

    return BrowserPitLossModel(
        fixture_id=fixture_id,
        method=MODEL_METHOD,
        baseline_ms=baseline_ms,
        prior_weight=prior_weight,
        time_ms=tuple(time_ms),
        estimated_loss_ms=tuple(estimated_loss_ms),
        observed_sample_count=tuple(observed_sample_count),
    )


def median_loss_ms(observed_losses: Iterable[int]) -> int:
    """Return a deterministic integer median, rounding an even midpoint up.

    Pit-loss values are non-negative integer milliseconds.  Keeping the
    calculation integer-exact avoids the platform and representation details
    of floating-point ``statistics.median`` at the wire boundary.
    """
    losses = tuple(sorted(observed_losses))
    if not losses or any(not _is_nonnegative_integer(loss) for loss in losses):
        raise ValueError("observed losses must contain non-negative integers")
    middle = len(losses) // 2
    if len(losses) % 2:
        return losses[middle]
    return (losses[middle - 1] + losses[middle] + 1) // 2


def build_pit_loss_estimate_timeline(
    replay_start_ms: int,
    observations: Iterable[PitLossObservation],
    *,
    status: PitLossStatus | None = None,
    baseline_ms: int = BASELINE_MS,
) -> BrowserPitLossEstimateTimeline:
    """Build a generation-time final median at the replay start.

    ``status=None`` retains the legacy All Clear shorthand. A concrete status
    limits the generation to observations whose immutable interval
    classification matches it. The estimate is intentionally static: every
    eligible observation contributes before one point is emitted.
    """
    selected_status = NORMAL_PIT_LOSS_STATUS if status is None else status
    return _build_pit_loss_estimate_timeline(
        replay_start_ms,
        observations,
        selected_status=selected_status,
        baseline_ms=baseline_ms,
    )


def _build_pit_loss_estimate_timeline(
    replay_start_ms: int,
    observations: Iterable[PitLossObservation],
    *,
    selected_status: PitLossStatus | None,
    baseline_ms: int,
) -> BrowserPitLossEstimateTimeline:
    """Build one static timeline, optionally aggregating every status."""
    _require_nonnegative_integer(replay_start_ms, "replay_start_ms")
    _require_nonnegative_integer(baseline_ms, "baseline_ms", positive=True)
    if selected_status is not None and selected_status not in {
        NORMAL_PIT_LOSS_STATUS,
        SAFETY_CAR_PIT_LOSS_STATUS,
        VIRTUAL_SAFETY_CAR_PIT_LOSS_STATUS,
    }:
        raise ValueError("pit loss estimate status is invalid")

    losses: list[int] = []
    for observation in sorted(tuple(observations), key=_observation_sort_key):
        if not isinstance(observation, PitLossObservation) or not is_eligible_observation(observation):
            continue
        if selected_status is not None and observation.status != selected_status:
            continue
        loss_ms = _observed_loss_ms(observation.before_gap, observation.after_gap)
        if loss_ms <= 0:
            continue
        losses.append(loss_ms)

    estimate_ms = baseline_ms if not losses else median_loss_ms(losses)

    return BrowserPitLossEstimateTimeline(
        (replay_start_ms,), (estimate_ms,), (len(losses),),
    )


def build_pit_loss_estimate_sidecar(
    replay_start_ms: int,
    observations: Iterable[PitLossObservation],
    fixture_id: str = "unknown",
    track_id: str = "unknown",
    track_status_codes: Iterable[int | None] = (),
    *,
    baseline_ms: int = BASELINE_MS,
) -> BrowserPitLossEstimateSidecar:
    """Construct a deterministic, per-track status-aware pit-loss sidecar.

    Canonical status codes are deliberately independent from observation
    availability.  A status seen in the canonical timeline with no eligible
    classified stop is represented by ``unavailable``; a status never seen is
    omitted from the sidecar entirely.
    """
    status_codes = _validate_track_status_codes(track_status_codes)
    observation_values = tuple(observations)
    race = _build_pit_loss_estimate_timeline(
        replay_start_ms,
        observation_values,
        selected_status=_legacy_race_status(status_codes),
        baseline_ms=baseline_ms,
    )
    safety_car = _build_status_estimate(
        replay_start_ms, observation_values, status_codes,
        SAFETY_CAR_PIT_LOSS_STATUS, baseline_ms,
    )
    virtual_safety_car = _build_status_estimate(
        replay_start_ms, observation_values, status_codes,
        VIRTUAL_SAFETY_CAR_PIT_LOSS_STATUS, baseline_ms,
    )
    return BrowserPitLossEstimateSidecar(
        fixture_id=fixture_id,
        track_id=track_id,
        method=PIT_LOSS_ESTIMATE_METHOD,
        race=race,
        safety_car=safety_car,
        virtual_safety_car=virtual_safety_car,
    )


def build_curated_pit_loss_estimate_sidecar(
    replay_start_ms: int,
    *,
    fixture_id: str,
    track_id: str,
    track_name: str | None = None,
    catalog: PitLossBaselineCatalog | Mapping[str, object] = DEFAULT_PIT_LOSS_BASELINE_CATALOG,
    catalog_track_id: str | None = None,
) -> BrowserPitLossEstimateSidecar:
    """Build the immutable catalog-backed sidecar available at replay start.

    This production path intentionally has no observation input.  Resolving all
    three status values independently makes Green, SC, and VSC available even
    when the current race never entered one of those states.  An unavailable
    catalog binding is an explicit generation error rather than a fallback to
    the legacy 22-second estimate.

    ``catalog_track_id`` lets generation resolve the physical circuit through
    the deterministic identity map and look the catalog up by that canonical
    circuit id, so repeated 2024/2025/2026 fixtures of one circuit reuse a
    single baseline entry, while the sidecar keeps the delivery's own
    ``track_id`` (which must equal the track-assets ``trackId``).  When omitted,
    the delivery ``track_id`` is used for the lookup, preserving the previous
    behavior.

    ``track_name`` optionally supplies the track asset's display name so the
    generator's actual binding (for example fixture ``2026-01-race`` with track
    asset ``2026-01-race-telemetry-layout-v1``) can resolve its physical
    circuit even though neither identifier is itself registered.

    Catalog audit metadata remains available on the internal resolver/catalog
    objects for review, but is deliberately not copied into the public sidecar.
    Curated timelines never fabricate a current-race ``observedSampleCount``;
    the monotonic ``SC <= VSC <= Green`` invariant is enforced by the sidecar
    model and fail-closed here as a boundary guard.
    """
    _require_nonnegative_integer(replay_start_ms, "replay_start_ms")
    lookup_track_id = track_id if catalog_track_id is None else catalog_track_id
    resolutions = tuple(
        resolve_pit_loss_baseline(
            fixture_id, lookup_track_id, status, catalog=catalog, track_name=track_name,
        )
        for status in (1, 4, 6)
    )
    unavailable = next(
        (resolution for resolution in resolutions if not resolution.available),
        None,
    )
    if unavailable is not None:
        raise CuratedPitLossBaselineUnavailableError(
            "curated pit-loss baseline unavailable for "
            f"fixture {fixture_id!r} and track {track_id!r}: {unavailable.error}"
        )

    green, safety_car, virtual_safety_car = resolutions
    green_ms = green.estimated_loss_ms
    safety_car_ms = safety_car.estimated_loss_ms
    virtual_safety_car_ms = virtual_safety_car.estimated_loss_ms
    if green_ms is None or safety_car_ms is None or virtual_safety_car_ms is None:
        raise CuratedPitLossBaselineUnavailableError(
            "curated pit-loss baseline values are incomplete for "
            f"fixture {fixture_id!r} and track {track_id!r}"
        )
    if not safety_car_ms <= virtual_safety_car_ms <= green_ms:
        raise CuratedPitLossBaselineUnavailableError(
            "curated pit-loss baseline violates the SC <= VSC <= Green "
            f"invariant for fixture {fixture_id!r} and track {track_id!r}"
        )
    return BrowserPitLossEstimateSidecar(
        fixture_id=fixture_id,
        track_id=track_id,
        method=CURATED_BASELINE_METHOD,
        race=_curated_timeline(replay_start_ms, green_ms),
        safety_car=_curated_timeline(replay_start_ms, safety_car_ms),
        virtual_safety_car=_curated_timeline(replay_start_ms, virtual_safety_car_ms),
    )


def _curated_timeline(
    replay_start_ms: int,
    estimated_loss_ms: int,
) -> BrowserPitLossEstimateTimeline:
    """Represent one source-backed value without race observation counts."""
    return BrowserPitLossEstimateTimeline((replay_start_ms,), (estimated_loss_ms,))


def _build_status_estimate(
    replay_start_ms: int,
    observations: Iterable[PitLossObservation],
    status_codes: tuple[int | None, ...],
    status: PitLossStatus,
    baseline_ms: int,
) -> BrowserPitLossEstimateTimeline | BrowserPitLossEstimateUnavailable | None:
    if not _status_occurs(status_codes, status):
        return None
    timeline = build_pit_loss_estimate_timeline(
        replay_start_ms, observations, status=status, baseline_ms=baseline_ms,
    )
    return (
        timeline
        if timeline.observed_sample_count is not None
        and timeline.observed_sample_count[-1] > 0
        else BrowserPitLossEstimateUnavailable()
    )


def _validate_track_status_codes(
    track_status_codes: Iterable[int | None],
) -> tuple[int | None, ...]:
    try:
        values = tuple(track_status_codes)
    except TypeError as error:
        raise ValueError("track_status_codes must be an iterable") from error
    if any(value is not None and (type(value) is not int or value < 0) for value in values):
        raise ValueError("track_status_codes must contain non-negative integers or null")
    return values


def _status_occurs(
    status_codes: tuple[int | None, ...], status: PitLossStatus,
) -> bool:
    codes = {
        SAFETY_CAR_PIT_LOSS_STATUS: {4},
        VIRTUAL_SAFETY_CAR_PIT_LOSS_STATUS: {6, 7},
    }.get(status, set())
    return any(code in codes for code in status_codes)


def _legacy_race_status(status_codes: tuple[int | None, ...]) -> PitLossStatus | None:
    """Choose the legacy race timeline class without changing its wire shape.

    Normal status remains the preferred race baseline. Synthetic or legacy
    deliveries that contain only non-normal classes aggregate eligible
    observations instead of manufacturing an empty race timeline.
    """
    if not status_codes or 1 in status_codes:
        return NORMAL_PIT_LOSS_STATUS
    if any(code in {4, 6, 7} for code in status_codes):
        return None
    return NORMAL_PIT_LOSS_STATUS


def _valid_parts(observation: PitLossObservation) -> bool:
    return (
        isinstance(observation.candidate, PitStopCandidate)
        and isinstance(observation.before_gap, GapSample)
        and isinstance(observation.after_gap, GapSample)
        and isinstance(observation.leader_snapshot, LeaderSnapshot)
        and isinstance(observation.stop_interval_status, StopIntervalStatus)
    )


def _valid_candidate(candidate: PitStopCandidate) -> bool:
    return (
        isinstance(candidate.driver_id, str)
        and bool(candidate.driver_id)
        and _is_nonnegative_integer(candidate.pit_in_time_ms)
        and _is_nonnegative_integer(candidate.pit_out_time_ms)
        and candidate.pit_out_time_ms > candidate.pit_in_time_ms
        and candidate.quality_gate_passed is True
        and candidate.mapping_complete_and_unique is True
    )


def _valid_gap_sample(candidate: PitStopCandidate, sample: GapSample, *, is_after: bool) -> bool:
    if not isinstance(sample.driver_id, str) or sample.driver_id != candidate.driver_id:
        return False
    if not _is_nonnegative_integer(sample.time_ms):
        return False
    if not _is_finite_nonnegative(sample.gap_to_leader_ms):
        return False
    boundary = candidate.pit_out_time_ms if is_after else candidate.pit_in_time_ms
    if (sample.time_ms < boundary if is_after else sample.time_ms > boundary):
        return False
    if not is_after:
        return True
    if sample.is_in_pit_lane is not False or sample.is_finished is True:
        return False
    if sample.is_on_track is False:
        return False
    if sample.status is not None:
        return isinstance(sample.status, str) and _normalise_status(sample.status) in {"active", "ontrack"}
    return sample.is_on_track is True


def _valid_leader_snapshot(snapshot: LeaderSnapshot) -> bool:
    before = snapshot.before_leader_id
    after = snapshot.after_leader_id
    return (
        isinstance(before, str)
        and bool(before)
        and isinstance(after, str)
        and before == after
        and isinstance(snapshot.interval_is_in_pit_lane, tuple)
    )


def classify_stop_interval_status(
    status: StopIntervalStatus | Iterable[int | None],
) -> PitLossStatus | None:
    """Classify an interval only when every known sample proves one class.

    Codes 6 and 7 are two phases of the same VSC state, so a combination of
    those codes remains unambiguous.  Every other mixed, null, or unknown
    interval fails closed.
    """
    if isinstance(status, StopIntervalStatus):
        codes = status.track_status_codes
    else:
        try:
            codes = tuple(status)
        except TypeError:
            return None
    if not codes or any(type(code) is not int for code in codes):
        return None
    if all(code == 1 for code in codes):
        return NORMAL_PIT_LOSS_STATUS
    if all(code == 4 for code in codes):
        return SAFETY_CAR_PIT_LOSS_STATUS
    if all(code in {6, 7} for code in codes):
        return VIRTUAL_SAFETY_CAR_PIT_LOSS_STATUS
    return None


def _leader_is_not_in_pit(snapshot: LeaderSnapshot) -> bool:
    interval = snapshot.interval_is_in_pit_lane
    observations = interval + (
        snapshot.before_is_in_pit_lane,
        snapshot.after_is_in_pit_lane,
    )
    return bool(observations) and all(type(value) is bool and value is False for value in observations)


def _observed_loss_ms(before_gap: GapSample, after_gap: GapSample) -> int:
    before = before_gap.gap_to_leader_ms
    after = after_gap.gap_to_leader_ms
    if (
        before is None
        or after is None
        or not _is_finite_nonnegative(before)
        or not _is_finite_nonnegative(after)
    ):
        return 0
    difference = _as_fraction(after) - _as_fraction(before)
    return _round_half_up(difference) if difference > 0 else 0


def _observation_sort_key(observation: object) -> tuple[int | float, str, int | float]:
    if not isinstance(observation, PitLossObservation) or not isinstance(observation.candidate, PitStopCandidate):
        return (math.inf, "", math.inf)
    candidate = observation.candidate
    pit_out = candidate.pit_out_time_ms if type(candidate.pit_out_time_ms) is int else math.inf
    pit_in = candidate.pit_in_time_ms if type(candidate.pit_in_time_ms) is int else math.inf
    driver_id = candidate.driver_id if isinstance(candidate.driver_id, str) else ""
    return (pit_out, driver_id, pit_in)


def _normalise_status(status: str) -> str:
    return status.strip().lower().replace("_", "").replace("-", "").replace(" ", "")


def _is_nonnegative_integer(value: object, *, positive: bool = False) -> bool:
    return type(value) is int and value >= (1 if positive else 0)


def _require_nonnegative_integer(value: object, label: str, *, positive: bool = False) -> None:
    if not _is_nonnegative_integer(value, positive=positive):
        qualifier = "positive " if positive else "non-negative "
        raise ValueError(f"{label} must be a {qualifier}integer")


def _is_finite_nonnegative(value: object) -> bool:
    if type(value) is int:
        return value >= 0
    if type(value) is float:
        return math.isfinite(value) and value >= 0
    return False


def _as_fraction(value: int | float) -> Fraction:
    return Fraction(value) if type(value) is int else Fraction(str(value))


def _round_half_up(value: Fraction) -> int:
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


__all__ = [
    "BASELINE_MS",
    "CuratedPitLossBaselineUnavailableError",
    "GapSample",
    "LeaderSnapshot",
    "MODEL_METHOD",
    "NORMAL_PIT_LOSS_STATUS",
    "PitLossObservation",
    "PitStopCandidate",
    "PitLossStatus",
    "PRIOR_WEIGHT",
    "SAFETY_CAR_PIT_LOSS_STATUS",
    "StopIntervalStatus",
    "VIRTUAL_SAFETY_CAR_PIT_LOSS_STATUS",
    "build_curated_pit_loss_estimate_sidecar",
    "build_pit_loss_estimate_sidecar",
    "build_pit_loss_estimate_timeline",
    "build_pit_loss_timeline",
    "classify_stop_interval_status",
    "is_eligible_observation",
    "median_loss_ms",
    "refine_prior_weighted_estimate",
]
