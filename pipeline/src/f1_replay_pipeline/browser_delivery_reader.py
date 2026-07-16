"""Read validated canonical generations and derive exact-time browser fields."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import cast

import polars as pl

from f1_replay_pipeline.browser_delivery_models import (
    FASTF1_POSITION_UNITS_PER_METER,
    BrowserDriverFields,
    CanonicalGenerationSnapshot,
)
from f1_replay_pipeline.canonical_generation_validation import validate_complete_canonical_generation
from f1_replay_pipeline.canonical_schema import CANONICAL_TABLE_SCHEMAS
from f1_replay_pipeline.dataset_manifest import DatasetManifest, TableManifestEntry
from f1_replay_pipeline.generation_publication import (
    GenerationPublicationError,
    GenerationPublicationResult,
    read_regular_file_no_follow,
    resolve_current_generation,
    verify_regular_file_identity,
)
from f1_replay_pipeline.logical_hashes import logical_table_sha256
from f1_replay_pipeline.validators import validate_canonical_table


GenerationResolver = Callable[[Path], GenerationPublicationResult]
GenerationValidator = Callable[..., DatasetManifest]
TableReader = Callable[[Path, tuple[str, ...]], pl.DataFrame]


class BrowserDeliveryReadError(ValueError):
    """An expected canonical-read failure at the browser delivery boundary."""


@dataclass(frozen=True)
class CanonicalReaderDependencies:
    """Injected read seams; production defaults only read already-validated files."""

    resolver: GenerationResolver = resolve_current_generation
    validator: GenerationValidator = validate_complete_canonical_generation
    table_reader: TableReader | None = None


def read_validated_canonical_generation(
    target_parent: Path, *, dependencies: CanonicalReaderDependencies = CanonicalReaderDependencies(),
) -> CanonicalGenerationSnapshot:
    """Resolve ``current.json``, validate its generation, then read its ten tables.

    The function has no write path and never chooses a generation directory on
    its own.  It validates before invoking the table reader, including injected
    readers used by focused tests.
    """
    if not isinstance(target_parent, Path):
        raise TypeError("target_parent must be a pathlib.Path")
    try:
        resolved = dependencies.resolver(target_parent)
        manifest = dependencies.validator(
            resolved.generation_path,
            expected_generation_id=resolved.generation_path.name,
            expected_manifest_sha256=resolved.manifest_sha256,
        )
        reader = _read_projected_table if dependencies.table_reader is None else dependencies.table_reader
        entries = cast(tuple[TableManifestEntry, ...], manifest.tables)
        frames = {
            entry.name: _read_and_verify_table(resolved.generation_path, entry, reader)
            for entry in entries
        }
    except (GenerationPublicationError, ValueError) as error:
        raise BrowserDeliveryReadError(str(error)) from error
    return CanonicalGenerationSnapshot(resolved.generation_path.name, resolved.manifest_sha256, frames)


def derive_browser_driver_fields(
    snapshot: CanonicalGenerationSnapshot, driver_id: str, *, timeline: tuple[int, ...] | None = None,
) -> BrowserDriverFields:
    """Map one driver's native observations without filling or temporal matching."""
    if not isinstance(driver_id, str) or not driver_id:
        raise ValueError("driver_id must be a non-empty string")
    car = _driver_rows(snapshot.frames["car_telemetry"], driver_id)
    position = _driver_rows(snapshot.frames["position_telemetry"], driver_id)
    laps = _driver_rows(snapshot.frames["laps"], driver_id)
    native_timestamps = tuple(sorted(set(_timestamps(car) + _timestamps(position))))
    timestamps = native_timestamps if timeline is None else timeline
    if tuple(sorted(set(timestamps))) != timestamps or any(type(value) is not int or value < 0 for value in timestamps):
        raise ValueError("timeline must contain sorted unique non-negative integer milliseconds")
    car_by_time = _rows_by_time(car)
    position_by_time = _rows_by_time(position)
    values = tuple(_field_values(time_ms, car_by_time, position_by_time, laps) for time_ms in timestamps)
    count = len(timestamps)
    return BrowserDriverFields(
        driver_id=driver_id, time_ms=timestamps,
        x=tuple(value[0] for value in values), y=tuple(value[1] for value in values),
        speed=tuple(value[2] for value in values), throttle=tuple(value[3] for value in values),
        brake=tuple(value[4] for value in values), gear=tuple(value[5] for value in values),
        drs=tuple(value[6] for value in values), status=tuple(value[7] for value in values),
        lap=tuple(value[8] for value in values), tyre_compound=tuple(value[9] for value in values),
        is_in_pit_lane=tuple(value[10] for value in values),
        track_distance_meters=(None,) * count, gap_to_leader_ms=(None,) * count,
        position=(None,) * count,
    )


def _read_and_verify_table(
    generation_path: Path, entry: TableManifestEntry, reader: TableReader,
) -> pl.DataFrame:
    columns = tuple(CANONICAL_TABLE_SCHEMAS[entry.name])
    frame = reader(generation_path / entry.path, columns)
    if tuple(frame.columns) != columns or frame.schema != CANONICAL_TABLE_SCHEMAS[entry.name]:
        raise ValueError(f"{entry.name} reader schema or column order differs from canonical contract")
    validate_canonical_table(entry.name, frame)
    if frame.height != entry.row_count or logical_table_sha256(entry.name, frame) != entry.logical_sha256:
        raise ValueError(f"{entry.name} changed after canonical generation validation")
    return frame


def _read_projected_table(path: Path, columns: tuple[str, ...]) -> pl.DataFrame:
    guarded = read_regular_file_no_follow(path, f"canonical table {path.name}")
    frame = pl.read_parquet(BytesIO(guarded.data), columns=list(columns), use_pyarrow=False)
    verify_regular_file_identity(path, guarded, f"canonical table {path.name}")
    return frame


def _driver_rows(frame: pl.DataFrame, driver_id: str) -> tuple[dict[str, object], ...]:
    return tuple(frame.filter(pl.col("driver_id") == driver_id).to_dicts())


def _timestamps(rows: tuple[dict[str, object], ...]) -> tuple[int, ...]:
    return tuple(cast(int, row["session_time_ms"]) for row in rows)


def _rows_by_time(rows: tuple[dict[str, object], ...]) -> dict[int, dict[str, object]]:
    return {cast(int, row["session_time_ms"]): row for row in rows}


def _field_values(
    time_ms: int, car: dict[int, dict[str, object]], position: dict[int, dict[str, object]], laps: tuple[dict[str, object], ...],
) -> tuple[float | None, float | None, float | None, float | None, int | None, int | None, int | None, str | None, int | None, str | None, bool | None]:
    car_row, position_row = car.get(time_ms), position.get(time_ms)
    lap_row = next((row for row in laps if _contains(row, time_ms)), None)
    brake = None if car_row is None or car_row["brake"] is None else int(cast(bool, car_row["brake"]))
    return (
        _browser_coordinate(None if position_row is None else position_row["x"]),
        _browser_coordinate(None if position_row is None else position_row["y"]),
        None if car_row is None else cast(float | None, car_row["speed_kph"]),
        None if car_row is None else cast(float | None, car_row["throttle_pct"]), brake,
        _browser_gear(None if car_row is None else car_row["gear"]),
        None if car_row is None else cast(int | None, car_row["drs"]),
        None if position_row is None else cast(str | None, position_row["status"]),
        None if lap_row is None else cast(int | None, lap_row["lap_number"]),
        None if lap_row is None else cast(str | None, lap_row["compound"]), _pit_state(lap_row, time_ms),
    )


def _contains(row: dict[str, object], time_ms: int) -> bool:
    start, end = cast(int, row["lap_start_time_ms"]), row["lap_end_time_ms"]
    return time_ms >= start and (end is None or time_ms < cast(int, end))


def _pit_state(row: dict[str, object] | None, time_ms: int) -> bool | None:
    if row is None:
        return None
    pit_in, pit_out = row["pit_in_time_ms"], row["pit_out_time_ms"]
    if pit_in is None and pit_out is None:
        return False
    if pit_in is None or pit_out is None:
        return None
    return cast(int, pit_in) <= time_ms < cast(int, pit_out)


def _browser_gear(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 8 else None


def _browser_coordinate(value: object) -> float | None:
    if value is None:
        return None
    return float(cast(float, value)) / FASTF1_POSITION_UNITS_PER_METER


__all__ = [
    "CanonicalReaderDependencies", "GenerationResolver", "GenerationValidator", "TableReader",
    "BrowserDeliveryReadError", "derive_browser_driver_fields", "read_validated_canonical_generation",
]
