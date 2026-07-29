"""Unit tests for issued-penalty models and race-control parsing."""

from dataclasses import FrozenInstanceError

import pytest

from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    BrowserPenaltyIssuance,
    BrowserPenaltySidecar,
)
from f1_replay_pipeline.delivery.browser.browser_penalty_issuance import (
    parse_race_control_penalties,
)


def test_penalty_issuance_is_frozen_and_serializes_optional_lap_number() -> None:
    issuance = BrowserPenaltyIssuance(
        "BEA", 120_000, "TIME_10S", "CAUSING A COLLISION",
        "FIA STEWARDS: 10 SECOND TIME PENALTY FOR CAR 50 (BEA) - CAUSING A COLLISION",
        lap_number=9,
    )

    assert issuance.as_dict() == {
        "driverId": "BEA", "sessionTimeMs": 120_000, "penaltyType": "TIME_10S",
        "reason": "CAUSING A COLLISION", "rawMessage": issuance.raw_message,
        "lapNumber": 9,
    }
    with pytest.raises(FrozenInstanceError):
        issuance.driver_id = "HAM"  # type: ignore[misc]


def test_penalty_sidecar_serializes_tuple_of_issuances() -> None:
    issuance = BrowserPenaltyIssuance("HAM", 1000, "DRIVE_THROUGH", "PIT ENTRY", "message")
    sidecar = BrowserPenaltySidecar("brazil-2024", (issuance,))

    assert sidecar.penalty_issuances == (issuance,)
    assert sidecar.as_dict() == {
        "contractVersion": "v1", "fixtureId": "brazil-2024",
        "penaltyIssuances": [issuance.as_dict()],
    }


def test_parser_extracts_standard_issuance_and_preserves_raw_message() -> None:
    raw = "FIA STEWARDS: 5 SECOND TIME PENALTY FOR CAR 44 (HAM) - CAUSING A COLLISION"

    result = parse_race_control_penalties(
        [{"session_time_ms": 12_500, "message": raw, "driver_id": "HAM", "lap_number": 4}]
    )

    assert len(result) == 1
    assert result[0].driver_id == "HAM"
    assert result[0].penalty_type == "TIME_5S"
    assert result[0].reason == "CAUSING A COLLISION"
    assert result[0].raw_message == raw
    assert result[0].lap_number == 4


def test_parser_returns_no_issuances_for_a_session_without_penalties() -> None:
    assert parse_race_control_penalties([
        {"session_time_ms": 100, "message": "TRACK CLEAR"},
        {"session_time_ms": 200, "message": "DRS ENABLED"},
    ]) == ()


def test_parser_resolves_null_driver_id_by_car_number_and_abbreviation() -> None:
    messages = [{
        "session_time_ms": 90_000,
        "message": "FIA STEWARDS: 10 SECOND TIME PENALTY FOR CAR 50 (BEA) - CAUSING A COLLISION",
        "driver_id": None,
        "lap_number": 9,
    }]
    metadata = [{"driver_id": "BEA", "driver_number": 50}]

    result = parse_race_control_penalties(messages, metadata)

    assert result[0].driver_id == "BEA"
    assert result[0].lap_number == 9


def test_parser_resolves_null_driver_id_by_abbreviation_when_number_is_unknown() -> None:
    result = parse_race_control_penalties(
        [{
            "session_time_ms": 100,
            "message": "FIA STEWARDS: 10 SECOND TIME PENALTY FOR CAR 81 (PIA) - CAUSING A COLLISION",
            "driver_id": None,
        }],
        [{"driver_id": "PIA", "driver_number": 81}],
    )

    assert result[0].driver_id == "PIA"


def test_parser_handles_brazil_2024_lap_9_and_lap_29_patterns() -> None:
    messages = [
        {
            "session_time_ms": 9_000,
            "message": "FIA STEWARDS: 10 SECOND TIME PENALTY FOR CAR 50 (BEA) - CAUSING A COLLISION",
            "driver_id": None,
            "lap_number": 9,
        },
        {
            "session_time_ms": 29_000,
            "message": "FIA STEWARDS: 10 SECOND TIME PENALTY FOR CAR 81 (PIA) - CAUSING A COLLISION",
            "driver_id": None,
            "lap_number": 29,
        },
    ]
    metadata = [
        {"driver_id": "PIA", "driver_number": 81},
        {"driver_id": "BEA", "driver_number": 50},
    ]

    result = parse_race_control_penalties(messages, metadata)

    assert [(item.driver_id, item.lap_number) for item in result] == [("BEA", 9), ("PIA", 29)]


def test_parser_rejects_investigations_pending_decisions_and_non_penalties() -> None:
    messages = [
        {"session_time_ms": 1, "message": "FIA STEWARDS: INCIDENT UNDER INVESTIGATION FOR CAR 44 (HAM)"},
        {"session_time_ms": 2, "message": "FIA STEWARDS: PENALTY DECISION PENDING FOR CAR 44 (HAM)"},
        {"session_time_ms": 3, "message": "FIA STEWARDS: CAR 44 (HAM) NOTED"},
        {"session_time_ms": 4, "message": "TRACK CLEAR"},
    ]

    assert parse_race_control_penalties(messages, [{"driver_id": "HAM", "driver_number": 44}]) == ()


def test_parser_supports_grid_and_drive_through_penalty_types() -> None:
    messages = [
        {"session_time_ms": 100, "message": "FIA STEWARDS: 3 PLACES GRID PENALTY FOR CAR 1 (VER) - BLOCKING"},
        {"session_time_ms": 200, "message": "FIA STEWARDS: DRIVE THROUGH PENALTY FOR CAR 44 (HAM) - SPEEDING"},
    ]

    result = parse_race_control_penalties(messages, {"1": "VER", "44": "HAM"})

    assert [item.penalty_type for item in result] == ["GRID_3PLACES", "DRIVE_THROUGH"]


def test_parser_does_not_infer_served_state_from_other_fields() -> None:
    result = parse_race_control_penalties([{
        "session_time_ms": 100,
        "message": "FIA STEWARDS: DRIVE THROUGH PENALTY FOR CAR 44 (HAM) - SPEEDING",
        "pit_duration_ms": 10_000,
    }], {"44": "HAM"})

    assert result[0].as_dict().keys() == {
        "driverId", "sessionTimeMs", "penaltyType", "reason", "rawMessage",
    }
