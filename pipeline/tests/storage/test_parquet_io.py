import hashlib
import builtins
from datetime import datetime, timezone

import polars as pl
from polars.testing import assert_frame_equal
import pytest

from f1_replay_pipeline.domain.canonical_schema import CANONICAL_TABLE_SCHEMAS
from f1_replay_pipeline.storage.parquet_io import (
    CANONICAL_PARQUET_TABLE_NAMES,
    PARQUET_WRITE_SETTINGS,
    ParquetCompatibilityError,
    ensure_native_parquet_compatibility,
    parquet_byte_sha256,
    validate_canonical_frames,
    verify_canonical_parquet_round_trip,
    write_canonical_parquet_tables,
)
from f1_replay_pipeline.domain.validators import CanonicalValidationError


def test_native_polars_writes_all_ten_tables_with_exact_schema_order_and_nulls(tmp_path):
    frames = _canonical_frames()

    paths = write_canonical_parquet_tables(frames, tmp_path)

    assert tuple(paths) == CANONICAL_PARQUET_TABLE_NAMES
    for table_name, path in paths.items():
        assert list(pl.read_parquet_schema(path).items()) == list(CANONICAL_TABLE_SCHEMAS[table_name].items())
        restored = pl.read_parquet(path, use_statistics=False, use_pyarrow=False)
        assert_frame_equal(restored, frames[table_name], check_exact=True)
        assert restored.null_count().row(0) == frames[table_name].null_count().row(0)


def test_native_writer_settings_are_the_complete_explicit_v1_contract():
    assert dict(PARQUET_WRITE_SETTINGS) == {
        "use_pyarrow": False,
        "compression": "zstd",
        "compression_level": 3,
        "statistics": "full",
        "row_group_size": 262144,
        "data_page_size": 1048576,
    }


def test_native_polars_preserves_distinguishable_canonical_row_order(tmp_path):
    frames = _canonical_frames()
    frames["drivers"] = _frame(
        "drivers",
        [
            _row("drivers"),
            {**_row("drivers"), "driver_id": "VER", "source_driver_key": "1", "driver_number": 1, "full_name": "Max Verstappen"},
        ],
    )

    path = write_canonical_parquet_tables(frames, tmp_path)["drivers"]

    assert pl.read_parquet(path, use_pyarrow=False).get_column("driver_id").to_list() == ["HAM", "VER"]


def test_native_polars_round_trips_typed_empty_tables_with_both_statistics_paths(tmp_path):
    frames = _canonical_frames(empty_non_metadata=True)

    paths = write_canonical_parquet_tables(frames, tmp_path)

    for table_name, path in paths.items():
        verify_canonical_parquet_round_trip(table_name, frames[table_name], path, use_statistics=True)
        verify_canonical_parquet_round_trip(table_name, frames[table_name], path, use_statistics=False)


def test_boundary_rejects_missing_extra_and_invalid_canonical_frames():
    frames = _canonical_frames()

    with pytest.raises(CanonicalValidationError, match="exactly ten tables"):
        validate_canonical_frames({name: frame for name, frame in frames.items() if name != "results"})
    with pytest.raises(CanonicalValidationError, match="schema mismatch"):
        validate_canonical_frames({**frames, "drivers": frames["drivers"].select("driver_id", "session_id", *frames["drivers"].columns[2:])})


def test_byte_sha256_is_the_exact_closed_artifact_digest_not_a_portability_claim(tmp_path):
    path = write_canonical_parquet_tables(_canonical_frames(), tmp_path)["drivers"]

    digest = parquet_byte_sha256(path)

    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(digest) == 64
    assert PARQUET_WRITE_SETTINGS["use_pyarrow"] is False


def test_native_path_neither_imports_nor_requires_pyarrow(tmp_path, monkeypatch):
    frames = _canonical_frames(empty_non_metadata=True)
    original_import = builtins.__import__

    def reject_pyarrow(name, *args, **kwargs):
        if name == "pyarrow" or name.startswith("pyarrow."):
            raise AssertionError("native Polars path must not import PyArrow")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_pyarrow)

    paths = write_canonical_parquet_tables(frames, tmp_path)

    assert all(path.is_file() for path in paths.values())


def test_incompatible_polars_signature_has_an_actionable_error(monkeypatch):
    def incompatible_writer(self, destination, *, compression):
        del self, destination, compression

    monkeypatch.setattr(pl.DataFrame, "write_parquet", incompatible_writer)

    with pytest.raises(ParquetCompatibilityError, match="lacks required v1 option"):
        ensure_native_parquet_compatibility()


def _canonical_frames(*, empty_non_metadata: bool = False) -> dict[str, pl.DataFrame]:
    return {
        name: _frame(name, [] if empty_non_metadata and name != "session_metadata" else [_row(name)])
        for name in CANONICAL_PARQUET_TABLE_NAMES
    }


def _frame(table_name: str, rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=dict(CANONICAL_TABLE_SCHEMAS[table_name]), strict=True)


def _row(table_name: str) -> dict[str, object]:
    row: dict[str, object] = {column: None for column in CANONICAL_TABLE_SCHEMAS[table_name]}
    row.update({"session_id": "2026-example-race", "driver_id": "HAM"})
    values = {
        "session_metadata": {
            "year": 2026, "round_number": 1, "event_name": "Example Grand Prix",
            "session_name": "Race", "session_type": "R", "session_start_time_utc": datetime(2026, 1, 1, tzinfo=timezone.utc),
        },
        "drivers": {"source_driver_key": "44", "driver_number": 44, "full_name": "Lewis Hamilton"},
        "car_telemetry": {"source_driver_key": "44", "session_time_ms": 0, "source": "car"},
        "position_telemetry": {"source_driver_key": "44", "session_time_ms": 0, "source": "position"},
        "laps": {"lap_number": 1, "lap_start_time_ms": 0},
        "stints": {"stint_number": 1, "start_lap_number": 1},
        "weather": {"session_time_ms": 0},
        "track_status_intervals": {"start_time_ms": 0, "status": "1"},
        "race_control_messages": {"session_time_ms": 0, "message_index": 0, "message": "Race start"},
        "results": {"classified_position": "1"},
    }
    row.update(values[table_name])
    return row
