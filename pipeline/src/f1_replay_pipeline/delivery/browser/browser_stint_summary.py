"""Pure, deterministic derivation of the compact browser stint summary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    BrowserDriverStintSummary,
    BrowserStintSummary,
    CanonicalGenerationSnapshot,
)


_StintKey = tuple[str, int]
_PitTimes = tuple[int | None, int | None]


def build_stint_summary(snapshot: CanonicalGenerationSnapshot) -> BrowserStintSummary:
    """Build null-preserving stint and exact pit-transition data from a snapshot."""
    fixture_id = cast(
        str,
        snapshot.frames["session_metadata"].row(0, named=True)["session_id"],
    )
    driver_ids = tuple(
        sorted(
            cast(str, driver_id)
            for driver_id in snapshot.frames["drivers"].get_column("driver_id").to_list()
        )
    )
    stints_by_driver, stints_by_key = _index_stints(snapshot.frames["stints"].to_dicts())
    laps = snapshot.frames["laps"]
    pit_event_laps = laps.filter(
        laps["pit_in_time_ms"].is_not_null() | laps["pit_out_time_ms"].is_not_null()
    )
    pit_times = _map_pit_events(pit_event_laps.to_dicts(), stints_by_key)
    return BrowserStintSummary(
        fixture_id,
        {
            driver_id: _build_driver_summary(
                driver_id,
                stints_by_driver.get(driver_id, ()),
                pit_times,
            )
            for driver_id in driver_ids
        },
    )


def _index_stints(
    rows: Sequence[Mapping[str, object]],
) -> tuple[dict[str, tuple[Mapping[str, object], ...]], dict[_StintKey, Mapping[str, object]]]:
    grouped: dict[_StintKey, list[Mapping[str, object]]] = {}
    for row in rows:
        key = _stint_key(row)
        grouped.setdefault(key, []).append(row)
    duplicate_keys = tuple(sorted(key for key, values in grouped.items() if len(values) > 1))
    if duplicate_keys:
        driver_id, stint_number = duplicate_keys[0]
        raise ValueError(
            f"duplicate canonical stints for driver {driver_id} and stint {stint_number}"
        )
    by_key = {key: values[0] for key, values in grouped.items()}
    by_driver: dict[str, tuple[Mapping[str, object], ...]] = {}
    for driver_id in sorted({key[0] for key in by_key}):
        driver_stints = tuple(
            sorted(
                (row for key, row in by_key.items() if key[0] == driver_id),
                key=lambda row: cast(int, row["stint_number"]),
            )
        )
        by_driver[driver_id] = driver_stints
    return by_driver, by_key


def _map_pit_events(
    rows: Sequence[Mapping[str, object]],
    stints_by_key: Mapping[_StintKey, Mapping[str, object]],
) -> dict[_StintKey, _PitTimes]:
    mapped: dict[_StintKey, _PitTimes] = {}
    for row in rows:
        if row["pit_in_time_ms"] is None and row["pit_out_time_ms"] is None:
            continue
        driver_id = cast(str, row["driver_id"])
        lap_number = _event_lap_number(row)
        stint_number = row["stint_number"]
        if stint_number is None:
            raise ValueError(
                f"pit event for driver {driver_id} on lap {lap_number} has no stint number"
            )
        if type(stint_number) is not int:
            raise ValueError(
                f"pit event for driver {driver_id} on lap {lap_number} has an invalid stint number"
            )
        key = (driver_id, stint_number)
        stint = stints_by_key.get(key)
        if stint is None:
            raise ValueError(
                f"pit event for driver {driver_id} on lap {lap_number} has no matching canonical stint {stint_number}"
            )
        if not _lap_in_stint_range(lap_number, stint):
            raise ValueError(
                f"pit event for driver {driver_id} on lap {lap_number} lies outside canonical stint {stint_number} range"
            )
        current = mapped.get(key, (None, None))
        pit_in_time_ms = cast(int | None, row["pit_in_time_ms"])
        pit_out_time_ms = cast(int | None, row["pit_out_time_ms"])
        if pit_in_time_ms is not None and current[0] is not None:
            raise ValueError(
                f"multiple pit-in candidates map to driver {driver_id} stint {stint_number}"
            )
        if pit_out_time_ms is not None and current[1] is not None:
            raise ValueError(
                f"multiple pit-out candidates map to driver {driver_id} stint {stint_number}"
            )
        mapped[key] = (
            pit_in_time_ms if pit_in_time_ms is not None else current[0],
            pit_out_time_ms if pit_out_time_ms is not None else current[1],
        )
    return mapped


def _event_lap_number(row: Mapping[str, object]) -> int:
    lap_number = row["lap_number"]
    if type(lap_number) is not int:
        raise ValueError("pit event has an invalid lap number")
    return lap_number


def _lap_in_stint_range(lap_number: int, stint: Mapping[str, object]) -> bool:
    start_lap = stint["start_lap_number"]
    end_lap = stint["end_lap_number"]
    if type(start_lap) is not int:
        raise ValueError("canonical stint has an invalid start lap number")
    if end_lap is not None and type(end_lap) is not int:
        raise ValueError("canonical stint has an invalid end lap number")
    return lap_number >= start_lap and (end_lap is None or lap_number <= end_lap)


def _stint_key(row: Mapping[str, object]) -> _StintKey:
    driver_id = row["driver_id"]
    stint_number = row["stint_number"]
    if not isinstance(driver_id, str) or type(stint_number) is not int:
        raise ValueError("canonical stint has an invalid driver or stint number")
    return driver_id, stint_number


def _build_driver_summary(
    driver_id: str,
    stints: Sequence[Mapping[str, object]],
    pit_times: Mapping[_StintKey, _PitTimes],
) -> BrowserDriverStintSummary:
    values = tuple(
        _stint_values(driver_id, row, pit_times)
        for row in stints
    )
    return BrowserDriverStintSummary(
        stint_number=tuple(value[0] for value in values),
        compound=tuple(value[1] for value in values),
        start_lap=tuple(value[2] for value in values),
        end_lap=tuple(value[3] for value in values),
        start_time_ms=tuple(value[4] for value in values),
        end_time_ms=tuple(value[5] for value in values),
        tyre_life_at_start=tuple(value[6] for value in values),
        is_fresh_tyre=tuple(value[7] for value in values),
        pit_in_time_ms=tuple(value[8] for value in values),
        pit_out_time_ms=tuple(value[9] for value in values),
    )


def _stint_values(
    driver_id: str,
    row: Mapping[str, object],
    pit_times: Mapping[_StintKey, _PitTimes],
) -> tuple[
    int,
    str | None,
    int,
    int | None,
    int | None,
    int | None,
    int | None,
    bool | None,
    int | None,
    int | None,
]:
    stint_number = cast(int, row["stint_number"])
    pit_in_time_ms, pit_out_time_ms = pit_times.get((driver_id, stint_number), (None, None))
    return (
        stint_number,
        cast(str | None, row["compound"]),
        cast(int, row["start_lap_number"]),
        cast(int | None, row["end_lap_number"]),
        cast(int | None, row["start_time_ms"]),
        cast(int | None, row["end_time_ms"]),
        cast(int | None, row["tyre_life_at_start"]),
        cast(bool | None, row["is_fresh_tyre"]),
        pit_in_time_ms,
        pit_out_time_ms,
    )


__all__ = ["build_stint_summary"]
