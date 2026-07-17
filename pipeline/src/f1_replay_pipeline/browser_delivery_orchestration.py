"""Pure orchestration from one validated canonical snapshot to browser chunks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from f1_replay_pipeline.browser_chunk_builder import (
    BrowserChunk,
    BrowserEvent,
    BrowserGlobalFields,
    build_browser_chunks,
)
from f1_replay_pipeline.browser_delivery_models import (
    BrowserManifest,
    CanonicalGenerationSnapshot,
    deep_freeze_json,
)
from f1_replay_pipeline.browser_delivery_reader import derive_browser_driver_fields


@dataclass(frozen=True)
class BrowserDeliveryBuild:
    """One immutable delivery derived from one immutable canonical snapshot."""

    source: CanonicalGenerationSnapshot
    manifest: BrowserManifest
    track_assets: Mapping[str, object]
    chunks: tuple[BrowserChunk, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "track_assets", deep_freeze_json(self.track_assets))
        object.__setattr__(self, "chunks", tuple(self.chunks))


class BrowserDeliveryBuildError(ValueError):
    """An expected failure deriving browser artifacts from canonical data."""


def build_browser_delivery(
    snapshot: CanonicalGenerationSnapshot,
    track_assets: Mapping[str, object],
    *,
    chunk_duration_ms: int = 10_000,
    overlap_ms: int = 1_000,
) -> BrowserDeliveryBuild:
    """Derive all contract fields without rereading or mutating canonical data."""
    try:
        session = snapshot.frames["session_metadata"].row(0, named=True)
        fixture_id = cast(str, session["session_id"])
        _validate_track_assets(track_assets, fixture_id)
        driver_ids = tuple(snapshot.frames["drivers"].get_column("driver_id").to_list())
        native_drivers = tuple(derive_browser_driver_fields(snapshot, driver_id) for driver_id in driver_ids)
        race_start_ms = _race_start_time_ms(snapshot)
        timeline = _delivery_timeline(snapshot, native_drivers, race_start_ms)
        if not timeline:
            raise ValueError("a browser delivery requires a canonical timestamp at or after the Lap 1 start")
        drivers = {
            driver_id: derive_browser_driver_fields(snapshot, driver_id, timeline=timeline)
            for driver_id in driver_ids
        }
        globals_ = _global_fields(snapshot, timeline, driver_ids)
        events = _events(snapshot)
        chunks = build_browser_chunks(
            drivers,
            globals_,
            events,
            start_ms=race_start_ms,
            end_ms=timeline[-1] + 1,
            chunk_duration_ms=chunk_duration_ms,
            overlap_ms=overlap_ms,
        )
        manifest = BrowserManifest(
            fixture_id,
            f"{session['event_name']} {session['session_name']}",
            _driver_metadata(snapshot),
        )
    except ValueError as error:
        raise BrowserDeliveryBuildError(str(error)) from error
    return BrowserDeliveryBuild(snapshot, manifest, track_assets, chunks)


def _race_start_time_ms(snapshot: CanonicalGenerationSnapshot) -> int:
    laps = snapshot.frames["laps"]
    lap_one_starts = (
        laps
        .filter((laps["lap_number"] == 1) & laps["lap_start_time_ms"].is_not_null())
        .get_column("lap_start_time_ms")
        .to_list()
    )
    if not lap_one_starts:
        raise ValueError("a browser delivery requires a non-null Lap 1 start time")
    return min(lap_one_starts)


def _delivery_timeline(snapshot, native_drivers, race_start_ms: int) -> tuple[int, ...]:
    values = {time_ms for driver in native_drivers for time_ms in driver.time_ms}
    values.update(snapshot.frames["weather"].get_column("session_time_ms").drop_nulls().to_list())
    values.update(snapshot.frames["track_status_intervals"].get_column("start_time_ms").drop_nulls().to_list())
    values.update(snapshot.frames["race_control_messages"].get_column("session_time_ms").drop_nulls().to_list())
    laps = snapshot.frames["laps"]
    for column in ("lap_start_time_ms", "pit_in_time_ms", "pit_out_time_ms"):
        values.update(laps.get_column(column).drop_nulls().to_list())
    return tuple(sorted(time_ms for time_ms in values if time_ms >= race_start_ms))


def _global_fields(snapshot, timeline, driver_ids) -> BrowserGlobalFields:
    results = snapshot.frames["results"].to_dicts()
    ranked = sorted(driver_ids, key=lambda driver_id: (_result_rank(results, driver_id), driver_id))
    statuses = snapshot.frames["track_status_intervals"].to_dicts()
    weather = snapshot.frames["weather"].to_dicts()
    return BrowserGlobalFields(
        timeline,
        (tuple(ranked),) * len(timeline),
        tuple(_track_status(statuses, time_ms) for time_ms in timeline),
        tuple(_weather_state(weather, time_ms) for time_ms in timeline),
    )


def _result_rank(rows, driver_id: str) -> int:
    row = next((item for item in rows if item["driver_id"] == driver_id), None)
    value = None if row is None else row["classified_position"]
    return int(value) if isinstance(value, str) and value.isdigit() else 1_000_000


def _track_status(rows, time_ms: int) -> int | None:
    row = next((item for item in rows if _contains_interval(item, time_ms)), None)
    value = None if row is None else row["status"]
    return int(value) if isinstance(value, str) and value.isdigit() else None


def _weather_state(rows, time_ms: int) -> str | None:
    candidates = [row for row in rows if row["session_time_ms"] <= time_ms]
    if not candidates:
        return None
    rainfall = max(candidates, key=lambda row: row["session_time_ms"])["rainfall"]
    return None if rainfall is None else ("rain" if rainfall else "clear")


def _events(snapshot) -> tuple[BrowserEvent, ...]:
    events = []
    for row in snapshot.frames["race_control_messages"].to_dicts():
        event_type = row["category"] or row["flag"] or "race_control"
        description = row["message"] or event_type
        payload = {
            key: row[source]
            for key, source in (("category", "category"), ("flag", "flag"), ("scope", "scope"), ("lapNumber", "lap_number"))
            if row[source] is not None
        }
        events.append(BrowserEvent(row["session_time_ms"], event_type, description, row["driver_id"], payload or None))
    return tuple(events)


def _driver_metadata(snapshot) -> tuple[Mapping[str, object], ...]:
    values = []
    for row in snapshot.frames["drivers"].to_dicts():
        colour = cast(str | None, row["team_colour"])
        number = row["driver_number"]
        if not colour or number is None or not cast(str | None, row["full_name"]) or not cast(str | None, row["team_name"]):
            raise ValueError("browser driver metadata requires name, team, colour, and number")
        values.append({
            "id": row["driver_id"],
            "displayName": row["full_name"],
            "teamName": row["team_name"],
            "colorHex": colour if colour.startswith("#") else f"#{colour}",
            "carNumber": str(number),
        })
    return tuple(values)


def _contains_interval(row, time_ms: int) -> bool:
    end = row["end_time_ms"]
    return row["start_time_ms"] <= time_ms and (end is None or time_ms < end)


def _validate_track_assets(track_assets: Mapping[str, object], fixture_id: str) -> None:
    if not isinstance(track_assets, Mapping):
        raise TypeError("track_assets must be a mapping")
    if track_assets.get("contractVersion") != "v1" or track_assets.get("fixtureId") != fixture_id:
        raise ValueError("track assets must be v1 and match the canonical session_id")


__all__ = ["BrowserDeliveryBuild", "BrowserDeliveryBuildError", "build_browser_delivery"]
