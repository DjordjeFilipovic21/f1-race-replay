"""Offline deterministic tests for qualifying lap-status parsing and reconciliation.

Covers:
  - deletion and reinstatement parsing from canonical race-control messages
  - canonical event-time handling and boundary failures
  - duplicate-message idempotence and deterministic ordering
  - unsupported / contradictory message forms failing closed
  - unambiguous lap matching and ambiguous/unmatched fail-closed behavior
  - final-state reconciliation against canonical ``laps.deleted`` authority
  - ``event_time_ms <= replay_time_ms`` effective-state semantics
  - BrowserQualifyingLapStatus* model validation and immutability

No network access and no web imports: all fixtures are synthetic, in-memory
Polars frames or plain mapping records.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from itertools import permutations

import polars as pl
import pytest

from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    BROWSER_QUALIFYING_LAP_STATUS_SCHEMA_ID,
    BrowserQualifyingLapStatusEvent,
    BrowserQualifyingLapStatusRecord,
    BrowserQualifyingLapStatusReference,
    BrowserQualifyingLapStatusSidecar,
    CanonicalGenerationSnapshot,
    MAX_INT64,
)
from f1_replay_pipeline.delivery.browser.browser_lap_status import (
    LapStatusReconciliationError,
    build_lap_status_sidecar,
    build_qualifying_lap_status_sidecar,
    parse_lap_status_events,
    parse_qualifying_lap_status_events,
    parse_race_control_lap_status_events,
    reconcile_lap_status,
    reconcile_lap_status_events,
    reconcile_race_control_lap_status,
)

# ---------------------------------------------------------------------------
# Shared deterministic fixtures
# ---------------------------------------------------------------------------

DRIVER_METADATA = {"44": "HAM", "1": "VER"}

# FastF1's race-control deletion format (see qualifying-api.md):
# "CAR <number> .* TIME <m:ss.mmm> DELETED - <reason>"; the lap is carried in
# the structured lap_number column, never as "LAP n" text.
DELETED_MESSAGE = "CAR 44 TIME 1:30.000 DELETED - TRACK LIMITS"
REINSTATED_MESSAGE = "CAR 44 TIME 1:30.000 REINSTATED"
VER_REINSTATED_MESSAGE = "CAR 1 TIME 1:31.000 REINSTATED"


def _lap(
    driver_id: str = "HAM",
    lap_number: int = 1,
    lap_start_time_ms: int = 0,
    lap_end_time_ms: int | None = 90_000,
    lap_duration_ms: int | None = 90_000,
    deleted: bool | None = False,
    deleted_reason: str | None = None,
    **changes: object,
) -> dict[str, object]:
    """One canonical-shaped lap row with all status-relevant fields."""
    row: dict[str, object] = {
        "session_id": "monza-2026",
        "driver_id": driver_id,
        "lap_number": lap_number,
        "lap_start_time_ms": lap_start_time_ms,
        "lap_end_time_ms": lap_end_time_ms,
        "lap_duration_ms": lap_duration_ms,
        "deleted": deleted,
        "deleted_reason": deleted_reason,
    }
    row.update(changes)
    return row


def _canonical_laps() -> list[dict[str, object]]:
    """Canonical final state: only HAM lap 2 is deleted."""
    return [
        _lap("HAM", 1, 0, 90_000, 90_000, False),
        _lap("HAM", 2, 90_000, 180_000, 90_000, True, "TRACK LIMITS"),
        _lap("HAM", 3, 180_000, 270_000, 90_000, False),
        _lap("VER", 1, 0, 91_000, 91_000, False),
        _lap("VER", 2, 91_000, 182_000, 91_000, False),
    ]


def _message(
    session_time_ms: int,
    message: str,
    driver_id: str | None = None,
    lap_number: int | None = None,
    **changes: object,
) -> dict[str, object]:
    """One canonical race-control message row (message_index left to fallback)."""
    row: dict[str, object] = {
        "session_id": "monza-2026",
        "session_time_ms": session_time_ms,
        "message": message,
        "driver_id": driver_id,
        "lap_number": lap_number,
    }
    row.update(changes)
    return row


def _event(**changes: object) -> BrowserQualifyingLapStatusEvent:
    values: dict[str, object] = {
        "driver_id": "HAM",
        "lap_number": 2,
        "event_time_ms": 185_000,
        "status": "deleted",
        "reason": "TRACK LIMITS",
        "raw_message": DELETED_MESSAGE,
    }
    values.update(changes)
    return BrowserQualifyingLapStatusEvent(**values)


def _record(**changes: object) -> BrowserQualifyingLapStatusRecord:
    values: dict[str, object] = {
        "lap_number": (1, 2, 3),
        "lap_start_ms": (0, 90_000, 180_000),
        "lap_end_ms": (90_000, 180_000, 270_000),
        "status": ("valid", "deleted", "valid"),
        "deleted_reason": (None, "TRACK LIMITS", None),
    }
    values.update(changes)
    return BrowserQualifyingLapStatusRecord(**values)


def _sidecar_drivers() -> Mapping[str, BrowserQualifyingLapStatusRecord]:
    return {
        "HAM": _record(),
        "VER": BrowserQualifyingLapStatusRecord(
            lap_number=(1, 2),
            lap_start_ms=(0, 91_000),
            lap_end_ms=(91_000, 182_000),
            status=("valid", "valid"),
            deleted_reason=(None, None),
        ),
    }


def _sidecar(
    *,
    events: tuple[BrowserQualifyingLapStatusEvent, ...] = (),
    **changes: object,
) -> BrowserQualifyingLapStatusSidecar:
    values: dict[str, object] = {
        "fixture_id": "monza-2026",
        "drivers": _sidecar_drivers(),
        "events": events,
        "contract_version": "v2",
    }
    values.update(changes)
    return BrowserQualifyingLapStatusSidecar(**values)


def _sequence_events() -> tuple[BrowserQualifyingLapStatusEvent, ...]:
    """Deleted -> reinstated -> deleted causal sequence for HAM lap 2."""
    return (
        _event(
            event_time_ms=185_000, status="deleted", reason="TRACK LIMITS",
            raw_message=DELETED_MESSAGE,
        ),
        _event(
            event_time_ms=190_000, status="reinstated", reason=None,
            raw_message=REINSTATED_MESSAGE,
        ),
        _event(
            event_time_ms=195_000, status="deleted", reason="TRACK LIMITS",
            raw_message=DELETED_MESSAGE,
        ),
    )


def _snapshot(
    lap_rows: list[dict[str, object]] | None = None,
    message_rows: list[dict[str, object]] | None = None,
    *,
    driver_ids: tuple[str, ...] = ("HAM", "VER"),
    fixture_id: str = "monza-2026",
) -> CanonicalGenerationSnapshot:
    """Minimal canonical generation snapshot for sidecar construction."""
    drivers = pl.DataFrame(
        [
            {
                "session_id": fixture_id,
                "driver_id": driver_id,
                "source_driver_key": source_key,
                "driver_number": number,
            }
            for driver_id, source_key, number in (("HAM", "44", 44), ("VER", "1", 1))
            if driver_id in driver_ids
        ]
    )
    laps = pl.DataFrame(lap_rows if lap_rows is not None else _canonical_laps())
    messages = pl.DataFrame(
        message_rows
        if message_rows is not None
        else [_message(185_000, DELETED_MESSAGE, "HAM", 2)]
    )
    return CanonicalGenerationSnapshot(
        "generation",
        "a" * 64,
        {
            "session_metadata": pl.DataFrame([{"session_id": fixture_id}]),
            "drivers": drivers,
            "laps": laps,
            "race_control_messages": messages,
        },
    )


# ===========================================================================
# Deletion / reinstatement parsing
# ===========================================================================


class TestParseDeletionAndReinstatement:
    """Positive and negative parsing of TIME ... DELETED / REINSTATED messages."""

    def test_parses_deletion_event_with_reason(self) -> None:
        """✅ Positive: a TIME ... DELETED message yields a deleted event."""
        # Arrange: the lap is carried by the structured lap_number column.
        messages = [_message(185_000, DELETED_MESSAGE, None, 2)]

        # Act
        events = parse_race_control_lap_status_events(messages, _canonical_laps(), DRIVER_METADATA)

        # Assert
        assert len(events) == 1
        event = events[0]
        assert event.driver_id == "HAM"
        assert event.lap_number == 2
        assert event.event_time_ms == 185_000
        assert event.status == "deleted"
        assert event.reason == "TRACK LIMITS"
        assert event.raw_message == DELETED_MESSAGE

    def test_parses_reinstatement_event_without_reason(self) -> None:
        """✅ Positive: a TIME ... REINSTATED message yields a reinstated event."""
        # Arrange
        messages = [_message(190_000, REINSTATED_MESSAGE, None, 2)]

        # Act
        events = parse_race_control_lap_status_events(messages, _canonical_laps(), DRIVER_METADATA)

        # Assert
        assert len(events) == 1
        event = events[0]
        assert event.driver_id == "HAM"
        assert event.lap_number == 2
        assert event.event_time_ms == 190_000
        assert event.status == "reinstated"
        assert event.reason is None
        assert event.raw_message == REINSTATED_MESSAGE

    def test_resolves_driver_from_canonical_driver_id_column(self) -> None:
        """✅ Positive: the canonical driver_id column needs no driver metadata."""
        # Arrange
        messages = [_message(185_000, DELETED_MESSAGE, driver_id="HAM", lap_number=2)]

        # Act
        events = parse_race_control_lap_status_events(messages, _canonical_laps())

        # Assert
        assert events[0].driver_id == "HAM"

    def test_resolves_driver_from_fastf1_racing_number_alias(self) -> None:
        """✅ Positive: RacingNumber / Lap columns resolve through aliases."""
        # Arrange
        messages = [{
            "session_time_ms": 185_000,
            "message": DELETED_MESSAGE,
            "RacingNumber": "44",
            "Lap": 2,
        }]

        # Act
        events = parse_race_control_lap_status_events(messages, _canonical_laps(), DRIVER_METADATA)

        # Assert
        assert events[0].driver_id == "HAM"
        assert events[0].lap_number == 2

    def test_parse_aliases_produce_identical_events(self) -> None:
        """✅ Positive: documented alias names behave exactly like the primary parser."""
        # Arrange
        messages = [_message(185_000, DELETED_MESSAGE, None, 2)]
        expected = parse_race_control_lap_status_events(messages, _canonical_laps(), DRIVER_METADATA)

        # Act
        via_lap = parse_lap_status_events(messages, _canonical_laps(), DRIVER_METADATA)
        via_qualifying = parse_qualifying_lap_status_events(messages, _canonical_laps(), DRIVER_METADATA)

        # Assert
        assert via_lap == expected
        assert via_qualifying == expected

    def test_reported_lap_from_record_resolves_canonical_lap(self) -> None:
        """✅ Positive: the structured lap column drives matching when absent in text."""
        # Arrange
        messages = [_message(
            185_000, "CAR 44 (HAM) TIME 1:30.000 DELETED - TRACK LIMITS",
            driver_id="HAM", lap_number=2,
        )]

        # Act
        events = parse_race_control_lap_status_events(messages, _canonical_laps(), DRIVER_METADATA)

        # Assert
        assert events[0].lap_number == 2

    def test_timing_evidence_outranks_stale_lap_hint(self) -> None:
        """✅ Positive: a stale structured lap hint is corrected by timing evidence."""
        # Arrange: the structured lap column says 1, but lap 2 completed closest
        # to the event, so timing evidence wins.
        messages = [_message(185_000, "CAR 44 TIME 1:30.000 DELETED - TRACK LIMITS", None, 1)]

        # Act
        events = parse_race_control_lap_status_events(messages, _canonical_laps(), DRIVER_METADATA)

        # Assert
        assert events[0].lap_number == 2

    def test_parse_without_canonical_laps_uses_reported_lap(self) -> None:
        """✅ Positive: without canonical laps the reported lap is authoritative."""
        # Arrange
        messages = [_message(185_000, DELETED_MESSAGE, None, 2)]

        # Act
        events = parse_race_control_lap_status_events(messages, None, DRIVER_METADATA)

        # Assert
        assert events[0].lap_number == 2

    def test_lap_hint_breaks_timing_tie(self) -> None:
        """✅ Positive: an explicit structured lap hint disambiguates equal timing candidates."""
        # Arrange: two laps with identical end time and duration.
        laps = [
            _lap("HAM", 1, 0, 90_000, 90_000),
            _lap("HAM", 2, 0, 90_000, 90_000),
        ]
        messages = [_message(92_000, DELETED_MESSAGE, None, 2)]

        # Act
        events = parse_race_control_lap_status_events(messages, laps, DRIVER_METADATA)

        # Assert
        assert events[0].lap_number == 2

    def test_future_lap_candidates_are_excluded(self) -> None:
        """✅ Positive: a status event cannot match a lap that has not ended yet."""
        # Arrange: both laps have the reported duration, but lap 2 ends after the event.
        laps = [
            _lap("HAM", 1, 0, 90_000, 90_000),
            _lap("HAM", 2, 110_000, 200_000, 90_000),
        ]
        messages = [_message(150_000, DELETED_MESSAGE, None, 2)]

        # Act
        events = parse_race_control_lap_status_events(messages, laps, DRIVER_METADATA)

        # Assert
        assert events[0].lap_number == 1

    def test_lap_without_end_boundary_fails_closed(self) -> None:
        """❌ Negative: a canonical lap without an end cannot be matched causally."""
        # Arrange
        laps = [_lap("HAM", 1, 0, None, 90_000)]
        messages = [_message(150_000, DELETED_MESSAGE, None, 1)]

        # Act / Assert
        with pytest.raises(LapStatusReconciliationError, match="no unambiguous canonical lap"):
            parse_race_control_lap_status_events(messages, laps, DRIVER_METADATA)


# ===========================================================================
# Canonical event-time boundaries
# ===========================================================================


class TestCanonicalEventTime:
    """Canonical ``session_time_ms`` handling and boundary failures."""

    def test_uses_canonical_session_time_ms(self) -> None:
        """✅ Positive: event time is the canonical session_time_ms value."""
        # Arrange
        messages = [_message(185_000, DELETED_MESSAGE, None, 2)]

        # Act
        events = parse_race_control_lap_status_events(messages, _canonical_laps(), DRIVER_METADATA)

        # Assert
        assert events[0].event_time_ms == 185_000

    def test_accepts_sessionTimeMs_alias(self) -> None:
        """✅ Positive: the sessionTimeMs alias is accepted as canonical time."""
        # Arrange
        messages = [{"sessionTimeMs": 185_000, "message": DELETED_MESSAGE, "lap_number": 2}]

        # Act
        events = parse_race_control_lap_status_events(messages, _canonical_laps(), DRIVER_METADATA)

        # Assert
        assert events[0].event_time_ms == 185_000

    def test_zero_event_time_is_valid(self) -> None:
        """✅ Positive: a zero canonical timestamp is a valid boundary."""
        # Arrange
        messages = [_message(0, "CAR 44 TIME 1:30.000 DELETED - TRACK LIMITS", None, 1)]

        # Act
        events = parse_race_control_lap_status_events(messages, None, DRIVER_METADATA)

        # Assert
        assert events[0].event_time_ms == 0
        assert events[0].lap_number == 1

    def test_missing_timestamp_on_recognized_event_raises(self) -> None:
        """❌ Negative: a deletion marker without canonical time fails closed."""
        # Arrange
        messages = [{"message": DELETED_MESSAGE}]

        # Act / Assert
        with pytest.raises(LapStatusReconciliationError, match="no canonical timestamp"):
            parse_race_control_lap_status_events(messages, _canonical_laps(), DRIVER_METADATA)

    def test_negative_timestamp_on_recognized_event_raises(self) -> None:
        """❌ Negative: a negative canonical timestamp fails closed."""
        # Arrange
        messages = [{"session_time_ms": -1, "message": DELETED_MESSAGE}]

        # Act / Assert
        with pytest.raises(LapStatusReconciliationError, match="no canonical timestamp"):
            parse_race_control_lap_status_events(messages, _canonical_laps(), DRIVER_METADATA)

    def test_missing_timestamp_on_unrelated_message_is_ignored(self) -> None:
        """✅ Positive: unrelated messages without timestamps are ignored."""
        # Arrange
        messages = [{"message": "TRACK CLEAR"}]

        # Act
        events = parse_race_control_lap_status_events(messages, _canonical_laps(), DRIVER_METADATA)

        # Assert
        assert events == ()


# ===========================================================================
# Duplicate idempotence and deterministic ordering
# ===========================================================================


class TestDuplicateIdempotence:
    """Repeated messages must collapse deterministically."""

    def test_duplicate_messages_collapse_to_one_event(self) -> None:
        """✅ Positive: replaying an identical message is idempotent."""
        # Arrange
        messages = [
            _message(185_000, DELETED_MESSAGE, None, 2),
            _message(185_000, DELETED_MESSAGE, None, 2),
        ]

        # Act
        events = parse_race_control_lap_status_events(messages, _canonical_laps(), DRIVER_METADATA)

        # Assert
        assert len(events) == 1

    def test_semantic_duplicates_collapse_to_one_event(self) -> None:
        """✅ Positive: messages with the same parsed key collapse."""
        # Arrange: identical parsed key but different raw text (trailing space).
        messages = [
            _message(185_000, DELETED_MESSAGE, None, 2),
            _message(185_000, DELETED_MESSAGE + " ", None, 2),
        ]

        # Act
        results = [
            parse_race_control_lap_status_events(order, _canonical_laps(), DRIVER_METADATA)
            for order in permutations(messages)
        ]

        # Assert
        assert all(len(events) == 1 for events in results)
        assert all(events[0].raw_message == DELETED_MESSAGE for events in results)

    def test_parse_is_idempotent_across_calls(self) -> None:
        """✅ Positive: parsing the same input twice yields identical events."""
        # Arrange
        messages = [
            _message(185_000, DELETED_MESSAGE, None, 2),
            _message(92_000, VER_REINSTATED_MESSAGE, None, 1),
        ]

        # Act
        first = parse_race_control_lap_status_events(messages, _canonical_laps(), DRIVER_METADATA)
        second = parse_race_control_lap_status_events(messages, _canonical_laps(), DRIVER_METADATA)

        # Assert
        assert first == second
        assert len(first) == 2

    def test_parse_is_deterministic_under_message_permutation(self) -> None:
        """✅ Positive: input order does not change the emitted event sequence."""
        # Arrange
        messages = [
            _message(185_000, DELETED_MESSAGE, None, 2),
            _message(92_000, VER_REINSTATED_MESSAGE, None, 1),
        ]

        # Act
        results = [
            parse_race_control_lap_status_events(order, _canonical_laps(), DRIVER_METADATA)
            for order in permutations(messages)
        ]

        # Assert
        assert results[0] == results[1]
        assert [event.event_time_ms for event in results[0]] == [92_000, 185_000]

    def test_reconcile_dedupes_duplicate_events(self) -> None:
        """✅ Positive: reconciliation deduplicates equal event values."""
        # Arrange
        events = (_event(), _event())

        # Act
        ordered = reconcile_lap_status_events(events, _canonical_laps())

        # Assert
        assert len(ordered) == 1

    def test_reconcile_dedupes_semantic_events_with_different_raw_text(self) -> None:
        """✅ Positive: raw-message formatting does not change semantic identity."""
        # Arrange
        events = (_event(raw_message=DELETED_MESSAGE), _event(raw_message=f"{DELETED_MESSAGE} "))

        # Act
        ordered = reconcile_lap_status_events(events, _canonical_laps())

        # Assert
        assert len(ordered) == 1
        assert ordered[0].raw_message == DELETED_MESSAGE

    def test_reconcile_rejects_contradictory_same_timestamp_statuses(self) -> None:
        """❌ Negative: same-time opposite statuses have no causal ordering."""
        # Arrange
        events = (
            _event(event_time_ms=185_000, status="deleted", reason="TRACK LIMITS"),
            _event(event_time_ms=185_000, status="reinstated", reason=None, raw_message=REINSTATED_MESSAGE),
        )

        # Act / Assert
        with pytest.raises(LapStatusReconciliationError, match="contradictory statuses"):
            reconcile_lap_status_events(events, _canonical_laps())


# ===========================================================================
# Unsupported and contradictory message forms
# ===========================================================================


class TestUnsupportedMessageForms:
    """Fail-closed handling of unsupported or contradictory forms."""

    def test_unrelated_messages_are_ignored(self) -> None:
        """✅ Positive: unrelated race-control rows are skipped."""
        # Arrange
        messages = [_message(100_000, "GREEN FLAG"), _message(120_000, "TRACK CLEAR")]

        # Act
        events = parse_race_control_lap_status_events(messages, _canonical_laps(), DRIVER_METADATA)

        # Assert
        assert events == ()

    def test_blank_message_is_ignored(self) -> None:
        """✅ Positive: a blank message row is skipped."""
        # Arrange
        messages = [_message(185_000, "   ")]

        # Act
        events = parse_race_control_lap_status_events(messages, _canonical_laps(), DRIVER_METADATA)

        # Assert
        assert events == ()

    def test_time_without_milliseconds_raises(self) -> None:
        """❌ Negative: a status marker with an unsupported time form fails closed."""
        # Arrange
        messages = [_message(185_000, "CAR 44 (HAM) LAP 2 TIME 1:30 DELETED - TRACK LIMITS")]

        # Act / Assert
        with pytest.raises(LapStatusReconciliationError, match="unsupported message form"):
            parse_race_control_lap_status_events(messages, _canonical_laps(), DRIVER_METADATA)

    def test_status_word_without_time_raises(self) -> None:
        """❌ Negative: a status word without the TIME pattern fails closed."""
        # Arrange
        messages = [_message(185_000, "CAR 44 (HAM) LAP 2 DELETED - TRACK LIMITS")]

        # Act / Assert
        with pytest.raises(LapStatusReconciliationError, match="unsupported message form"):
            parse_race_control_lap_status_events(messages, _canonical_laps(), DRIVER_METADATA)

    def test_contradictory_status_markers_raise(self) -> None:
        """❌ Negative: both DELETED and REINSTATED in one message fail closed."""
        # Arrange
        messages = [{
            "session_time_ms": 185_000,
            "message": (
                "CAR 44 (HAM) LAP 2 TIME 1:30.000 DELETED - TRACK LIMITS; "
                "LAP 3 TIME 1:31.000 REINSTATED"
            ),
        }]

        # Act / Assert
        with pytest.raises(LapStatusReconciliationError, match="contradictory status markers"):
            parse_race_control_lap_status_events(messages, _canonical_laps(), DRIVER_METADATA)

    def test_invalid_deletion_reason_raises(self) -> None:
        """❌ Negative: a non-prefixed suffix after the status word fails closed."""
        # Arrange
        messages = [{
            "session_time_ms": 185_000,
            "message": "CAR 44 (HAM) LAP 2 TIME 1:30.000 DELETED DUE TO TRACK LIMITS",
        }]

        # Act / Assert
        with pytest.raises(LapStatusReconciliationError, match="invalid deletion reason"):
            parse_race_control_lap_status_events(messages, _canonical_laps(), DRIVER_METADATA)

    def test_invalid_reported_lap_time_raises(self) -> None:
        """❌ Negative: a reported lap time with seconds > 59 fails closed."""
        # Arrange
        messages = [{
            "session_time_ms": 185_000,
            "message": "CAR 44 TIME 1:90.000 DELETED - TRACK LIMITS",
            "lap_number": 2,
        }]

        # Act / Assert
        with pytest.raises(LapStatusReconciliationError, match="invalid reported lap time"):
            parse_race_control_lap_status_events(messages, _canonical_laps(), DRIVER_METADATA)

    def test_contradictory_lap_fields_raise(self) -> None:
        """❌ Negative: a non-positive structured lap number fails closed."""
        # Arrange
        messages = [_message(185_000, DELETED_MESSAGE, "HAM", lap_number=-1)]

        # Act / Assert
        with pytest.raises(LapStatusReconciliationError, match="contradictory lap fields"):
            parse_race_control_lap_status_events(messages, _canonical_laps(), DRIVER_METADATA)

    def test_invalid_message_index_raises(self) -> None:
        """❌ Negative: a negative message_index fails closed."""
        # Arrange
        messages = [{
            "session_time_ms": 185_000,
            "message": DELETED_MESSAGE,
            "message_index": -1,
        }]

        # Act / Assert
        with pytest.raises(LapStatusReconciliationError, match="invalid message index"):
            parse_race_control_lap_status_events(messages, _canonical_laps(), DRIVER_METADATA)


# ===========================================================================
# Unambiguous and ambiguous matching
# ===========================================================================


class TestMatchingFailClosed:
    """Unambiguous matches succeed; ambiguous or unmatched evidence fails closed."""

    def test_ambiguous_lap_tie_raises(self) -> None:
        """❌ Negative: equal timing without a hint is ambiguous and fails closed."""
        # Arrange: identical timing candidates and no textual lap hint.
        laps = [
            _lap("HAM", 1, 0, 90_000, 90_000),
            _lap("HAM", 2, 0, 90_000, 90_000),
        ]
        messages = [_message(92_000, "CAR 44 (HAM) TIME 1:30.000 DELETED - TRACK LIMITS")]

        # Act / Assert
        with pytest.raises(LapStatusReconciliationError, match="no unambiguous canonical lap"):
            parse_race_control_lap_status_events(messages, laps, DRIVER_METADATA)

    def test_unmatched_duration_raises(self) -> None:
        """❌ Negative: a reported time matching no canonical lap fails closed."""
        # Arrange: HAM canonical laps are all 90.000; message reports 1:31.000.
        messages = [{
            "session_time_ms": 185_000,
            "message": "CAR 44 TIME 1:31.000 DELETED - TRACK LIMITS",
            "lap_number": 2,
        }]

        # Act / Assert
        with pytest.raises(LapStatusReconciliationError, match="no unambiguous canonical lap"):
            parse_race_control_lap_status_events(messages, _canonical_laps(), DRIVER_METADATA)

    def test_empty_laps_raise(self) -> None:
        """❌ Negative: an empty canonical laps table cannot be matched."""
        # Arrange
        messages = [_message(185_000, DELETED_MESSAGE)]

        # Act / Assert
        with pytest.raises(LapStatusReconciliationError, match="no unambiguous canonical lap"):
            parse_race_control_lap_status_events(messages, [], DRIVER_METADATA)

    def test_missing_driver_raises(self) -> None:
        """❌ Negative: no driver identity anywhere fails closed."""
        # Arrange
        messages = [{
            "session_time_ms": 185_000,
            "message": "TIME 1:30.000 DELETED - TRACK LIMITS",
        }]

        # Act / Assert
        with pytest.raises(LapStatusReconciliationError, match="ambiguous or missing driver"):
            parse_race_control_lap_status_events(messages, _canonical_laps(), DRIVER_METADATA)


# ===========================================================================
# Final-state reconciliation
# ===========================================================================


class TestReconcileFinalState:
    """Canonical ``laps.deleted`` is authoritative for final reconciliation."""

    def test_reconcile_matches_canonical_final_state(self) -> None:
        """✅ Positive: a reconciled final state exactly equals the canonical table."""
        # Arrange
        events = _sequence_events()

        # Act
        state = reconcile_lap_status(events, _canonical_laps())

        # Assert
        assert state[("HAM", 1)] is False
        assert state[("HAM", 2)] is True
        assert state[("HAM", 3)] is False
        assert state[("VER", 1)] is False
        assert state[("VER", 2)] is False

    def test_reconcile_applies_deleted_reinstated_deleted_sequence(self) -> None:
        """✅ Positive: later events override earlier ones before final compare."""
        # Arrange
        events = _sequence_events()

        # Act
        state = reconcile_lap_status(events, _canonical_laps())

        # Assert: reinstatement at 190_000 is overridden by the 195_000 deletion.
        assert state[("HAM", 2)] is True

    def test_reconcile_without_events_contradicting_deleted_lap_raises(self) -> None:
        """❌ Negative: a deleted canonical lap requires causal evidence to reconcile."""
        # Arrange / Act / Assert
        with pytest.raises(LapStatusReconciliationError, match="contradict canonical laps.deleted"):
            reconcile_lap_status((), _canonical_laps())

    def test_reconcile_without_events_matches_all_valid_canonical(self) -> None:
        """✅ Positive: an empty event set passes when every canonical lap is valid."""
        # Arrange
        laps = [
            _lap("HAM", 1, 0, 90_000, 90_000, False),
            _lap("VER", 1, 0, 91_000, 91_000, False),
        ]

        # Act
        state = reconcile_lap_status((), laps)

        # Assert
        assert all(not deleted for deleted in state.values())

    def test_reconcile_contradicting_canonical_raises(self) -> None:
        """❌ Negative: events leaving a lap valid against a deleted canonical lap fail."""
        # Arrange: only the reinstatement, final state valid for HAM lap 2.
        events = (_sequence_events()[1],)

        # Act / Assert
        with pytest.raises(LapStatusReconciliationError, match="contradict canonical laps.deleted"):
            reconcile_lap_status(events, _canonical_laps())

    def test_reconcile_rejects_unknown_lap(self) -> None:
        """❌ Negative: an event for an unpublished canonical lap fails closed."""
        # Arrange
        events = (_event(lap_number=5),)

        # Act / Assert
        with pytest.raises(LapStatusReconciliationError, match="references an unknown canonical lap"):
            reconcile_lap_status(events, _canonical_laps())

    def test_reconcile_rejects_non_event_values(self) -> None:
        """❌ Negative: non-event values in the sequence are rejected."""
        # Arrange
        events = ("not-an-event",)

        # Act / Assert
        with pytest.raises(TypeError, match="must contain event values"):
            reconcile_lap_status(events, _canonical_laps())

    def test_reconcile_rejects_duplicate_canonical_lap_key(self) -> None:
        """❌ Negative: duplicate canonical lap keys fail closed."""
        # Arrange
        laps = _canonical_laps() + [_lap("HAM", 2, 90_000, 180_000, 90_000, True, "TRACK LIMITS")]

        # Act / Assert
        with pytest.raises(LapStatusReconciliationError, match="duplicate lap key"):
            reconcile_lap_status((), laps)

    def test_reconcile_allows_unknown_deleted_state_without_inference(self) -> None:
        """✅ Positive: nullable canonical deletion state is not coerced to final valid."""
        # Arrange
        laps = [_lap("HAM", 1, 0, 90_000, 90_000, deleted=None)]

        # Act
        state = reconcile_lap_status((), laps)

        # Assert: the causal default is valid, while no final-state claim is made.
        assert state[("HAM", 1)] is False

    def test_reconcile_rejects_event_for_unknown_final_deleted_state(self) -> None:
        """❌ Negative: an event cannot establish final state for a nullable lap."""
        # Arrange
        laps = [_lap("HAM", 1, 0, 90_000, 90_000, deleted=None)]

        # Act / Assert
        with pytest.raises(LapStatusReconciliationError, match="without an authoritative final"):
            reconcile_lap_status((_event(lap_number=1),), laps)

    def test_reconcile_returns_immutable_mapping(self) -> None:
        """✅ Positive: the reconciled state is a read-only mapping."""
        # Arrange
        state = reconcile_lap_status(_sequence_events(), _canonical_laps())

        # Act / Assert
        with pytest.raises(TypeError):
            state[("HAM", 2)] = False  # type: ignore[index]

    def test_reconcile_race_control_lap_status_end_to_end(self) -> None:
        """✅ Positive: parse + reconcile round trip matches the canonical table."""
        # Arrange
        messages = [
            _message(185_000, DELETED_MESSAGE, None, 2),
            _message(92_000, VER_REINSTATED_MESSAGE, None, 1),
        ]

        # Act
        state = reconcile_race_control_lap_status(messages, _canonical_laps(), DRIVER_METADATA)

        # Assert
        assert state[("HAM", 2)] is True
        assert state[("VER", 1)] is False
        assert state[("VER", 2)] is False


# ===========================================================================
# Replay-time effective state
# ===========================================================================


class TestReplayTimeEffectiveState:
    """Effective state is computed over events with event_time_ms <= boundary."""

    def test_replay_boundary_before_events_returns_all_valid(self) -> None:
        """✅ Positive: no event before the boundary means every lap is valid."""
        # Arrange
        events = _sequence_events()

        # Act
        state = reconcile_lap_status(events, _canonical_laps(), replay_time_ms=180_000)

        # Assert
        assert all(not deleted for deleted in state.values())

    def test_replay_boundary_at_event_time_applies_event(self) -> None:
        """✅ Positive: an event exactly at the boundary is included (<=)."""
        # Arrange
        events = _sequence_events()

        # Act
        state = reconcile_lap_status(events, _canonical_laps(), replay_time_ms=185_000)

        # Assert
        assert state[("HAM", 2)] is True

    def test_replay_boundary_between_events_returns_partial_state(self) -> None:
        """✅ Positive: only events at or before the boundary shape the state."""
        # Arrange: reinstatement at 190_000 must not yet be visible at 189_000.
        events = _sequence_events()

        # Act
        state = reconcile_lap_status(events, _canonical_laps(), replay_time_ms=189_000)

        # Assert
        assert state[("HAM", 2)] is True

    def test_replay_boundary_at_reinstatement_returns_valid(self) -> None:
        """✅ Positive: a boundary at the reinstatement reflects the reinstated lap."""
        # Arrange
        events = _sequence_events()

        # Act
        state = reconcile_lap_status(events, _canonical_laps(), replay_time_ms=190_000)

        # Assert
        assert state[("HAM", 2)] is False

    def test_replay_boundary_after_events_returns_final_state(self) -> None:
        """✅ Positive: a boundary after all events equals the final sequence state."""
        # Arrange
        events = _sequence_events()

        # Act
        state = reconcile_lap_status(events, _canonical_laps(), replay_time_ms=200_000)

        # Assert
        assert state[("HAM", 2)] is True

    def test_reconcile_events_filters_to_boundary(self) -> None:
        """✅ Positive: event sequences expose only causal events at the boundary."""
        # Arrange
        events = _sequence_events()

        # Act
        ordered = reconcile_lap_status_events(events, _canonical_laps(), replay_time_ms=190_000)

        # Assert
        assert [event.event_time_ms for event in ordered] == [185_000, 190_000]
        assert ordered[1].status == "reinstated"

    def test_reconcile_events_without_boundary_returns_all(self) -> None:
        """✅ Positive: without a boundary the full validated sequence is returned."""
        # Arrange
        events = _sequence_events()

        # Act
        ordered = reconcile_lap_status_events(events, _canonical_laps())

        # Assert
        assert len(ordered) == 3

    def test_reconcile_rejects_negative_replay_time(self) -> None:
        """❌ Negative: a negative replay boundary is rejected."""
        # Arrange
        events = _sequence_events()

        # Act / Assert
        with pytest.raises(ValueError, match="replay_time_ms"):
            reconcile_lap_status(events, _canonical_laps(), replay_time_ms=-1)


# ===========================================================================
# Sidecar construction
# ===========================================================================


class TestBuildLapStatusSidecar:
    """Final-state sidecar construction from a canonical generation snapshot."""

    def test_build_publishes_reconciled_records(self) -> None:
        """✅ Positive: the sidecar mirrors canonical final lap status."""
        # Arrange
        snapshot = _snapshot()

        # Act
        sidecar = build_lap_status_sidecar(snapshot)

        # Assert
        assert sidecar.fixture_id == "monza-2026"
        assert tuple(sidecar.drivers) == ("HAM", "VER")
        ham = sidecar.drivers["HAM"]
        assert ham.lap_number == (1, 2, 3)
        assert ham.status == ("valid", "deleted", "valid")
        assert ham.deleted_reason == (None, "TRACK LIMITS", None)
        assert sidecar.drivers["VER"].status == ("valid", "valid")
        assert len(sidecar.events) == 1
        assert sidecar.events[0].driver_id == "HAM"
        assert sidecar.events[0].lap_number == 2
        assert sidecar.events[0].status == "deleted"

    def test_build_is_deterministic(self) -> None:
        """✅ Positive: identical snapshots produce identical sidecars."""
        # Arrange / Act
        first = build_lap_status_sidecar(_snapshot())
        second = build_lap_status_sidecar(_snapshot())

        # Assert
        assert first.as_dict() == second.as_dict()

    def test_build_qualifying_alias_matches_build_function(self) -> None:
        """✅ Positive: the descriptive alias produces the same sidecar."""
        # Arrange / Act
        snapshot = _snapshot()

        # Assert
        assert (
            build_qualifying_lap_status_sidecar(snapshot).as_dict()
            == build_lap_status_sidecar(snapshot).as_dict()
        )

    def test_build_rejects_lap_without_complete_boundaries(self) -> None:
        """❌ Negative: incomplete lap boundaries fail closed."""
        # Arrange
        laps = [_lap("HAM", 1, 0, None, 90_000, False)]

        # Act / Assert
        with pytest.raises(LapStatusReconciliationError, match="complete ordered boundaries"):
            build_lap_status_sidecar(_snapshot(lap_rows=laps))

    def test_build_rejects_deleted_reason_on_valid_lap(self) -> None:
        """❌ Negative: a deleted reason on a valid canonical lap fails closed."""
        # Arrange
        laps = [_lap("HAM", 1, 0, 90_000, 90_000, False, "BOGUS")]

        # Act / Assert
        with pytest.raises(LapStatusReconciliationError, match="must not contain a deleted reason"):
            build_lap_status_sidecar(_snapshot(lap_rows=laps))

    def test_build_rejects_laps_for_unpublished_driver(self) -> None:
        """❌ Negative: a lap referencing an unpublished driver fails closed."""
        # Arrange
        laps = [_lap("LEG", 1, 0, 90_000, 90_000, False)]

        # Act / Assert
        with pytest.raises(LapStatusReconciliationError, match="unpublished driver"):
            build_lap_status_sidecar(_snapshot(lap_rows=laps, driver_ids=("HAM",)))

    def test_build_fails_closed_when_events_contradict_canonical(self) -> None:
        """❌ Negative: contradictory causal events fail sidecar construction."""
        # Arrange: only a reinstatement, contradicting the deleted canonical lap.
        messages = [_message(185_000, REINSTATED_MESSAGE, "HAM", 2)]

        # Act / Assert
        with pytest.raises(LapStatusReconciliationError, match="contradict canonical laps.deleted"):
            build_lap_status_sidecar(_snapshot(message_rows=messages))

    def test_build_rejects_incomplete_snapshot_frames(self) -> None:
        """❌ Negative: missing canonical frames fail closed."""
        # Arrange
        base = _snapshot()
        frames = {name: frame for name, frame in base.frames.items() if name != "race_control_messages"}
        snapshot = CanonicalGenerationSnapshot("generation", "a" * 64, frames)

        # Act / Assert
        with pytest.raises(LapStatusReconciliationError, match="inputs are incomplete"):
            build_lap_status_sidecar(snapshot)


# ===========================================================================
# BrowserQualifyingLapStatusEvent model
# ===========================================================================


class TestBrowserQualifyingLapStatusEvent:
    """Positive and negative contract enforcement for the event model."""

    def test_constructs_with_all_fields(self) -> None:
        """✅ Positive: a fully populated event is accepted."""
        # Arrange & Act
        event = _event()

        # Assert
        assert event.driver_id == "HAM"
        assert event.lap_number == 2
        assert event.event_time_ms == 185_000
        assert event.status == "deleted"
        assert event.reason == "TRACK LIMITS"
        assert event.raw_message == DELETED_MESSAGE

    def test_provides_compatibility_properties(self) -> None:
        """✅ Positive: status / session-time aliases used by consumers work."""
        # Arrange & Act
        event = _event()

        # Assert
        assert event.event_type == "deleted"
        assert event.session_time_ms == 185_000

    def test_as_dict_serializes_contract_format(self) -> None:
        """✅ Positive: as_dict emits camelCase contract keys."""
        # Arrange & Act
        result = _event().as_dict()

        # Assert
        assert result == {
            "driverId": "HAM",
            "lapNumber": 2,
            "eventTimeMs": 185_000,
            "status": "deleted",
            "reason": "TRACK LIMITS",
            "rawMessage": DELETED_MESSAGE,
        }

    def test_rejects_invalid_driver_id(self) -> None:
        """❌ Negative: a non-canonical driver id is rejected."""
        with pytest.raises(ValueError, match="driver_id must match"):
            _event(driver_id="ham")

    def test_rejects_zero_lap_number(self) -> None:
        """❌ Negative: a non-positive lap number is rejected."""
        with pytest.raises(ValueError, match="lap_number must be a positive integer"):
            _event(lap_number=0)

    def test_rejects_negative_event_time(self) -> None:
        """❌ Negative: a negative event time is rejected."""
        with pytest.raises(ValueError, match="event_time_ms must be a non-negative"):
            _event(event_time_ms=-1)

    def test_rejects_event_time_overflow(self) -> None:
        """❌ Negative: an event time beyond Int64 is rejected."""
        with pytest.raises(ValueError, match="event_time_ms"):
            _event(event_time_ms=MAX_INT64 + 1)

    def test_rejects_invalid_status(self) -> None:
        """❌ Negative: an unsupported status value is rejected."""
        with pytest.raises(ValueError, match="status is invalid"):
            _event(status="banished")

    def test_rejects_empty_reason(self) -> None:
        """❌ Negative: a blank reason is rejected."""
        with pytest.raises(ValueError, match="reason must be a non-empty string or null"):
            _event(reason="")

    def test_rejects_empty_raw_message(self) -> None:
        """❌ Negative: a blank raw message is rejected."""
        with pytest.raises(ValueError, match="raw_message must be a non-empty string"):
            _event(raw_message="   ")

    def test_is_immutable(self) -> None:
        """✅ Positive: the frozen event rejects attribute assignment."""
        # Arrange
        event = _event()

        # Act / Assert
        with pytest.raises(FrozenInstanceError):
            event.driver_id = "VER"  # type: ignore[misc]


# ===========================================================================
# BrowserQualifyingLapStatusRecord model
# ===========================================================================


class TestBrowserQualifyingLapStatusRecord:
    """Positive and negative contract enforcement for the record model."""

    def test_constructs_with_all_fields(self) -> None:
        """✅ Positive: aligned final-state columns are accepted."""
        # Arrange & Act
        record = _record()

        # Assert
        assert record.lap_number == (1, 2, 3)
        assert record.lap_start_ms == (0, 90_000, 180_000)
        assert record.lap_end_ms == (90_000, 180_000, 270_000)
        assert record.status == ("valid", "deleted", "valid")
        assert record.deleted_reason == (None, "TRACK LIMITS", None)

    def test_converts_sequences_to_tuples(self) -> None:
        """✅ Positive: list inputs are normalized to immutable tuples."""
        # Arrange & Act
        record = BrowserQualifyingLapStatusRecord(
            lap_number=[1],
            lap_start_ms=[0],
            lap_end_ms=[90_000],
            status=["valid"],
            deleted_reason=[None],
        )

        # Assert
        assert record.lap_number == (1,)
        assert record.status == ("valid",)

    def test_provides_compatibility_views(self) -> None:
        """✅ Positive: canonical naming and boolean views are available."""
        # Arrange & Act
        record = _record()

        # Assert
        assert record.lap_start_time_ms == (0, 90_000, 180_000)
        assert record.lap_end_time_ms == (90_000, 180_000, 270_000)
        assert record.final_status == ("valid", "deleted", "valid")
        assert record.deleted == (False, True, False)

    def test_as_dict_serializes_contract_format(self) -> None:
        """✅ Positive: as_dict emits the aligned columnar contract."""
        # Arrange & Act
        result = _record().as_dict()

        # Assert
        assert result == {
            "lapNumber": [1, 2, 3],
            "lapStartMs": [0, 90_000, 180_000],
            "lapEndMs": [90_000, 180_000, 270_000],
            "status": ["valid", "deleted", "valid"],
            "deletedReason": [None, "TRACK LIMITS", None],
        }

    def test_rejects_misaligned_columns(self) -> None:
        """❌ Negative: misaligned column lengths are rejected."""
        with pytest.raises(ValueError, match="aligned"):
            _record(lap_start_ms=(0,))

    def test_rejects_non_increasing_lap_number(self) -> None:
        """❌ Negative: duplicate lap numbers are rejected."""
        with pytest.raises(ValueError, match="strictly increasing"):
            _record(
                lap_number=(1, 1),
                lap_start_ms=(0, 90_000),
                lap_end_ms=(90_000, 180_000),
                status=("valid", "valid"),
                deleted_reason=(None, None),
            )

    def test_rejects_end_at_or_before_start(self) -> None:
        """❌ Negative: lap end must follow lap start."""
        with pytest.raises(ValueError, match="must follow lap start times"):
            _record(lap_end_ms=(90_000, 180_000, 180_000))

    def test_rejects_negative_times(self) -> None:
        """❌ Negative: negative Int64 timings are rejected."""
        with pytest.raises(TypeError, match="non-negative signed Int64"):
            _record(lap_start_ms=(-1, 90_000, 180_000))

    def test_rejects_invalid_status(self) -> None:
        """❌ Negative: a non-contract status value is rejected."""
        with pytest.raises(ValueError, match="status values are invalid"):
            _record(status=("valid", "DELETED", "valid"))

    def test_rejects_empty_deleted_reason(self) -> None:
        """❌ Negative: a blank deleted reason is rejected."""
        with pytest.raises(ValueError, match="deleted reasons must be non-empty strings or null"):
            _record(deleted_reason=(None, "", None))

    def test_rejects_reason_on_valid_lap(self) -> None:
        """❌ Negative: a deleted reason on a valid lap is rejected."""
        with pytest.raises(ValueError, match="must not contain a deleted reason"):
            _record(status=("valid", "valid", "valid"), deleted_reason=(None, None, "BOGUS"))

    def test_is_immutable(self) -> None:
        """✅ Positive: the frozen record rejects attribute assignment."""
        # Arrange
        record = _record()

        # Act / Assert
        with pytest.raises(FrozenInstanceError):
            record.lap_number = ()  # type: ignore[misc]


# ===========================================================================
# BrowserQualifyingLapStatusSidecar model
# ===========================================================================


class TestBrowserQualifyingLapStatusSidecar:
    """Positive and negative contract enforcement for the sidecar model."""

    def test_constructs_with_events(self) -> None:
        """✅ Positive: a sidecar with records and events is accepted."""
        # Arrange & Act
        sidecar = _sidecar(events=(_event(),))

        # Assert
        assert sidecar.fixture_id == "monza-2026"
        assert tuple(sidecar.drivers) == ("HAM", "VER")
        assert sidecar.events == (_event(),)

    def test_sorts_events_deterministically(self) -> None:
        """✅ Positive: unsorted events are normalized to causal order."""
        # Arrange
        later = _event(event_time_ms=195_000, status="deleted")
        earlier = _event(event_time_ms=185_000, status="deleted")

        # Act
        sidecar = _sidecar(events=(later, earlier))

        # Assert
        assert tuple(event.event_time_ms for event in sidecar.events) == (185_000, 195_000)

    def test_as_dict_serializes_contract_format(self) -> None:
        """✅ Positive: as_dict emits the v2 sidecar contract."""
        # Arrange & Act
        result = _sidecar(events=(_event(),)).as_dict()

        # Assert
        assert result["contractVersion"] == "v2"
        assert result["fixtureId"] == "monza-2026"
        assert set(result["drivers"]) == {"HAM", "VER"}
        assert result["drivers"]["HAM"]["status"] == ["valid", "deleted", "valid"]
        assert result["events"] == [_event().as_dict()]

    def test_rejects_non_v2_contract(self) -> None:
        """❌ Negative: only contract version v2 is accepted."""
        with pytest.raises(ValueError, match="available only in contract version v2"):
            _sidecar(contract_version="v1")

    def test_rejects_empty_drivers(self) -> None:
        """❌ Negative: an empty driver mapping is rejected."""
        with pytest.raises(ValueError, match="non-empty mapping"):
            _sidecar(drivers={})

    def test_rejects_invalid_driver_id(self) -> None:
        """❌ Negative: a non-canonical driver key is rejected."""
        with pytest.raises(ValueError, match="driver IDs are invalid"):
            _sidecar(drivers={"ham": _record()})

    def test_rejects_event_for_unknown_driver(self) -> None:
        """❌ Negative: an event for an unpublished driver is rejected."""
        with pytest.raises(ValueError, match="must reference a published driver"):
            _sidecar(events=(_event(driver_id="LEG"),))

    def test_rejects_event_for_unknown_lap(self) -> None:
        """❌ Negative: an event for an unpublished lap is rejected."""
        with pytest.raises(ValueError, match="must reference a published lap"):
            _sidecar(events=(_event(lap_number=9),))

    def test_rejects_duplicate_events(self) -> None:
        """❌ Negative: duplicate events are rejected."""
        with pytest.raises(ValueError, match="must not contain duplicates"):
            _sidecar(events=(_event(), _event()))

    def test_rejects_semantic_duplicate_events_with_different_raw_text(self) -> None:
        """❌ Negative: sidecar models reject duplicate causal identities."""
        with pytest.raises(ValueError, match="semantic duplicates"):
            _sidecar(events=(_event(), _event(raw_message=f"{DELETED_MESSAGE} ")))

    def test_rejects_contradictory_same_time_events(self) -> None:
        """❌ Negative: sidecar models reject unordered same-time transitions."""
        with pytest.raises(ValueError, match="contradictory same-time"):
            _sidecar(events=(
                _event(event_time_ms=185_000, status="deleted"),
                _event(
                    event_time_ms=185_000, status="reinstated", reason=None,
                    raw_message=REINSTATED_MESSAGE,
                ),
            ))

    def test_is_immutable(self) -> None:
        """✅ Positive: drivers mapping and events stay immutable after construction."""
        # Arrange
        sidecar = _sidecar(events=(_event(),))

        # Act / Assert
        with pytest.raises(TypeError):
            sidecar.drivers["LEG"] = _record()  # type: ignore[index]
        with pytest.raises(FrozenInstanceError):
            sidecar.events = ()  # type: ignore[misc]


# ===========================================================================
# BrowserQualifyingLapStatusReference model
# ===========================================================================


class TestBrowserQualifyingLapStatusReference:
    """Positive and negative contract enforcement for the manifest reference."""

    def test_accepts_correct_values(self) -> None:
        """✅ Positive: the qualifying lap-status artifact reference is accepted."""
        # Arrange & Act
        ref = BrowserQualifyingLapStatusReference(
            path="qualifying-lap-status.json",
            schema_id=BROWSER_QUALIFYING_LAP_STATUS_SCHEMA_ID,
            sha256="a" * 64,
        )

        # Assert
        assert ref.path == "qualifying-lap-status.json"
        assert ref.schema_id == BROWSER_QUALIFYING_LAP_STATUS_SCHEMA_ID
        assert ref.as_dict() == {
            "path": "qualifying-lap-status.json",
            "schemaId": BROWSER_QUALIFYING_LAP_STATUS_SCHEMA_ID,
            "sha256": "a" * 64,
        }

    def test_rejects_wrong_path(self) -> None:
        """❌ Negative: a different artifact path is rejected."""
        with pytest.raises(ValueError, match="reference is invalid"):
            BrowserQualifyingLapStatusReference(
                path="other.json",
                schema_id=BROWSER_QUALIFYING_LAP_STATUS_SCHEMA_ID,
                sha256="a" * 64,
            )

    def test_rejects_wrong_schema_id(self) -> None:
        """❌ Negative: a different schema id is rejected."""
        with pytest.raises(ValueError, match="reference is invalid"):
            BrowserQualifyingLapStatusReference(
                path="qualifying-lap-status.json",
                schema_id="urn:wrong",
                sha256="a" * 64,
            )

    def test_rejects_invalid_sha256(self) -> None:
        """❌ Negative: a non-SHA-256 digest is rejected."""
        with pytest.raises(ValueError, match="sha256"):
            BrowserQualifyingLapStatusReference(
                path="qualifying-lap-status.json",
                schema_id=BROWSER_QUALIFYING_LAP_STATUS_SCHEMA_ID,
                sha256="not-a-sha",
            )
