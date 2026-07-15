from datetime import timedelta
from itertools import permutations
import math

import pandas as pd
import pytest

from f1_replay_pipeline.canonical_schema import POSITION_TELEMETRY_SCHEMA
from f1_replay_pipeline.normalizers import NormalizationError
from f1_replay_pipeline.position_telemetry_adapter import adapt_position_telemetry


class PositionOnlySession:
    def __init__(self, pos_data: dict[str, list[dict[str, object]]]) -> None:
        self.pos_data = pos_data

    @property
    def car_data(self) -> object:
        raise AssertionError("position adapter must not read car_data")

    @property
    def telemetry(self) -> object:
        raise AssertionError("position adapter must not read merged telemetry")


def test_adapter_preserves_native_position_timestamps_and_forces_pos_provenance():
    session = PositionOnlySession(
        {"44": [{"SessionTime": timedelta(seconds=1, microseconds=4_500), "X": 1, "Y": 2, "Z": 3,
                  "Status": "OnTrack", "Source": "interpolated"}]}
    )

    frame = adapt_position_telemetry(session, "2026-03-race", {"44": "HAM"})

    assert list(frame.schema.items()) == list(POSITION_TELEMETRY_SCHEMA.items())
    assert frame.to_dicts() == [{"session_id": "2026-03-race", "driver_id": "HAM", "source_driver_key": "44",
                                 "session_time_ms": 1005, "x": 1.0, "y": 2.0, "z": 3.0,
                                 "status": "OnTrack", "source": "pos"}]


def test_adapter_keeps_missing_measurements_as_typed_nulls():
    session = PositionOnlySession(
        {"44": [{"SessionTime": timedelta(seconds=1), "X": math.nan, "Y": None,
                  "Z": math.inf, "Status": None}]}
    )

    frame = adapt_position_telemetry(session, "2026-03-race", {"44": "HAM"})

    assert frame.row(0, named=True) == {"session_id": "2026-03-race", "driver_id": "HAM",
                                         "source_driver_key": "44", "session_time_ms": 1000,
                                          "x": None, "y": None, "z": None, "status": None, "source": "pos"}


def test_adapter_normalizes_pandas_missing_and_nonfinite_position_scalars_to_nulls():
    session = PositionOnlySession(
        {"44": [{"SessionTime": timedelta(seconds=1), "X": pd.NA, "Y": pd.NaT,
                 "Z": -math.inf, "Status": pd.NA}]}
    )

    row = adapt_position_telemetry(session, "2026-03-race", {"44": "HAM"}).row(0, named=True)

    assert all(row[field] is None for field in ("x", "y", "z", "status"))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [("X", True, "position X must be numeric"), ("Y", "1", "position Y must be numeric"),
     ("Status", 1, "position Status must be a string")],
)
def test_adapter_rejects_invalid_position_scalar_types(field, value, message):
    record = {"SessionTime": timedelta(seconds=1), "X": 1, "Y": 2, "Z": 3, "Status": "OnTrack"}
    record[field] = value

    with pytest.raises(NormalizationError, match=message):
        adapt_position_telemetry(PositionOnlySession({"44": [record]}), "2026-03-race", {"44": "HAM"})


def test_adapter_deduplicates_native_timestamp_by_documented_measurement_priority():
    session = PositionOnlySession(
        {"44": [
            {"SessionTime": timedelta(seconds=1), "X": 8, "Y": None, "Z": 0, "Status": "OnTrack"},
            {"SessionTime": timedelta(seconds=1), "X": 9, "Y": 2, "Z": 0, "Status": "OnTrack"},
        ]}
    )

    frame = adapt_position_telemetry(session, "2026-03-race", {"44": "HAM"})

    assert frame.height == 1
    assert frame.to_dicts()[0]["x"] == 9.0


def test_adapter_output_is_invariant_under_driver_and_row_permutations():
    records = {
        "44": [{"SessionTime": timedelta(seconds=2), "X": 4, "Y": 4, "Z": 0, "Status": "OnTrack"},
               {"SessionTime": timedelta(seconds=1), "X": 1, "Y": 1, "Z": 0, "Status": "OnTrack"}],
        "1": [{"SessionTime": timedelta(seconds=1), "X": 2, "Y": 2, "Z": 0, "Status": "OnTrack"}],
    }
    frames = []
    for driver_order in permutations(records):
        for ham_rows in permutations(records["44"]):
            pos_data = {key: (list(ham_rows) if key == "44" else records[key]) for key in driver_order}
            frames.append(adapt_position_telemetry(PositionOnlySession(pos_data), "2026-03-race", {"44": "HAM", "1": "VER"}))

    assert all(frame.equals(frames[0]) for frame in frames)
    assert [row["session_time_ms"] for row in frames[0].to_dicts()] == [1000, 2000, 1000]


def test_adapter_rejects_missing_native_session_timestamp():
    session = PositionOnlySession({"44": [{"X": 1, "Y": 2, "Z": 3, "Status": "OnTrack"}]})

    with pytest.raises(NormalizationError, match="SessionTime"):
        adapt_position_telemetry(session, "2026-03-race", {"44": "HAM"})
