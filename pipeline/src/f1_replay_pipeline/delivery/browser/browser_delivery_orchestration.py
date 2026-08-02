"""Pure orchestration from one validated canonical snapshot to browser chunks."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from statistics import median
from types import MappingProxyType
from typing import cast

from f1_replay_pipeline.delivery.browser.browser_chunk_builder import (
    BrowserChunk,
    BrowserEvent,
    BrowserGlobalFields,
    build_browser_chunks,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    BrowserDnfMarker,
    BrowserDriverFields,
    BrowserLapSectorSidecar,
    BrowserManifest,
    BrowserLapStart,
    BrowserPenaltySidecar,
    BrowserPitLossModel,
    BrowserQualifyingLapStatusSidecar,
    BrowserStintSummary,
    BrowserTimelineInterval,
    BrowserTimelineSummary,
    CanonicalGenerationSnapshot,
    TimelineSummaryKind,
    deep_freeze_json,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_reader import derive_browser_driver_fields
from f1_replay_pipeline.delivery.browser.browser_lap_sector_sidecar import build_lap_sector_sidecar
from f1_replay_pipeline.delivery.browser.browser_lap_status import (
    build_qualifying_lap_status_sidecar,
    has_qualifying_lap_status_messages,
)
from f1_replay_pipeline.delivery.browser.browser_penalty_sidecar import build_penalty_sidecar
from f1_replay_pipeline.delivery.browser.browser_pit_loss_model import build_pit_loss_timeline
from f1_replay_pipeline.delivery.browser.browser_pit_loss_observation import (
    extract_eligible_pit_loss_observations,
)
from f1_replay_pipeline.delivery.browser.browser_stint_summary import build_stint_summary
from f1_replay_pipeline.analysis.live_position.live_position_progress import (
    ProgressMode,
    ProgressState,
    ProgressUpdate,
    advance_progress,
    ranking_distance,
    seed_progress,
)
from f1_replay_pipeline.analysis.live_position.live_position_projection import (
    CenterlineProjection,
    ProjectionGeometry,
    ProjectionGeometryError,
    project_meters,
)
from f1_replay_pipeline.analysis.live_position.live_position_quality import (
    QUALITY_GATE_VERSION,
    ProjectionQualityAssessment,
    assess_projection_quality,
)
from f1_replay_pipeline.analysis.live_position.live_position_ranking import DriverProgressInput, RankingTimelineFrame, rank_timeline
from f1_replay_pipeline.app.track_assets_generator import TrackAssetsGenerationError
from f1_replay_pipeline.domain.session_modes import SessionMode, normalize_session_mode


ProjectionQualityAssessor = Callable[[CanonicalGenerationSnapshot, Mapping[str, object]], ProjectionQualityAssessment]

_STARTUP_PROJECTION_WINDOW_MS = 5_000
_MAX_SHIFTED_STARTUP_SPAN_FRACTION = 0.2
_NON_STARTER_RESULT_STATUSES = frozenset({"dns", "didnotstart"})
_RACE_SESSION_MODES = frozenset({"race", "sprint"})
_QUALIFYING_SESSION_MODES = frozenset({
    "qualifying", "sprint-qualifying", "sprint-shootout",
})


@dataclass(frozen=True)
class _StartupCandidate:
    grid_position: int
    driver_id: str
    fields: BrowserDriverFields


@dataclass(frozen=True)
class _StartupProjection:
    timestamp_ms: int
    candidates: tuple[tuple[_StartupCandidate, int, CenterlineProjection], ...]


@dataclass(frozen=True)
class _StartupSeed:
    state: ProgressState
    update: ProgressUpdate


@dataclass(frozen=True)
class _StartupSeedPlan:
    timestamp_ms: int
    seeds: Mapping[str, _StartupSeed]


@dataclass(frozen=True)
class BrowserDeliveryBuild:
    """One immutable delivery derived from one immutable canonical snapshot."""

    source: CanonicalGenerationSnapshot
    manifest: BrowserManifest
    track_assets: Mapping[str, object]
    chunks: tuple[BrowserChunk, ...]
    projection_quality_assessment: ProjectionQualityAssessment | None = None
    timeline_summary: BrowserTimelineSummary | None = None
    lap_sector_sidecar: BrowserLapSectorSidecar | None = None
    stint_summary: BrowserStintSummary | None = None
    pit_loss_model: BrowserPitLossModel | None = None
    penalty_sidecar: BrowserPenaltySidecar | None = None
    qualifying_lap_status_sidecar: BrowserQualifyingLapStatusSidecar | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "track_assets", deep_freeze_json(self.track_assets))
        object.__setattr__(self, "chunks", tuple(self.chunks))
        if self.projection_quality_assessment is not None and not isinstance(self.projection_quality_assessment, ProjectionQualityAssessment):
            raise TypeError("projection_quality_assessment must be a ProjectionQualityAssessment or None")
        if self.timeline_summary is not None and not isinstance(self.timeline_summary, BrowserTimelineSummary):
            raise TypeError("timeline_summary must be a BrowserTimelineSummary or None")
        if self.lap_sector_sidecar is not None and not isinstance(self.lap_sector_sidecar, BrowserLapSectorSidecar):
            raise TypeError("lap_sector_sidecar must be a BrowserLapSectorSidecar or None")
        if self.stint_summary is not None and not isinstance(self.stint_summary, BrowserStintSummary):
            raise TypeError("stint_summary must be a BrowserStintSummary or None")
        if self.pit_loss_model is not None and not isinstance(self.pit_loss_model, BrowserPitLossModel):
            raise TypeError("pit_loss_model must be a BrowserPitLossModel or None")
        if self.penalty_sidecar is not None and not isinstance(self.penalty_sidecar, BrowserPenaltySidecar):
            raise TypeError("penalty_sidecar must be a BrowserPenaltySidecar or None")
        if self.qualifying_lap_status_sidecar is not None and not isinstance(
            self.qualifying_lap_status_sidecar, BrowserQualifyingLapStatusSidecar,
        ):
            raise TypeError(
                "qualifying_lap_status_sidecar must be a "
                "BrowserQualifyingLapStatusSidecar or None"
            )


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
    pit_loss_model: BrowserPitLossModel | None = None
    assessment: ProjectionQualityAssessment | None = None
    try:
        session = snapshot.frames["session_metadata"].row(0, named=True)
        fixture_id = cast(str, session["session_id"])
        session_mode = _session_mode(session)
        if session_mode == "testing":
            raise ValueError(
                "testing sessions are not supported by the active browser delivery boundary"
            )
        race_semantics = session_mode in _RACE_SESSION_MODES
        season_metadata, telemetry_capabilities = _telemetry_metadata(session)
        _validate_track_assets(track_assets, fixture_id)
        driver_ids = tuple(snapshot.frames["drivers"].get_column("driver_id").to_list())
        replay_start_ms = _session_start_time_ms(snapshot, session_mode)
        timeline = _delivery_timeline(snapshot, replay_start_ms)
        if not timeline:
            raise ValueError("a browser delivery requires a canonical timestamp at or after the session boundary")
        drivers = {
            driver_id: derive_browser_driver_fields(snapshot, driver_id, timeline=timeline)
            for driver_id in driver_ids
        }
        terminal_end_times: Mapping[str, int] = {}
        finish_end_times: Mapping[str, int] = {}
        dynamic_orders: tuple[tuple[str, ...] | None, ...] | None = None
        if race_semantics:
            terminal_end_times = _terminal_end_times(snapshot, replay_start_ms)
            drivers = {
                driver_id: _with_terminal_status(fields, terminal_end_times.get(driver_id))
                for driver_id, fields in drivers.items()
            }
            finish_end_times = _finished_end_times(snapshot, replay_start_ms)
            drivers = {
                driver_id: _with_finished_state(fields, finish_end_times.get(driver_id))
                for driver_id, fields in drivers.items()
            }
            assessment = _assess_quality(snapshot, track_assets, quality_assessor)
        if race_semantics and assessment is not None and assessment.passed:
            geometry = _projection_geometry(track_assets)
            pit_lane_starts = _pit_lane_starter_pit_out_times(snapshot, replay_start_ms)
            startup_plan = _startup_seed_plan(
                snapshot.frames["results"].to_dicts(), drivers, geometry, replay_start_ms,
                excluded_driver_ids=frozenset(pit_lane_starts),
            )
            drivers, dynamic_orders = _derive_live_fields(
                snapshot, geometry, drivers, timeline, terminal_end_times, finish_end_times,
                startup_plan=startup_plan, pit_lane_starts=pit_lane_starts,
            )
        globals_ = _global_fields(
            snapshot, timeline, driver_ids, dynamic_orders,
            include_ranking=race_semantics,
        )
        # BrowserLapStart historically means “the displayed race leader started a
        # lap”.  Non-race sessions have no such leader; lap/sector navigation is
        # supplied by the dedicated sidecar instead of inventing one here.
        lap_starts = (
            _leader_lap_starts(timeline, drivers, globals_.leaderboard_order)
            if race_semantics else ()
        )
        events = _events(snapshot)
        chunks = build_browser_chunks(
            drivers,
            globals_,
            events,
            start_ms=replay_start_ms,
            end_ms=timeline[-1] + 1,
            chunk_duration_ms=chunk_duration_ms,
            overlap_ms=overlap_ms,
        )
        timeline_summary = (
            build_timeline_summary(snapshot, replay_start_ms, timeline[-1] + 1)
            if race_semantics else None
        )
        lap_sector_sidecar = build_lap_sector_sidecar(snapshot)
        parsed_penalty_sidecar = build_penalty_sidecar(snapshot)
        penalty_sidecar = (
            parsed_penalty_sidecar if parsed_penalty_sidecar.penalty_issuances else None
        )
        # Causal lap status is derived once from canonical final state; publication
        # reuses this immutable value instead of reparsing the source snapshot.
        qualifying_lap_status_sidecar = (
            build_qualifying_lap_status_sidecar(snapshot)
            if (
                session_mode in _QUALIFYING_SESSION_MODES
                and has_qualifying_lap_status_messages(
                    snapshot.frames["race_control_messages"]
                )
            ) else None
        )
        stint_summary = build_stint_summary(snapshot)
        if race_semantics and assessment is not None:
            observations = extract_eligible_pit_loss_observations(
                stint_summary=stint_summary,
                drivers=drivers,
                leaderboard_order=globals_.leaderboard_order,
                track_status_code=globals_.track_status_code,
                quality_gate_passed=assessment.passed,
            )
            pit_loss_model = build_pit_loss_timeline(
                replay_start_ms, observations, fixture_id=fixture_id,
            )
        manifest = BrowserManifest(
            fixture_id,
            f"{session['event_name']} {session['session_name']}",
            _driver_metadata(snapshot),
            lap_starts,
            stint_summary=None,
            pit_loss_model=None,
            penalty_sidecar=None,
            season_metadata=season_metadata,
            telemetry_capabilities=telemetry_capabilities,
        )
    except ValueError as error:
        raise BrowserDeliveryBuildError(str(error)) from error
    return BrowserDeliveryBuild(
        snapshot, manifest, track_assets, chunks, assessment, timeline_summary, lap_sector_sidecar,
        stint_summary, pit_loss_model, penalty_sidecar, qualifying_lap_status_sidecar,
    )


def _session_mode(session: Mapping[str, object]) -> SessionMode:
    """Normalize the canonical mode, retaining legacy direct race snapshots."""
    value = session.get("session_mode")
    if value is None:
        return "race"
    return normalize_session_mode(value)


def _session_start_time_ms(snapshot: CanonicalGenerationSnapshot, mode: SessionMode) -> int:
    """Choose a mode-appropriate replay boundary without fabricating Lap 1."""
    if mode in _RACE_SESSION_MODES:
        return _race_start_time_ms(snapshot)
    primary_columns = (
        ("car_telemetry", "session_time_ms"),
        ("position_telemetry", "session_time_ms"),
        ("laps", "lap_start_time_ms"),
        ("laps", "pit_in_time_ms"),
        ("laps", "pit_out_time_ms"),
    )
    values = _timestamp_values(snapshot, primary_columns)
    if values:
        return min(values)
    fallback_columns = (
        ("weather", "session_time_ms"),
        ("track_status_intervals", "start_time_ms"),
        ("race_control_messages", "session_time_ms"),
    )
    values = _timestamp_values(snapshot, fallback_columns)
    if not values:
        raise ValueError("a non-race browser delivery requires usable telemetry or timing data")
    return min(values)


def _timestamp_values(
    snapshot: CanonicalGenerationSnapshot,
    columns: Sequence[tuple[str, str]],
) -> tuple[int, ...]:
    return tuple(
        cast(int, time_ms)
        for table, column in columns
        for time_ms in snapshot.frames[table].get_column(column).drop_nulls().to_list()
        if type(time_ms) is int and time_ms >= 0
    )


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


def _telemetry_metadata(session: Mapping[str, object]) -> tuple[Mapping[str, object], Mapping[str, object]]:
    """Describe season-specific telemetry without synthesizing unavailable channels."""
    year = session.get("year")
    if type(year) is not int or not 1 <= year <= 9999:
        raise ValueError("session metadata year must be an integer from 1 to 9999")
    drs_status = "available" if year < 2026 else "not-published"
    return (
        {"year": year},
        {
            "drs": drs_status,
            "overtakeMode": "not-published",
            "activeAero": "not-published",
            "ersReplacement": "not-published",
        },
    )


def _delivery_timeline(snapshot: CanonicalGenerationSnapshot, race_start_ms: int) -> tuple[int, ...]:
    """Return the sorted unique canonical timestamp union at the replay boundary."""
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


def build_timeline_summary(
    snapshot: CanonicalGenerationSnapshot, replay_start_ms: int, replay_end_ms: int,
) -> BrowserTimelineSummary:
    """Build the deterministic, chunk-independent browser timeline summary."""
    _validate_summary_bounds(replay_start_ms, replay_end_ms)
    intervals = _timeline_status_intervals(snapshot, replay_start_ms, replay_end_ms)
    markers = _timeline_dnf_markers(snapshot, replay_start_ms, replay_end_ms)
    fixture_id = cast(str, snapshot.frames["session_metadata"].row(0, named=True)["session_id"])
    return BrowserTimelineSummary(fixture_id, replay_start_ms, replay_end_ms, intervals, markers)


def _timeline_status_intervals(
    snapshot: CanonicalGenerationSnapshot, replay_start_ms: int, replay_end_ms: int,
) -> tuple[BrowserTimelineInterval, ...]:
    clipped = []
    for row in snapshot.frames["track_status_intervals"].to_dicts():
        kind = _timeline_status_kind(row["status"])
        if kind is None:
            continue
        start_ms = row["start_time_ms"]
        end_ms = replay_end_ms if row["end_time_ms"] is None else row["end_time_ms"]
        if type(start_ms) is not int or type(end_ms) is not int:
            continue
        start_ms = max(replay_start_ms, start_ms)
        end_ms = min(replay_end_ms, end_ms)
        if start_ms < end_ms:
            clipped.append(BrowserTimelineInterval(kind, start_ms, end_ms))
    ordered = sorted(clipped, key=lambda interval: (interval.start_ms, interval.end_ms, interval.kind))
    merged: list[BrowserTimelineInterval] = []
    for interval in ordered:
        if merged and merged[-1].kind == interval.kind and interval.start_ms <= merged[-1].end_ms:
            previous = merged[-1]
            merged[-1] = BrowserTimelineInterval(
                previous.kind, previous.start_ms, max(previous.end_ms, interval.end_ms),
            )
        else:
            merged.append(interval)
    return tuple(merged)


def _timeline_status_kind(value: object) -> TimelineSummaryKind | None:
    if type(value) is int:
        code = value
    elif isinstance(value, str) and value.strip().isdigit():
        code = int(value.strip())
    else:
        return None
    kind = {2: "yellow", 4: "sc", 5: "red", 6: "vsc", 7: "vsc"}.get(code)
    return cast(TimelineSummaryKind | None, kind)


def _timeline_dnf_markers(
    snapshot: CanonicalGenerationSnapshot, replay_start_ms: int, replay_end_ms: int,
) -> tuple[BrowserDnfMarker, ...]:
    terminal_end_times = _terminal_end_times(snapshot, replay_start_ms)
    markers = []
    for row in snapshot.frames["results"].to_dicts():
        if _terminal_mode(row["status"]) is None:
            continue
        driver_id = row["driver_id"]
        terminal_time = terminal_end_times.get(driver_id, replay_start_ms)
        marker_time = min(replay_end_ms - 1, max(replay_start_ms, terminal_time))
        markers.append(BrowserDnfMarker(driver_id, marker_time))
    return tuple(sorted(markers, key=lambda marker: (marker.time_ms, marker.driver_id)))


def _validate_summary_bounds(replay_start_ms: int, replay_end_ms: int) -> None:
    if any(type(value) is not int or value < 0 for value in (replay_start_ms, replay_end_ms)):
        raise ValueError("timeline summary bounds must be non-negative integer milliseconds")
    if replay_start_ms >= replay_end_ms:
        raise ValueError("timeline summary bounds must be a non-empty interval")


def _global_fields(
    snapshot,
    timeline,
    driver_ids,
    dynamic_orders: tuple[tuple[str, ...] | None, ...] | None = None,
    *,
    include_ranking: bool = True,
) -> BrowserGlobalFields:
    results = snapshot.frames["results"].to_dicts()
    statuses = snapshot.frames["track_status_intervals"].to_dicts()
    weather = snapshot.frames["weather"].to_dicts()
    if not include_ranking:
        leaderboard_order = (None,) * len(timeline)
    else:
        ranked = sorted(driver_ids, key=lambda driver_id: (_result_rank(results, driver_id), driver_id))
        leaderboard_order = (tuple(ranked),) * len(timeline) if dynamic_orders is None else dynamic_orders
    return BrowserGlobalFields(
        timeline,
        leaderboard_order,
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


def _startup_seed_plan(
    results: Sequence[Mapping[str, object]], drivers: Mapping[str, BrowserDriverFields],
    geometry: ProjectionGeometry, race_start_ms: int, *,
    excluded_driver_ids: frozenset[str] = frozenset(),
) -> _StartupSeedPlan | None:
    """Seed a compact field against a ranking cut half a circuit from the grid."""
    candidates = _eligible_startup_candidates(results, drivers, excluded_driver_ids)
    if not candidates:
        return None
    observation = _earliest_startup_projection(candidates, geometry, race_start_ms)
    if observation is None:
        raise ValueError(
            "dynamic ranking rejected: synchronized startup projections are unreliable"
        )
    length = geometry.circuit_length_meters
    distances = tuple(
        ranking_distance(projection.track_distance_meters, length)
        for _, _, projection in observation.candidates
    )
    if len(set(distances)) != len(distances):
        raise ValueError("dynamic ranking rejected: startup projections contain duplicate positions")
    if max(distances) - min(distances) > length * _MAX_SHIFTED_STARTUP_SPAN_FRACTION:
        raise ValueError(
            "dynamic ranking rejected: startup projections are not a compact shifted grid"
        )

    seeds = {}
    for candidate, lap_number, projection in observation.candidates:
        update = seed_progress(
            session_time_ms=observation.timestamp_ms,
            lap_number=lap_number,
            circuit_length_meters=length,
            projection=projection,
            cut_crossing_count=0,
        )
        seeds[candidate.driver_id] = _StartupSeed(update.state, update)
    return _StartupSeedPlan(observation.timestamp_ms, MappingProxyType(seeds))


def _eligible_startup_candidates(
    results: Sequence[Mapping[str, object]], drivers: Mapping[str, BrowserDriverFields],
    excluded_driver_ids: frozenset[str] = frozenset(),
) -> tuple[_StartupCandidate, ...]:
    candidates = []
    seen_drivers: set[str] = set()
    seen_grid_positions: set[int] = set()
    for result in results:
        driver_id = result.get("driver_id")
        grid_position = result.get("grid_position")
        if (
            not isinstance(driver_id, str) or not driver_id
            or driver_id in excluded_driver_ids
            or type(grid_position) is not int or grid_position <= 0
            or _normalized_status(result.get("status")) in _NON_STARTER_RESULT_STATUSES
        ):
            continue
        fields = drivers.get(driver_id)
        if fields is None:
            continue
        if driver_id in seen_drivers or grid_position in seen_grid_positions:
            raise ValueError("dynamic ranking rejected: startup grid positions are not unique")
        seen_drivers.add(driver_id)
        seen_grid_positions.add(grid_position)
        candidates.append(_StartupCandidate(grid_position, driver_id, fields))
    return tuple(sorted(candidates, key=lambda candidate: (candidate.grid_position, candidate.driver_id)))


def _pit_lane_starter_pit_out_times(
    snapshot: CanonicalGenerationSnapshot, race_start_ms: int,
) -> Mapping[str, int]:
    return MappingProxyType({
        cast(str, row["driver_id"]): cast(int, row["pit_out_time_ms"])
        for row in snapshot.frames["laps"].to_dicts()
        if row.get("lap_number") == 1
        and isinstance(row.get("driver_id"), str)
        and type(row.get("pit_out_time_ms")) is int
        and cast(int, row["pit_out_time_ms"]) > race_start_ms
    })


def _earliest_startup_projection(
    candidates: Sequence[_StartupCandidate], geometry: ProjectionGeometry, race_start_ms: int,
) -> _StartupProjection | None:
    if not candidates:
        return None
    shared_timestamps = set(candidates[0].fields.time_ms)
    for candidate in candidates[1:]:
        shared_timestamps.intersection_update(candidate.fields.time_ms)
    for time_ms in sorted(shared_timestamps):
        if time_ms < race_start_ms:
            continue
        if time_ms - race_start_ms > _STARTUP_PROJECTION_WINDOW_MS:
            break
        observations = []
        for candidate in candidates:
            index = candidate.fields.time_ms.index(time_ms)
            if _normalized_status(candidate.fields.status[index]) in _NON_STARTER_RESULT_STATUSES:
                break
            lap_number = candidate.fields.lap[index]
            if type(lap_number) is not int or lap_number < 1:
                break
            projection = project_meters(candidate.fields.x[index], candidate.fields.y[index], geometry)
            if projection is None or projection.is_ambiguous:
                break
            observations.append((candidate, lap_number, projection))
        if len(observations) == len(candidates):
            return _StartupProjection(time_ms, tuple(observations))
    return None


def _derive_live_fields(
    snapshot, geometry, drivers, timeline, terminal_end_times, finish_end_times=None, *,
    startup_plan: _StartupSeedPlan | None = None,
    pit_lane_starts: Mapping[str, int] | None = None,
):
    finish_end_times = {} if finish_end_times is None else finish_end_times
    pit_lane_starts = {} if pit_lane_starts is None else pit_lane_starts
    result_statuses = {row["driver_id"]: row["status"] for row in snapshot.frames["results"].to_dicts()}
    states = {driver_id: ProgressState() for driver_id in drivers}
    distances = {driver_id: [] for driver_id in drivers}
    gaps = {driver_id: [] for driver_id in drivers}
    positions = {driver_id: [] for driver_id in drivers}
    ranking_frames = []
    startup_index = None if startup_plan is None else timeline.index(startup_plan.timestamp_ms)
    for index, time_ms in enumerate(timeline):
        inputs = []
        for driver_id, fields in drivers.items():
            lap = fields.lap[index]
            mode = _progress_mode(
                fields.is_in_pit_lane[index], result_statuses.get(driver_id),
                terminal_end_times.get(driver_id), time_ms,
                finish_end_time=finish_end_times.get(driver_id),
            )
            pit_out_time = pit_lane_starts.get(driver_id)
            awaiting_pit_lane_start = (
                pit_out_time is not None and states[driver_id].last_valid_progress_meters is None
            )
            if awaiting_pit_lane_start and time_ms < pit_out_time:
                mode = ProgressMode.PIT
            effective_lap = lap
            if effective_lap is None and mode in (ProgressMode.FINISHED, ProgressMode.RETIRED, ProgressMode.OUT):
                effective_lap = states[driver_id].last_lap_number
            seed = None if startup_plan is None else startup_plan.seeds.get(driver_id)
            if awaiting_pit_lane_start and time_ms >= cast(int, pit_out_time) and mode is ProgressMode.ACTIVE:
                projection = project_meters(fields.x[index], fields.y[index], geometry)
                if projection is None:
                    update = None
                elif effective_lap is None:
                    update = None
                else:
                    distance = projection.track_distance_meters
                    update = seed_progress(
                        session_time_ms=time_ms,
                        lap_number=effective_lap,
                        circuit_length_meters=geometry.circuit_length_meters,
                        projection=projection,
                        cut_crossing_count=_pit_starter_cut_crossing_count(
                            effective_lap, distance, geometry.circuit_length_meters,
                            states, driver_id,
                        ),
                        mode=mode,
                    )
                    states[driver_id] = update.state
            elif awaiting_pit_lane_start:
                update = None
            elif startup_index is not None and index < startup_index:
                update = None
            elif startup_index == index and seed is not None:
                if mode is not ProgressMode.ACTIVE or effective_lap is None:
                    raise ValueError("dynamic ranking rejected: startup seed is not an active lap observation")
                update = seed.update
                states[driver_id] = seed.state
            elif effective_lap is None:
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


def _pit_starter_cut_crossing_count(
    lap: int, distance: float, length: float,
    states: Mapping[str, ProgressState], driver_id: str,
) -> int:
    """Attach a lap-one pit starter to the closest already-established field epoch."""
    candidates = (lap - 1, lap)
    references = tuple(
        state.last_valid_progress_meters
        for candidate_id, state in states.items()
        if candidate_id != driver_id and state.last_valid_progress_meters is not None
    )
    if not references:
        return lap - 1 + int(distance >= length / 2.0)
    reference = median(references)
    shifted = ranking_distance(distance, length)
    return min(candidates, key=lambda count: (abs(count * length + shifted - reference), count))


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


def _finished_end_times(snapshot, race_start_ms: int) -> dict[str, int]:
    """Return finish boundaries only when final results and completed laps agree."""
    laps_by_driver: dict[str, tuple[dict[str, object], ...]] = {}
    for row in snapshot.frames["laps"].to_dicts():
        laps_by_driver.setdefault(row["driver_id"], ())
        laps_by_driver[row["driver_id"]] += (row,)

    values: dict[str, int] = {}
    for result in snapshot.frames["results"].to_dicts():
        if not _is_completed_result(result["status"]):
            continue
        laps_completed = result["laps_completed"]
        if type(laps_completed) is not int or laps_completed < 1:
            continue
        driver_laps = laps_by_driver.get(result["driver_id"], ())
        completed_lap_numbers = {
            row["lap_number"] for row in driver_laps if type(row["lap_number"]) is int
        }
        if max(completed_lap_numbers, default=None) != laps_completed:
            continue
        finish_times = tuple(
            row["lap_end_time_ms"]
            for row in driver_laps
            if row["lap_number"] == laps_completed
            and type(row["lap_end_time_ms"]) is int
            and row["lap_end_time_ms"] >= race_start_ms
        )
        if finish_times:
            values[result["driver_id"]] = max(finish_times)
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


def _progress_mode(in_pit, result_status, terminal_end_time, time_ms, *, finish_end_time=None) -> ProgressMode:
    terminal_mode = _terminal_mode(result_status)
    if terminal_mode is not None and terminal_end_time is not None and time_ms > terminal_end_time:
        return terminal_mode
    if _is_completed_result(result_status) and finish_end_time is not None and time_ms >= finish_end_time:
        return ProgressMode.FINISHED
    return ProgressMode.PIT if in_pit is True else ProgressMode.ACTIVE


def _terminal_mode(result_status) -> ProgressMode | None:
    normalized = _normalized_status(result_status)
    if normalized in {"disqualified", "excluded", "didnotstart", "dns"}:
        return ProgressMode.OUT
    return ProgressMode.RETIRED if normalized in _KNOWN_NON_COMPLETION_STATUSES else None


def _normalized_status(value) -> str:
    return "" if not isinstance(value, str) else "".join(character for character in value.lower() if character.isalnum())


_KNOWN_COMPLETION_STATUSES = frozenset({"finished", "lapped"})


def _is_completed_result(value: object) -> bool:
    return _normalized_status(value) in _KNOWN_COMPLETION_STATUSES


_KNOWN_NON_COMPLETION_STATUSES = frozenset({
    "retired", "accident", "collision", "engine", "gearbox", "transmission", "clutch",
    "hydraulics", "electrical", "brakes", "suspension", "damage", "mechanical", "fuel",
    "tyre", "wheel", "overheating", "withdrawn", "didnotfinish", "dnf", "notclassified",
})


def _with_terminal_status(fields, terminal_end_time):
    statuses = tuple(
        "OUT" if terminal_end_time is not None and time_ms > terminal_end_time else status
        for time_ms, status in zip(fields.time_ms, fields.status, strict=True)
    )
    return replace(fields, status=statuses)


def _with_finished_state(fields, finish_end_time: int | None):
    if finish_end_time is None:
        return fields
    return replace(
        fields,
        is_finished=tuple(time_ms >= finish_end_time for time_ms in fields.time_ms),
    )


def _with_derived_fields(fields, distances, gaps, positions):
    return replace(
        fields,
        track_distance_meters=tuple(distances),
        gap_to_leader_ms=tuple(gaps),
        position=tuple(positions),
    )


__all__ = [
    "BrowserDeliveryBuild", "BrowserDeliveryBuildError", "ProjectionQualityAssessor",
    "build_browser_delivery", "build_timeline_summary",
]
