from datetime import datetime, timedelta, timezone
from itertools import permutations
from types import SimpleNamespace

import pandas as pd
import pytest

from fixtures.fake_fastf1_session import (
    build_complete_session,
    build_practice_session,
    build_qualifying_session,
    build_qualifying_session_with_cancelled_q3,
    build_sprint_session,
)
from f1_replay_pipeline.domain.canonical_schema import DRIVERS_SCHEMA_V2, SESSION_METADATA_SCHEMA_V2
from f1_replay_pipeline.domain.normalizers import NormalizationError
from f1_replay_pipeline.adapters.fastf1.session_metadata import adapt_drivers, adapt_session_metadata


class FakeSession:
    def __init__(self, drivers: dict[str, dict[str, object]], **metadata: object) -> None:
        self.event = metadata.pop("event", {"Year": 2026, "RoundNumber": 3, "EventName": "Australian GP"})
        self.name = metadata.pop("name", "Race")
        self.drivers = list(drivers)
        self._drivers = drivers
        for name, value in metadata.items():
            setattr(self, name, value)

    def get_driver(self, identifier: object) -> dict[str, object]:
        return self._drivers[str(identifier)]


def test_adapt_session_metadata_emits_one_ordered_typed_stable_row():
    session = FakeSession({}, date=pd.Timestamp("2026-03-08T05:00:00"))

    frame = adapt_session_metadata(session)

    assert list(frame.schema.items()) == list(SESSION_METADATA_SCHEMA_V2.items())
    assert frame.to_dicts() == [
        {
            "session_id": "2026-03-race",
            "year": 2026,
            "round_number": 3,
            "event_name": "Australian GP",
            "session_name": "Race",
            "session_type": "race",
            "session_mode": "race",
            "session_start_time_utc": datetime(2026, 3, 8, 5, tzinfo=timezone.utc),
        }
    ]


def test_adapt_session_metadata_uses_absolute_session_date_not_duration_start_time():
    session = FakeSession(
        {},
        date=pd.Timestamp("2026-03-08T05:00:00"),
        session_start_time=timedelta(minutes=12),
    )

    frame = adapt_session_metadata(session)

    assert frame.item(0, "session_start_time_utc") == datetime(2026, 3, 8, 5, tzinfo=timezone.utc)


def test_adapt_session_metadata_does_not_convert_duration_start_time_to_datetime():
    session = FakeSession({}, session_start_time=timedelta(minutes=12))

    frame = adapt_session_metadata(session)

    assert frame.item(0, "session_start_time_utc") is None


def test_adapt_session_metadata_normalizes_local_event_session_date_to_utc():
    session = FakeSession(
        {},
        event={
            "Year": 2026,
            "RoundNumber": 3,
            "EventName": "Australian GP",
            "SessionDate": pd.Timestamp("2026-03-08T16:00:00+11:00"),
        },
    )

    frame = adapt_session_metadata(session)

    assert frame.item(0, "session_start_time_utc") == datetime(2026, 3, 8, 5, tzinfo=timezone.utc)


def test_adapt_session_metadata_uses_event_utc_date_when_session_date_is_nat():
    session = FakeSession(
        {},
        date=pd.NaT,
        event={
            "Year": 2026,
            "RoundNumber": 3,
            "EventName": "Australian GP",
            "SessionDateUtc": pd.Timestamp("2026-03-08T05:00:00"),
        },
    )

    frame = adapt_session_metadata(session)

    assert frame.item(0, "session_start_time_utc") == datetime(2026, 3, 8, 5, tzinfo=timezone.utc)


def test_adapt_session_metadata_accepts_round_zero_for_testing_event():
    session = FakeSession(
        {}, event={"Year": 2026, "RoundNumber": 0, "EventName": "Pre-Season Testing"}, name="Testing"
    )

    frame = adapt_session_metadata(session)

    assert frame.item(0, "session_id") == "2026-00-testing"


def test_adapt_session_metadata_uses_naive_utc_fastf1_session_date_not_start_offset():
    # Arrange: the reusable fake models FastF1's Timestamp date and Timedelta start offset.
    session = build_complete_session()

    # Act: normalize public session metadata.
    frame = adapt_session_metadata(session)

    # Assert: only the absolute date becomes the canonical UTC datetime.
    assert session.date.tz is None
    assert frame.item(0, "session_start_time_utc") == datetime(2026, 3, 8, 5, tzinfo=timezone.utc)


def test_adapt_session_metadata_maps_missing_optional_fields_to_typed_nulls():
    session = FakeSession({}, event={"Year": 2026, "RoundNumber": 3}, name="Qualifying")

    frame = adapt_session_metadata(session)

    assert list(frame.schema.items()) == list(SESSION_METADATA_SCHEMA_V2.items())
    assert frame.row(0, named=True) == {
        "session_id": "2026-03-qualifying",
        "year": 2026,
        "round_number": 3,
        "event_name": None,
        "session_name": "Qualifying",
        "session_type": "qualifying",
        "session_mode": "qualifying",
        "session_start_time_utc": None,
    }


@pytest.mark.parametrize(
    ("name", "expected_mode", "expected_id"),
    [
        ("FP1", "practice", "2026-03-practice-1"),
        ("Practice 2", "practice", "2026-03-practice-2"),
        ("FP3", "practice", "2026-03-practice-3"),
        ("Qualifying", "qualifying", "2026-03-qualifying"),
        ("R", "race", "2026-03-race"),
        ("S", "sprint", "2026-03-sprint"),
        ("SQ", "sprint-qualifying", "2026-03-sprint-qualifying"),
        ("Sprint Shootout", "sprint-shootout", "2026-03-sprint-shootout"),
    ],
)
def test_adapt_session_metadata_normalizes_session_mode_without_collapsing_practice_identity(
    name, expected_mode, expected_id
):
    frame = adapt_session_metadata(FakeSession({}, name=name))

    assert frame.item(0, "session_type") == expected_mode
    assert frame.item(0, "session_mode") == expected_mode
    assert frame.item(0, "session_id") == expected_id


@pytest.mark.parametrize("name", ["", "Unknown session"])
def test_adapt_session_metadata_rejects_blank_or_unsupported_session_mode(name):
    with pytest.raises(NormalizationError, match="session mode"):
        adapt_session_metadata(FakeSession({}, name=name))


@pytest.mark.parametrize(
    ("practice_index", "expected_name", "expected_id"),
    [
        (1, "FP1", "2026-03-practice-1"),
        (2, "FP2", "2026-03-practice-2"),
        (3, "FP3", "2026-03-practice-3"),
    ],
)
def test_practice_fixture_session_metadata_is_deterministic(practice_index, expected_name, expected_id):
    # Arrange / Act: normalize the deterministic FP fixture through the public adapter.
    frame = adapt_session_metadata(build_practice_session(practice_index))

    # Assert: practice identity survives without collapsing the run number.
    assert frame.item(0, "session_type") == "practice"
    assert frame.item(0, "session_mode") == "practice"
    assert frame.item(0, "session_name") == expected_name
    assert frame.item(0, "session_id") == expected_id


def test_qualifying_fixture_session_metadata_is_deterministic():
    # Arrange / Act: normalize the deterministic qualifying fixture.
    frame = adapt_session_metadata(build_qualifying_session())

    # Assert: qualifying identity is stable and consistent across its variants.
    assert frame.item(0, "session_type") == "qualifying"
    assert frame.item(0, "session_mode") == "qualifying"
    assert frame.item(0, "session_name") == "Qualifying"
    assert frame.item(0, "session_id") == "2026-03-qualifying"
    cancelled = adapt_session_metadata(build_qualifying_session_with_cancelled_q3())
    assert cancelled.item(0, "session_id") == "2026-03-qualifying"


def test_sprint_fixture_session_metadata_is_deterministic():
    # Arrange / Act: normalize the deterministic sprint fixture.
    frame = adapt_session_metadata(build_sprint_session())

    # Assert: sprint is race-shaped in data but remains mode-distinct in metadata.
    assert frame.item(0, "session_type") == "sprint"
    assert frame.item(0, "session_mode") == "sprint"
    assert frame.item(0, "session_name") == "Sprint"
    assert frame.item(0, "session_id") == "2026-03-sprint"


def test_practice_fixture_rejects_unsupported_run_index():
    with pytest.raises(ValueError, match="practice session"):
        build_practice_session(4)


@pytest.mark.parametrize("event", [{"RoundNumber": 3}, {"Year": 2026}])
def test_adapt_session_metadata_rejects_missing_required_identity(event):
    with pytest.raises(NormalizationError, match="required"):
        adapt_session_metadata(FakeSession({}, event=event))


def test_adapt_drivers_uses_abbreviation_or_car_number_fallback_and_preserves_source_keys():
    session = FakeSession({"044": {"DriverNumber": "044", "TeamName": None}, "1": {
        "DriverNumber": "1", "Abbreviation": " ver ", "FullName": "Max Verstappen", "TeamName": "Red Bull",
        "TeamColor": "3671C6"}})

    frame = adapt_drivers(session)

    assert list(frame.schema.items()) == list(DRIVERS_SCHEMA_V2.items())
    assert frame.to_dicts() == [
        {"session_id": "2026-03-race", "driver_id": "D44", "source_driver_key": "044", "driver_number": 44,
         "full_name": None, "team_name": None, "team_colour": None},
        {"session_id": "2026-03-race", "driver_id": "VER", "source_driver_key": "1", "driver_number": 1,
         "full_name": "Max Verstappen", "team_name": "Red Bull", "team_colour": "3671C6"},
    ]


def test_adapt_drivers_is_invariant_under_source_driver_order_permutation():
    records: dict[str, dict[str, object]] = {
        "44": {"DriverNumber": "44", "Abbreviation": "HAM"},
        "1": {"DriverNumber": "1", "Abbreviation": "VER"},
    }
    frames = []
    for keys in permutations(records):
        session = FakeSession({key: records[key] for key in keys})
        frames.append(adapt_drivers(session))

    assert frames[0].equals(frames[1])


def test_adapt_drivers_rejects_normalized_abbreviation_collisions():
    session = FakeSession({"44": {"DriverNumber": "44", "Abbreviation": "ham"}, "1": {
        "DriverNumber": "1", "Abbreviation": "HAM"}})

    with pytest.raises(NormalizationError, match="collision"):
        adapt_drivers(session)


def test_adapt_drivers_rejects_equivalent_fallback_identifier_collisions():
    session = FakeSession({"44": {"DriverNumber": "44"}, "044": {"DriverNumber": "044"}})

    with pytest.raises(NormalizationError, match="collision"):
        adapt_drivers(session)


def test_adapt_session_metadata_uses_session_info_type_when_name_is_missing():
    # Arrange: FastF1 can expose the session label through session_info instead of name.
    session = FakeSession({}, name=None, session_info={"session_type": "Qualifying"})

    # Act: normalize public session metadata without a top-level name.
    frame = adapt_session_metadata(session)

    # Assert: the session_info label drives mode and identity while name stays null.
    assert frame.item(0, "session_name") is None
    assert frame.item(0, "session_type") == "qualifying"
    assert frame.item(0, "session_mode") == "qualifying"
    assert frame.item(0, "session_id") == "2026-03-qualifying"


def test_adapt_session_metadata_uses_session_info_name_for_practice_identity():
    # Arrange: session_info can name a practice run when top-level name is absent.
    session = FakeSession({}, name=None, session_info={"name": "FP2"})

    # Act: normalize the metadata from the session_info name.
    frame = adapt_session_metadata(session)

    # Assert: the practice ordinal survives into the canonical identity.
    assert frame.item(0, "session_name") == "FP2"
    assert frame.item(0, "session_mode") == "practice"
    assert frame.item(0, "session_id") == "2026-03-practice-2"


def test_adapt_session_metadata_prefers_event_utc_date_over_local_date():
    # Arrange: both UTC and local schedule datetimes are present for the same event.
    session = FakeSession(
        {},
        event={
            "Year": 2026,
            "RoundNumber": 3,
            "EventName": "Australian GP",
            "SessionDateUtc": pd.Timestamp("2026-03-08T05:00:00"),
            "SessionDate": pd.Timestamp("2026-03-08T12:00:00+11:00"),
        },
    )

    # Act: normalize the metadata with both candidates available.
    frame = adapt_session_metadata(session)

    # Assert: the explicit UTC schedule wins over the local-time conversion.
    assert frame.item(0, "session_start_time_utc") == datetime(2026, 3, 8, 5, tzinfo=timezone.utc)


def test_adapt_session_metadata_extracts_year_from_event_date_timestamp():
    # Arrange: FastF1 event records can carry EventDate without a Year column.
    session = FakeSession(
        {},
        event={
            "RoundNumber": 3,
            "EventName": "Australian GP",
            "EventDate": pd.Timestamp("2026-03-08T16:00:00"),
        },
    )

    # Act: normalize the metadata from the timestamp-derived year.
    frame = adapt_session_metadata(session)

    # Assert: the timestamp year becomes the canonical Int16 year.
    assert frame.item(0, "year") == 2026
    assert frame.item(0, "session_id") == "2026-03-race"


def test_adapt_session_metadata_requires_event_attribute():
    # Arrange: an otherwise session-shaped object with no event schedule.
    session = SimpleNamespace(name="Race", date=pd.Timestamp("2026-03-08T05:00:00"))

    # Act / Assert: missing event metadata fails explicitly instead of guessing.
    with pytest.raises(NormalizationError, match="missing required event"):
        adapt_session_metadata(session)
