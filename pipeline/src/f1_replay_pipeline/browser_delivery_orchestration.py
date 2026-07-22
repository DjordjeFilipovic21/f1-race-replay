"""Pure orchestration from one validated canonical snapshot to browser chunks."""

from __future__ import annotations

from collections.abc import Callable, Mapping
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
    BrowserLapStart,
    CanonicalGenerationSnapshot,
    deep_freeze_json,
)
from f1_replay_pipeline.browser_delivery_reader import derive_browser_driver_fields
from f1_replay_pipeline.live_position_progress import ProgressMode, ProgressState, advance_progress
from f1_replay_pipeline.live_position_projection import ProjectionGeometry, ProjectionGeometryError, project_meters
from f1_replay_pipeline.live_position_quality import (
    QUALITY_GATE_VERSION,
    ProjectionQualityAssessment,
    assess_projection_quality,
)
from f1_replay_pipeline.live_position_ranking import DriverProgressInput, RankingTimelineFrame, rank_timeline
from f1_replay_pipeline.track_assets_generator import TrackAssetsGenerationError


ProjectionQualityAssessor = Callable[[CanonicalGenerationSnapshot, Mapping[str, object]], ProjectionQualityAssessment]


@dataclass(frozen=True)
class BrowserDeliveryBuild:
    """One immutable delivery derived from one immutable canonical snapshot."""

    source: CanonicalGenerationSnapshot
    manifest: BrowserManifest
    track_assets: Mapping[str, object]
    chunks: tuple[BrowserChunk, ...]
    projection_quality_assessment: ProjectionQualityAssessment | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "track_assets", deep_freeze_json(self.track_assets))
        object.__setattr__(self, "chunks", tuple(self.chunks))
        if self.projection_quality_assessment is not None and not isinstance(self.projection_quality_assessment, ProjectionQualityAssessment):
            raise TypeError("projection_quality_assessment must be a ProjectionQualityAssessment or None")


class BrowserDeliveryBuildError(ValueError):
    """An expected failure deriving browser artifacts from canonical data."""


def build_browser_delivery(
    snapshot: CanonicalGenerationSnapshot,
    track_assets: Mapping[str, object],
    *,
    chunk_duration_ms: int = 10_000,
    overlap_ms: int = 1_000,
    quality_assessor: ProjectionQualityAssessor = assess_projection_quality,
) -> BrowserDeliveryBuild:
    """Derive all contract fields without rereading or mutating canonical data."""
    try:
        session = snapshot.frames["session_metadata"].row(0, named=True)
        fixture_id = cast(str, session["session_id"])
        _validate_track_assets(track_assets, fixture_id)
        driver_ids = tuple(snapshot.frames["drivers"].get_column("driver_id").to_list())
        race_start_ms = _race_start_time_ms(snapshot)
        timeline = _delivery_timeline(snapshot, race_start_ms)
        if not timeline:
            raise ValueError("a browser delivery requires a canonical timestamp at or after the Lap 1 start")
        drivers = {
            driver_id: derive_browser_driver_fields(snapshot, driver_id, timeline=timeline)
            for driver_id in driver_ids
        }
        terminal_end_times = _terminal_end_times(snapshot, race_start_ms)
        drivers = {
            driver_id: _with_terminal_status(fields, terminal_end_times.get(driver_id))
            for driver_id, fields in drivers.items()
        }
        assessment = _assess_quality(snapshot, track_assets, quality_assessor)
        if assessment.passed:
            drivers, dynamic_orders = _derive_live_fields(
                snapshot, track_assets, drivers, timeline, terminal_end_times,
            )
        else:
            dynamic_orders = None
        globals_ = _global_fields(snapshot, timeline, driver_ids, dynamic_orders)
        lap_starts = _leader_lap_starts(timeline, drivers, globals_.leaderboard_order)
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
            lap_starts,
        )
    except ValueError as error:
        raise BrowserDeliveryBuildError(str(error)) from error
    return BrowserDeliveryBuild(snapshot, manifest, track_assets, chunks, assessment)


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


def _delivery_timeline(snapshot: CanonicalGenerationSnapshot, race_start_ms: int) -> tuple[int, ...]:
    """Return the sorted unique canonical timestamp union at the race boundary."""
    timestamp_columns = (
        ("car_telemetry", "session_time_ms"),
        ("position_telemetry", "session_time_ms"),
        ("weather", "session_time_ms"),
        ("track_status_intervals", "start_time_ms"),
        ("race_control_messages", "session_time_ms"),
        ("laps", "lap_start_time_ms"),
        ("laps", "pit_in_time_ms"),
        ("laps", "pit_out_time_ms"),
    )
    values = {
        cast(int, time_ms)
        for table, column in timestamp_columns
        for time_ms in snapshot.frames[table].get_column(column).drop_nulls().to_list()
    }
    return tuple(sorted(time_ms for time_ms in values if time_ms >= race_start_ms))


def _global_fields(snapshot, timeline, driver_ids, dynamic_orders: tuple[tuple[str, ...] | None, ...] | None = None) -> BrowserGlobalFields:
    results = snapshot.frames["results"].to_dicts()
    ranked = sorted(driver_ids, key=lambda driver_id: (_result_rank(results, driver_id), driver_id))
    statuses = snapshot.frames["track_status_intervals"].to_dicts()
    weather = snapshot.frames["weather"].to_dicts()
    return BrowserGlobalFields(
        timeline,
        (tuple(ranked),) * len(timeline) if dynamic_orders is None else dynamic_orders,
        tuple(_track_status(statuses, time_ms) for time_ms in timeline),
        tuple(_weather_state(weather, time_ms) for time_ms in timeline),
    )


def _leader_lap_starts(timeline, drivers, leaderboard_orders) -> tuple[BrowserLapStart, ...]:
    """Index first timestamps for increasing laps of the displayed leader."""
    markers = []
    last_lap = 0
    for index, (time_ms, order) in enumerate(zip(timeline, leaderboard_orders, strict=True)):
        if not order:
            continue
        lap = drivers[order[0]].lap[index]
        if type(lap) is int and lap > last_lap:
            markers.append(BrowserLapStart(lap, time_ms))
            last_lap = lap
    return tuple(markers)


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


def _assess_quality(snapshot, track_assets, assessor: ProjectionQualityAssessor) -> ProjectionQualityAssessment:
    if not callable(assessor):
        raise TypeError("quality_assessor must be callable")
    try:
        assessment = assessor(snapshot, track_assets)
    except (ProjectionGeometryError, TrackAssetsGenerationError):
        return ProjectionQualityAssessment(
            QUALITY_GATE_VERSION, False, ("insufficient projection quality evidence",), "", 0,
            0, 0, None, None, 0, 0, 0, 0, None, None,
        )
    if not isinstance(assessment, ProjectionQualityAssessment):
        raise TypeError("quality_assessor must return ProjectionQualityAssessment")
    return assessment


def _derive_live_fields(snapshot, track_assets, drivers, timeline, terminal_end_times):
    geometry = _projection_geometry(track_assets)
    result_statuses = {row["driver_id"]: row["status"] for row in snapshot.frames["results"].to_dicts()}
    states = {driver_id: ProgressState() for driver_id in drivers}
    distances = {driver_id: [] for driver_id in drivers}
    gaps = {driver_id: [] for driver_id in drivers}
    positions = {driver_id: [] for driver_id in drivers}
    ranking_frames = []
    for index, time_ms in enumerate(timeline):
        inputs = []
        for driver_id, fields in drivers.items():
            lap = fields.lap[index]
            mode = _progress_mode(
                fields.is_in_pit_lane[index], result_statuses.get(driver_id),
                terminal_end_times.get(driver_id), time_ms,
            )
            effective_lap = lap
            if effective_lap is None and mode in (ProgressMode.RETIRED, ProgressMode.OUT):
                effective_lap = states[driver_id].last_lap_number
            if effective_lap is None:
                update = None
            else:
                projection = project_meters(fields.x[index], fields.y[index], geometry, previous_track_distance_meters=states[driver_id].last_track_distance_meters)
                update = advance_progress(states[driver_id], session_time_ms=time_ms, lap_number=effective_lap, circuit_length_meters=geometry.circuit_length_meters, projection=projection, mode=mode)
                states[driver_id] = update.state
            inputs.append(DriverProgressInput(driver_id, None if update is None else update.race_progress_meters, mode))
            frozen_distance = states[driver_id].last_track_distance_meters if update is not None and update.race_progress_meters is not None else None
            distance = None if update is None else update.track_distance_meters if update.track_distance_meters is not None else frozen_distance
            distances[driver_id].append(distance)
        ranking_frames.append(RankingTimelineFrame(time_ms, tuple(inputs)))
    ranked_frames = rank_timeline(tuple(ranking_frames))
    orders = []
    for ranking in ranked_frames:
        for entry in ranking.drivers:
            gaps[entry.driver_id].append(entry.gap_to_leader_ms)
            positions[entry.driver_id].append(entry.position)
        orders.append(ranking.leaderboard_order or None)
    return {
        driver_id: _with_derived_fields(fields, distances[driver_id], gaps[driver_id], positions[driver_id])
        for driver_id, fields in drivers.items()
    }, tuple(orders)


def _projection_geometry(track_assets: Mapping[str, object]) -> ProjectionGeometry:
    centerline = track_assets.get("centerLine")
    if not isinstance(centerline, (list, tuple)):
        raise ProjectionGeometryError("track assets centerLine must be a sequence")
    points = tuple(
        cast(tuple[float, float], (point.get("x"), point.get("y")))
        for point in centerline
        if isinstance(point, Mapping)
    )
    return ProjectionGeometry(points, cast(float, track_assets.get("circuitLengthMeters")))


def _terminal_end_times(snapshot, race_start_ms: int) -> dict[str, int]:
    lap_ends = _last_lap_end_times(snapshot)
    car_activity = _last_car_activity_times(snapshot)
    values = {}
    for result in snapshot.frames["results"].to_dicts():
        mode = _terminal_mode(result["status"])
        if mode is None:
            continue
        driver_id = result["driver_id"]
        if mode is ProgressMode.OUT and _normalized_status(result["status"]) in {"didnotstart", "dns"}:
            values[driver_id] = race_start_ms - 1
            continue
        values[driver_id] = max(race_start_ms, lap_ends.get(driver_id, race_start_ms), car_activity.get(driver_id, race_start_ms))
    return values


def _last_lap_end_times(snapshot) -> dict[str, int]:
    return _last_times(snapshot.frames["laps"].to_dicts(), "lap_end_time_ms", lambda _: True)


def _last_car_activity_times(snapshot) -> dict[str, int]:
    return _last_times(
        snapshot.frames["car_telemetry"].to_dicts(), "session_time_ms",
        lambda row: isinstance(row["speed_kph"], (int, float)) and row["speed_kph"] > 5,
    )


def _last_times(rows, time_key, predicate) -> dict[str, int]:
    values = {}
    for row in rows:
        time_ms = row[time_key]
        if predicate(row) and type(time_ms) is int:
            values[row["driver_id"]] = max(values.get(row["driver_id"], time_ms), time_ms)
    return values


def _progress_mode(in_pit, result_status, terminal_end_time, time_ms) -> ProgressMode:
    terminal_mode = _terminal_mode(result_status)
    if terminal_mode is not None and terminal_end_time is not None and time_ms > terminal_end_time:
        return terminal_mode
    return ProgressMode.PIT if in_pit is True else ProgressMode.ACTIVE


def _terminal_mode(result_status) -> ProgressMode | None:
    normalized = _normalized_status(result_status)
    if normalized in {"disqualified", "excluded", "didnotstart", "dns"}:
        return ProgressMode.OUT
    return ProgressMode.RETIRED if normalized in _KNOWN_NON_COMPLETION_STATUSES else None


def _normalized_status(value) -> str:
    return "" if not isinstance(value, str) else "".join(character for character in value.lower() if character.isalnum())


_KNOWN_NON_COMPLETION_STATUSES = frozenset({
    "retired", "accident", "collision", "engine", "gearbox", "transmission", "clutch",
    "hydraulics", "electrical", "brakes", "suspension", "damage", "mechanical", "fuel",
    "tyre", "wheel", "overheating", "withdrawn",
})


def _with_terminal_status(fields, terminal_end_time):
    statuses = tuple(
        "OUT" if terminal_end_time is not None and time_ms > terminal_end_time else status
        for time_ms, status in zip(fields.time_ms, fields.status, strict=True)
    )
    return type(fields)(
        fields.driver_id, fields.time_ms, fields.x, fields.y, fields.speed, fields.throttle,
        fields.brake, fields.gear, fields.drs, statuses, fields.lap, fields.tyre_compound,
        fields.is_in_pit_lane, fields.track_distance_meters, fields.gap_to_leader_ms, fields.position, fields.rpm,
    )


def _with_derived_fields(fields, distances, gaps, positions):
    return type(fields)(
        fields.driver_id, fields.time_ms, fields.x, fields.y, fields.speed, fields.throttle,
        fields.brake, fields.gear, fields.drs, fields.status, fields.lap, fields.tyre_compound,
        fields.is_in_pit_lane, tuple(distances), tuple(gaps), tuple(positions), fields.rpm,
    )


__all__ = ["BrowserDeliveryBuild", "BrowserDeliveryBuildError", "ProjectionQualityAssessor", "build_browser_delivery"]
