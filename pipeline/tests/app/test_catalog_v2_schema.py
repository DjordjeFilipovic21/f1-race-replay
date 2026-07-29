"""Contract tests for the pure catalog v2 models and helpers."""

from __future__ import annotations

import json

import pytest

from f1_replay_pipeline.app.catalog_v2_schema import (
    CATALOG_SCHEMA_VERSION,
    CatalogV2Payload,
    CatalogV2RaceRecord,
    CatalogV2SessionRecord,
    session_code_from_generation_id,
    strip_generation_suffix,
)


def test_catalog_serialization_is_explicit_and_deterministic() -> None:
    session = CatalogV2SessionRecord("r", "Race", "2024-round-05-r", "2024-round-05-r", "generated", True, "canonical/x", "browser/x")
    payload = CatalogV2Payload(2024, (CatalogV2RaceRecord("2024-round-05", 5, "Race", (session,)),))
    encoded = payload.to_json_bytes()

    assert json.loads(encoded) == {
        "schemaVersion": CATALOG_SCHEMA_VERSION,
        "year": 2024,
        "atomicAcrossRaces": False,
        "races": [{
            "race_id": "2024-round-05",
            "round_number": 5,
            "event_name": "Race",
            "sessions": [session.to_dict()],
        }],
    }
    assert encoded.endswith(b"\n")


def test_session_identity_helpers_handle_force_and_browser_successors() -> None:
    assert strip_generation_suffix("2024-round-05-r-force-2") == "2024-round-05-r"
    assert strip_generation_suffix("2024-round-05-r-browser-3") == "2024-round-05-r"
    assert session_code_from_generation_id("2024-round-05-r-force-2", "2024-round-05") == "r"
    assert session_code_from_generation_id(
        "2024-round-08-r", "2024-round-08-monaco-grand-prix",
    ) == "r"
    with pytest.raises(ValueError):
        session_code_from_generation_id("2023-round-08-r", "2024-round-08-monaco-grand-prix")


def test_catalog_models_reject_malformed_boundaries_and_duplicate_sessions() -> None:
    with pytest.raises(ValueError):
        CatalogV2Payload(2024, (), schema_version=1)
    session = CatalogV2SessionRecord("R", "Race", None, None, "failed", False, None, None)
    with pytest.raises(ValueError):
        CatalogV2RaceRecord("2024-round-08", 8, "Race", (session, session))
    with pytest.raises(ValueError):
        CatalogV2SessionRecord("../r", "Race", None, None, "failed", False, None, None)
