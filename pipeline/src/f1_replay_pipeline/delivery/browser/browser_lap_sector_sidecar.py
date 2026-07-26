"""Pure, deterministic derivation of the compact browser lap/sector sidecar."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import cast

import polars as pl

from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    BrowserDriverLapSector,
    BrowserLapSectorSidecar,
    CanonicalGenerationSnapshot,
)

_LAP_COLUMNS = (
    "lap_number",
    "lap_start_time_ms",
    "lap_end_time_ms",
    "lap_duration_ms",
    "sector_1_duration_ms",
    "sector_2_duration_ms",
    "sector_3_duration_ms",
    "sector_1_session_time_ms",
    "sector_2_session_time_ms",
    "sector_3_session_time_ms",
)

def build_lap_sector_sidecar(snapshot: CanonicalGenerationSnapshot) -> BrowserLapSectorSidecar:
    """Return a compact columnar BrowserLapSectorSidecar from canonical laps.

    The function is pure, immutable, and deterministic: the same canonical snapshot
    always produces the same sidecar. Rows with a null ``lap_start_time_ms`` or
    ``lap_end_time_ms`` are dropped because the sidecar contains only completed
    laps; null canonical sector/session fields are propagated as ``None``; no
    values are invented.
    """
    fixture_id = cast(str, snapshot.frames["session_metadata"].row(0, named=True)["session_id"])
    driver_ids = tuple(sorted(snapshot.frames["drivers"].get_column("driver_id").to_list()))
    laps = snapshot.frames["laps"]
    completed_laps = laps.filter(
        laps["lap_start_time_ms"].is_not_null() & laps["lap_end_time_ms"].is_not_null()
    )
    driver_groups = _group_laps_by_driver(completed_laps)
    return BrowserLapSectorSidecar(
        fixture_id,
        {
            driver_id: _driver_lap_sector(
                driver_groups.get(driver_id, _EMPTY_LAP_GROUP),
                total_rows=0 if driver_id not in driver_groups else len(driver_groups[driver_id]["lap_number"]),
            )
            for driver_id in driver_ids
        },
    )


_EMPTY_LAP_GROUP: Mapping[str, tuple[object, ...]] = MappingProxyType({})


def _group_laps_by_driver(laps: pl.DataFrame) -> dict[str, dict[str, tuple[object, ...]]]:
    """Return canonical lap columns per driver, sorted by lap_number ascending."""
    groups: dict[str, dict[str, tuple[object, ...]]] = {}
    if laps.is_empty():
        return groups
    for driver_id in laps.get_column("driver_id").unique(maintain_order=True):
        driver_rows = (
            laps.filter(laps["driver_id"] == driver_id)
            .sort("lap_number", descending=False)
            .select(_LAP_COLUMNS)
        )
        groups[driver_id] = {
            column: tuple(driver_rows.get_column(column).to_list())
            for column in _LAP_COLUMNS
        }
    return groups


def _driver_lap_sector(
    group: Mapping[str, tuple[object, ...]], *, total_rows: int,
) -> BrowserDriverLapSector:
    """Build one BrowserDriverLapSector from aligned column tuples."""
    if total_rows == 0:
        return BrowserDriverLapSector(
            lap_number=(),
            lap_start_ms=(),
            lap_end_ms=(),
            lap_duration_ms=(),
            sector_1_duration_ms=(),
            sector_2_duration_ms=(),
            sector_3_duration_ms=(),
            sector_1_session_time_ms=(),
            sector_2_session_time_ms=(),
            sector_3_session_time_ms=(),
        )
    return BrowserDriverLapSector(
        lap_number=cast(tuple[int, ...], group["lap_number"]),
        lap_start_ms=cast(tuple[int, ...], group["lap_start_time_ms"]),
        lap_end_ms=cast(tuple[int, ...], group["lap_end_time_ms"]),
        lap_duration_ms=cast(tuple[int | None, ...], group["lap_duration_ms"]),
        sector_1_duration_ms=cast(tuple[int | None, ...], group["sector_1_duration_ms"]),
        sector_2_duration_ms=cast(tuple[int | None, ...], group["sector_2_duration_ms"]),
        sector_3_duration_ms=cast(tuple[int | None, ...], group["sector_3_duration_ms"]),
        sector_1_session_time_ms=cast(tuple[int | None, ...], group["sector_1_session_time_ms"]),
        sector_2_session_time_ms=cast(tuple[int | None, ...], group["sector_2_session_time_ms"]),
        sector_3_session_time_ms=cast(tuple[int | None, ...], group["sector_3_session_time_ms"]),
    )


__all__ = ["build_lap_sector_sidecar"]
