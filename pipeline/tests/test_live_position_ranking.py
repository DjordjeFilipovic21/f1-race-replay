from dataclasses import FrozenInstanceError
from typing import cast
import math

import pytest

from f1_replay_pipeline.live_position_progress import ProgressMode
from f1_replay_pipeline.live_position_ranking import (
    BatchRankingFrame,
    DriverProgressInput,
    RankingState,
    RankingTimelineFrame,
    rank_drivers,
    rank_timeline,
)


def _input(driver_id, progress, mode=ProgressMode.ACTIVE):
    return DriverProgressInput(driver_id, progress, mode)


def _frame(time_ms, *inputs):
    return RankingTimelineFrame(time_ms, inputs)


def test_ranks_known_drivers_with_unique_consecutive_positions():
    result = _rank(10, _input("A", 100.0), _input("B", 200.0), _input("C", 150.0))

    assert result.leaderboard_order == ("B", "C", "A")
    assert tuple(entry.position for entry in result.drivers) == (3, 1, 2)


def test_initial_and_prior_order_ties_are_deterministic():
    first = _rank(0, _input("B", 100.0), _input("A", 100.0))
    second = _rank(1, _input("A", 100.0), _input("B", 100.0), state=first.state)

    assert first.leaderboard_order == ("A", "B")
    assert second.leaderboard_order == ("A", "B")


def test_missing_progress_is_excluded_with_null_position_and_gap():
    result = _rank(0, _input("A", 100.0), _input("B", None))
    missing = next(entry for entry in result.drivers if entry.driver_id == "B")

    assert result.leaderboard_order == ("A",)
    assert missing.position is None and missing.gap_to_leader_ms is None


def test_pit_driver_is_not_artificially_demoted_and_terminal_driver_is_naturally_passed():
    pit = _rank(0, _input("A", 100.0, ProgressMode.PIT), _input("B", 90.0))
    passed = _rank(1, _input("A", 100.0, ProgressMode.OUT), _input("B", 110.0), state=pit.state)

    assert pit.leaderboard_order == ("A", "B")
    assert passed.leaderboard_order == ("B", "A")


def test_leader_gap_is_zero_and_follower_gap_uses_exact_or_interpolated_crossing():
    first = _rank(0, _input("A", 0.0), _input("B", 0.0))
    result = _rank(10, _input("A", 100.0), _input("B", 50.0), state=first.state)
    leader, follower = result.drivers

    assert leader.gap_to_leader_ms == 0.0
    assert follower.gap_to_leader_ms == 5.0


def test_follower_gap_uses_an_exact_leader_history_crossing_point():
    first = _rank(0, _input("A", 0.0), _input("B", 0.0))
    middle = _rank(5, _input("A", 50.0), _input("B", 20.0), state=first.state)
    result = _rank(10, _input("A", 100.0), _input("B", 50.0), state=middle.state)

    assert next(entry for entry in result.drivers if entry.driver_id == "B").gap_to_leader_ms == 5.0


def test_insufficient_leader_history_yields_null_gap():
    result = _rank(10, _input("A", 100.0), _input("B", 50.0))

    assert next(entry for entry in result.drivers if entry.driver_id == "B").gap_to_leader_ms is None


def test_leader_change_uses_new_leader_history():
    first = _rank(0, _input("A", 100.0), _input("B", 0.0))
    result = _rank(10, _input("A", 100.0), _input("B", 200.0), state=first.state)

    assert next(entry for entry in result.drivers if entry.driver_id == "A").gap_to_leader_ms == 5.0


def test_small_regression_retains_monotonic_envelope():
    first = _rank(0, _input("A", 100.0), _input("B", 90.0))
    result = _rank(1, _input("A", 99.0), _input("B", 101.0), state=first.state)

    assert result.leaderboard_order == ("B", "A")
    assert next(entry for entry in result.drivers if entry.driver_id == "A").effective_progress_meters == 100.0


@pytest.mark.parametrize("inputs", [
    (DriverProgressInput("A", 1.0, ProgressMode.ACTIVE), DriverProgressInput("A", 2.0, ProgressMode.ACTIVE)),
])
def test_rejects_duplicate_or_invalid_driver_inputs(inputs):
    with pytest.raises((TypeError, ValueError)):
        rank_drivers(RankingState(), session_time_ms=0, inputs=inputs)


def test_rejects_invalid_time_and_mode():
    with pytest.raises(ValueError):
        rank_drivers(RankingState(), session_time_ms=-1, inputs=(_input("A", 1.0),))
    with pytest.raises(ValueError):
        DriverProgressInput("A", 1.0, cast(ProgressMode, "unknown"))
    with pytest.raises(ValueError):
        DriverProgressInput("", 1.0, ProgressMode.ACTIVE)
    with pytest.raises(ValueError):
        DriverProgressInput("A", -1.0, ProgressMode.ACTIVE)
    with pytest.raises(ValueError):
        DriverProgressInput("A", math.nan, ProgressMode.ACTIVE)


def test_outputs_are_immutable_and_replay_is_deterministic():
    result = _rank(0, _input("A", 10.0), _input("B", 5.0))

    assert result == _rank(0, _input("B", 5.0), _input("A", 10.0))
    with pytest.raises(FrozenInstanceError):
        setattr(result.state, "last_session_time_ms", 2)


@pytest.mark.parametrize("frames", [
    (
        _frame(0, _input("A", 0.0), _input("B", 0.0)),
        _frame(10, _input("A", 100.0), _input("B", 50.0)),
    ),
    (
        _frame(0, _input("B", 100.0), _input("A", 100.0)),
        _frame(1, _input("A", 100.0), _input("B", 100.0)),
    ),
    (
        _frame(0, _input("A", 100.0), _input("B", 90.0)),
        _frame(1, _input("A", None), _input("B", 101.0)),
    ),
    (
        _frame(0, _input("A", 100.0), _input("B", 0.0)),
        _frame(10, _input("A", 99.0, ProgressMode.OUT), _input("B", 200.0)),
    ),
    (
        _frame(0, _input("A", 0.0), _input("B", 0.0)),
        _frame(5, _input("A", 50.0), _input("B", 20.0)),
        _frame(10, _input("A", 100.0), _input("B", 50.0)),
    ),
])
def test_batch_ranking_exactly_matches_reducer_for_representative_timelines(frames):
    batch = rank_timeline(frames)

    state = RankingState()
    reduced = []
    for frame in frames:
        result = rank_drivers(state, session_time_ms=frame.session_time_ms, inputs=frame.inputs)
        reduced.append((result.drivers, result.leaderboard_order))
        state = result.state

    assert tuple((frame.drivers, frame.leaderboard_order) for frame in batch) == tuple(reduced)


def test_batch_outputs_are_immutable_lightweight_and_deterministic():
    frames = (_frame(0, _input("A", 10.0), _input("B", 5.0)),)

    result = rank_timeline(frames)

    assert result == rank_timeline(frames)
    assert isinstance(result[0], BatchRankingFrame) and not hasattr(result[0], "state")
    assert isinstance(result[0].drivers, tuple) and isinstance(result[0].leaderboard_order, tuple)
    with pytest.raises(FrozenInstanceError):
        setattr(result[0], "session_time_ms", 2)


def test_batch_rejects_regressing_frame_times():
    frames = (_frame(1, _input("A", 1.0)), _frame(0, _input("A", 2.0)))

    with pytest.raises(ValueError, match="non-regressing"):
        rank_timeline(frames)


def test_batch_uses_one_binary_search_per_non_leader_gap(monkeypatch):
    import f1_replay_pipeline.live_position_ranking as ranking

    calls = 0
    original = ranking.bisect_left

    def counting_bisect(values, target):
        nonlocal calls
        calls += 1
        return original(values, target)

    monkeypatch.setattr(ranking, "bisect_left", counting_bisect)
    frames = tuple(_frame(index, _input("A", float(index * 2)), _input("B", float(index))) for index in range(300))

    results = rank_timeline(frames)

    assert len(results) == len(frames) and calls == len(frames) - 1


def _rank(time_ms, *inputs, state=None):
    return rank_drivers(RankingState() if state is None else state, session_time_ms=time_ms, inputs=inputs)
