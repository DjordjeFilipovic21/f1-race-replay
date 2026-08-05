"""Parse canonical race-control messages into qualifying lap-status events.

FastF1 exposes the final ``Deleted`` flag on laps, but the raw race-control
message is the only stable source for the causal event.  This module therefore
matches explicit ``TIME ... DELETED`` and ``TIME ... REINSTATED`` messages to
canonical laps without mutating either canonical table.  Non-timed ``LAP
DELETED`` advisories are source notifications, not causal events, and are
ignored in their explicitly recognized form.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from types import MappingProxyType
from typing import cast

from .browser_delivery_models import (
    BrowserQualifyingLapStatusEvent,
    BrowserQualifyingLapStatusRecord,
    BrowserQualifyingLapStatusSidecar,
    CanonicalGenerationSnapshot,
    QualifyingLapStatusEventKind,
)


_CANONICAL_DRIVER_ID = re.compile(r"(?:[A-Z]{3}|D(?:0|[1-9][0-9]*))\Z")
_CAR = re.compile(
    r"\bCAR\s*#?\s*(?P<number>[0-9]+)"
    r"(?:\s*\((?P<abbreviation>[A-Za-z]{3})\))?",
    re.IGNORECASE,
)
_LAP = re.compile(r"\bLAP(?:\s+NUMBER)?\s*[:#-]?\s*(?P<number>[0-9]+)\b", re.IGNORECASE)
_TIME_STATUS = re.compile(
    r"\bTIME\s+(?P<time>[0-9]+:[0-9]{2}\.[0-9]{3})\s+"
    r"(?P<status>DELETED|REINSTATED)\b(?P<suffix>.*)\Z",
    re.IGNORECASE,
)
_NON_TIMED_LAP_DELETED_ADVISORY = re.compile(
    r"\A\s*CAR\s*#?\s*[0-9]+(?:\s*\([A-Za-z]{3}\))?\s+LAP\s+DELETED\b"
    r"\s*-\s+.+?\bLAP\s+[0-9]+\b.+\Z",
    re.IGNORECASE,
)
_STATUS_WORD = re.compile(r"\b(?:DELETED|REINSTATED)\b", re.IGNORECASE)
_TIME_WORD = re.compile(r"\bTIME\b", re.IGNORECASE)
_MESSAGE_KEYS = ("message", "Message", "raw_message", "rawMessage")
_TIME_KEYS = ("session_time_ms", "sessionTimeMs")
_INDEX_KEYS = ("message_index", "messageIndex")
_DRIVER_KEYS = ("driver_id", "driverId")
_NUMBER_KEYS = ("driver_number", "driverNumber", "RacingNumber", "source_driver_key")
_LAP_KEYS = ("lap_number", "lapNumber", "Lap", "lap")
_LAP_DURATION_KEYS = ("lap_duration_ms", "lapDurationMs", "LapTime")
_LAP_END_KEYS = ("lap_end_time_ms", "lapEndTimeMs", "Time")
_ALIAS_KEYS = (
    "abbreviation", "driver_abbreviation", "driverAbbreviation", "code",
    "short_code", "shortCode", "short_name", "shortName", "Driver",
)


@dataclass(frozen=True)
class _ParsedEvent:
    """Keep source ordering metadata without exposing it in the sidecar."""

    event: BrowserQualifyingLapStatusEvent
    message_index: int
    has_explicit_message_index: bool


class LapStatusReconciliationError(ValueError):
    """Raised when qualifying lap-status evidence cannot be reconciled safely."""


def parse_race_control_lap_status_events(
    messages: object,
    laps: object | None = None,
    driver_metadata: object | None = None,
) -> tuple[BrowserQualifyingLapStatusEvent, ...]:
    """Return deterministic, fail-closed deletion/reinstatement events.

    ``messages`` is normally the canonical ``race_control_messages`` frame and
    ``laps`` is the canonical ``laps`` frame.  Driver metadata is optional when
    messages already contain canonical ``driver_id`` values; it supplies the
    number, source-key, and abbreviation aliases otherwise.

    Unrelated race-control rows are ignored.  Once a row contains an explicit
    deletion/reinstatement marker, however, missing or ambiguous identity is a
    hard error: publishing a guessed lap is worse than publishing no sidecar.
    Duplicate semantic events collapse to one value, so replaying the same
    message is idempotent.
    """
    if _looks_like_driver_metadata(laps):
        if driver_metadata is None:
            driver_metadata, laps = laps, None
        elif not _looks_like_driver_metadata(driver_metadata):
            laps, driver_metadata = driver_metadata, laps
    aliases = _driver_lookup(driver_metadata)
    lap_rows = _lap_records(laps)
    parsed = [
        candidate
        for row_number, record in enumerate(_records(messages, "race_control_messages"))
        for candidate in (_parse_record(record, row_number, aliases, lap_rows),)
        if candidate is not None
    ]
    unique: dict[tuple[object, ...], _ParsedEvent] = {}
    for candidate in parsed:
        event = candidate.event
        key = (event.driver_id, event.lap_number, event.event_time_ms, event.status, event.reason)
        current = unique.get(key)
        if current is None or _parsed_winner_key(candidate) < _parsed_winner_key(current):
            unique[key] = candidate
    return tuple(item.event for item in sorted(unique.values(), key=_parsed_sort_key))


def has_qualifying_lap_status_messages(messages: object) -> bool:
    """Return whether a message table contains actionable timed status evidence."""
    return any(
        raw_message is not None and _TIME_STATUS.search(raw_message) is not None
        for record in _records(messages, "race_control_messages")
        for raw_message in (_first_text(record, _MESSAGE_KEYS),)
    )


def _parse_record(
    record: Mapping[str, object],
    row_number: int,
    aliases: Mapping[str, frozenset[str]],
    lap_rows: tuple[Mapping[str, object], ...] | None,
) -> _ParsedEvent | None:
    raw_message = _first_text(record, _MESSAGE_KEYS)
    if raw_message is None:
        return None
    if _is_non_timed_lap_deleted_advisory(raw_message):
        return None
    recognized = bool(_STATUS_WORD.search(raw_message))
    event_time_ms = _canonical_time(record)
    if event_time_ms is None:
        if recognized:
            raise LapStatusReconciliationError("qualifying lap-status event has no canonical timestamp")
        return None
    match = _TIME_STATUS.search(raw_message)
    if match is None:
        if recognized:
            raise LapStatusReconciliationError("qualifying lap-status event has an unsupported message form")
        return None
    if len(_STATUS_WORD.findall(raw_message)) != 1:
        raise LapStatusReconciliationError("qualifying lap-status event contains contradictory status markers")
    reason = _reason(match.group("suffix"))
    if match.group("suffix").strip() and reason is None:
        raise LapStatusReconciliationError("qualifying lap-status event has an invalid deletion reason")
    driver_id = _resolve_driver(record, raw_message, aliases)
    if driver_id is None:
        raise LapStatusReconciliationError("qualifying lap-status event has an ambiguous or missing driver")
    reported_lap, lap_fields_are_consistent = _reported_lap(record, raw_message[: match.start()])
    if not lap_fields_are_consistent:
        raise LapStatusReconciliationError("qualifying lap-status event has contradictory lap fields")
    reported_time_ms = _reported_time_ms(match.group("time"))
    if reported_time_ms is None:
        raise LapStatusReconciliationError("qualifying lap-status event has an invalid reported lap time")
    lap_number = _match_lap(
        driver_id,
        reported_lap,
        reported_time_ms,
        event_time_ms,
        lap_rows,
    )
    if lap_number is None:
        raise LapStatusReconciliationError("qualifying lap-status event has no unambiguous canonical lap")
    event = BrowserQualifyingLapStatusEvent(
        driver_id=driver_id,
        lap_number=lap_number,
        event_time_ms=event_time_ms,
        status=cast(QualifyingLapStatusEventKind, match.group("status").lower()),
        reason=reason,
        raw_message=raw_message,
    )
    message_index = _message_index(record, row_number)
    if message_index is None:
        raise LapStatusReconciliationError("qualifying lap-status event has an invalid message index")
    return _ParsedEvent(event, message_index[0], message_index[1])


def _is_non_timed_lap_deleted_advisory(message: str) -> bool:
    """Identify FastF1's non-timed ``LAP DELETED`` notification form only."""
    return (
        _NON_TIMED_LAP_DELETED_ADVISORY.fullmatch(message) is not None
        and len(_STATUS_WORD.findall(message)) == 1
        and _TIME_WORD.search(message) is None
    )


def _match_lap(
    driver_id: str,
    reported_lap: int | None,
    reported_time_ms: int,
    event_time_ms: int,
    lap_rows: tuple[Mapping[str, object], ...] | None,
) -> int | None:
    if lap_rows is None:
        return reported_lap
    if not lap_rows:
        return None
    candidates: list[tuple[tuple[int, int, int], int]] = []
    for row in lap_rows:
        if _canonical_driver(row.get("driver_id")) != driver_id:
            continue
        lap_number = _positive_int(_first_value(row, _LAP_KEYS))
        duration_ms = _exact_int(_first_value(row, _LAP_DURATION_KEYS))
        end_time_ms = _exact_int(_first_value(row, _LAP_END_KEYS))
        start_time_ms = _exact_int(_first_value(row, ("lap_start_time_ms", "lapStartTimeMs", "LapStartTime")))
        if lap_number is None:
            continue
        if duration_ms is None and start_time_ms is not None and end_time_ms is not None:
            duration_ms = end_time_ms - start_time_ms
        if duration_ms != reported_time_ms:
            continue
        if end_time_ms is None or end_time_ms > event_time_ms:
            continue
        # FastF1's message lap and normalized LapNumber are useful hints, not
        # an identity transform.  Source streams can disagree by a local
        # offset, so rank each candidate using its own timing evidence instead
        # of applying one guessed offset to the whole session.
        has_completed_before_event = int(end_time_ms is not None and end_time_ms <= event_time_ms)
        timing_distance = -abs(event_time_ms - end_time_ms) if end_time_ms is not None else -(1 << 62)
        lap_hint = int(reported_lap is not None and lap_number == reported_lap)
        candidates.append(((timing_distance, has_completed_before_event, lap_hint), lap_number))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    best_score, best_lap = candidates[0]
    if len(candidates) > 1 and candidates[1][0] == best_score:
        return None
    return best_lap


def _resolve_driver(
    record: Mapping[str, object],
    raw_message: str,
    aliases: Mapping[str, frozenset[str]],
) -> str | None:
    candidates: set[str] = set()
    explicit = _canonical_driver(_first_value(record, _DRIVER_KEYS))
    if explicit is not None:
        candidates.add(explicit)
    car_match = _CAR.search(raw_message)
    if car_match is not None:
        candidates.update(aliases.get(_alias_key(car_match.group("number")), frozenset()))
        abbreviation = car_match.group("abbreviation")
        if abbreviation is not None:
            candidates.update(aliases.get(_alias_key(abbreviation), frozenset()))
            direct_abbreviation = _canonical_driver(abbreviation)
            if direct_abbreviation is not None:
                candidates.add(direct_abbreviation)
    for key in _NUMBER_KEYS:
        value = record.get(key)
        if value is not None:
            candidates.update(aliases.get(_alias_key(value), frozenset()))
    for key in _ALIAS_KEYS:
        value = record.get(key)
        direct_alias = _canonical_driver(value)
        if direct_alias is not None:
            candidates.add(direct_alias)
    return next(iter(candidates)) if len(candidates) == 1 else None


def _driver_lookup(source: object | None) -> dict[str, frozenset[str]]:
    mappings: dict[str, set[str]] = {}
    for record in _metadata_records(source):
        driver_id = _canonical_driver(_first_value(record, ("driver_id", "driverId", "id")))
        if driver_id is None:
            continue
        values = [record.get(key) for key in _NUMBER_KEYS + _ALIAS_KEYS]
        values.append(driver_id)
        for value in values:
            if value is not None:
                alias = _alias_key(value)
                if alias:
                    mappings.setdefault(alias, set()).add(driver_id)
    return {alias: frozenset(driver_ids) for alias, driver_ids in mappings.items()}


def _metadata_records(source: object | None) -> tuple[Mapping[str, object], ...]:
    if source is None:
        return ()
    if isinstance(source, Mapping):
        if any(key in source for key in ("driver_id", "driverId", "id")):
            return (source,)
        if all(isinstance(value, str) for value in source.values()):
            return tuple({"driver_number": key, "driver_id": value} for key, value in source.items())
        return tuple(
            {**value, "driver_id": value.get("driver_id", key)}
            for key, value in source.items()
            if isinstance(value, Mapping)
        )
    return _records(source, "drivers")


def _looks_like_driver_metadata(source: object | None) -> bool:
    if source is None:
        return False
    if isinstance(source, Mapping) and all(isinstance(value, str) for value in source.values()):
        return True
    records = _records(source, "drivers")
    return bool(records) and all(
        _canonical_driver(_first_value(record, ("driver_id", "driverId", "id"))) is not None
        and _first_value(record, _LAP_KEYS) is None
        for record in records
    )


def _lap_records(source: object | None) -> tuple[Mapping[str, object], ...] | None:
    return None if source is None else _records(source, "laps")


def _records(source: object, table_attribute: str) -> tuple[Mapping[str, object], ...]:
    table = getattr(source, table_attribute, None)
    if table is None:
        frames = getattr(source, "frames", None)
        table = frames.get(table_attribute) if isinstance(frames, Mapping) else source
    if table is None:
        return ()
    to_dicts = getattr(table, "to_dicts", None)
    if callable(to_dicts):
        return _mapping_records(to_dicts())
    to_dict = getattr(table, "to_dict", None)
    if callable(to_dict):
        try:
            return _mapping_records(to_dict("records"))
        except TypeError:
            try:
                return _mapping_records(to_dict(orient="records"))
            except TypeError:
                return ()
    if isinstance(table, Mapping):
        return (table,)
    if isinstance(table, Iterable) and not isinstance(table, (str, bytes)):
        return _mapping_records(table)
    return ()


def _mapping_records(records: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(records, Iterable) or isinstance(records, (str, bytes, Mapping)):
        return ()
    return tuple(record for record in records if isinstance(record, Mapping))


def _reported_lap(record: Mapping[str, object], message: str) -> tuple[int | None, bool]:
    record_lap_value = _first_value(record, _LAP_KEYS)
    record_lap = _positive_int(record_lap_value)
    if record_lap_value is not None and record_lap is None:
        return None, False
    match = _LAP.search(message)
    message_lap = _positive_int(match.group("number")) if match else None
    if match is not None and message_lap is None:
        return None, False
    # The message and the structured source can use different lap counters
    # around a timing boundary.  Keep the textual counter as a soft hint and
    # let candidate timing/duration evidence resolve the canonical key.
    return message_lap or record_lap, True


def _reason(suffix: str) -> str | None:
    text = suffix.strip()
    if not text:
        return None
    if text[0] not in "-–—:":
        return None
    reason = re.sub(r"^[-–—:]\s*", "", text).strip()
    return re.sub(r"\s+", " ", reason) or None


def _reported_time_ms(value: str) -> int | None:
    minutes, seconds_with_ms = value.split(":")
    seconds, milliseconds = seconds_with_ms.split(".")
    if int(seconds) > 59:
        return None
    try:
        duration = Decimal(minutes) * Decimal(60) + Decimal(seconds) + Decimal(f"0.{milliseconds}")
        return int((duration * Decimal(1000)).to_integral_value(rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return None


def _canonical_time(record: Mapping[str, object]) -> int | None:
    return _non_negative_int(_first_value(record, _TIME_KEYS))


def _message_index(record: Mapping[str, object], fallback: int) -> tuple[int, bool] | None:
    value = _first_value(record, _INDEX_KEYS)
    if value is None:
        return fallback, False
    normalized = _non_negative_int(value)
    return None if normalized is None else (normalized, True)


def _exact_int(value: object | None) -> int | None:
    return value if type(value) is int else None


def _non_negative_int(value: object | None) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _positive_int(value: object | None) -> int | None:
    return value if type(value) is int and value > 0 else None


def _canonical_driver(value: object | None) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().upper()
    return candidate if _CANONICAL_DRIVER_ID.fullmatch(candidate) else None


def _alias_key(value: object) -> str:
    if isinstance(value, bool):
        return ""
    text = str(value).strip().upper()
    return str(int(text)) if text.isdigit() else text


def _first_value(record: Mapping[str, object], keys: Iterable[str]) -> object | None:
    return next((record[key] for key in keys if key in record and record[key] is not None), None)


def _first_text(record: Mapping[str, object], keys: Iterable[str]) -> str | None:
    value = _first_value(record, keys)
    return value if isinstance(value, str) and value.strip() else None


def _parsed_sort_key(value: _ParsedEvent) -> tuple[object, ...]:
    event = value.event
    return (
        event.event_time_ms, event.driver_id, event.lap_number,
        event.status, event.reason or "", event.raw_message,
    )


def _parsed_winner_key(value: _ParsedEvent) -> tuple[object, ...]:
    event = value.event
    return (
        0 if value.has_explicit_message_index else 1,
        value.message_index if value.has_explicit_message_index else 0,
        event.raw_message,
    )


def reconcile_lap_status(
    events: Iterable[BrowserQualifyingLapStatusEvent],
    laps: object,
    *,
    replay_time_ms: int | None = None,
) -> Mapping[tuple[str, int], bool]:
    """Apply causal events to canonical lap keys and verify final state.

    State starts as valid because a deletion event is the only evidence that
    changes it.  With no replay boundary, the resulting state must exactly
    equal every canonical ``laps.deleted`` value; the canonical table is the
    authority and is never rewritten.  A boundary returns the state effective
    at ``event_time_ms <= replay_time_ms`` without comparing it to the later
    final snapshot.
    """
    canonical = _canonical_lap_state(laps)
    boundary = _replay_boundary(replay_time_ms)
    ordered = _validated_events(events, canonical)
    state = {key: False for key in canonical}
    for event in ordered:
        if boundary is None or event.event_time_ms <= boundary:
            state[(event.driver_id, event.lap_number)] = event.status == "deleted"
    if boundary is None:
        mismatches = tuple(
            key
            for key, deleted in canonical.items()
            if deleted is not None and state[key] != deleted
        )
        if mismatches:
            raise LapStatusReconciliationError(
                "qualifying lap-status events contradict canonical laps.deleted: "
                + ", ".join(f"{driver_id}/{lap_number}" for driver_id, lap_number in mismatches)
            )
    return MappingProxyType(dict(sorted(state.items())))


def reconcile_lap_status_events(
    events: Iterable[BrowserQualifyingLapStatusEvent],
    laps: object,
    *,
    replay_time_ms: int | None = None,
) -> tuple[BrowserQualifyingLapStatusEvent, ...]:
    """Validate and return the immutable causal event sequence for a boundary."""
    boundary = _replay_boundary(replay_time_ms)
    ordered = _validated_events(events, _canonical_lap_state(laps))
    reconcile_lap_status(ordered, laps, replay_time_ms=boundary)
    return tuple(event for event in ordered if boundary is None or event.event_time_ms <= boundary)


def reconcile_race_control_lap_status(
    messages: object,
    laps: object,
    driver_metadata: object | None = None,
    *,
    replay_time_ms: int | None = None,
) -> Mapping[tuple[str, int], bool]:
    """Parse raw canonical messages and reconcile their effective lap state."""
    events = parse_race_control_lap_status_events(messages, laps, driver_metadata)
    return reconcile_lap_status(events, laps, replay_time_ms=replay_time_ms)


def build_lap_status_sidecar(
    snapshot: CanonicalGenerationSnapshot,
) -> BrowserQualifyingLapStatusSidecar:
    """Build the immutable V2 qualifying status sidecar from one snapshot."""
    try:
        session = snapshot.frames["session_metadata"].row(0, named=True)
        fixture_id = session["session_id"]
        drivers = snapshot.frames["drivers"]
        laps = snapshot.frames["laps"]
        messages = snapshot.frames["race_control_messages"]
    except (KeyError, IndexError, TypeError) as error:
        raise LapStatusReconciliationError("qualifying lap-status inputs are incomplete") from error
    if not isinstance(fixture_id, str) or not fixture_id:
        raise LapStatusReconciliationError("qualifying lap-status fixture_id is invalid")

    driver_rows = _records(drivers, "drivers")
    driver_ids = _canonical_driver_ids(driver_rows)
    lap_rows = _canonical_lap_records(laps)
    _validate_lap_driver_roster(lap_rows, driver_ids)
    events = parse_race_control_lap_status_events(messages, laps, drivers)
    reconcile_lap_status(events, laps)
    records = _status_records(lap_rows, driver_ids)
    return BrowserQualifyingLapStatusSidecar(fixture_id, records, events)


def build_qualifying_lap_status_sidecar(
    snapshot: CanonicalGenerationSnapshot,
) -> BrowserQualifyingLapStatusSidecar:
    """Descriptive alias for the browser delivery orchestration boundary."""
    return build_lap_status_sidecar(snapshot)


def _validated_events(
    events: Iterable[BrowserQualifyingLapStatusEvent],
    canonical: Mapping[tuple[str, int], bool | None],
) -> tuple[BrowserQualifyingLapStatusEvent, ...]:
    values = tuple(events)
    if any(not isinstance(event, BrowserQualifyingLapStatusEvent) for event in values):
        raise TypeError("qualifying lap-status events must contain event values")
    unique_by_key: dict[tuple[object, ...], BrowserQualifyingLapStatusEvent] = {}
    for event in values:
        key = (
            event.driver_id, event.lap_number, event.event_time_ms,
            event.status, event.reason,
        )
        current = unique_by_key.get(key)
        if current is None or event.raw_message < current.raw_message:
            unique_by_key[key] = event
    unique = tuple(unique_by_key.values())
    unknown = tuple(
        (event.driver_id, event.lap_number)
        for event in unique
        if (event.driver_id, event.lap_number) not in canonical
    )
    if unknown:
        raise LapStatusReconciliationError(
            "qualifying lap-status event references an unknown canonical lap"
        )
    unknown_final_state = tuple(
        (event.driver_id, event.lap_number)
        for event in unique
        if canonical[(event.driver_id, event.lap_number)] is None
    )
    if unknown_final_state:
        raise LapStatusReconciliationError(
            "qualifying lap-status event references a lap without an authoritative final deleted state"
        )
    same_time_statuses: dict[tuple[str, int, int], set[str]] = {}
    for event in unique:
        same_time_statuses.setdefault(
            (event.driver_id, event.lap_number, event.event_time_ms),
            set(),
        ).add(event.status)
    if any(len(statuses) > 1 for statuses in same_time_statuses.values()):
        raise LapStatusReconciliationError(
            "qualifying lap-status events contain contradictory statuses at one timestamp"
        )
    return tuple(sorted(unique, key=_event_sort_key))


def _event_sort_key(event: BrowserQualifyingLapStatusEvent) -> tuple[object, ...]:
    return (
        event.event_time_ms, event.driver_id, event.lap_number,
        event.status, event.reason or "", event.raw_message,
    )


def _canonical_lap_state(laps: object) -> dict[tuple[str, int], bool | None]:
    state: dict[tuple[str, int], bool | None] = {}
    for row in _records(laps, "laps"):
        driver_id = _canonical_driver(row.get("driver_id"))
        lap_number = _positive_int(_first_value(row, _LAP_KEYS))
        deleted = row.get("deleted")
        if driver_id is None or lap_number is None:
            raise LapStatusReconciliationError("canonical laps contain an invalid identity")
        if deleted is not None and type(deleted) is not bool:
            raise LapStatusReconciliationError(
                "canonical laps.deleted must be a boolean or null for status reconciliation"
            )
        key = (driver_id, lap_number)
        if key in state:
            raise LapStatusReconciliationError("canonical laps contain a duplicate lap key")
        state[key] = deleted
    return state


def _canonical_lap_records(laps: object) -> tuple[Mapping[str, object], ...]:
    rows = _records(laps, "laps")
    _canonical_lap_state(laps)
    for row in rows:
        start = _exact_int(_first_value(row, ("lap_start_time_ms", "lapStartMs", "LapStartTime")))
        end = _exact_int(_first_value(row, ("lap_end_time_ms", "lapEndMs", "Time")))
        if start is None or end is None or start < 0 or end <= start:
            raise LapStatusReconciliationError(
                "canonical laps require complete ordered boundaries for status delivery"
            )
        reason = row.get("deleted_reason")
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            raise LapStatusReconciliationError("canonical laps contain an invalid deleted reason")
        deleted = row.get("deleted")
        if deleted is not True and reason is not None:
            raise LapStatusReconciliationError(
                "canonical laps with unknown or valid status must not contain a deleted reason"
            )
    return tuple(rows)


def _canonical_driver_ids(rows: tuple[Mapping[str, object], ...]) -> tuple[str, ...]:
    driver_ids = tuple(_canonical_driver(row.get("driver_id")) for row in rows)
    if any(driver_id is None for driver_id in driver_ids):
        raise LapStatusReconciliationError("canonical drivers contain an invalid driver ID")
    normalized = cast(tuple[str, ...], driver_ids)
    if not normalized:
        raise LapStatusReconciliationError("qualifying lap-status requires at least one canonical driver")
    if len(set(normalized)) != len(normalized):
        raise LapStatusReconciliationError("canonical drivers contain duplicate driver IDs")
    return tuple(sorted(normalized))


def _validate_lap_driver_roster(
    rows: tuple[Mapping[str, object], ...], driver_ids: tuple[str, ...],
) -> None:
    roster = frozenset(driver_ids)
    if any(_canonical_driver(row.get("driver_id")) not in roster for row in rows):
        raise LapStatusReconciliationError("canonical laps reference an unpublished driver")


def _status_records(
    rows: tuple[Mapping[str, object], ...], driver_ids: tuple[str, ...],
) -> Mapping[str, BrowserQualifyingLapStatusRecord]:
    grouped: dict[str, list[Mapping[str, object]]] = {driver_id: [] for driver_id in driver_ids}
    for row in rows:
        if row.get("deleted") is None:
            continue
        driver_id = _canonical_driver(row.get("driver_id"))
        assert driver_id is not None
        grouped[driver_id].append(row)
    records: dict[str, BrowserQualifyingLapStatusRecord] = {}
    for driver_id in driver_ids:
        driver_rows = sorted(grouped[driver_id], key=lambda row: cast(int, row["lap_number"]))
        records[driver_id] = BrowserQualifyingLapStatusRecord(
            lap_number=tuple(cast(int, row["lap_number"]) for row in driver_rows),
            lap_start_ms=tuple(cast(int, row["lap_start_time_ms"]) for row in driver_rows),
            lap_end_ms=tuple(cast(int, row["lap_end_time_ms"]) for row in driver_rows),
            status=tuple("deleted" if row["deleted"] else "valid" for row in driver_rows),
            deleted_reason=tuple(
                cast(str | None, row.get("deleted_reason")) if row["deleted"] else None
                for row in driver_rows
            ),
        )
    return records


def _replay_boundary(value: int | None) -> int | None:
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError("replay_time_ms must be a non-negative integer or None")
    return value


parse_qualifying_lap_status_events = parse_race_control_lap_status_events
parse_race_control_lap_status = parse_race_control_lap_status_events
parse_lap_status_events = parse_race_control_lap_status_events


__all__ = [
    "has_qualifying_lap_status_messages",
    "parse_lap_status_events",
    "parse_qualifying_lap_status_events",
    "parse_race_control_lap_status",
    "parse_race_control_lap_status_events",
    "LapStatusReconciliationError",
    "reconcile_lap_status",
    "reconcile_lap_status_events",
    "reconcile_race_control_lap_status",
    "build_lap_status_sidecar",
    "build_qualifying_lap_status_sidecar",
]
