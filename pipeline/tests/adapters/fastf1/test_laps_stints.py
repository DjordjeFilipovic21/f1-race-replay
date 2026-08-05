from datetime import timedelta
import math
from types import SimpleNamespace

import pandas as pd
import pytest

from fixtures.fake_fastf1_session import (
    build_practice_session,
    build_qualifying_session,
    build_qualifying_session_with_cancelled_q3,
    build_qualifying_session_with_duplicate_partition,
    build_qualifying_session_with_incomplete_partition,
    build_qualifying_session_with_malformed_partition,
    build_qualifying_session_with_missing_splitter,
    build_qualifying_session_with_missing_status,
)
from f1_replay_pipeline.domain.canonical_schema import LAPS_SCHEMA_V2 as LAPS_SCHEMA, STINTS_SCHEMA
from f1_replay_pipeline.adapters.fastf1.laps_stints import adapt_laps, adapt_stints
from f1_replay_pipeline.domain.normalizers import NormalizationError

PRACTICE_DRIVER_IDS = {"44": "HAM", "1": "VER"}


class LapsOnlySession:
    def __init__(self, laps: object) -> None:
        self.laps = laps

    @property
    def car_data(self) -> object:
        raise AssertionError("laps adapter must not read telemetry")

    @property
    def pos_data(self) -> object:
        raise AssertionError("laps adapter must not read telemetry")

    @property
    def telemetry(self) -> object:
        raise AssertionError("laps adapter must not read merged telemetry")


class _IndexedPartition:
    def __init__(self, frame: pd.DataFrame, source_index: object) -> None:
        self._frame = frame
        self._source_index = source_index

    def iterrows(self):  # type: ignore[no-untyped-def]
        for _, row in self._frame.iterrows():
            yield self._source_index, row


class _PartitionedQualifyingLaps:
    def __init__(
        self,
        source: pd.DataFrame,
        partitions: list[object | None],
        source_indexes: list[object] | None = None,
    ) -> None:
        self._source = source
        self._partitions = partitions
        self._source_indexes = source_indexes

    def iterrows(self):  # type: ignore[no-untyped-def]
        for position, (_, row) in enumerate(self._source.iterrows()):
            source_index = self._source.index[position] if self._source_indexes is None else self._source_indexes[position]
            yield source_index, row

    def split_qualifying_sessions(self) -> list[object | None]:
        return self._partitions


def _lap(**changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "DriverNumber": "44", "LapNumber": 1, "Stint": 1,
        "LapStartTime": timedelta(seconds=0), "Time": timedelta(seconds=91.5),
        "LapTime": timedelta(seconds=91.5), "PitInTime": None, "PitOutTime": None,
        "Compound": "SOFT", "TyreLife": 1, "FreshTyre": True,
        "TrackStatus": "1", "IsAccurate": True, "Deleted": False, "DeletedReason": None,
    }
    row.update(changes)
    return row


def test_adapt_laps_emits_explicit_schema_uses_mapping_and_sorts_rows():
    session = LapsOnlySession([_lap(DriverNumber="1", LapNumber=2, LapStartTime=timedelta(seconds=100), Time=timedelta(seconds=190)), _lap()])

    frame = adapt_laps(session, "2026-03-race", {"44": "HAM", "1": "VER"})

    assert list(frame.schema.items()) == list(LAPS_SCHEMA.items())
    assert [(row["driver_id"], row["lap_number"]) for row in frame.to_dicts()] == [("HAM", 1), ("VER", 2)]


def test_adapt_laps_normalizes_optional_values_to_typed_nulls_and_preserves_compounds_as_strings():
    session = LapsOnlySession([_lap(Time=math.nan, LapTime=None, PitInTime=math.inf, PitOutTime=None,
                                    Compound="INTERMEDIATE", TyreLife=math.nan, FreshTyre=None,
                                    TrackStatus=None, IsAccurate=None, Deleted=None)])

    row = adapt_laps(session, "2026-03-race", {"44": "HAM"}).row(0, named=True)

    assert row["compound"] == "INTERMEDIATE"
    assert all(row[field] is None for field in ("lap_end_time_ms", "lap_duration_ms", "pit_in_time_ms", "pit_out_time_ms", "tyre_life", "is_fresh_tyre", "track_status", "is_accurate", "deleted", "deleted_reason"))


@pytest.mark.parametrize("record, message", [
    (_lap(LapStartTime=None), "lap start time is required"),
    (_lap(DriverNumber="99"), "missing canonical driver ID"),
    (_lap(LapNumber=None), "lap number is required"),
])
def test_adapt_laps_rejects_required_identity_and_time_failures(record, message):
    with pytest.raises(NormalizationError, match=message):
        adapt_laps(LapsOnlySession([record]), "2026-03-race", {"44": "HAM"})


def test_adapt_laps_rejects_duplicate_canonical_keys_regardless_of_source_order():
    session = LapsOnlySession([_lap(), _lap(Compound="MEDIUM")])

    with pytest.raises(NormalizationError, match="duplicate canonical lap key"):
        adapt_laps(session, "2026-03-race", {"44": "HAM"})


def test_adapt_laps_is_invariant_under_source_row_permutations_and_returns_typed_empty_frame():
    rows = [_lap(LapNumber=2, LapStartTime=timedelta(seconds=100), Time=timedelta(seconds=190)), _lap(LapNumber=1)]

    ordered = adapt_laps(LapsOnlySession(rows), "2026-03-race", {"44": "HAM"})
    reversed_rows = adapt_laps(LapsOnlySession(list(reversed(rows))), "2026-03-race", {"44": "HAM"})
    empty = adapt_laps(LapsOnlySession([]), "2026-03-race", {"44": "HAM"})

    assert ordered.equals(reversed_rows)
    assert empty.is_empty() and list(empty.schema.items()) == list(LAPS_SCHEMA.items())


def test_adapt_stints_derives_contiguous_summaries_from_ordered_laps():
    session = LapsOnlySession([
        _lap(LapNumber=3, Stint=2, LapStartTime=timedelta(seconds=190), Time=timedelta(seconds=280), Compound="MEDIUM", TyreLife=2, FreshTyre=False),
        _lap(LapNumber=1, Stint=1, LapStartTime=timedelta(seconds=0), Time=timedelta(seconds=90)),
        _lap(LapNumber=2, Stint=2, LapStartTime=timedelta(seconds=100), Time=timedelta(seconds=190), Compound="MEDIUM", TyreLife=1, FreshTyre=True),
    ])

    frame = adapt_stints(session, "2026-03-race", {"44": "HAM"})

    assert list(frame.schema.items()) == list(STINTS_SCHEMA.items())
    assert frame.to_dicts() == [
        {"session_id": "2026-03-race", "driver_id": "HAM", "stint_number": 1, "start_lap_number": 1, "end_lap_number": 1, "start_time_ms": 0, "end_time_ms": 90_000, "compound": "SOFT", "tyre_life_at_start": 1, "is_fresh_tyre": True},
        {"session_id": "2026-03-race", "driver_id": "HAM", "stint_number": 2, "start_lap_number": 2, "end_lap_number": 3, "start_time_ms": 100_000, "end_time_ms": 280_000, "compound": "MEDIUM", "tyre_life_at_start": 1, "is_fresh_tyre": True},
    ]


def test_adapt_stints_skips_unassigned_stints_and_rejects_noncontiguous_reuse():
    skipped = adapt_stints(LapsOnlySession([_lap(Stint=None)]), "2026-03-race", {"44": "HAM"})
    assert skipped.is_empty()
    session = LapsOnlySession([_lap(LapNumber=1, Stint=1), _lap(LapNumber=2, Stint=2), _lap(LapNumber=3, Stint=1)])
    with pytest.raises(NormalizationError, match="not contiguous"):
        adapt_stints(session, "2026-03-race", {"44": "HAM"})


def test_adapt_stints_sorts_by_canonical_stint_key_when_source_labels_are_nonmonotonic():
    session = LapsOnlySession([_lap(LapNumber=1, Stint=2), _lap(LapNumber=2, Stint=1)])

    frame = adapt_stints(session, "2026-03-race", {"44": "HAM"})

    assert [row["stint_number"] for row in frame.to_dicts()] == [1, 2]


def test_adapter_accepts_dataframe_like_records_without_telemetry_operations():
    class Frame:
        def to_dict(self, orient: str):
            assert orient == "records"
            return [_lap()]

    frame = adapt_laps(LapsOnlySession(Frame()), "2026-03-race", {"44": "HAM"})
    assert frame.height == 1


def test_adapter_rejects_an_unvalidated_driver_mapping_value():
    with pytest.raises(NormalizationError, match="invalid canonical driver ID"):
        adapt_laps(LapsOnlySession([_lap()]), "2026-03-race", {"44": "Hamilton"})


# ── Sector normalization positive tests ──


def test_adapt_laps_normalizes_all_sector_durations_from_timedelta_to_ms():
    """All three sector durations populated become Int64 millisecond values."""
    session = LapsOnlySession([_lap(
        Sector1Time=timedelta(seconds=28, microseconds=500_000),
        Sector2Time=timedelta(seconds=35, microseconds=200_000),
        Sector3Time=timedelta(seconds=27, microseconds=800_000),
    )])

    frame = adapt_laps(session, "2026-03-race", {"44": "HAM"})
    row = frame.row(0, named=True)

    assert row["sector_1_duration_ms"] == 28_500
    assert row["sector_2_duration_ms"] == 35_200
    assert row["sector_3_duration_ms"] == 27_800


def test_adapt_laps_normalizes_all_sector_session_timestamps_from_timedelta_to_ms():
    """All three sector session timestamps populated become Int64 millisecond values."""
    session = LapsOnlySession([_lap(
        Sector1SessionTime=timedelta(seconds=28, microseconds=500_000),
        Sector2SessionTime=timedelta(seconds=63, microseconds=700_000),
        Sector3SessionTime=timedelta(seconds=91, microseconds=500_000),
    )])

    frame = adapt_laps(session, "2026-03-race", {"44": "HAM"})
    row = frame.row(0, named=True)

    assert row["sector_1_session_time_ms"] == 28_500
    assert row["sector_2_session_time_ms"] == 63_700
    assert row["sector_3_session_time_ms"] == 91_500


def test_adapt_laps_rounds_sector_times_half_up_at_millisecond_boundary():
    """Sector duration times round half-up at the millisecond boundary."""
    session = LapsOnlySession([_lap(
        Sector1Time=timedelta(seconds=12, microseconds=499_500),
        Sector2Time=timedelta(seconds=12, microseconds=499_499),
    )])

    frame = adapt_laps(session, "2026-03-race", {"44": "HAM"})
    row = frame.row(0, named=True)

    assert row["sector_1_duration_ms"] == 12_500
    assert row["sector_2_duration_ms"] == 12_499


# ── Sector normalization negative / edge-case tests ──


def test_adapt_laps_sets_missing_sector_fields_to_null():
    """Subset of sector fields omitted from the source record become null."""
    session = LapsOnlySession([_lap(
        Sector1Time=timedelta(seconds=28, microseconds=500_000),
        Sector2Time=None,
        # Sector3Time deliberately omitted from the record
        Sector1SessionTime=timedelta(seconds=28, microseconds=500_000),
        Sector2SessionTime=None,
        Sector3SessionTime=timedelta(seconds=91, microseconds=500_000),
    )])

    frame = adapt_laps(session, "2026-03-race", {"44": "HAM"})
    row = frame.row(0, named=True)

    assert row["sector_1_duration_ms"] == 28_500
    assert row["sector_2_duration_ms"] is None
    assert row["sector_3_duration_ms"] is None
    assert row["sector_1_session_time_ms"] == 28_500
    assert row["sector_2_session_time_ms"] is None
    assert row["sector_3_session_time_ms"] == 91_500


def test_adapt_laps_converts_nonfinite_and_nat_sector_values_to_null():
    """NaN, inf, and NaT sector values become null instead of raising or storing garbage."""
    session = LapsOnlySession([_lap(
        Sector1Time=math.nan,
        Sector2Time=math.inf,
        Sector3Time=pd.NaT,
        Sector1SessionTime=math.nan,
        Sector2SessionTime=-math.inf,
        Sector3SessionTime=pd.NaT,
    )])

    frame = adapt_laps(session, "2026-03-race", {"44": "HAM"})
    row = frame.row(0, named=True)

    assert row["sector_1_duration_ms"] is None
    assert row["sector_2_duration_ms"] is None
    assert row["sector_3_duration_ms"] is None
    assert row["sector_1_session_time_ms"] is None
    assert row["sector_2_session_time_ms"] is None
    assert row["sector_3_session_time_ms"] is None


def test_adapt_laps_rejects_negative_sector_duration():
    """A negative sector duration raises NormalizationError."""
    session = LapsOnlySession([_lap(
        Sector1Time=timedelta(seconds=-1),
    )])

    with pytest.raises(NormalizationError, match="invalid sector 1 duration"):
        adapt_laps(session, "2026-03-race", {"44": "HAM"})


def test_adapt_laps_rejects_negative_sector_session_timestamp():
    """A negative sector session timestamp raises NormalizationError."""
    session = LapsOnlySession([_lap(
        Sector1SessionTime=timedelta(seconds=-1),
    )])

    with pytest.raises(NormalizationError, match="invalid sector 1 session time"):
        adapt_laps(session, "2026-03-race", {"44": "HAM"})


def test_adapt_laps_rejects_non_numeric_sector_value():
    """A non-numeric sector value (e.g. a string) raises NormalizationError."""
    session = LapsOnlySession([_lap(
        Sector1Time="not a time",
    )])

    with pytest.raises(NormalizationError, match="invalid sector 1 duration"):
        adapt_laps(session, "2026-03-race", {"44": "HAM"})


def test_adapt_laps_sector_values_are_deterministic_regardless_of_input_order():
    """Sector normalization produces identical results regardless of input record order."""
    rows = [
        _lap(LapNumber=2, LapStartTime=timedelta(seconds=100), Time=timedelta(seconds=190),
             Sector1Time=timedelta(seconds=28, microseconds=500_000),
             Sector2Time=timedelta(seconds=35, microseconds=200_000),
             Sector3Time=timedelta(seconds=27, microseconds=800_000)),
        _lap(LapNumber=1, LapStartTime=timedelta(seconds=0), Time=timedelta(seconds=90),
             Sector1Time=timedelta(seconds=29, microseconds=100_000),
             Sector2Time=timedelta(seconds=34, microseconds=900_000),
             Sector3Time=timedelta(seconds=28, microseconds=300_000)),
    ]

    ordered = adapt_laps(LapsOnlySession(rows), "2026-03-race", {"44": "HAM"})
    reversed_frame = adapt_laps(LapsOnlySession(list(reversed(rows))), "2026-03-race", {"44": "HAM"})

    assert ordered.equals(reversed_frame)

    ordered_rows = ordered.sort("lap_number").to_dicts()
    assert ordered_rows[0]["sector_1_duration_ms"] == 29_100
    assert ordered_rows[0]["sector_2_duration_ms"] == 34_900
    assert ordered_rows[0]["sector_3_duration_ms"] == 28_300
    assert ordered_rows[1]["sector_1_duration_ms"] == 28_500
    assert ordered_rows[1]["sector_2_duration_ms"] == 35_200
    assert ordered_rows[1]["sector_3_duration_ms"] == 27_800


# ── Practice and Qualifying fixture laps ──


def test_practice_fixture_laps_normalize_with_valid_times_and_compounds():
    # Arrange: the FP fixture keeps valid timed laps and no per-lap live order.
    session = build_practice_session(1)

    # Act: normalize the practice laps through the public adapter.
    frame = adapt_laps(session, "2026-03-practice-1", PRACTICE_DRIVER_IDS)

    # Assert: both drivers keep their valid lap durations and compounds.
    assert list(frame.schema.items()) == list(LAPS_SCHEMA.items())
    assert frame.select("driver_id", "lap_duration_ms", "compound").to_dicts() == [
        {"driver_id": "HAM", "lap_duration_ms": 92_500, "compound": "SOFT"},
        {"driver_id": "VER", "lap_duration_ms": 93_200, "compound": "MEDIUM"},
    ]


@pytest.mark.parametrize(
    "builder",
    [build_qualifying_session, build_qualifying_session_with_cancelled_q3],
)
def test_qualifying_fixture_laps_normalize_with_valid_times(builder):
    # Arrange: qualifying flying laps remain valid in every qualifying fixture variant.
    session = builder()

    # Act: normalize the qualifying laps through the public adapter.
    frame = adapt_laps(session, "2026-03-qualifying", PRACTICE_DRIVER_IDS)

    # Assert: both drivers keep their deterministic flying-lap durations.
    expected = [
        {"driver_id": "HAM", "lap_duration_ms": 105_123, "qualifying_phase": "Q1"},
        {"driver_id": "VER", "lap_duration_ms": 105_200, "qualifying_phase": "Q2"},
    ]
    if builder is build_qualifying_session:
        expected.insert(1, {"driver_id": "HAM", "lap_duration_ms": 103_999, "qualifying_phase": "Q3"})
    assert frame.select("driver_id", "lap_duration_ms", "qualifying_phase").to_dicts() == expected


def test_qualifying_split_is_authoritative_and_phase_order_is_not_source_order():
    frame = adapt_laps(build_qualifying_session(), "2026-03-qualifying", PRACTICE_DRIVER_IDS)

    assert frame.select("lap_number", "qualifying_phase").to_dicts() == [
        {"lap_number": 1, "qualifying_phase": "Q1"},
        {"lap_number": 2, "qualifying_phase": "Q3"},
        {"lap_number": 1, "qualifying_phase": "Q2"},
    ]


def test_non_qualifying_sessions_do_not_call_or_fabricate_qualifying_phases():
    frame = adapt_laps(build_practice_session(), "2026-03-practice-1", PRACTICE_DRIVER_IDS)

    assert frame.get_column("qualifying_phase").to_list() == [None, None]


def test_cancelled_q3_without_eligible_laps_preserves_valid_phase_assignment():
    frame = adapt_laps(
        build_qualifying_session_with_cancelled_q3(), "2026-03-qualifying", PRACTICE_DRIVER_IDS
    )

    assert frame.get_column("qualifying_phase").to_list() == ["Q1", "Q2"]


def test_missing_session_status_is_an_authoritative_split_failure():
    with pytest.raises(NormalizationError, match="loaded session status data"):
        adapt_laps(
            build_qualifying_session_with_missing_status(),
            "2026-03-qualifying",
            PRACTICE_DRIVER_IDS,
        )


def test_missing_splitter_is_an_authoritative_split_failure():
    with pytest.raises(NormalizationError, match="split_qualifying_sessions"):
        adapt_laps(
            build_qualifying_session_with_missing_splitter(),
            "2026-03-qualifying",
            PRACTICE_DRIVER_IDS,
        )


def test_incomplete_qualifying_partition_is_rejected():
    with pytest.raises(NormalizationError, match="assignment is incomplete"):
        adapt_laps(
            build_qualifying_session_with_incomplete_partition(),
            "2026-03-qualifying",
            PRACTICE_DRIVER_IDS,
        )


def test_malformed_qualifying_partition_is_rejected():
    with pytest.raises(NormalizationError, match="iterable mapping records"):
        adapt_laps(
            build_qualifying_session_with_malformed_partition(),
            "2026-03-qualifying",
            PRACTICE_DRIVER_IDS,
        )


def test_duplicate_qualifying_partition_rows_are_rejected():
    with pytest.raises(NormalizationError, match="duplicate lap"):
        adapt_laps(
            build_qualifying_session_with_duplicate_partition(),
            "2026-03-qualifying",
            PRACTICE_DRIVER_IDS,
        )


@pytest.mark.parametrize(
    "source_indexes",
    ([0, 0, 2], [None, 1, 2], [[], 1, 2]),
    ids=["duplicate", "missing", "unhashable"],
)
def test_qualifying_assignment_rejects_invalid_authoritative_source_indexes(source_indexes):
    session = build_qualifying_session()
    partitions = list(session.laps.split_qualifying_sessions())
    session.laps = _PartitionedQualifyingLaps(session.laps, partitions, source_indexes)

    with pytest.raises(NormalizationError, match="unique authoritative source lap indices"):
        adapt_laps(session, "2026-03-qualifying", PRACTICE_DRIVER_IDS)


@pytest.mark.parametrize("partition_index", [99, None, []], ids=["unknown", "missing", "unhashable"])
def test_qualifying_assignment_rejects_invalid_partition_source_indexes(partition_index):
    session = build_qualifying_session()
    partitions = list(session.laps.split_qualifying_sessions())
    assert isinstance(partitions[0], pd.DataFrame)
    partitions[0] = _IndexedPartition(partitions[0], partition_index)
    session.laps = _PartitionedQualifyingLaps(session.laps, partitions)

    with pytest.raises(NormalizationError, match="unknown or mismatched source lap index"):
        adapt_laps(session, "2026-03-qualifying", PRACTICE_DRIVER_IDS)


def test_qualifying_assignment_rejects_record_mismatch_at_a_known_source_index():
    session = build_qualifying_session()
    partitions = list(session.laps.split_qualifying_sessions())
    assert isinstance(partitions[0], pd.DataFrame)
    mismatched = partitions[0].copy()
    mismatched.loc[:, "LapNumber"] = 99
    partitions[0] = mismatched
    session.laps = _PartitionedQualifyingLaps(session.laps, partitions)

    with pytest.raises(NormalizationError, match="unknown or mismatched source lap index"):
        adapt_laps(session, "2026-03-qualifying", PRACTICE_DRIVER_IDS)


def test_practice_fixture_stints_stay_empty_without_stint_labels():
    # Arrange / Act: the practice fixture carries no source Stint labels.
    frame = adapt_stints(build_practice_session(1), "2026-03-practice-1", PRACTICE_DRIVER_IDS)

    # Assert: no stint summaries are fabricated from a stintless practice source.
    assert frame.is_empty()
    assert list(frame.schema.items()) == list(STINTS_SCHEMA.items())


def test_adapt_laps_returns_typed_empty_frame_when_session_laps_is_none():
    # Arrange: a loaded session may expose no laps table at all.
    session = LapsOnlySession(None)

    # Act: normalize the missing table through the public adapter.
    frame = adapt_laps(session, "2026-03-practice-1", PRACTICE_DRIVER_IDS)

    # Assert: an absent table becomes a typed empty frame, not a fabricated lap.
    assert frame.is_empty()
    assert list(frame.schema.items()) == list(LAPS_SCHEMA.items())


def test_adapt_laps_requires_laps_attribute():
    # Arrange: a session-shaped object without a laps attribute at all.
    session = SimpleNamespace()

    # Act / Assert: the adapter fails explicitly instead of fabricating rows.
    with pytest.raises(NormalizationError, match="missing required laps"):
        adapt_laps(session, "2026-03-practice-1", PRACTICE_DRIVER_IDS)


def test_adapt_laps_rejects_negative_lap_start_time():
    # Arrange: a lap whose start time precedes the session origin.
    session = LapsOnlySession([_lap(LapStartTime=timedelta(seconds=-1))])

    # Act / Assert: negative session-relative timing is rejected explicitly.
    with pytest.raises(NormalizationError, match="invalid lap start time"):
        adapt_laps(session, "2026-03-race", {"44": "HAM"})


def test_qualifying_fixture_laps_keep_absent_optional_flags_null():
    # Arrange / Act: normalize the deterministic qualifying fixture laps.
    frame = adapt_laps(build_qualifying_session(), "2026-03-qualifying", PRACTICE_DRIVER_IDS)

    # Assert: optional FastF1 flags absent from the fixture stay typed null rather
    # than being invented, matching FastF1's nullable sector/deletion semantics.
    selected = frame.select(
        "is_accurate", "deleted", "deleted_reason",
        "sector_1_duration_ms", "sector_2_duration_ms", "sector_3_duration_ms",
    )
    assert selected.null_count().row(0) == (3, 3, 3, 3, 3, 3)
