from dataclasses import FrozenInstanceError

import pytest

from f1_replay_pipeline.analysis.live_position.live_position_progress import (
    ProgressMode,
    ProgressReason,
    ProgressState,
    advance_progress,
)
from f1_replay_pipeline.analysis.live_position.live_position_projection import CenterlineProjection


LENGTH = 1_000.0


def test_active_same_lap_progress_uses_lap_local_distance():
    update = advance_progress(ProgressState(), session_time_ms=0, lap_number=2, circuit_length_meters=LENGTH, projection=_projection(250.0), mode=ProgressMode.ACTIVE)

    assert update.race_progress_meters == 1_250.0


def test_official_lap_increment_resets_within_lap_wrap_state():
    first = advance_progress(ProgressState(), session_time_ms=0, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(900.0), mode=ProgressMode.ACTIVE)
    update = advance_progress(first.state, session_time_ms=10, lap_number=2, circuit_length_meters=LENGTH, projection=_projection(20.0), mode=ProgressMode.ACTIVE)

    assert update.race_progress_meters == 1_020.0 and update.state.within_lap_wrap_count == 0


def test_official_lap_increment_carries_prior_geometric_wrap_when_reset_regresses():
    first = advance_progress(ProgressState(), session_time_ms=0, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(950.0), mode=ProgressMode.ACTIVE)
    wrapped = advance_progress(first.state, session_time_ms=10, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(20.0), mode=ProgressMode.ACTIVE)
    lap_end = advance_progress(wrapped.state, session_time_ms=20, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(950.0), mode=ProgressMode.ACTIVE)
    update = advance_progress(lap_end.state, session_time_ms=30, lap_number=2, circuit_length_meters=LENGTH, projection=_projection(10.0), mode=ProgressMode.ACTIVE)

    assert update.race_progress_meters == 2_010.0
    assert update.state.within_lap_wrap_count == 1
    assert update.state.within_lap_offset_meters == LENGTH


def test_official_lap_increment_after_wrap_resets_when_candidate_is_monotonic():
    first = advance_progress(ProgressState(), session_time_ms=0, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(950.0), mode=ProgressMode.ACTIVE)
    wrapped = advance_progress(first.state, session_time_ms=10, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(20.0), mode=ProgressMode.ACTIVE)
    update = advance_progress(wrapped.state, session_time_ms=20, lap_number=2, circuit_length_meters=LENGTH, projection=_projection(990.0), mode=ProgressMode.ACTIVE)

    assert update.race_progress_meters == 1_990.0
    assert update.state.within_lap_wrap_count == 0
    assert update.state.within_lap_offset_meters == 0.0


def test_approved_geometric_wrap_offsets_remaining_timing_lap_samples():
    first = advance_progress(ProgressState(), session_time_ms=0, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(950.0), mode=ProgressMode.ACTIVE)
    wrapped = advance_progress(first.state, session_time_ms=10, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(20.0), mode=ProgressMode.ACTIVE)
    following = advance_progress(wrapped.state, session_time_ms=20, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(80.0), mode=ProgressMode.ACTIVE)

    assert wrapped.race_progress_meters == 1_020.0
    assert following.race_progress_meters == 1_080.0


def test_second_wrap_and_non_boundary_backward_jump_fail_closed():
    first = advance_progress(ProgressState(), session_time_ms=0, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(950.0), mode=ProgressMode.ACTIVE)
    wrapped = advance_progress(first.state, session_time_ms=10, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(20.0), mode=ProgressMode.ACTIVE)
    second = advance_progress(wrapped.state, session_time_ms=20, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(950.0), mode=ProgressMode.ACTIVE)
    second = advance_progress(second.state, session_time_ms=30, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(20.0), mode=ProgressMode.ACTIVE)
    backward = advance_progress(first.state, session_time_ms=10, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(400.0), mode=ProgressMode.ACTIVE)

    assert second.reason is ProgressReason.MULTIPLE_WRAP and second.race_progress_meters is None
    assert backward.reason is ProgressReason.INVALID_WRAP and backward.race_progress_meters is None


def test_active_missing_projection_freezes_at_999_ms_and_expires_at_1000_ms():
    first = advance_progress(ProgressState(), session_time_ms=0, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(100.0), mode=ProgressMode.ACTIVE)
    fresh = advance_progress(first.state, session_time_ms=999, lap_number=1, circuit_length_meters=LENGTH, projection=None, mode=ProgressMode.ACTIVE)
    stale = advance_progress(fresh.state, session_time_ms=1_000, lap_number=1, circuit_length_meters=LENGTH, projection=None, mode=ProgressMode.ACTIVE)

    assert fresh.race_progress_meters == 100.0 and fresh.is_frozen is True
    assert stale.race_progress_meters is None and stale.reason is ProgressReason.STALE_PROJECTION


def test_invalid_projection_without_valid_state_is_unknown():
    update = advance_progress(ProgressState(), session_time_ms=0, lap_number=1, circuit_length_meters=LENGTH, projection=None, mode=ProgressMode.ACTIVE)

    assert update.race_progress_meters is None and update.reason is ProgressReason.MISSING_PROJECTION


def test_pit_freezes_beyond_stale_cutoff_and_active_resume_reconciles():
    first = advance_progress(ProgressState(), session_time_ms=0, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(900.0), mode=ProgressMode.ACTIVE)
    pit = advance_progress(first.state, session_time_ms=2_000, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(10.0), mode=ProgressMode.PIT)
    resumed = advance_progress(pit.state, session_time_ms=2_100, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(20.0), mode=ProgressMode.ACTIVE)

    assert pit.race_progress_meters == 900.0 and pit.reason is ProgressReason.PIT_FROZEN
    assert resumed.race_progress_meters == 1_020.0


@pytest.mark.parametrize("mode", [ProgressMode.RETIRED, ProgressMode.OUT])
def test_terminal_modes_freeze_last_valid_progress_indefinitely(mode):
    first = advance_progress(ProgressState(), session_time_ms=0, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(100.0), mode=ProgressMode.ACTIVE)
    update = advance_progress(first.state, session_time_ms=99_999, lap_number=1, circuit_length_meters=LENGTH, projection=None, mode=mode)

    assert update.race_progress_meters == 100.0 and update.is_terminal is True


def test_lap_regression_and_skipped_lap_fail_closed():
    first = advance_progress(ProgressState(), session_time_ms=0, lap_number=2, circuit_length_meters=LENGTH, projection=_projection(100.0), mode=ProgressMode.ACTIVE)
    regression = advance_progress(first.state, session_time_ms=1, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(100.0), mode=ProgressMode.ACTIVE)
    skipped = advance_progress(first.state, session_time_ms=1, lap_number=4, circuit_length_meters=LENGTH, projection=_projection(100.0), mode=ProgressMode.ACTIVE)

    assert regression.reason is ProgressReason.LAP_REGRESSION
    assert skipped.reason is ProgressReason.INVALID_LAP_TRANSITION


def test_unrelated_invalid_transition_fails_without_fabricating_progress():
    first = advance_progress(ProgressState(), session_time_ms=0, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(100.0), mode=ProgressMode.ACTIVE)
    invalid = advance_progress(first.state, session_time_ms=1, lap_number=3, circuit_length_meters=LENGTH, projection=_projection(100.0), mode=ProgressMode.ACTIVE)
    repeated = advance_progress(invalid.state, session_time_ms=2, lap_number=3, circuit_length_meters=LENGTH, projection=_projection(100.0), mode=ProgressMode.ACTIVE)

    assert invalid.reason is ProgressReason.INVALID_LAP_TRANSITION
    assert repeated.reason is ProgressReason.INVALID_LAP_TRANSITION
    assert repeated.race_progress_meters is None


def test_lap_regression_remains_invalid_after_a_failed_observation():
    first = advance_progress(ProgressState(), session_time_ms=0, lap_number=2, circuit_length_meters=LENGTH, projection=_projection(100.0), mode=ProgressMode.ACTIVE)
    failed = advance_progress(first.state, session_time_ms=1, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(100.0), mode=ProgressMode.ACTIVE)
    repeated = advance_progress(failed.state, session_time_ms=2, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(100.0), mode=ProgressMode.ACTIVE)

    assert repeated.reason is ProgressReason.LAP_REGRESSION
    assert repeated.race_progress_meters is None


def test_valid_observation_after_failure_recovers_from_last_valid_state():
    first = advance_progress(ProgressState(), session_time_ms=0, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(950.0), mode=ProgressMode.ACTIVE)
    failed = advance_progress(first.state, session_time_ms=10, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(400.0), mode=ProgressMode.ACTIVE)
    missing = advance_progress(failed.state, session_time_ms=20, lap_number=1, circuit_length_meters=LENGTH, projection=None, mode=ProgressMode.ACTIVE)
    recovered = advance_progress(missing.state, session_time_ms=30, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(960.0), mode=ProgressMode.ACTIVE)

    assert failed.reason is ProgressReason.INVALID_WRAP
    assert missing.race_progress_meters == 950.0
    assert recovered.race_progress_meters == 960.0
    assert recovered.state.failure_reason is None


@pytest.mark.parametrize("keywords", [
    {"session_time_ms": -1}, {"lap_number": 0}, {"circuit_length_meters": 0.0},
    {"projection": CenterlineProjection(1_000.0, 0.0, (), False)}, {"mode": "unknown"},
])
def test_rejects_invalid_input_boundaries(keywords):
    values = {"session_time_ms": 0, "lap_number": 1, "circuit_length_meters": LENGTH, "projection": _projection(1.0), "mode": ProgressMode.ACTIVE}
    values.update(keywords)

    with pytest.raises((TypeError, ValueError)):
        advance_progress(ProgressState(), **values)


def test_rejects_regressing_session_time():
    state = advance_progress(ProgressState(), session_time_ms=1, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(1.0), mode=ProgressMode.ACTIVE).state

    with pytest.raises(ValueError, match="session_time_ms"):
        advance_progress(state, session_time_ms=0, lap_number=1, circuit_length_meters=LENGTH, projection=_projection(2.0), mode=ProgressMode.ACTIVE)


def test_state_is_immutable_and_replay_is_deterministic():
    observations = ((0, 1, 100.0), (1, 1, 200.0), (2, 2, 10.0))

    def replay():
        state = ProgressState()
        outputs = []
        for time_ms, lap, distance in observations:
            update = advance_progress(state, session_time_ms=time_ms, lap_number=lap, circuit_length_meters=LENGTH, projection=_projection(distance), mode=ProgressMode.ACTIVE)
            state = update.state
            outputs.append(update)
        return tuple(outputs)

    assert replay() == replay()
    with pytest.raises(FrozenInstanceError):
        setattr(ProgressState(), "last_session_time_ms", 1)


def _projection(distance: float) -> CenterlineProjection:
    return CenterlineProjection(distance, 0.0, (), False)
