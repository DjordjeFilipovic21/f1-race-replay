"""Focused tests for the optional issued-penalty sidecar builder."""

import polars as pl

from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    CanonicalGenerationSnapshot,
)
from f1_replay_pipeline.delivery.browser.browser_penalty_sidecar import (
    build_penalty_sidecar,
)


def _snapshot(messages: list[dict[str, object]]) -> CanonicalGenerationSnapshot:
    return CanonicalGenerationSnapshot(
        "generation-one",
        "a" * 64,
        {
            "session_metadata": pl.DataFrame([{"session_id": "brazil-2024"}]),
            "drivers": pl.DataFrame([{"driver_id": "BEA", "driver_number": 50}]),
            "race_control_messages": pl.DataFrame(messages),
        },
    )


def test_builder_resolves_null_driver_id_and_preserves_issued_penalty_details() -> None:
    raw = "FIA STEWARDS: 10 SECOND TIME PENALTY FOR CAR 50 (BEA) - CAUSING A COLLISION"

    sidecar = build_penalty_sidecar(_snapshot([{
        "session_time_ms": 90_000,
        "message": raw,
        "driver_id": None,
        "lap_number": 9,
    }]))

    assert sidecar.penalty_issuances[0].as_dict() == {
        "driverId": "BEA",
        "sessionTimeMs": 90_000,
        "penaltyType": "TIME_10S",
        "reason": "CAUSING A COLLISION",
        "rawMessage": raw,
        "lapNumber": 9,
    }


def test_builder_returns_empty_sidecar_when_no_penalty_is_issued() -> None:
    sidecar = build_penalty_sidecar(_snapshot([{
        "session_time_ms": 100,
        "message": "TRACK CLEAR",
        "driver_id": None,
        "lap_number": None,
    }]))

    assert sidecar.penalty_issuances == ()
