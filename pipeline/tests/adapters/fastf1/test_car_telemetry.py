from datetime import timedelta
from itertools import permutations
from collections.abc import Mapping

from polars.testing import assert_frame_equal

from f1_replay_pipeline.domain.canonical_schema import CAR_TELEMETRY_SCHEMA
from f1_replay_pipeline.adapters.fastf1.car_telemetry import adapt_car_telemetry


class FakeSession:
    def __init__(self, car_data: dict[str, list[dict[str, object]]]) -> None:
        self.car_data = car_data
        self.drivers = ["44", "1"]
        self._drivers = {
            "44": {"DriverNumber": "44", "Abbreviation": "HAM"},
            "1": {"DriverNumber": "1", "Abbreviation": "VER"},
        }
        self.pos_data: dict[str, list[dict[str, object]]] = {}

    def get_driver(self, source_key: object) -> Mapping[str, object]:
        return self._drivers[str(source_key)]


def test_adapt_car_telemetry_deduplicates_native_rows_and_preserves_car_provenance():
    session = FakeSession(
        {
            "44": [
                {"SessionTime": timedelta(milliseconds=1_000), "Speed": 300.0},
                {"SessionTime": timedelta(milliseconds=1_000), "Speed": 299.5, "RPM": 11_000},
            ]
        }
    )

    frame = adapt_car_telemetry(session, "2026-03-race")

    assert list(frame.schema.items()) == list(CAR_TELEMETRY_SCHEMA.items())
    assert frame.to_dicts() == [
        {
            "session_id": "2026-03-race", "driver_id": "HAM", "source_driver_key": "44",
            "session_time_ms": 1000, "speed_kph": 299.5, "rpm": 11000.0, "gear": None,
            "throttle_pct": None, "brake": None, "drs": None, "source": "car",
        }
    ]


def test_adapt_car_telemetry_is_invariant_under_source_row_permutation():
    rows = [
        {"SessionTime": timedelta(milliseconds=1_000), "Speed": 300.0, "RPM": 11_000},
        {"SessionTime": timedelta(milliseconds=1_000), "Speed": 299.0, "RPM": 11_000},
        {"SessionTime": timedelta(milliseconds=1_240), "Speed": 301.0},
    ]
    frames = [
        adapt_car_telemetry(FakeSession({"44": list(order)}), "2026-03-race")
        for order in permutations(rows)
    ]

    for frame in frames[1:]:
        assert_frame_equal(frame, frames[0])


def test_adapt_car_telemetry_maps_missing_measurement_fields_to_typed_nulls():
    session = FakeSession({"44": [{"SessionTime": timedelta(milliseconds=1_000), "Speed": None}]})

    row = adapt_car_telemetry(session, "2026-03-race").row(0, named=True)

    assert row == {
        "session_id": "2026-03-race", "driver_id": "HAM", "source_driver_key": "44",
        "session_time_ms": 1000, "speed_kph": None, "rpm": None, "gear": None,
        "throttle_pct": None, "brake": None, "drs": None, "source": "car",
    }


def test_adapt_car_telemetry_preserves_nonaligned_native_timestamps_without_pos_data():
    session = FakeSession(
        {"44": [{"SessionTime": timedelta(milliseconds=100)}, {"SessionTime": timedelta(milliseconds=340)}]}
    )
    session.pos_data = {"44": [{"SessionTime": timedelta(milliseconds=120)}]}

    frame = adapt_car_telemetry(session, "2026-03-race")

    assert frame.get_column("session_time_ms").to_list() == [100, 340]
