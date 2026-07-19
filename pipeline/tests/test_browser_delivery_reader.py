"""Focused tests for validated canonical reads and null-preserving field mapping."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from f1_replay_pipeline.browser_delivery_models import CanonicalGenerationSnapshot
from f1_replay_pipeline.browser_delivery_reader import (
    CanonicalReaderDependencies,
    derive_browser_driver_fields,
    read_validated_canonical_generation,
)
from f1_replay_pipeline.canonical_schema import CANONICAL_TABLE_SCHEMAS
from f1_replay_pipeline.generation_publication import GenerationPublicationResult


def test_reader_rejects_pointer_before_opening_a_table() -> None:
    opened = False

    def reject_pointer(_: Path) -> GenerationPublicationResult:
        raise ValueError("invalid current pointer")

    def open_table(_: Path, __: tuple[str, ...]) -> pl.DataFrame:
        nonlocal opened
        opened = True
        raise AssertionError("must not open a table")

    with pytest.raises(ValueError, match="invalid current pointer"):
        read_validated_canonical_generation(
            Path("artifacts"), dependencies=CanonicalReaderDependencies(
                resolver=reject_pointer, table_reader=open_table,
            ),
        )
    assert not opened


def test_field_mapping_uses_exact_timestamp_order_and_preserves_nulls() -> None:
    snapshot = CanonicalGenerationSnapshot("generation", "a" * 64, _frames())

    fields = derive_browser_driver_fields(snapshot, "HAM")

    assert fields.time_ms == (1000, 1003, 1012)
    assert fields.x == (None, 0.1, None)
    assert fields.speed == (300.0, None, None)
    assert fields.brake == (None, None, 0)
    assert fields.gear == (None, None, 6)
    assert fields.status == (None, None, None)
    assert fields.track_distance_meters == (None, None, None)


def test_field_mapping_preserves_raw_invalid_gear_in_canonical_but_exposes_null() -> None:
    frames = _frames()
    invalid = pl.DataFrame([{
        "session_id": "race", "driver_id": "HAM", "source_driver_key": "44",
        "session_time_ms": 1020, "speed_kph": 0.0, "rpm": 3900.0, "gear": 75,
        "throttle_pct": 0.0, "brake": False, "drs": 0, "source": "car",
    }], schema=dict(CANONICAL_TABLE_SCHEMAS["car_telemetry"]), strict=True)
    frames["car_telemetry"] = frames["car_telemetry"].vstack(invalid).sort("session_time_ms")
    snapshot = CanonicalGenerationSnapshot("generation", "a" * 64, frames)

    fields = derive_browser_driver_fields(snapshot, "HAM")

    assert snapshot.frames["car_telemetry"].filter(pl.col("session_time_ms") == 1020).item(0, "gear") == 75
    assert fields.gear[fields.time_ms.index(1020)] is None


def test_field_mapping_pairs_pit_entry_and_exit_across_lap_rows() -> None:
    frames = _frames()
    frames["laps"] = _laps([
        _lap(1, 1000, 1100, pit_in=1050),
        _lap(2, 1100, 1200, pit_out=1120),
    ])

    fields = derive_browser_driver_fields(
        CanonicalGenerationSnapshot("generation", "a" * 64, frames),
        "HAM", timeline=(1049, 1050, 1119, 1120),
    )

    assert fields.is_in_pit_lane == (False, True, True, False)


def test_field_mapping_keeps_an_unclosed_final_pit_interval_open() -> None:
    frames = _frames()
    frames["laps"] = _laps([_lap(1, 1000, None, pit_in=1050)])

    fields = derive_browser_driver_fields(
        CanonicalGenerationSnapshot("generation", "a" * 64, frames),
        "HAM", timeline=(1049, 1050, 1200),
    )

    assert fields.is_in_pit_lane == (False, True, True)


def test_field_mapping_ignores_pit_exit_without_a_preceding_entry() -> None:
    frames = _frames()
    frames["laps"] = _laps([_lap(1, 1000, 1200, pit_out=1050)])

    fields = derive_browser_driver_fields(
        CanonicalGenerationSnapshot("generation", "a" * 64, frames),
        "HAM", timeline=(1049, 1050),
    )

    assert fields.is_in_pit_lane == (False, False)


def test_field_mapping_closes_a_zero_duration_pit_interval() -> None:
    frames = _frames()
    frames["laps"] = _laps([_lap(1, 1000, 1200, pit_in=1050, pit_out=1050)])

    fields = derive_browser_driver_fields(
        CanonicalGenerationSnapshot("generation", "a" * 64, frames),
        "HAM", timeline=(1049, 1050, 1100),
    )

    assert fields.is_in_pit_lane == (False, False, False)


def test_field_mapping_keeps_pit_state_through_a_red_flag_gap_between_laps() -> None:
    frames = _frames()
    frames["laps"] = _laps([
        _lap(32, 7000, 7300, pit_in=7200),
        _lap(33, 8600, 9000, pit_out=8650),
    ])

    fields = derive_browser_driver_fields(
        CanonicalGenerationSnapshot("generation", "a" * 64, frames),
        "HAM", timeline=(6999, 7199, 7200, 7299, 7300, 8500, 8649, 8650),
    )

    assert fields.is_in_pit_lane == (None, False, True, True, True, True, True, False)


@pytest.mark.parametrize(("raw_gear", "expected"), [(-1, None), (0, 0), (8, 8), (9, None)])
def test_field_mapping_enforces_browser_gear_domain(raw_gear: int, expected: int | None) -> None:
    frames = _frames()
    row = pl.DataFrame([{
        "session_id": "race", "driver_id": "HAM", "source_driver_key": "44",
        "session_time_ms": 1020, "speed_kph": 0.0, "rpm": 3900.0, "gear": raw_gear,
        "throttle_pct": 0.0, "brake": False, "drs": 0, "source": "car",
    }], schema=dict(CANONICAL_TABLE_SCHEMAS["car_telemetry"]), strict=True)
    frames["car_telemetry"] = frames["car_telemetry"].vstack(row).sort("session_time_ms")

    fields = derive_browser_driver_fields(CanonicalGenerationSnapshot("generation", "a" * 64, frames), "HAM")

    assert fields.gear[fields.time_ms.index(1020)] == expected


def _frames() -> dict[str, pl.DataFrame]:
    frames = {name: pl.DataFrame(schema=dict(schema)) for name, schema in CANONICAL_TABLE_SCHEMAS.items()}
    frames["car_telemetry"] = pl.DataFrame([
        {"session_id": "race", "driver_id": "HAM", "source_driver_key": "44", "session_time_ms": 1012,
         "speed_kph": None, "rpm": None, "gear": 6, "throttle_pct": None, "brake": False, "drs": None, "source": "car"},
        {"session_id": "race", "driver_id": "HAM", "source_driver_key": "44", "session_time_ms": 1000,
         "speed_kph": 300.0, "rpm": None, "gear": None, "throttle_pct": None, "brake": None, "drs": None, "source": "car"},
    ], schema=dict(CANONICAL_TABLE_SCHEMAS["car_telemetry"]), strict=True)
    frames["position_telemetry"] = pl.DataFrame([
        {"session_id": "race", "driver_id": "HAM", "source_driver_key": "44", "session_time_ms": 1003,
         "x": 1.0, "y": None, "z": None, "status": None, "source": "pos"},
    ], schema=dict(CANONICAL_TABLE_SCHEMAS["position_telemetry"]), strict=True)
    return frames


def _laps(rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=dict(CANONICAL_TABLE_SCHEMAS["laps"]), strict=True)


def _lap(
    number: int, start: int, end: int | None, *, pit_in: int | None = None, pit_out: int | None = None,
) -> dict[str, object]:
    return {
        "session_id": "race", "driver_id": "HAM", "source_driver_key": "44",
        "lap_number": number, "stint_number": 1, "lap_start_time_ms": start,
        "lap_end_time_ms": end, "lap_duration_ms": None, "pit_in_time_ms": pit_in,
        "pit_out_time_ms": pit_out, "compound": "SOFT", "tyre_life": number,
        "is_fresh_tyre": number == 1, "track_status": "1", "is_accurate": True,
        "deleted": False, "deleted_reason": None,
    }
