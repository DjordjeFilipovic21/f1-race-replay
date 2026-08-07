"""Parametrized tests for the pure generation-time pit-loss derivation.

Covers the legacy prior-weighted timeline, the new status classification
(``classify_stop_interval_status``), the deterministic integer median
(``median_loss_ms``), the static status-aware estimate timelines, and the
per-track status-aware sidecar (absent status, unavailable fallback, and
status-specific medians).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    PIT_LOSS_ESTIMATE_METHOD,
)
from f1_replay_pipeline.delivery.browser.browser_pit_loss_model import (
    BASELINE_MS,
    NORMAL_PIT_LOSS_STATUS,
    SAFETY_CAR_PIT_LOSS_STATUS,
    VIRTUAL_SAFETY_CAR_PIT_LOSS_STATUS,
    GapSample,
    LeaderSnapshot,
    PitLossObservation,
    PitStopCandidate,
    StopIntervalStatus,
    build_pit_loss_estimate_sidecar,
    build_pit_loss_estimate_timeline,
    build_pit_loss_timeline,
    classify_stop_interval_status,
    is_eligible_observation,
    median_loss_ms,
    refine_prior_weighted_estimate,
)
from f1_replay_pipeline.delivery.browser.browser_pit_loss_sidecar import (
    BrowserPitLossEstimateTimeline,
    BrowserPitLossEstimateUnavailable,
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


def test_legacy_timeline_uses_only_all_clear_observations() -> None:
    observations = (
        _observation(),
        _observation(status=StopIntervalStatus((4, 4)), pit_out=400, pit_in=300, after_gap=25_000),
    )

    model = build_pit_loss_timeline(0, observations, fixture_id="race-01")

    assert model.time_ms == (0, 200)
    assert model.observed_sample_count == (0, 1)


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
        pytest.param(_observation(status=StopIntervalStatus((8,))), id="unknown-code"),
        pytest.param(_observation(status=StopIntervalStatus((3,))), id="red-ending"),
        pytest.param(_observation(status=StopIntervalStatus((None,))), id="unknown-status"),
        pytest.param(_observation(status=StopIntervalStatus((1, 4))), id="mixed-normal-safety-car"),
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


# --- Status classification -------------------------------------------------


def test_safety_car_interval_observation_is_eligible_and_classified() -> None:
    observation = _observation(status=StopIntervalStatus((4, 4)))

    assert is_eligible_observation(observation)
    assert observation.status == SAFETY_CAR_PIT_LOSS_STATUS


def test_vsc_interval_observation_is_eligible_and_classified() -> None:
    observation = _observation(status=StopIntervalStatus((6, 6)))

    assert is_eligible_observation(observation)
    assert observation.status == VIRTUAL_SAFETY_CAR_PIT_LOSS_STATUS


def test_observation_rejects_status_mismatching_its_interval() -> None:
    with pytest.raises(ValueError, match="does not match"):
        PitLossObservation(
            _observation().candidate,
            _observation().before_gap,
            _observation().after_gap,
            _observation().leader_snapshot,
            StopIntervalStatus((4, 4)),
            status=NORMAL_PIT_LOSS_STATUS,
        )


@pytest.mark.parametrize(
    ("codes", "expected"),
    [
        ((1,), NORMAL_PIT_LOSS_STATUS),
        ((1, 1, 1), NORMAL_PIT_LOSS_STATUS),
        ((4,), SAFETY_CAR_PIT_LOSS_STATUS),
        ((4, 4), SAFETY_CAR_PIT_LOSS_STATUS),
        ((6,), VIRTUAL_SAFETY_CAR_PIT_LOSS_STATUS),
        ((7,), VIRTUAL_SAFETY_CAR_PIT_LOSS_STATUS),
        ((6, 7), VIRTUAL_SAFETY_CAR_PIT_LOSS_STATUS),
        ((7, 6, 6), VIRTUAL_SAFETY_CAR_PIT_LOSS_STATUS),
    ],
)
def test_classify_stop_interval_status_accepts_unambiguous_intervals(
    codes: tuple[int, ...], expected: str,
) -> None:
    assert classify_stop_interval_status(codes) == expected
    assert classify_stop_interval_status(list(codes)) == expected
    assert classify_stop_interval_status(StopIntervalStatus(codes)) == expected


@pytest.mark.parametrize(
    "codes",
    [
        (),
        (2,),       # yellow
        (3,),       # red / vsc-ending
        (5,),       # unknown code
        (8,),       # unknown code
        (1, 4),     # mixed normal + safety car
        (4, 6),     # mixed safety car + vsc
        (1, 6),     # mixed normal + vsc
        (1, 4, 6),  # three-way mix
        (None,),    # null sample
        (1, None),  # partial null
        (1.0,),     # float is not a canonical code
        (True,),    # bool is not an int code
    ],
)
def test_classify_stop_interval_status_fails_closed(codes: tuple[int | None, ...]) -> None:
    assert classify_stop_interval_status(codes) is None
    assert classify_stop_interval_status(StopIntervalStatus(codes)) is None


@pytest.mark.parametrize("value", [42, None, "1"])
def test_classify_stop_interval_status_rejects_non_iterable_input(value: object) -> None:
    assert classify_stop_interval_status(value) is None  # type: ignore[arg-type]


# --- Deterministic median ---------------------------------------------------


@pytest.mark.parametrize(
    ("losses", "expected"),
    [
        ((5,), 5),
        ((10, 20, 30), 20),
        ((10, 11), 11),       # midpoint 10.5 rounds half up
        ((10, 13), 12),       # midpoint 11.5 rounds half up
        ((10, 12), 11),       # exact midpoint stays integer-exact
        ((10, 10), 10),
        ((22_000, 23_000), 22_500),
    ],
)
def test_median_loss_ms_is_integer_exact_and_rounds_half_up(
    losses: tuple[int, ...], expected: int,
) -> None:
    assert median_loss_ms(losses) == expected


def test_median_loss_ms_is_order_independent_and_does_not_mutate_input() -> None:
    losses = [100, 3, 2, 1]

    assert median_loss_ms(losses) == 3
    assert median_loss_ms(list(reversed(losses))) == 3
    assert losses == [100, 3, 2, 1]


@pytest.mark.parametrize(
    "losses",
    [
        (),
        (-1,),
        (10, -2),
        (1.5,),
        (True,),
        (None,),
        ("100",),
    ],
)
def test_median_loss_ms_rejects_invalid_losses(losses: tuple[object, ...]) -> None:
    with pytest.raises(ValueError):
        median_loss_ms(losses)  # type: ignore[arg-type]


# --- Causal estimate timelines ----------------------------------------------


def test_estimate_timeline_baseline_only_at_replay_start() -> None:
    timeline = build_pit_loss_estimate_timeline(1_000, (), baseline_ms=21_500)

    assert timeline.time_ms == (1_000,)
    assert timeline.estimated_loss_ms == (21_500,)
    assert timeline.observed_sample_count == (0,)


def test_race_estimate_timeline_is_final_all_clear_median() -> None:
    observations = (
        _observation(),
        _observation(pit_out=400, pit_in=300, after_gap=25_000),
    )

    timeline = build_pit_loss_estimate_timeline(0, observations)

    assert timeline.time_ms == (0,)
    assert timeline.estimated_loss_ms == (23_000,)
    assert timeline.observed_sample_count == (2,)


def test_status_specific_timeline_filters_observations_by_classification() -> None:
    observations = (
        _observation(),
        _observation(
            status=StopIntervalStatus((4, 4)),
            pit_out=400, pit_in=300, after_gap=25_000,
        ),
    )

    race = build_pit_loss_estimate_timeline(0, observations)
    safety_car = build_pit_loss_estimate_timeline(
        0, observations, status=SAFETY_CAR_PIT_LOSS_STATUS,
    )
    normal = build_pit_loss_estimate_timeline(
        0, observations, status=NORMAL_PIT_LOSS_STATUS,
    )

    assert race.time_ms == (0,)
    assert race.observed_sample_count == (1,)
    assert safety_car.time_ms == (0,)
    assert safety_car.estimated_loss_ms == (24_000,)
    assert safety_car.observed_sample_count == (1,)
    assert normal.time_ms == (0,)
    assert normal.estimated_loss_ms == (22_000,)
    assert normal.observed_sample_count == (1,)


def test_generation_time_estimate_includes_observations_before_or_at_replay_start() -> None:
    observation = _observation()

    before = build_pit_loss_estimate_timeline(150, (observation,))
    at_boundary = build_pit_loss_estimate_timeline(200, (observation,))
    after_boundary = build_pit_loss_estimate_timeline(199, (observation,))

    assert before.time_ms == (150,)
    assert before.observed_sample_count == (1,)
    assert at_boundary.time_ms == (200,)
    assert at_boundary.observed_sample_count == (1,)
    assert after_boundary.time_ms == (199,)
    assert after_boundary.observed_sample_count == (1,)


def test_duplicate_pit_out_timestamps_collapse_into_one_point() -> None:
    observations = (
        _observation(pit_out=400, pit_in=300, after_gap=23_000),
        _observation(pit_out=400, pit_in=350, after_gap=25_000),
    )

    timeline = build_pit_loss_estimate_timeline(0, observations)

    assert timeline.time_ms == (0,)
    assert timeline.observed_sample_count == (2,)
    assert timeline.estimated_loss_ms == (23_000,)


def test_estimate_timeline_ordering_is_deterministic() -> None:
    observations = (
        _observation(pit_out=400, pit_in=300, after_gap=24_000),
        _observation(),
    )

    left = build_pit_loss_estimate_timeline(0, observations)
    right = build_pit_loss_estimate_timeline(0, reversed(observations))

    assert left == right


def test_estimate_timeline_skips_ineligible_and_non_observation_inputs() -> None:
    timeline = build_pit_loss_estimate_timeline(
        0, (_observation(), "junk", object()),  # type: ignore[list-item]
    )

    assert timeline.time_ms == (0,)
    assert timeline.observed_sample_count == (1,)


@pytest.mark.parametrize("status", ["yellow", "SC", ""])
def test_estimate_timeline_rejects_invalid_status(status: str) -> None:
    with pytest.raises(ValueError, match="status is invalid"):
        build_pit_loss_estimate_timeline(0, (), status=status)  # type: ignore[arg-type]


def test_estimate_timeline_rejects_invalid_baseline_and_replay_start() -> None:
    with pytest.raises(ValueError, match="replay_start_ms"):
        build_pit_loss_estimate_timeline(-1, ())
    with pytest.raises(ValueError, match="baseline_ms"):
        build_pit_loss_estimate_timeline(0, (), baseline_ms=0)
    with pytest.raises(ValueError, match="baseline_ms"):
        build_pit_loss_estimate_timeline(0, (), baseline_ms=-1)


def test_estimate_timeline_outputs_are_immutable_tuples() -> None:
    timeline = build_pit_loss_estimate_timeline(0, (_observation(),))

    assert isinstance(timeline.time_ms, tuple)
    assert isinstance(timeline.estimated_loss_ms, tuple)
    assert isinstance(timeline.observed_sample_count, tuple)
    with pytest.raises(FrozenInstanceError):
        timeline.time_ms = (0,)  # type: ignore[misc]


def test_estimate_timeline_as_dict_uses_wire_names() -> None:
    timeline = build_pit_loss_estimate_timeline(0, (_observation(),))

    assert timeline.as_dict() == {
        "timeMs": [0],
        "estimatedLossMs": [22_000],
        "observedSampleCount": [1],
    }


# --- Status-aware sidecar ----------------------------------------------------


def test_sidecar_emits_race_timeline_and_omits_never_occurring_statuses() -> None:
    sidecar = build_pit_loss_estimate_sidecar(
        0, (), fixture_id="race-01", track_id="track-01", track_status_codes=(1, 1),
    )

    assert sidecar.fixture_id == "race-01"
    assert sidecar.track_id == "track-01"
    assert sidecar.method == PIT_LOSS_ESTIMATE_METHOD
    assert isinstance(sidecar.race, BrowserPitLossEstimateTimeline)
    assert sidecar.race.time_ms == (0,)
    assert sidecar.safety_car is None
    assert sidecar.virtual_safety_car is None
    payload = sidecar.as_dict()
    assert payload["contractVersion"] == "v2"
    assert payload["fixtureId"] == "race-01"
    assert payload["trackId"] == "track-01"
    assert payload["method"] == PIT_LOSS_ESTIMATE_METHOD
    assert payload["race"] == {
        "timeMs": [0],
        "estimatedLossMs": [22_000],
        "observedSampleCount": [0],
    }
    assert "safetyCar" not in payload
    assert "virtualSafetyCar" not in payload


def test_sidecar_omits_status_never_present_in_canonical_codes() -> None:
    observation = _observation(status=StopIntervalStatus((4, 4)))

    sidecar = build_pit_loss_estimate_sidecar(
        0, (observation,), fixture_id="race-01", track_id="track-01",
        track_status_codes=(1,),
    )

    assert sidecar.safety_car is None
    assert sidecar.virtual_safety_car is None
    assert "safetyCar" not in sidecar.as_dict()
    assert "virtualSafetyCar" not in sidecar.as_dict()
    assert isinstance(sidecar.race, BrowserPitLossEstimateTimeline)
    assert sidecar.race.observed_sample_count == (0,)


def test_sidecar_marks_status_unavailable_when_occurring_without_sample() -> None:
    sidecar = build_pit_loss_estimate_sidecar(
        0, (), fixture_id="race-01", track_id="track-01", track_status_codes=(4,),
    )

    assert isinstance(sidecar.safety_car, BrowserPitLossEstimateUnavailable)
    assert sidecar.safety_car.as_dict() == {"status": "unavailable"}
    assert sidecar.virtual_safety_car is None
    assert sidecar.as_dict()["safetyCar"] == {"status": "unavailable"}
    assert "virtualSafetyCar" not in sidecar.as_dict()


def test_sidecar_falls_back_to_race_median_when_status_has_no_sample() -> None:
    observation = _observation()

    sidecar = build_pit_loss_estimate_sidecar(
        0, (observation,), fixture_id="race-01", track_id="track-01",
        track_status_codes=(4,),
    )

    assert isinstance(sidecar.safety_car, BrowserPitLossEstimateUnavailable)
    assert isinstance(sidecar.race, BrowserPitLossEstimateTimeline)
    assert sidecar.race.observed_sample_count[-1] == 1


def test_sidecar_includes_an_eligible_observation_at_replay_start() -> None:
    observation = _observation(status=StopIntervalStatus((4, 4)), pit_out=200)

    sidecar = build_pit_loss_estimate_sidecar(
        200, (observation,), fixture_id="race-01", track_id="track-01",
        track_status_codes=(4,),
    )

    assert isinstance(sidecar.safety_car, BrowserPitLossEstimateTimeline)
    assert sidecar.safety_car.time_ms == (200,)
    assert sidecar.safety_car.observed_sample_count == (1,)
    assert isinstance(sidecar.race, BrowserPitLossEstimateTimeline)
    assert sidecar.race.time_ms == (200,)
    assert sidecar.race.observed_sample_count == (1,)


def test_sidecar_builds_status_specific_median_timelines() -> None:
    normal = _observation(after_gap=23_000)
    safety_car = _observation(
        status=StopIntervalStatus((4, 4)), pit_out=400, pit_in=300, after_gap=25_000,
    )
    vsc = _observation(
        status=StopIntervalStatus((6, 6)), pit_out=500, pit_in=450, after_gap=25_000,
    )

    sidecar = build_pit_loss_estimate_sidecar(
        0, (normal, safety_car, vsc), fixture_id="race-01", track_id="track-01",
        track_status_codes=(1, 4, 6),
    )

    assert isinstance(sidecar.race, BrowserPitLossEstimateTimeline)
    assert isinstance(sidecar.safety_car, BrowserPitLossEstimateTimeline)
    assert isinstance(sidecar.virtual_safety_car, BrowserPitLossEstimateTimeline)
    assert sidecar.race.time_ms == (0,)
    assert sidecar.race.estimated_loss_ms == (22_000,)
    assert sidecar.race.observed_sample_count == (1,)
    assert sidecar.safety_car.time_ms == (0,)
    assert sidecar.safety_car.estimated_loss_ms == (24_000,)
    assert sidecar.safety_car.observed_sample_count == (1,)
    assert sidecar.virtual_safety_car.time_ms == (0,)
    assert sidecar.virtual_safety_car.estimated_loss_ms == (24_000,)
    assert sidecar.virtual_safety_car.observed_sample_count == (1,)
    payload = sidecar.as_dict()
    assert payload["safetyCar"] == {
        "timeMs": [0],
        "estimatedLossMs": [24_000],
        "observedSampleCount": [1],
    }
    assert payload["virtualSafetyCar"] == {
        "timeMs": [0],
        "estimatedLossMs": [24_000],
        "observedSampleCount": [1],
    }


@pytest.mark.parametrize("code", [6, 7])
def test_sidecar_treats_both_vsc_codes_as_occurrence(code: int) -> None:
    sidecar = build_pit_loss_estimate_sidecar(
        0, (), fixture_id="race-01", track_id="track-01", track_status_codes=(code,),
    )

    assert isinstance(sidecar.virtual_safety_car, BrowserPitLossEstimateUnavailable)
    assert sidecar.safety_car is None


def test_sidecar_accepts_null_track_status_codes() -> None:
    sidecar = build_pit_loss_estimate_sidecar(
        0, (), fixture_id="race-01", track_id="track-01",
        track_status_codes=(None, 1, None),
    )

    assert isinstance(sidecar.race, BrowserPitLossEstimateTimeline)
    assert sidecar.race.time_ms == (0,)


@pytest.mark.parametrize(
    "codes",
    [42, "4", (True,), (1, -1), (1, "4"), (1, 1.0)],
)
def test_sidecar_rejects_invalid_track_status_codes(codes: object) -> None:
    with pytest.raises(ValueError, match="track_status_codes"):
        build_pit_loss_estimate_sidecar(0, (), track_status_codes=codes)  # type: ignore[arg-type]
