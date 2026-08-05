"""Pure, deterministic derivation of the mode-neutral lap/sector sidecar.

This module also derives the optional qualifying-safe timeline artifact
(``qualifying-timeline``) from canonical track-status and race-control
evidence.  Both derivations are additive and never mutate canonical Parquet.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import cast

import polars as pl

from f1_replay_pipeline.adapters.fastf1.messages_results import (
    parse_qualifying_incident_markers,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    BrowserDriverLapSector,
    BrowserLapSectorSidecar,
    BrowserQualifyingIncidentMarker,
    BrowserQualifyingPhaseBoundary,
    BrowserQualifyingTimeline,
    BrowserQualifyingTimelineInterval,
    CanonicalGenerationSnapshot,
    LapKind,
    QualifyingTimelineIntervalKind,
)
from f1_replay_pipeline.domain.canonical_contract import QUALIFYING_PHASES, QualifyingPhase
from f1_replay_pipeline.domain.normalizers import NormalizationError
from f1_replay_pipeline.domain.session_modes import normalize_session_mode

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
# Canonical evidence consumed by the qualifying flying-lap policy (ADR-003).
# Canonical Parquet already carries every column; the sidecar boundary drops
# them today, so the builder re-reads them purely for classification.
_LAP_EVIDENCE_COLUMNS = (
    "pit_in_time_ms",
    "pit_out_time_ms",
    "is_accurate",
    "deleted",
    "track_status",
)
_SECTOR_DURATION_COLUMNS = (
    "sector_1_duration_ms",
    "sector_2_duration_ms",
    "sector_3_duration_ms",
)
_SECTOR_SESSION_COLUMNS = (
    "sector_1_session_time_ms",
    "sector_2_session_time_ms",
    "sector_3_session_time_ms",
)
# FastF1 marks a lap accurate only for green/yellow track status.
_GREEN_YELLOW_TRACK_STATUS = frozenset({"1", "2", "12", "21"})
# FastF1's own accuracy check tolerates a 3 ms sector-sum delta.
_SECTOR_SUM_TOLERANCE_MS = 3
# FastF1's QUICKLAP_THRESHOLD = 1.07, applied as exact integer arithmetic.
_QUICKLAP_NUMERATOR = 107
_QUICKLAP_DENOMINATOR = 100
_QUALIFYING_MODES = frozenset({"qualifying", "sprint-qualifying", "sprint-shootout"})
# Qualifying-safe timeline kinds are restricted to yellow/red in this revision;
# SC/VSC codes are intentionally not exposed as intervals.
_QUALIFYING_STATUS_KINDS = {2: "yellow", 5: "red"}


def build_lap_sector_sidecar(snapshot: CanonicalGenerationSnapshot) -> BrowserLapSectorSidecar:
    """Return a compact columnar BrowserLapSectorSidecar from canonical laps.

    The function is pure, immutable, and deterministic: the same canonical snapshot
    always produces the same sidecar for Race, Practice, or Qualifying. Rows with a null ``lap_start_time_ms`` or
    ``lap_end_time_ms`` are dropped because the sidecar contains only completed
    laps; null canonical sector/session fields are propagated as ``None``; no
    values are invented.

    For qualifying-like sessions the sidecar also derives the optional aligned
    ``lap_kind`` column following ADR-003: pit-out => ``outlap``, pit-in =>
    ``inlap``, a row with both pit signals or any missing/contradictory timing
    evidence => ``unknown``, and a complete accurate/non-deleted/pit-free/
    sector-consistent lap within the per-Q-phase 107% quicklap gate => ``flying``.
    Canonical Parquet and the existing phase-boundary assignment are unchanged.
    """
    fixture_id = cast(str, snapshot.frames["session_metadata"].row(0, named=True)["session_id"])
    driver_ids = tuple(sorted(snapshot.frames["drivers"].get_column("driver_id").to_list()))
    laps = snapshot.frames["laps"]
    completed_laps = laps.filter(
        laps["lap_start_time_ms"].is_not_null() & laps["lap_end_time_ms"].is_not_null()
    )
    driver_groups = _group_laps_by_driver(completed_laps)
    phase_boundaries = _phase_boundaries(driver_groups)
    derive_lap_kind = _session_mode(snapshot) in _QUALIFYING_MODES
    phase_bests = _phase_quicklap_bests(driver_groups) if derive_lap_kind else {}
    return BrowserLapSectorSidecar(
        fixture_id,
        {
            driver_id: _driver_lap_sector(
                driver_groups.get(driver_id, _EMPTY_LAP_GROUP),
                total_rows=0 if driver_id not in driver_groups else len(driver_groups[driver_id]["lap_number"]),
                derive_lap_kind=derive_lap_kind,
                phase_bests=phase_bests,
            )
            for driver_id in driver_ids
        },
        qualifying_phase_boundaries=phase_boundaries,
    )


def build_qualifying_timeline(
    snapshot: CanonicalGenerationSnapshot,
    replay_start_ms: int,
    replay_end_ms: int,
) -> BrowserQualifyingTimeline | None:
    """Derive the optional qualifying-safe timeline artifact, or ``None``.

    The artifact is omitted for non-qualifying-like sessions and whenever there
    is no actionable evidence (no yellow/red interval and no incident marker)
    within the replay window.  Intervals come from canonical
    ``track_status_intervals`` rows restricted to yellow (2) and red (5) status
    codes, clipped to the half-open ``[replay_start_ms, replay_end_ms)`` window
    and merged when adjacent and same-kind.  Incident markers come from
    canonical ``race_control_messages`` rows through the fail-closed CarEvent
    terminal-form parser; they carry canonical driver identity, causal time, and
    raw evidence and never fabricate race ``OUT``/DNF semantics.  When the
    artifact is absent, consumers render no intervals and hide no markers.
    """
    if _session_mode(snapshot) not in _QUALIFYING_MODES:
        return None
    fixture_id = cast(str, snapshot.frames["session_metadata"].row(0, named=True)["session_id"])
    _validate_qualifying_timeline_bounds(replay_start_ms, replay_end_ms)
    intervals = _qualifying_timeline_intervals(snapshot, replay_start_ms, replay_end_ms)
    markers = _qualifying_incident_markers(snapshot, replay_start_ms, replay_end_ms)
    if not intervals and not markers:
        return None
    return BrowserQualifyingTimeline(fixture_id, replay_start_ms, replay_end_ms, intervals, markers)


_EMPTY_LAP_GROUP: Mapping[str, tuple[object, ...]] = MappingProxyType({})


def _phase_boundaries(
    groups: Mapping[str, Mapping[str, tuple[object, ...]]],
) -> tuple[BrowserQualifyingPhaseBoundary, ...]:
    """Return first completed-lap starts for the canonical phases, if present."""
    starts: dict[QualifyingPhase, int] = {}
    for group in groups.values():
        lap_starts = group["lap_start_time_ms"]
        phases = group.get("qualifying_phase", (None,) * len(lap_starts))
        for phase, start_ms in zip(phases, lap_starts, strict=True):
            if phase is None:
                continue
            if type(start_ms) is not int:
                raise TypeError("qualifying phase boundaries require integer lap starts")
            qualifying_phase = cast(QualifyingPhase, phase)
            starts[qualifying_phase] = min(starts.get(qualifying_phase, start_ms), start_ms)
    ordered = tuple(
        BrowserQualifyingPhaseBoundary(phase, starts[phase])
        for phase in QUALIFYING_PHASES
        if phase in starts
    )
    return ordered


def _group_laps_by_driver(laps: pl.DataFrame) -> dict[str, dict[str, tuple[object, ...]]]:
    """Return canonical lap columns per driver, sorted by lap_number ascending."""
    groups: dict[str, dict[str, tuple[object, ...]]] = {}
    if laps.is_empty():
        return groups
    available = set(laps.columns)
    all_columns = _LAP_COLUMNS + _LAP_EVIDENCE_COLUMNS + (
        ("qualifying_phase",) if "qualifying_phase" in available else ()
    )
    selected_columns = tuple(column for column in all_columns if column in available)
    for driver_id in laps.get_column("driver_id").unique(maintain_order=True):
        driver_rows = (
            laps.filter(laps["driver_id"] == driver_id)
            .sort("lap_number", descending=False)
            .select(selected_columns)
        )
        groups[driver_id] = {
            column: tuple(driver_rows.get_column(column).to_list())
            for column in selected_columns
        }
        for column in all_columns:
            if column not in groups[driver_id]:
                groups[driver_id][column] = (None,) * len(driver_rows)
    return groups


def _driver_lap_sector(
    group: Mapping[str, tuple[object, ...]],
    *,
    total_rows: int,
    derive_lap_kind: bool = False,
    phase_bests: Mapping[QualifyingPhase, int] | None = None,
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
            qualifying_phase=(),
            lap_kind=(),
        )
    qualifying_phase = group.get("qualifying_phase", (None,) * total_rows)
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
        qualifying_phase=cast(tuple[QualifyingPhase | None, ...], qualifying_phase),
        lap_kind=(_lap_kinds(group, phase_bests) if derive_lap_kind else ()),
    )


def _session_mode(snapshot: CanonicalGenerationSnapshot) -> str | None:
    """Return the normalized canonical session mode, or None when unavailable."""
    try:
        session = snapshot.frames["session_metadata"].row(0, named=True)
    except (KeyError, IndexError, TypeError):
        return None
    value = session.get("session_mode")
    if value is None:
        return None
    try:
        return normalize_session_mode(value)
    except NormalizationError:
        return None


def _phase_quicklap_bests(
    groups: Mapping[str, Mapping[str, tuple[object, ...]]],
) -> dict[QualifyingPhase, int]:
    """Return the fastest pit-free accurate timed lap per Q phase.

    Mirrors FastF1's ``_calculate_qualifying_results`` aggregate pool:
    ``pick_accurate()`` (which already excludes pit laps) + non-null ``LapTime``
    + ``~Deleted``, applied per Q phase across all drivers.  The per-phase best
    is the reference for the 107% quicklap gate.
    """
    bests: dict[QualifyingPhase, int] = {}
    for group in groups.values():
        phases = group.get("qualifying_phase", ())
        for index, phase in enumerate(phases):
            if phase is None:
                continue
            duration = _aggregate_quicklap_duration(group, index)
            if duration is None:
                continue
            qualifying_phase = cast(QualifyingPhase, phase)
            bests[qualifying_phase] = min(bests.get(qualifying_phase, duration), duration)
    return bests


def _aggregate_quicklap_duration(
    group: Mapping[str, tuple[object, ...]], index: int,
) -> int | None:
    """Return a lap's duration when it belongs to the cross-driver quicklap pool."""
    if group["pit_in_time_ms"][index] is not None or group["pit_out_time_ms"][index] is not None:
        return None
    if group["is_accurate"][index] is not True:
        return None
    if group["deleted"][index] is not False:
        return None
    duration = group["lap_duration_ms"][index]
    return duration if type(duration) is int else None


def _lap_kinds(
    group: Mapping[str, tuple[object, ...]],
    phase_bests: Mapping[QualifyingPhase, int] | None,
) -> tuple[LapKind, ...]:
    """Classify every completed lap in one driver group."""
    return tuple(
        cast(LapKind, _classify_lap(group, index, phase_bests))
        for index in range(len(group["lap_number"]))
    )


def _classify_lap(
    group: Mapping[str, tuple[object, ...]],
    index: int,
    phase_bests: Mapping[QualifyingPhase, int] | None,
) -> LapKind:
    """Classify one completed lap under the ADR-003 deterministic policy.

    Pit evidence is authoritative and checked first; a row with both pit
    signals is ``unknown`` because the policy refuses to guess between outlap
    and inlap.  A pit-free lap that lacks complete, consistent timing evidence
    is ``unknown`` (fail closed), and a complete candidate only becomes
    ``flying`` when it is within the per-Q-phase 107% quicklap gate.  ``unknown``
    is never promoted to ``flying``.
    """
    pit_in = group["pit_in_time_ms"][index]
    pit_out = group["pit_out_time_ms"][index]
    if pit_in is not None and pit_out is not None:
        return "unknown"
    if pit_out is not None:
        return "outlap"
    if pit_in is not None:
        return "inlap"
    if not _is_flying_candidate(group, index):
        return "unknown"
    phase = group["qualifying_phase"][index]
    if phase is None:
        return "unknown"
    phase_best = None if phase_bests is None else phase_bests.get(cast(QualifyingPhase, phase))
    if phase_best is None:
        return "unknown"
    duration = group["lap_duration_ms"][index]
    if duration is None:
        return "unknown"
    duration_ms = cast(int, duration)
    if duration_ms * _QUICKLAP_DENOMINATOR <= phase_best * _QUICKLAP_NUMERATOR:
        return "flying"
    return "unknown"


def _is_flying_candidate(
    group: Mapping[str, tuple[object, ...]], index: int,
) -> bool:
    """Return whether a pit-free lap carries complete, consistent timing evidence."""
    if group["is_accurate"][index] is not True:
        return False
    if group["deleted"][index] is not False:
        return False
    duration = group["lap_duration_ms"][index]
    if duration is None:
        return False
    status = group["track_status"][index]
    if status is None or status not in _GREEN_YELLOW_TRACK_STATUS:
        return False
    sector_durations = tuple(group[column][index] for column in _SECTOR_DURATION_COLUMNS)
    sector_times = tuple(group[column][index] for column in _SECTOR_SESSION_COLUMNS)
    if any(value is None for value in sector_durations) or any(value is None for value in sector_times):
        return False
    sector_sum = sum(cast(tuple[int, ...], sector_durations))
    return abs(sector_sum - cast(int, duration)) <= _SECTOR_SUM_TOLERANCE_MS


def _qualifying_timeline_intervals(
    snapshot: CanonicalGenerationSnapshot, replay_start_ms: int, replay_end_ms: int,
) -> tuple[BrowserQualifyingTimelineInterval, ...]:
    """Derive bounded, merged yellow/red intervals within the replay window."""
    clipped = []
    track_status_intervals = snapshot.frames.get("track_status_intervals")
    if track_status_intervals is None:
        return ()
    for row in track_status_intervals.to_dicts():
        kind = _qualifying_status_kind(row["status"])
        if kind is None:
            continue
        start_ms = row["start_time_ms"]
        end_ms = replay_end_ms if row["end_time_ms"] is None else row["end_time_ms"]
        if type(start_ms) is not int or type(end_ms) is not int:
            continue
        start_ms = max(replay_start_ms, start_ms)
        end_ms = min(replay_end_ms, end_ms)
        if start_ms < end_ms:
            clipped.append(BrowserQualifyingTimelineInterval(kind, start_ms, end_ms))
    ordered = sorted(clipped, key=lambda interval: (interval.start_ms, interval.end_ms, interval.kind))
    merged: list[BrowserQualifyingTimelineInterval] = []
    for interval in ordered:
        if merged and merged[-1].kind == interval.kind and interval.start_ms <= merged[-1].end_ms:
            previous = merged[-1]
            merged[-1] = BrowserQualifyingTimelineInterval(
                previous.kind, previous.start_ms, max(previous.end_ms, interval.end_ms),
            )
        else:
            merged.append(interval)
    return tuple(merged)


def _qualifying_status_kind(value: object) -> QualifyingTimelineIntervalKind | None:
    """Map a canonical track-status code to a qualifying-safe interval kind."""
    if type(value) is int:
        code = value
    elif isinstance(value, str) and value.strip().isdigit():
        code = int(value.strip())
    else:
        return None
    return cast(QualifyingTimelineIntervalKind | None, _QUALIFYING_STATUS_KINDS.get(code))


def _qualifying_incident_markers(
    snapshot: CanonicalGenerationSnapshot, replay_start_ms: int, replay_end_ms: int,
) -> tuple[BrowserQualifyingIncidentMarker, ...]:
    """Derive qualifying incident markers within the replay window."""
    try:
        messages = snapshot.frames["race_control_messages"]
        drivers = snapshot.frames["drivers"]
    except KeyError:
        return ()
    markers = []
    for marker in parse_qualifying_incident_markers(messages, drivers):
        if replay_start_ms <= marker.time_ms < replay_end_ms:
            markers.append(BrowserQualifyingIncidentMarker(
                driver_id=marker.driver_id,
                time_ms=marker.time_ms,
                raw_message=marker.raw_message,
                lap_number=marker.lap_number,
            ))
    return tuple(sorted(markers, key=lambda marker: (marker.time_ms, marker.driver_id, marker.raw_message)))


def _validate_qualifying_timeline_bounds(replay_start_ms: int, replay_end_ms: int) -> None:
    if any(type(value) is not int or value < 0 for value in (replay_start_ms, replay_end_ms)):
        raise ValueError("qualifying timeline bounds must be non-negative integer milliseconds")
    if replay_start_ms >= replay_end_ms:
        raise ValueError("qualifying timeline bounds must be a non-empty interval")


__all__ = ["build_lap_sector_sidecar", "build_qualifying_timeline"]
