"""Parametrized tests for the pure causal pit-loss derivation."""

from __future__ import annotations

from dataclasses import replace

import pytest

from f1_replay_pipeline.delivery.browser.browser_pit_loss_model import (
    BASELINE_MS,
    GapSample,
    LeaderSnapshot,
    PitLossObservation,
    PitStopCandidate,
    StopIntervalStatus,
    build_pit_loss_timeline,
    is_eligible_observation,
    refine_prior_weighted_estimate,
)


def _observation(
    *,
    pit_in: int = 100,
    pit_out: int = 200,
    before_gap: int | float | None = 1_000,
    after_gap: int | float | None = 23_000,
    candidate: PitStopCandidate | None = None,
    before: GapSample | None = None,
    after: GapSample | None = None,
    leader: LeaderSnapshot | None = None,
    status: StopIntervalStatus | None = None,
) -> PitLossObservation:
    return PitLossObservation(
        candidate or PitStopCandidate("VER", pit_in, pit_out, True, True),
        before or GapSample("VER", pit_in, before_gap, None, None, None, None),
        after or GapSample("VER", pit_out, after_gap, "active", False, True, False),
        leader or LeaderSnapshot("HAM", "HAM", False, False, (False,)),
        status or StopIntervalStatus((1, 1)),
    )


def test_baseline_only_timeline_is_emitted_at_replay_start() -> None:
    model = build_pit_loss_timeline(1_000, (), fixture_id="race-01")

    assert model.time_ms == (1_000,)
    assert model.estimated_loss_ms == (BASELINE_MS,)
    assert model.observed_sample_count == (0,)


def test_valid_lapped_observation_is_eligible() -> None:
    assert is_eligible_observation(_observation())


def test_timeline_refines_and_collapses_same_timestamp_observations() -> None:
    observations = (
        _observation(pit_out=400, pit_in=300, after_gap=24_000),
        _observation(pit_out=400, pit_in=350, after_gap=25_000),
        _observation(after_gap=23_000),
    )

    model = build_pit_loss_timeline(0, observations, fixture_id="race-01")

    assert model.time_ms == (0, 200, 400)
    assert model.observed_sample_count == (0, 1, 3)
    assert model.estimated_loss_ms == (22_000, 22_000, 22_600)


@pytest.mark.parametrize(
    "observation",
    [
        pytest.param(_observation(candidate=PitStopCandidate("VER", 100, 100, True, True)), id="empty-interval"),
        pytest.param(_observation(candidate=PitStopCandidate("VER", 100, 200, False, True)), id="quality-gate"),
        pytest.param(_observation(candidate=PitStopCandidate("VER", 100, 200, True, False)), id="mapping"),
        pytest.param(_observation(before=GapSample("VER", 101, 1_000, None, None, None, None)), id="before-time"),
        pytest.param(_observation(after=GapSample("VER", 199, 23_000, "active", False, True, False)), id="after-time"),
        pytest.param(_observation(after_gap=None), id="missing-gap"),
        pytest.param(_observation(after_gap=float("nan")), id="non-finite-gap"),
        pytest.param(_observation(after_gap=999), id="non-positive-loss"),
        pytest.param(_observation(after=GapSample("HAM", 200, 23_000, "active", False, True, False)), id="driver-mismatch"),
        pytest.param(_observation(status=StopIntervalStatus((2,))), id="yellow"),
        pytest.param(_observation(status=StopIntervalStatus((4,))), id="safety-car"),
        pytest.param(_observation(status=StopIntervalStatus((8,))), id="vsc"),
        pytest.param(_observation(status=StopIntervalStatus((3,))), id="vsc-ending"),
        pytest.param(_observation(status=StopIntervalStatus((None,))), id="unknown-status"),
        pytest.param(_observation(leader=LeaderSnapshot("HAM", "LEC", False, False, (False,))), id="leader-change"),
        pytest.param(_observation(leader=LeaderSnapshot("HAM", "HAM", True, False, (False,))), id="leader-before-pit"),
        pytest.param(_observation(leader=LeaderSnapshot("HAM", "HAM", None, True, (False,))), id="unknown-before-known-after-pit"),
        pytest.param(_observation(leader=LeaderSnapshot("HAM", "HAM", True, None, (False,))), id="known-before-pit-unknown-after"),
        pytest.param(_observation(leader=LeaderSnapshot("HAM", "HAM", False, False, (True,))), id="leader-interval-pit"),
        pytest.param(_observation(leader=LeaderSnapshot("HAM", "HAM", False, False, (None,))), id="unknown-leader-pit-state"),
        pytest.param(_observation(after=GapSample("VER", 200, 23_000, "finished", False, True, True)), id="finished"),
        pytest.param(_observation(after=GapSample("VER", 200, 23_000, "OUT", False, True, False)), id="retired"),
        pytest.param(_observation(after=GapSample("VER", 200, 23_000, "active", True, True, False)), id="still-in-pit"),
        pytest.param(_observation(after=GapSample("VER", 200, 23_000, "active", False, False, False)), id="off-track"),
        pytest.param(_observation(after=GapSample("VER", 200, 23_000, None, False, None, False)), id="unknown-driver-state"),
    ],
)
def test_each_ineligibility_reason_fails_closed(observation: PitLossObservation) -> None:
    assert not is_eligible_observation(observation)


@pytest.mark.parametrize(
    ("baseline", "prior", "losses", "expected"),
    [
        (21_500, 1, (22_500,), 22_000),
        (22_000, 2, (21_800,), 21_933),
        (22_000, 2, (22_200,), 22_067),
    ],
)
def test_prior_weighted_round_half_up_is_integer_exact(
    baseline: int, prior: int, losses: tuple[int, ...], expected: int,
) -> None:
    assert refine_prior_weighted_estimate(baseline, prior, losses) == expected


def test_timeline_ordering_is_deterministic() -> None:
    observations = (
        _observation(pit_in=300, pit_out=400, after_gap=24_000),
        _observation(pit_in=100, pit_out=200, after_gap=23_000),
    )

    left = build_pit_loss_timeline(0, observations, fixture_id="race-01")
    right = build_pit_loss_timeline(0, reversed(observations), fixture_id="race-01")

    assert left == right


def test_status_normalization_accepts_on_track() -> None:
    observation = replace(
        _observation(),
        after_gap=GapSample("VER", 200, 23_000, "on_track", False, True, False),
    )

    assert is_eligible_observation(observation)
