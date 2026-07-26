"""Pure causal derivation of the optional browser pit-loss timeline."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math
from typing import Iterable

from f1_replay_pipeline.delivery.browser.browser_delivery_models import BrowserPitLossModel


BASELINE_MS = 22_000
PRIOR_WEIGHT = 2
MODEL_METHOD = "global-prior-weighted-mean-v1"


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


@dataclass(frozen=True)
class PitLossObservation:
    """All pure inputs needed to validate and price one pit stop."""

    candidate: PitStopCandidate
    before_gap: GapSample
    after_gap: GapSample
    leader_snapshot: LeaderSnapshot
    stop_interval_status: StopIntervalStatus


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
    if not _all_clear(observation.stop_interval_status):
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
    """Build the placeholder-plus-refinement model without future leakage."""
    _require_nonnegative_integer(replay_start_ms, "replay_start_ms")
    _require_nonnegative_integer(baseline_ms, "baseline_ms", positive=True)
    _require_nonnegative_integer(prior_weight, "prior_weight", positive=True)

    grouped_losses: dict[int, list[int]] = {}
    for observation in sorted(tuple(observations), key=_observation_sort_key):
        if not isinstance(observation, PitLossObservation) or not is_eligible_observation(observation):
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


def _all_clear(status: StopIntervalStatus) -> bool:
    codes = status.track_status_codes
    return (
        isinstance(codes, tuple)
        and bool(codes)
        and all(type(code) is int and code == 1 for code in codes)
    )


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
    "GapSample",
    "LeaderSnapshot",
    "MODEL_METHOD",
    "PitLossObservation",
    "PitStopCandidate",
    "PRIOR_WEIGHT",
    "StopIntervalStatus",
    "build_pit_loss_timeline",
    "is_eligible_observation",
    "refine_prior_weighted_estimate",
]
