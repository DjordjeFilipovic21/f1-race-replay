"""Duck-typed, in-memory normalization of FastF1 timing laps and stints."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import groupby
import math
import re
from typing import cast

import polars as pl

from ...domain.canonical_schema import LAPS_SCHEMA_V2, STINTS_SCHEMA_V2
from ...domain.normalizers import NormalizationError, normalize_nullable_scalar, normalize_session_time_ms
from ...domain.session_modes import normalize_session_mode
from ...domain.validators import validate_canonical_table


_INT16_MAX = 32_767
_CANONICAL_DRIVER_ID = re.compile(r"(?:[A-Z]{3}|D(?:0|[1-9][0-9]*))\Z")
_QUALIFYING_MODES = frozenset({"qualifying", "sprint-qualifying", "sprint-shootout"})


@dataclass(frozen=True)
class _SourceLap:
    position: int
    source_index: object | None
    record: Mapping[str, object]
    composite_identity: tuple[object, ...]


def adapt_laps(
    session: object,
    session_id: str,
    driver_ids: Mapping[str, str],
    session_mode: str | None = None,
) -> pl.DataFrame:
    """Adapt ``session.laps`` without consulting source ``Driver`` labels.

    ``driver_ids`` is the already-normalized, session-scoped mapping from the
    original FastF1 driver-number key to its canonical driver identifier.
    """
    _require_session_id(session_id)
    source_laps = _source_laps(session)
    phase_by_position = _qualifying_phase_by_position(session, source_laps, session_mode)
    rows = []
    for source_lap in source_laps:
        row = _lap_row(source_lap.record, session_id, driver_ids)
        row["qualifying_phase"] = phase_by_position.get(source_lap.position)
        rows.append(row)
    _reject_duplicate_laps(rows)
    frame = pl.DataFrame(sorted(rows, key=_lap_key), schema=LAPS_SCHEMA_V2, strict=True)
    validate_canonical_table("laps", frame, version="v2")
    return frame


def adapt_stints(
    session: object,
    session_id: str,
    driver_ids: Mapping[str, str],
    laps_frame: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Derive contiguous tyre-stint summaries solely from canonical timing laps.

    ``laps_frame`` lets an orchestrator derive stints from the exact already
    validated lap snapshot instead of reading mutable session data a second time.
    """
    laps = (laps_frame if laps_frame is not None else adapt_laps(session, session_id, driver_ids)).to_dicts()
    stints = [
        _stint_row(group)
        for _, driver_laps in groupby(laps, key=lambda row: (row["session_id"], row["driver_id"]))
        for _, group in _contiguous_stint_groups(list(driver_laps))
    ]
    frame = pl.DataFrame(sorted(stints, key=_stint_key), schema=STINTS_SCHEMA_V2, strict=True)
    validate_canonical_table("stints", frame, version="v2")
    return frame


normalize_laps = adapt_laps
normalize_stints = adapt_stints


def _records(session: object) -> Iterable[Mapping[str, object]]:
    return tuple(source_lap.record for source_lap in _source_laps(session))


def _source_laps(session: object) -> tuple[_SourceLap, ...]:
    try:
        laps = getattr(session, "laps")
    except AttributeError as error:
        raise NormalizationError("loaded session is missing required laps") from error
    if laps is None:
        return ()
    iterrows = getattr(laps, "iterrows", None)
    if callable(iterrows):
        source_laps: list[_SourceLap] = []
        rows = cast(Iterable[tuple[object, object]], iterrows())
        for position, (source_index, row) in enumerate(rows):
            converter = getattr(row, "to_dict", None)
            record = converter() if callable(converter) else row
            if not isinstance(record, Mapping):
                raise NormalizationError("lap records must be mappings")
            source_laps.append(_source_lap(position, source_index, record))
        return tuple(source_laps)
    return tuple(_source_lap(position, None, record) for position, record in enumerate(_records_from_table(laps)))


def _source_lap(position: int, source_index: object | None, record: Mapping[str, object]) -> _SourceLap:
    return _SourceLap(position, source_index, record, _lap_composite_identity(record))


def _records_from_table(laps: object) -> tuple[Mapping[str, object], ...]:
    to_dicts = getattr(laps, "to_dicts", None)
    if callable(to_dicts):
        return _mapping_records(to_dicts())
    to_dict = getattr(laps, "to_dict", None)
    if callable(to_dict):
        try:
            return _mapping_records(to_dict("records"))
        except TypeError:
            return _mapping_records(to_dict(orient="records"))
    if isinstance(laps, Iterable) and not isinstance(laps, (str, bytes, Mapping)):
        return _mapping_records(laps)
    raise NormalizationError("session laps must provide iterable mapping records")


def _qualifying_phase_by_position(
    session: object,
    source_laps: tuple[_SourceLap, ...],
    session_mode: str | None,
) -> dict[int, str]:
    mode = _session_mode(session, session_mode)
    if mode not in _QUALIFYING_MODES:
        return {}
    laps = getattr(session, "laps", None)
    splitter = getattr(laps, "split_qualifying_sessions", None)
    if not callable(splitter):
        raise NormalizationError(
            "qualifying phase assignment requires split_qualifying_sessions"
        )
    try:
        raw_partitions = splitter()
    except Exception as error:
        if type(error).__name__ in {"DataNotLoadedError", "NoLapDataError"}:
            raise NormalizationError(
                "qualifying phase assignment failed: loaded session status data is required"
            ) from error
        raise NormalizationError(
            "qualifying phase assignment failed while obtaining authoritative partitions"
        ) from error
    if not isinstance(raw_partitions, Iterable) or isinstance(raw_partitions, (str, bytes, Mapping)):
        raise NormalizationError("split_qualifying_sessions must return Q1, Q2, and Q3 partitions")
    partitions = tuple(raw_partitions)
    if len(partitions) != 3:
        raise NormalizationError("split_qualifying_sessions must return Q1, Q2, and Q3 partitions")

    source_by_index = _unique_source_index_map(source_laps)
    if len(source_by_index) != len(source_laps):
        raise NormalizationError(
            "qualifying phase assignment requires unique authoritative source lap indices"
        )
    source_positions = {source_lap.position for source_lap in source_laps}

    assignments: dict[int, str] = {}
    for phase_number, partition in enumerate(partitions, start=1):
        if partition is None:
            continue
        for partition_index, record in _partition_records(partition):
            position = _match_source_lap(partition_index, record, source_by_index)
            if position is None:
                raise NormalizationError(
                    "qualifying partition contains an unknown or mismatched source lap index"
                )
            if position in assignments:
                raise NormalizationError("qualifying partition contains a duplicate lap")
            assignments[position] = f"Q{phase_number}"
    if set(assignments) != source_positions:
        raise NormalizationError(
            "qualifying phase assignment is incomplete: every source lap must belong to exactly one partition"
        )
    return assignments


def _session_mode(session: object, explicit_mode: str | None) -> str | None:
    value = explicit_mode if explicit_mode is not None else getattr(session, "name", None)
    if value is None:
        return None
    try:
        return normalize_session_mode(value)
    except NormalizationError:
        return None


def _unique_source_index_map(source_laps: tuple[_SourceLap, ...]) -> dict[object, _SourceLap]:
    indexed: dict[object, _SourceLap] = {}
    for source_lap in source_laps:
        if source_lap.source_index is None:
            return {}
        try:
            if source_lap.source_index in indexed:
                return {}
            indexed[source_lap.source_index] = source_lap
        except TypeError:
            return {}
    return indexed


def _partition_records(partition: object) -> tuple[tuple[object | None, Mapping[str, object]], ...]:
    iterrows = getattr(partition, "iterrows", None)
    if callable(iterrows):
        records = []
        rows = cast(Iterable[tuple[object, object]], iterrows())
        for source_index, row in rows:
            converter = getattr(row, "to_dict", None)
            record = converter() if callable(converter) else row
            if not isinstance(record, Mapping):
                raise NormalizationError("qualifying partition records must be mappings")
            records.append((source_index, record))
        return tuple(records)
    return tuple((None, record) for record in _records_from_table(partition))


def _match_source_lap(
    partition_index: object | None,
    record: Mapping[str, object],
    source_by_index: Mapping[object, _SourceLap],
) -> int | None:
    if partition_index is None:
        return None
    try:
        source_lap = source_by_index[partition_index]
    except (KeyError, TypeError):
        return None
    if source_lap.composite_identity != _lap_composite_identity(record):
        return None
    return source_lap.position


def _lap_composite_identity(record: Mapping[str, object]) -> tuple[object, ...]:
    return (
        _identity_value(record.get("DriverNumber")),
        _identity_value(record.get("Time")),
        _identity_value(record.get("LapStartTime")),
        _identity_value(record.get("LapNumber")),
    )


def _identity_value(value: object | None) -> object | None:
    if _is_missing(value):
        return None
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


def _mapping_records(records: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(records, Iterable) or isinstance(records, (str, bytes, Mapping)):
        raise NormalizationError("lap records must be iterable mappings")
    materialized = tuple(records)
    if not all(isinstance(record, Mapping) for record in materialized):
        raise NormalizationError("lap records must be mappings")
    return tuple(cast(Mapping[str, object], record) for record in materialized)


def _lap_row(record: Mapping[str, object], session_id: str, driver_ids: Mapping[str, str]) -> dict[str, object | None]:
    source_key = _source_key(_required(record, "DriverNumber"))
    try:
        driver_id = driver_ids[source_key]
    except KeyError as error:
        raise NormalizationError(f"missing canonical driver ID for source key: {source_key}") from error
    if not isinstance(driver_id, str) or not _CANONICAL_DRIVER_ID.fullmatch(driver_id):
        raise NormalizationError(f"invalid canonical driver ID for source key: {source_key}")
    return {
        "session_id": session_id,
        "driver_id": driver_id,
        "lap_number": _required_int16(record, "LapNumber", "lap number"),
        "stint_number": _nullable_int16(record.get("Stint"), "stint number"),
        "lap_start_time_ms": _required_time(record, "LapStartTime", "lap start time"),
        "lap_end_time_ms": _nullable_time(record.get("Time"), "lap end time"),
        "lap_duration_ms": _nullable_time(record.get("LapTime"), "lap duration"),
        "pit_in_time_ms": _nullable_time(record.get("PitInTime"), "pit-in time"),
        "pit_out_time_ms": _nullable_time(record.get("PitOutTime"), "pit-out time"),
        "compound": _nullable_text(record.get("Compound"), "compound"),
        "tyre_life": _nullable_int16(record.get("TyreLife"), "tyre life"),
        "is_fresh_tyre": _nullable_bool(record.get("FreshTyre"), "fresh tyre"),
        "track_status": _nullable_text(record.get("TrackStatus"), "track status"),
        "is_accurate": _nullable_bool(record.get("IsAccurate"), "accuracy flag"),
        "deleted": _nullable_bool(record.get("Deleted"), "deleted flag"),
        "deleted_reason": _nullable_text(record.get("DeletedReason"), "deleted reason"),
        "sector_1_duration_ms": _nullable_time(record.get("Sector1Time"), "sector 1 duration"),
        "sector_2_duration_ms": _nullable_time(record.get("Sector2Time"), "sector 2 duration"),
        "sector_3_duration_ms": _nullable_time(record.get("Sector3Time"), "sector 3 duration"),
        "sector_1_session_time_ms": _nullable_time(record.get("Sector1SessionTime"), "sector 1 session time"),
        "sector_2_session_time_ms": _nullable_time(record.get("Sector2SessionTime"), "sector 2 session time"),
        "sector_3_session_time_ms": _nullable_time(record.get("Sector3SessionTime"), "sector 3 session time"),
    }


def _contiguous_stint_groups(laps: list[dict[str, object | None]]) -> Iterable[tuple[int, list[dict[str, object | None]]]]:
    seen: set[int] = set()
    for stint_number, group in groupby(laps, key=lambda row: row["stint_number"]):
        grouped_laps = list(group)
        if stint_number is None:
            continue
        assert isinstance(stint_number, int)
        if stint_number in seen:
            raise NormalizationError(f"stint number is not contiguous: {stint_number}")
        seen.add(stint_number)
        yield stint_number, grouped_laps


def _stint_row(laps: list[dict[str, object | None]]) -> dict[str, object | None]:
    first, last = laps[0], laps[-1]
    return {
        "session_id": first["session_id"], "driver_id": first["driver_id"],
        "stint_number": first["stint_number"], "start_lap_number": first["lap_number"],
        "end_lap_number": last["lap_number"], "start_time_ms": first["lap_start_time_ms"],
        "end_time_ms": last["lap_end_time_ms"], "compound": first["compound"],
        "tyre_life_at_start": first["tyre_life"], "is_fresh_tyre": first["is_fresh_tyre"],
    }


def _stint_key(row: Mapping[str, object | None]) -> tuple[str, str, int]:
    session_id, driver_id, stint_number = row["session_id"], row["driver_id"], row["stint_number"]
    assert isinstance(session_id, str) and isinstance(driver_id, str) and isinstance(stint_number, int)
    return session_id, driver_id, stint_number


def _reject_duplicate_laps(rows: Iterable[Mapping[str, object | None]]) -> None:
    keys = [_lap_key(row) for row in rows]
    if len(keys) != len(set(keys)):
        raise NormalizationError("duplicate canonical lap key")


def _lap_key(row: Mapping[str, object | None]) -> tuple[str, str, int]:
    session_id, driver_id, lap_number = row["session_id"], row["driver_id"], row["lap_number"]
    assert isinstance(session_id, str) and isinstance(driver_id, str) and isinstance(lap_number, int)
    return session_id, driver_id, lap_number


def _required(record: Mapping[str, object], field: str) -> object:
    if field not in record:
        raise NormalizationError(f"lap row is missing required {field}")
    return record[field]


def _required_time(record: Mapping[str, object], field: str, label: str) -> int:
    value = _required(record, field)
    if _is_missing(value):
        raise NormalizationError(f"{label} is required")
    return _time(value, label)


def _nullable_time(value: object | None, label: str) -> int | None:
    return None if _is_missing(value) else _time(value, label)


def _time(value: object, label: str) -> int:
    try:
        return normalize_session_time_ms(value)
    except NormalizationError as error:
        raise NormalizationError(f"invalid {label}: {error}") from error


def _required_int16(record: Mapping[str, object], field: str, label: str) -> int:
    value = _nullable_int16(_required(record, field), label)
    if value is None:
        raise NormalizationError(f"{label} is required")
    return value


def _nullable_int16(value: object | None, label: str) -> int | None:
    normalized = normalize_nullable_scalar(value)
    if normalized is None:
        return None
    if isinstance(normalized, bool) or not isinstance(normalized, (int, float)) or int(normalized) != normalized:
        raise NormalizationError(f"{label} must be an integer")
    if not 0 <= int(normalized) <= _INT16_MAX:
        raise NormalizationError(f"{label} must fit in Int16")
    return int(normalized)


def _nullable_text(value: object | None, label: str) -> str | None:
    normalized = normalize_nullable_scalar(value)
    if normalized is None:
        return None
    if not isinstance(normalized, str):
        raise NormalizationError(f"{label} must be a string")
    return normalized or None


def _nullable_bool(value: object | None, label: str) -> bool | None:
    normalized = normalize_nullable_scalar(value)
    if normalized is None:
        return None
    if not isinstance(normalized, bool):
        raise NormalizationError(f"{label} must be a boolean")
    return normalized


def _is_missing(value: object | None) -> bool:
    return (
        value is None
        or (isinstance(value, float) and not math.isfinite(value))
        or type(value).__name__ in {"NAType", "NaTType"}
    )


def _source_key(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NormalizationError("lap DriverNumber must be a non-empty string")
    return value


def _require_session_id(value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise NormalizationError("session_id must be a non-empty string")


__all__ = ["adapt_laps", "adapt_stints", "normalize_laps", "normalize_stints"]
