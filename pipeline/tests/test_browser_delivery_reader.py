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
    assert fields.x == (None, 1.0, None)
    assert fields.speed == (300.0, None, None)
    assert fields.brake == (None, None, 0)
    assert fields.gear == (None, None, 6)
    assert fields.status == (None, None, None)
    assert fields.track_distance_meters == (None, None, None)


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
