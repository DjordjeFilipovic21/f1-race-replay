"""Focused tests for browser-layer pit-loss observation extraction."""

from __future__ import annotations

import pytest

from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    BrowserDriverFields,
    BrowserDriverStintSummary,
    BrowserStintSummary,
)
from f1_replay_pipeline.delivery.browser.browser_pit_loss_observation import (
    extract_eligible_pit_loss_observations,
)
from f1_replay_pipeline.delivery.browser.browser_pit_loss_model import (
    NORMAL_PIT_LOSS_STATUS,
    SAFETY_CAR_PIT_LOSS_STATUS,
    VIRTUAL_SAFETY_CAR_PIT_LOSS_STATUS,
    PitLossObservation,
)


TIMES = (0, 50, 100, 150, 200, 250, 300, 350, 400)
ALL_CLEAR = (1, 1, 1, 1, 1, 1, 1, 1, 1)


def _driver(
    driver_id: str,
    *,
    gaps: dict[int, float] | None = None,
    statuses: tuple[str | None, ...] | None = None,
    pit_lane: tuple[bool | None, ...] | None = None,
    finished: tuple[bool | None, ...] | None = None,
    time_ms: tuple[int, ...] = TIMES,
) -> BrowserDriverFields:
    size = len(time_ms)
    values = {} if gaps is None else gaps
    return BrowserDriverFields(
        driver_id=driver_id,
        time_ms=time_ms,
        x=(0.0,) * size,
        y=(0.0,) * size,
        speed=(0.0,) * size,
        throttle=(0.0,) * size,
        brake=(None,) * size,
        gear=(None,) * size,
        drs=(None,) * size,
        status=("OnTrack",) * size if statuses is None else statuses,
        lap=(1,) * size,
        tyre_compound=("MEDIUM",) * size,
        is_in_pit_lane=(False,) * size if pit_lane is None else pit_lane,
        track_distance_meters=(0.0,) * size,
        gap_to_leader_ms=tuple(values.get(timestamp) for timestamp in time_ms),
        position=(1 if driver_id == "HAM" else 2,) * size,
        is_finished=(False,) * size if finished is None else finished,
    )


def _stints(*transitions: tuple[int | None, int | None]) -> BrowserDriverStintSummary:
    size = len(transitions)
    return BrowserDriverStintSummary(
        stint_number=tuple(range(1, size + 1)),
        compound=("MEDIUM",) * size,
        start_lap=tuple(range(1, size + 1)),
        end_lap=(None,) * size,
        start_time_ms=(0,) * size,
        end_time_ms=(None,) * size,
        tyre_life_at_start=(0,) * size,
        is_fresh_tyre=(True,) * size,
        pit_in_time_ms=tuple(pit_in for pit_in, _ in transitions),
        pit_out_time_ms=tuple(pit_out for _, pit_out in transitions),
    )


def _inputs(
    transitions: tuple[tuple[str, int, int], ...] = (("VER", 100, 200),),
    *,
    track_status_code: tuple[int | None, ...] = ALL_CLEAR,
    statuses: dict[str, tuple[str | None, ...]] | None = None,
    time_ms: tuple[int, ...] = TIMES,
) -> tuple[
    BrowserStintSummary,
    dict[str, BrowserDriverFields],
    tuple[tuple[str, ...] | None, ...],
    tuple[int | None, ...],
]:
    stopped_ids = tuple(sorted({driver_id for driver_id, _, _ in transitions}))
    driver_ids = ("HAM",) + stopped_ids
    summary = BrowserStintSummary(
        "race-01",
        {
            driver_id: _stints(
                *tuple(
                    (pit_in, pit_out)
                    for current_id, pit_in, pit_out in transitions
                    if current_id == driver_id
                ),
            )
            for driver_id in driver_ids
        },
    )
    gaps = {
        "VER": {50: 1_000.0, 100: 9_000.0, 200: 23_000.0},
        "LEC": {100: 2_000.0, 150: 8_000.0, 300: 26_000.0},
    }
    pit_lane = {
        driver_id: tuple(
            any(
                pit_in <= timestamp < pit_out
                for current_id, pit_in, pit_out in transitions
                if current_id == driver_id
            )
            for timestamp in time_ms
        )
        for driver_id in stopped_ids
    }
    driver_fields = {
        "HAM": _driver("HAM", gaps={timestamp: 0.0 for timestamp in time_ms}, time_ms=time_ms),
        **{
            driver_id: _driver(
                driver_id,
                gaps=gaps.get(driver_id, {}),
                statuses=None if statuses is None else statuses.get(driver_id),
                pit_lane=pit_lane[driver_id],
                time_ms=time_ms,
            )
            for driver_id in stopped_ids
        },
    }
    order = tuple(tuple(driver_ids) for _ in time_ms)
    return summary, driver_fields, order, track_status_code


def _extract(
    inputs: tuple[
        BrowserStintSummary,
        dict[str, BrowserDriverFields],
        tuple[tuple[str, ...] | None, ...],
        tuple[int | None, ...],
    ],
    *,
    quality_gate_passed: bool = True,
) -> tuple[PitLossObservation, ...]:
    summary, drivers, order, track_status = inputs
    return extract_eligible_pit_loss_observations(
        stint_summary=summary,
        drivers=drivers,
        leaderboard_order=order,
        track_status_code=track_status,
        quality_gate_passed=quality_gate_passed,
    )


def test_no_complete_pit_transition_is_a_no_op() -> None:
    assert _extract(_inputs(transitions=())) == ()


def test_selects_latest_prior_gap_and_first_on_track_return() -> None:
    observations = _extract(_inputs())

    assert len(observations) == 1
    observation = observations[0]
    assert observation.candidate.driver_id == "VER"
    assert observation.before_gap.time_ms == 50
    assert observation.before_gap.gap_to_leader_ms == 1_000.0
    assert observation.after_gap.time_ms == 200
    assert observation.after_gap.gap_to_leader_ms == 23_000.0


def test_pairs_pit_in_on_ending_stint_with_pit_out_on_next_stint() -> None:
    inputs = _inputs()
    split_summary = BrowserStintSummary(
        "race-01",
        {
            "HAM": _stints(),
            "VER": BrowserDriverStintSummary(
                stint_number=(1, 2),
                compound=("MEDIUM", "HARD"),
                start_lap=(1, 2),
                end_lap=(1, None),
                start_time_ms=(0, 200),
                end_time_ms=(100, None),
                tyre_life_at_start=(0, 0),
                is_fresh_tyre=(True, True),
                pit_in_time_ms=(100, None),
                pit_out_time_ms=(None, 200),
            ),
        },
    )

    observations = _extract((split_summary, inputs[1], inputs[2], inputs[3]))

    assert len(observations) == 1
    assert observations[0].candidate.pit_in_time_ms == 100
    assert observations[0].candidate.pit_out_time_ms == 200


def test_two_stops_have_deterministic_pit_out_order() -> None:
    observations = _extract(
        _inputs(transitions=(("LEC", 150, 300), ("VER", 100, 200))),
    )

    assert tuple(
        (item.candidate.pit_out_time_ms, item.candidate.driver_id)
        for item in observations
    ) == ((200, "VER"), (300, "LEC"))


def test_yellow_overlap_is_rejected_when_status_samples_are_unknown() -> None:
    status = (None, 1, None, 2, None, 1, None, None, None)

    assert _extract(_inputs(track_status_code=status)) == ()


def test_leader_change_between_selected_samples_is_rejected() -> None:
    summary, drivers, order, track_status = _inputs()
    changed_order = order[:4] + (("VER", "HAM"),) + order[5:]

    assert _extract((summary, drivers, changed_order, track_status)) == ()


def test_leader_in_pit_lane_during_stop_is_rejected() -> None:
    summary, drivers, order, track_status = _inputs()
    changed_drivers = dict(drivers)
    changed_drivers["HAM"] = _driver(
        "HAM",
        gaps={timestamp: 0.0 for timestamp in TIMES},
        pit_lane=(False, False, False, True, False, False, False, False, False),
    )

    assert _extract((summary, changed_drivers, order, track_status)) == ()


def test_stop_without_return_to_active_on_track_is_rejected() -> None:
    statuses = ("OnTrack", "OnTrack", "Pit", "Pit", "OUT", "OUT", "OUT", "OUT", "OUT")

    assert _extract(_inputs(statuses={"VER": statuses})) == ()


def test_missing_before_gap_is_an_ineligible_stop() -> None:
    inputs = _inputs()
    drivers = dict(inputs[1])
    drivers["VER"] = _driver(
        "VER",
        gaps={200: 23_000.0},
        pit_lane=inputs[1]["VER"].is_in_pit_lane,
    )

    assert _extract((inputs[0], drivers, inputs[2], inputs[3])) == ()


def test_quality_gate_false_returns_no_observations() -> None:
    assert _extract(_inputs(), quality_gate_passed=False) == ()


def test_mismatched_driver_timeline_fails_closed() -> None:
    inputs = _inputs()
    drivers = dict(inputs[1])
    drivers["VER"] = _driver("VER", time_ms=TIMES[:-1] + (401,))

    with pytest.raises(ValueError, match="share the same time_ms"):
        _extract((inputs[0], drivers, inputs[2], inputs[3]))


def test_misaligned_global_array_fails_closed() -> None:
    inputs = _inputs()

    with pytest.raises(ValueError, match="align"):
        _extract((inputs[0], inputs[1], inputs[2][:-1], inputs[3]))


def test_mismatched_driver_set_fails_closed() -> None:
    inputs = _inputs()

    with pytest.raises(ValueError, match="driver sets"):
        _extract((inputs[0], {"HAM": inputs[1]["HAM"]}, inputs[2], inputs[3]))


def test_duplicate_pit_transition_fails_closed() -> None:
    inputs = _inputs(transitions=(("VER", 100, 200), ("VER", 100, 300)))

    with pytest.raises(ValueError, match="duplicate pit-in"):
        _extract(inputs)


def test_all_clear_interval_classifies_observation_normal() -> None:
    observations = _extract(_inputs())

    assert len(observations) == 1
    assert observations[0].status == NORMAL_PIT_LOSS_STATUS


def test_safety_car_interval_classifies_observation_safety_car() -> None:
    status = (None, 1, 4, 4, 4, 4, None, None, None)

    observations = _extract(_inputs(track_status_code=status))

    assert len(observations) == 1
    assert observations[0].status == SAFETY_CAR_PIT_LOSS_STATUS


@pytest.mark.parametrize("code", [6, 7])
def test_vsc_interval_classifies_observation_virtual_safety_car(code: int) -> None:
    status = (None, 1, code, code, code, 1, None, None, None)

    observations = _extract(_inputs(track_status_code=status))

    assert len(observations) == 1
    assert observations[0].status == VIRTUAL_SAFETY_CAR_PIT_LOSS_STATUS


def test_mixed_status_interval_is_rejected() -> None:
    status = (None, 1, 4, 4, 6, 6, None, None, None)

    assert _extract(_inputs(track_status_code=status)) == ()


def test_unknown_status_code_interval_is_rejected() -> None:
    status = (None, 1, 3, 3, 3, 1, None, None, None)

    assert _extract(_inputs(track_status_code=status)) == ()


def test_quality_gate_false_skips_safety_car_observations() -> None:
    status = (None, 1, 4, 4, 4, 4, None, None, None)

    assert _extract(_inputs(track_status_code=status), quality_gate_passed=False) == ()
