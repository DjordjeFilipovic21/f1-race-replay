import copy
import json
import math
from pathlib import Path
from typing import cast

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry, Resource

from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    BROWSER_LAP_SECTOR_SIDECAR_SCHEMA_ID,
    PENALTY_SIDECAR_SCHEMA_ID,
    PIT_LOSS_ESTIMATE_METHOD,
    PIT_LOSS_ESTIMATE_SIDECAR_FILENAME,
    PIT_LOSS_ESTIMATE_SIDECAR_SCHEMA_ID,
    PIT_LOSS_MODEL_SCHEMA_ID,
    STINT_SUMMARY_SCHEMA_ID,
    TIMELINE_SUMMARY_SCHEMA_ID,
    WEATHER_SIDECAR_SCHEMA_ID,
    BrowserLapSectorSidecarReference,
    BrowserManifest,
    BrowserPenaltySidecarReference,
    BrowserPitLossEstimateSidecarReference,
    BrowserStintSummaryReference,
    BrowserTimelineSummaryReference,
    BrowserWeatherSidecarReference,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = REPO_ROOT / "contracts" / "replay-data" / "v2"
SCHEMA_ROOT = CONTRACT_ROOT / "schemas"
FIXTURE_ROOT = CONTRACT_ROOT / "fixtures" / "deterministic-race"

V2_SCHEMA_ROOT = REPO_ROOT / "contracts" / "replay-data" / "v2" / "schemas"
V2_MANIFEST_SCHEMA_ID = "urn:f1-cache-replay:schema:replay-data:v2:manifest"
V2_QUALIFYING_SUMMARY_SCHEMA_ID = "urn:f1-cache-replay:schema:replay-data:v2:qualifying-summary"

CONTINUOUS_DRIVER_FIELDS = {
    "x",
    "y",
    "trackDistanceMeters",
    "speed",
    "throttle",
    "brake",
    "gapToLeaderMs",
}
STEP_DRIVER_FIELDS = {
    "lap",
    "position",
    "gear",
    "drs",
    "tyreCompound",
    "status",
    "isInPitLane",
}
STEP_SAMPLE_FIELDS = {"leaderboardOrder", "trackStatusCode", "weatherState"}
DRIVER_COLUMN_FIELDS = CONTINUOUS_DRIVER_FIELDS | STEP_DRIVER_FIELDS


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_contract_bundle():
    manifest = load_json(FIXTURE_ROOT / "manifest.json")
    track_assets = load_json(FIXTURE_ROOT / manifest["trackAssets"]["path"])
    chunks = {
        chunk_ref["path"]: load_json(FIXTURE_ROOT / chunk_ref["path"])
        for chunk_ref in manifest["chunks"]
    }
    golden = load_json(FIXTURE_ROOT / manifest["goldenSnapshots"]["path"])
    schemas = {
        "manifest": load_json(SCHEMA_ROOT / "manifest.schema.json"),
        "chunk": load_json(SCHEMA_ROOT / "chunk.schema.json"),
        "trackAssets": load_json(SCHEMA_ROOT / "track-assets.schema.json"),
        "timelineSummary": load_json(SCHEMA_ROOT / "timeline-summary.schema.json"),
        "browserLapSectorSidecar": load_json(SCHEMA_ROOT / "browser-lap-sector-sidecar.schema.json"),
        "penaltySidecar": load_json(SCHEMA_ROOT / "penalty-sidecar.schema.json"),
        "stintSummary": load_json(SCHEMA_ROOT / "stint-summary.schema.json"),
        "pitLossModel": load_json(SCHEMA_ROOT / "pit-loss-model.schema.json"),
        "weatherSidecar": load_json(SCHEMA_ROOT / "weather-sidecar.schema.json"),
        "pitLossEstimateSidecar": load_json(SCHEMA_ROOT / "pit-loss-estimate-sidecar.schema.json"),
    }
    return {
        "manifest": manifest,
        "track_assets": track_assets,
        "chunks": chunks,
        "golden": golden,
        "schemas": schemas,
    }


@pytest.fixture(scope="module")
def contract_bundle():
    return load_contract_bundle()


@pytest.fixture(scope="module")
def schema_registry(contract_bundle):
    registry = Registry()

    for schema in contract_bundle["schemas"].values():
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))

    return registry


@pytest.fixture(scope="module")
def v2_schema_registry():
    registry = Registry()

    for name in ("manifest", "qualifying-summary", "browser-lap-sector-sidecar"):
        schema = load_json(V2_SCHEMA_ROOT / f"{name}.schema.json")
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))

    return registry


def validate_instance(schema, instance, registry):
    validator = Draft202012Validator(
        schema,
        registry=registry,
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )
    validator.validate(instance)


def assert_manifest_semantics(bundle):
    manifest = bundle["manifest"]
    chunks = bundle["chunks"]
    track_assets = bundle["track_assets"]

    assert manifest["contractVersion"] == "v2"
    assert manifest["fixtureId"] == track_assets["fixtureId"]
    manifest_driver_ids = {driver["id"] for driver in manifest["drivers"]}
    assert manifest_driver_ids

    previous_ref = None
    for chunk_ref in manifest["chunks"]:
        chunk = chunks[chunk_ref["path"]]

        assert chunk_ref["sequence"] == chunk["sequence"]
        assert chunk_ref["startMs"] == chunk["startMs"]
        assert chunk_ref["endMs"] == chunk["endMs"]
        assert manifest["fixtureId"] == chunk["fixtureId"]

        time_ms = chunk["timeMs"]
        authoritative_start_index = chunk["authoritativeStartIndex"]
        assert time_ms == sorted(time_ms)
        assert len(time_ms) == len(set(time_ms))
        assert 0 <= authoritative_start_index < len(time_ms)
        assert set(chunk["drivers"]) == manifest_driver_ids

        for driver_columns in chunk["drivers"].values():
            assert set(driver_columns) == DRIVER_COLUMN_FIELDS
            for column in driver_columns.values():
                assert len(column) == len(time_ms)

        for global_column in STEP_SAMPLE_FIELDS:
            assert len(chunk[global_column]) == len(time_ms)

        for time_index, session_time_ms in enumerate(time_ms):
            if time_index >= authoritative_start_index:
                assert chunk["startMs"] <= session_time_ms < chunk["endMs"]
            else:
                overlap = chunk["overlap"]
                assert overlap["kind"] == "handoff"
                assert overlap["range"]["startMs"] <= session_time_ms < overlap["range"]["endMs"]
                assert session_time_ms < chunk["startMs"]

            assert set(chunk["leaderboardOrder"][time_index]) == manifest_driver_ids

        for event in chunk["events"]:
            if event.get("driverId") is not None:
                assert event["driverId"] in manifest_driver_ids
            assert chunk["startMs"] <= event["sessionTimeMs"] < chunk["endMs"]

        if previous_ref is not None:
            assert previous_ref["endMs"] == chunk_ref["startMs"]
            assert chunk_ref["overlapWithPreviousMs"] == (
                chunk["overlap"]["range"]["endMs"] - chunk["overlap"]["range"]["startMs"]
            )
            assert chunk["overlap"]["authoritativeFromMs"] == chunk_ref["startMs"]
            assert time_ms[authoritative_start_index] == chunk["startMs"]

        previous_ref = chunk_ref

    for marker in track_assets["distanceMarkersMeters"]:
        assert marker <= track_assets["circuitLengthMeters"]

    for zone in track_assets["drsZones"]:
        assert zone["startMeters"] < zone["endMeters"] <= track_assets["circuitLengthMeters"]


def read_sample(chunk, time_index):
    return {
        "sessionTimeMs": chunk["timeMs"][time_index],
        "leaderboardOrder": copy.deepcopy(chunk["leaderboardOrder"][time_index]),
        "trackStatusCode": chunk["trackStatusCode"][time_index],
        "weatherState": chunk["weatherState"][time_index],
        "drivers": {
            driver_id: {
                field: copy.deepcopy(columns[field][time_index])
                for field in DRIVER_COLUMN_FIELDS
            }
            for driver_id, columns in chunk["drivers"].items()
        },
    }


def event_lookup(bundle):
    events = {}

    for chunk_path, chunk in bundle["chunks"].items():
        for event in chunk["events"]:
            events.setdefault(event["sessionTimeMs"], []).append(copy.deepcopy(event))

    return events


def find_sample(bundle, chunk_path, session_time_ms):
    chunk = bundle["chunks"][chunk_path]
    return read_sample(chunk, chunk["timeMs"].index(session_time_ms))


def interpolate_value(previous_value, next_value, ratio):
    if previous_value is None or next_value is None:
        return None

    return previous_value + ((next_value - previous_value) * ratio)


def resolve_snapshot(bundle, snapshot):
    if snapshot["expectationKind"] == "exact":
        source = snapshot["expectedSource"]
        sample = find_sample(bundle, source["chunkPath"], source["sampleTimeMs"])
        resolved = copy.deepcopy(sample)
    else:
        previous_meta = snapshot["interpolationBounds"]["previous"]
        next_meta = snapshot["interpolationBounds"]["next"]
        previous_sample = find_sample(bundle, previous_meta["chunkPath"], previous_meta["sampleTimeMs"])
        next_sample = find_sample(bundle, next_meta["chunkPath"], next_meta["sampleTimeMs"])
        ratio = (
            (snapshot["sessionTimeMs"] - previous_sample["sessionTimeMs"])
            / (next_sample["sessionTimeMs"] - previous_sample["sessionTimeMs"])
        )

        resolved = {
            "sessionTimeMs": snapshot["sessionTimeMs"],
            "leaderboardOrder": copy.deepcopy(previous_sample["leaderboardOrder"]),
            "trackStatusCode": previous_sample["trackStatusCode"],
            "weatherState": previous_sample["weatherState"],
            "drivers": {},
        }

        for driver_id, previous_driver in previous_sample["drivers"].items():
            next_driver = next_sample["drivers"][driver_id]
            resolved_driver = {}

            for field in CONTINUOUS_DRIVER_FIELDS:
                resolved_driver[field] = interpolate_value(
                    previous_driver.get(field),
                    next_driver.get(field),
                    ratio,
                )

            for field in STEP_DRIVER_FIELDS:
                resolved_driver[field] = copy.deepcopy(previous_driver.get(field))

            resolved["drivers"][driver_id] = resolved_driver

    leaderboard_order = resolved["leaderboardOrder"]
    if leaderboard_order:
        leader = resolved["drivers"].get(leaderboard_order[0])
        if leader is not None and leader.get("position") == 1:
            leader["gapToLeaderMs"] = 0

    resolved["events"] = copy.deepcopy(event_lookup(bundle).get(snapshot["sessionTimeMs"], []))
    return resolved


def assert_snapshot_matches_expected(bundle, snapshot):
    actual = resolve_snapshot(bundle, snapshot)

    assert actual["leaderboardOrder"] == snapshot["leaderboardOrder"]
    assert actual["trackStatusCode"] == snapshot["trackStatusCode"]
    assert actual["weatherState"] == snapshot["weatherState"]
    assert actual["events"] == snapshot["events"]

    for driver_id, expected_driver in snapshot["drivers"].items():
        actual_driver = actual["drivers"][driver_id]

        for field, expected_value in expected_driver.items():
            if isinstance(expected_value, float):
                assert actual_driver[field] == pytest.approx(expected_value)
            else:
                assert actual_driver[field] == expected_value


def test_replay_contract_schemas_are_valid(contract_bundle):
    # Arrange
    schemas = contract_bundle["schemas"]

    # Act / Assert
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)


def test_replay_contract_invalid_schema_is_rejected(contract_bundle):
    # Arrange
    invalid_schema = copy.deepcopy(contract_bundle["schemas"]["manifest"])
    invalid_schema["type"] = 1

    # Act / Assert
    with pytest.raises(SchemaError):
        Draft202012Validator.check_schema(invalid_schema)


def test_replay_contract_fixture_instances_validate_against_local_registry(contract_bundle, schema_registry):
    # Arrange
    manifest_schema = contract_bundle["schemas"]["manifest"]
    chunk_schema = contract_bundle["schemas"]["chunk"]
    track_assets_schema = contract_bundle["schemas"]["trackAssets"]

    # Act
    validate_instance(manifest_schema, contract_bundle["manifest"], schema_registry)
    validate_instance(track_assets_schema, contract_bundle["track_assets"], schema_registry)

    for chunk in contract_bundle["chunks"].values():
        validate_instance(chunk_schema, chunk, schema_registry)

    # Assert
    assert True


def test_replay_contract_invalid_manifest_format_is_rejected(contract_bundle, schema_registry):
    # Arrange
    invalid_manifest = copy.deepcopy(contract_bundle["manifest"])
    invalid_manifest["createdAt"] = "2026-07-14"

    # Act / Assert
    with pytest.raises(ValidationError):
        validate_instance(contract_bundle["schemas"]["manifest"], invalid_manifest, schema_registry)


def test_replay_contract_accepts_v2_manifest_without_optional_capability_metadata(
    contract_bundle, schema_registry
):
    # Arrange
    manifest = copy.deepcopy(contract_bundle["manifest"])
    manifest.pop("seasonMetadata")
    manifest.pop("telemetryCapabilities")

    # Act / Assert
    validate_instance(contract_bundle["schemas"]["manifest"], manifest, schema_registry)


def test_replay_contract_accepts_2026_capability_metadata_without_new_telemetry_columns(
    contract_bundle, schema_registry
):
    # Arrange
    manifest = copy.deepcopy(contract_bundle["manifest"])
    manifest["seasonMetadata"] = {"year": 2026}
    manifest["telemetryCapabilities"] = {
        "drs": "not-published",
        "overtakeMode": "not-published",
        "activeAero": "not-published",
        "ersReplacement": "not-published",
    }

    # Act / Assert
    validate_instance(contract_bundle["schemas"]["manifest"], manifest, schema_registry)
    driver_columns = contract_bundle["chunks"]["chunks/chunk-001.json"]["drivers"]["HAM"]
    assert "overtakeMode" not in driver_columns


@pytest.mark.parametrize(
    ("metadata_path", "value"),
    [
        (("seasonMetadata", "year"), "2026"),
        (("seasonMetadata", "year"), 0),
        (("seasonMetadata", "unexpected"), "available"),
        (("telemetryCapabilities", "drs"), "disabled"),
        (("telemetryCapabilities", "unknown"), "available"),
    ],
)
def test_replay_contract_rejects_malformed_capability_metadata(
    contract_bundle, schema_registry, metadata_path, value
):
    # Arrange
    invalid_manifest = copy.deepcopy(contract_bundle["manifest"])
    target = invalid_manifest
    for key in metadata_path[:-1]:
        target = target[key]
    target[metadata_path[-1]] = value

    # Act / Assert
    with pytest.raises(ValidationError):
        validate_instance(contract_bundle["schemas"]["manifest"], invalid_manifest, schema_registry)


def manifest_driver() -> dict[str, str]:
    return {
        "id": "HAM",
        "displayName": "Lewis Hamilton",
        "teamName": "Mercedes",
        "colorHex": "#00D2BE",
        "carNumber": "44",
    }


@pytest.mark.parametrize(
    ("capability_kwargs", "message"),
    [
        ({"season_metadata": {"year": "2026"}}, "year must be an integer from 1 to 9999"),
        ({"season_metadata": {"year": 0}}, "year must be an integer from 1 to 9999"),
        ({"season_metadata": {"year": 2026, "round": 1}}, "must contain only year"),
        (
            {"telemetry_capabilities": {"drs": "available"}},
            "must contain the four capability values",
        ),
        (
            {"telemetry_capabilities": {
                "drs": "disabled", "overtakeMode": "not-published",
                "activeAero": "not-published", "ersReplacement": "not-published",
            }},
            "must be available or not-published",
        ),
    ],
)
def test_browser_manifest_rejects_malformed_capability_metadata_at_serialization(
    capability_kwargs, message
):
    # Arrange: describe a manifest whose optional capability metadata is malformed.

    # Act / Assert: the immutable model rejects it at the serialization boundary.
    with pytest.raises(ValueError, match=message):
        BrowserManifest(
            "deterministic-race", "Deterministic Race", (manifest_driver(),),
            session_mode="race",
            **capability_kwargs,
        )


def test_replay_contract_rejects_overtake_mode_as_chunk_telemetry(
    contract_bundle, schema_registry
):
    # Arrange
    invalid_chunk = copy.deepcopy(contract_bundle["chunks"]["chunks/chunk-001.json"])
    invalid_chunk["drivers"]["HAM"]["overtakeMode"] = [None] * len(invalid_chunk["timeMs"])

    # Act / Assert
    with pytest.raises(ValidationError):
        validate_instance(contract_bundle["schemas"]["chunk"], invalid_chunk, schema_registry)


def timeline_summary_payload() -> dict[str, object]:
    return {
        "contractVersion": "v2",
        "fixtureId": "deterministic-race",
        "startMs": 0,
        "endMs": 4_000,
        "intervals": [
            {"kind": "yellow", "startMs": 500, "endMs": 1_000},
            {"kind": "sc", "startMs": 1_200, "endMs": 1_300},
            {"kind": "red", "startMs": 1_600, "endMs": 1_700},
            {"kind": "vsc", "startMs": 2_000, "endMs": 2_500},
        ],
        "dnfMarkers": [{"driverId": "HAM", "timeMs": 3_000}],
    }


def timeline_summary_reference() -> dict[str, str]:
    return {
        "path": "timeline-summary.json",
        "schemaId": "urn:f1-cache-replay:schema:replay-data:v2:timeline-summary",
        "sha256": "a" * 64,
    }


def lap_sector_sidecar_payload() -> dict[str, object]:
    return {
        "contractVersion": "v2",
        "fixtureId": "deterministic-race",
        "phaseBoundaries": [],
        "drivers": {
            "HAM": {
                "lapNumber": [1],
                "lapStartMs": [0],
                "lapEndMs": [100_000],
                "lapDurationMs": [100_000],
                "sector1DurationMs": [30_000],
                "sector2DurationMs": [30_000],
                "sector3DurationMs": [40_000],
                "sector1SessionTimeMs": [30_000],
                "sector2SessionTimeMs": [60_000],
                "sector3SessionTimeMs": [100_000],
                "qualifyingPhase": [None],
            },
        },
    }


def lap_sector_sidecar_reference() -> dict[str, str]:
    return {
        "path": "lap-sector-sidecar.json",
        "schemaId": BROWSER_LAP_SECTOR_SIDECAR_SCHEMA_ID,
        "sha256": "a" * 64,
    }


def stint_summary_payload() -> dict[str, object]:
    return {
        "contractVersion": "v2",
        "fixtureId": "deterministic-race",
        "drivers": {
            "HAM": {
                "stintNumber": [],
                "compound": [],
                "startLap": [],
                "endLap": [],
                "startTimeMs": [],
                "endTimeMs": [],
                "tyreLifeAtStart": [],
                "isFreshTyre": [],
                "pitInTimeMs": [],
                "pitOutTimeMs": [],
            },
        },
    }


def stint_summary_reference() -> dict[str, str]:
    return {
        "path": "stint-summary.json",
        "schemaId": STINT_SUMMARY_SCHEMA_ID,
        "sha256": "a" * 64,
    }


def pit_loss_model_payload() -> dict[str, object]:
    return {
        "contractVersion": "v2",
        "fixtureId": "deterministic-race",
        "method": "global-prior-weighted-mean-v1",
        "baselineMs": 22_000,
        "priorWeight": 2,
        "timeMs": [0, 1_523_000, 1_678_000],
        "estimatedLossMs": [22_000, 21_867, 22_150],
        "observedSampleCount": [0, 1, 3],
    }


def pit_loss_model_reference() -> dict[str, str]:
    return {
        "path": "pit-loss-model.json",
        "schemaId": "urn:f1-cache-replay:schema:replay-data:v2:pit-loss-model",
        "sha256": "a" * 64,
    }


WEATHER_ARRAY_NAMES = (
    "timeMs", "airTempC", "humidityPct", "pressureMbar", "rainfall",
    "trackTempC", "windDirectionDeg", "windSpeedMps",
)


def weather_sidecar_payload() -> dict[str, object]:
    return {
        "contractVersion": "v2",
        "fixtureId": "deterministic-race",
        "timeMs": [0, 1_000, 2_000],
        "airTempC": [21.5, None, 22.0],
        "humidityPct": [50.0, 55.0, None],
        "pressureMbar": [1013.0, None, 1012.0],
        "rainfall": [False, True, False],
        "trackTempC": [35.0, None, 36.0],
        "windDirectionDeg": [90, 0, None],
        "windSpeedMps": [2.5, 0.0, None],
    }


def weather_sidecar_reference() -> dict[str, str]:
    return {
        "path": "weather-sidecar.json",
        "schemaId": WEATHER_SIDECAR_SCHEMA_ID,
        "sha256": "a" * 64,
    }


def pit_loss_estimate_sidecar_payload() -> dict[str, object]:
    """A race-only status-aware sidecar payload bound to the fixture track."""
    return {
        "contractVersion": "v2",
        "fixtureId": "deterministic-race",
        "trackId": "deterministic-short-loop",
        "method": PIT_LOSS_ESTIMATE_METHOD,
        "race": {
            "timeMs": [0, 1_523_000],
            "estimatedLossMs": [22_000, 21_867],
            "observedSampleCount": [0, 1],
        },
    }


def pit_loss_estimate_sidecar_sc_vsc_payload() -> dict[str, object]:
    """A sidecar payload whose Safety Car and Virtual Safety Car both occur."""
    payload = pit_loss_estimate_sidecar_payload()
    payload["safetyCar"] = {
        "timeMs": [0, 1_523_000],
        "estimatedLossMs": [22_000, 22_250],
        "observedSampleCount": [0, 1],
    }
    payload["virtualSafetyCar"] = {
        "timeMs": [0, 1_678_000],
        "estimatedLossMs": [22_000, 21_900],
        "observedSampleCount": [0, 1],
    }
    return payload


def pit_loss_estimate_sidecar_reference() -> dict[str, str]:
    return {
        "path": PIT_LOSS_ESTIMATE_SIDECAR_FILENAME,
        "schemaId": PIT_LOSS_ESTIMATE_SIDECAR_SCHEMA_ID,
        "sha256": "a" * 64,
    }


def assert_weather_sidecar_semantics(sidecar):
    """Apply weather checks the compact schema cannot express.

    JSON Schema enforces non-negative, unique integer timestamps and per-array
    ranges, but not cross-array alignment, strict ascending order, or the
    finite guarantee (reference engines treat NaN as a nullable number).
    """
    arrays = [sidecar[name] for name in WEATHER_ARRAY_NAMES]
    assert arrays[0] and len({len(values) for values in arrays}) == 1
    times = arrays[0]
    assert all(type(value) is int and value >= 0 for value in times)
    assert all(current < following for current, following in zip(times, times[1:]))
    for name, values in zip(WEATHER_ARRAY_NAMES[1:], arrays[1:]):
        if name == "rainfall":
            assert all(value is None or type(value) is bool for value in values)
        elif name == "windDirectionDeg":
            assert all(value is None or (type(value) is int and 0 <= value <= 359) for value in values)
        else:
            assert all(value is None or math.isfinite(value) for value in values)


def assert_timeline_summary_semantics(summary):
    assert summary["startMs"] < summary["endMs"]
    for interval in summary["intervals"]:
        assert summary["startMs"] <= interval["startMs"] < interval["endMs"] <= summary["endMs"]
    for marker in summary["dnfMarkers"]:
        assert summary["startMs"] <= marker["timeMs"] < summary["endMs"]


def test_replay_contract_timeline_summary_validates_and_is_optional(contract_bundle, schema_registry):
    summary = timeline_summary_payload()
    manifest = copy.deepcopy(contract_bundle["manifest"])
    manifest["timelineSummary"] = timeline_summary_reference()

    validate_instance(contract_bundle["schemas"]["timelineSummary"], summary, schema_registry)
    validate_instance(contract_bundle["schemas"]["manifest"], manifest, schema_registry)
    assert_timeline_summary_semantics(summary)


def test_replay_contract_lap_sector_sidecar_validates_and_is_optional(contract_bundle, schema_registry):
    sidecar = lap_sector_sidecar_payload()
    manifest = copy.deepcopy(contract_bundle["manifest"])
    manifest["lapSectorSidecar"] = lap_sector_sidecar_reference()

    validate_instance(contract_bundle["schemas"]["browserLapSectorSidecar"], sidecar, schema_registry)
    validate_instance(contract_bundle["schemas"]["manifest"], manifest, schema_registry)


def test_replay_contract_penalty_sidecar_validates_and_is_optional(contract_bundle, schema_registry):
    sidecar = {
        "contractVersion": "v2",
        "fixtureId": "deterministic-race",
        "penaltyIssuances": [{
            "driverId": "HAM",
            "sessionTimeMs": 9_000,
            "penaltyType": "TIME_10S",
            "reason": "CAUSING A COLLISION",
            "rawMessage": (
                "FIA STEWARDS: 10 SECOND TIME PENALTY FOR CAR 44 (HAM) - "
                "CAUSING A COLLISION"
            ),
            "lapNumber": 9,
        }],
    }
    manifest = copy.deepcopy(contract_bundle["manifest"])
    manifest["penaltySidecar"] = {
        "path": "penalty-sidecar.json",
        "schemaId": "urn:f1-cache-replay:schema:replay-data:v2:penalty-sidecar",
        "sha256": "a" * 64,
    }

    validate_instance(contract_bundle["schemas"]["penaltySidecar"], sidecar, schema_registry)
    validate_instance(contract_bundle["schemas"]["manifest"], manifest, schema_registry)


def test_replay_contract_stint_summary_validates_and_is_optional(contract_bundle, schema_registry):
    summary = stint_summary_payload()
    manifest = copy.deepcopy(contract_bundle["manifest"])
    manifest["stintSummary"] = stint_summary_reference()

    validate_instance(contract_bundle["schemas"]["stintSummary"], summary, schema_registry)
    validate_instance(contract_bundle["schemas"]["manifest"], manifest, schema_registry)


def test_replay_contract_pit_loss_model_validates_and_is_optional(contract_bundle, schema_registry):
    model = pit_loss_model_payload()
    manifest = copy.deepcopy(contract_bundle["manifest"])
    manifest["pitLossModel"] = pit_loss_model_reference()

    validate_instance(contract_bundle["schemas"]["pitLossModel"], model, schema_registry)
    validate_instance(contract_bundle["schemas"]["manifest"], manifest, schema_registry)


def test_replay_contract_pit_loss_model_rejects_structurally_invalid_payload(
    contract_bundle, schema_registry
):
    model = pit_loss_model_payload()
    model["baselineMs"] = 0

    with pytest.raises(ValidationError):
        validate_instance(contract_bundle["schemas"]["pitLossModel"], model, schema_registry)


def test_replay_contract_weather_sidecar_validates_and_is_optional(
    contract_bundle, schema_registry
):
    sidecar = weather_sidecar_payload()
    manifest = copy.deepcopy(contract_bundle["manifest"])
    manifest["weatherSidecar"] = weather_sidecar_reference()

    validate_instance(contract_bundle["schemas"]["weatherSidecar"], sidecar, schema_registry)
    validate_instance(contract_bundle["schemas"]["manifest"], manifest, schema_registry)
    assert_weather_sidecar_semantics(sidecar)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeMs", [0, "1000", 2_000]),
        ("airTempC", [21.5, "warm", 22.0]),
        ("humidityPct", [50.0, None, "fifty"]),
        ("pressureMbar", [1013.0, None, True]),
        ("rainfall", [False, 1, False]),
        ("trackTempC", [35.0, None, "hot"]),
        ("windDirectionDeg", [90, 12.5, None]),
        ("windSpeedMps", [2.5, None, "fast"]),
    ],
)
def test_replay_contract_weather_sidecar_rejects_invalid_types(
    contract_bundle, schema_registry, field, value
):
    sidecar = weather_sidecar_payload()
    sidecar[field] = value

    with pytest.raises(ValidationError):
        validate_instance(contract_bundle["schemas"]["weatherSidecar"], sidecar, schema_registry)


def test_replay_contract_weather_sidecar_rejects_negative_times(
    contract_bundle, schema_registry
):
    sidecar = weather_sidecar_payload()
    sidecar["timeMs"] = [-1, 0, 2_000]

    with pytest.raises(ValidationError):
        validate_instance(contract_bundle["schemas"]["weatherSidecar"], sidecar, schema_registry)


def test_replay_contract_weather_sidecar_rejects_null_in_time_array(
    contract_bundle, schema_registry
):
    sidecar = weather_sidecar_payload()
    sidecar["timeMs"] = [0, None, 2_000]

    with pytest.raises(ValidationError):
        validate_instance(contract_bundle["schemas"]["weatherSidecar"], sidecar, schema_registry)


def test_replay_contract_weather_sidecar_rejects_duplicate_times(
    contract_bundle, schema_registry
):
    sidecar = weather_sidecar_payload()
    sidecar["timeMs"] = [0, 0, 2_000]

    with pytest.raises(ValidationError):
        validate_instance(contract_bundle["schemas"]["weatherSidecar"], sidecar, schema_registry)


def test_replay_contract_pit_loss_estimate_sidecar_race_only_validates_and_is_optional(
    contract_bundle, schema_registry
):
    # Arrange: a per-track sidecar carrying only the race-wide median.
    sidecar = pit_loss_estimate_sidecar_payload()
    manifest = copy.deepcopy(contract_bundle["manifest"])
    manifest["pitLossEstimateSidecar"] = pit_loss_estimate_sidecar_reference()

    # Act / Assert: the race-only payload and its manifest reference are v2-valid.
    validate_instance(
        contract_bundle["schemas"]["pitLossEstimateSidecar"], sidecar, schema_registry,
    )
    validate_instance(contract_bundle["schemas"]["manifest"], manifest, schema_registry)


def test_replay_contract_pit_loss_estimate_sidecar_sc_vsc_present_validates(
    contract_bundle, schema_registry
):
    # Arrange: a sidecar whose Safety Car and Virtual Safety Car statuses occur.
    sidecar = pit_loss_estimate_sidecar_sc_vsc_payload()

    # Act / Assert: both status-specific median timelines satisfy the schema.
    validate_instance(
        contract_bundle["schemas"]["pitLossEstimateSidecar"], sidecar, schema_registry,
    )


def test_replay_contract_pit_loss_estimate_sidecar_statuses_are_independently_optional(
    contract_bundle, schema_registry
):
    # Arrange: start from a SC+VSC payload and drop one status estimate.
    sidecar = pit_loss_estimate_sidecar_sc_vsc_payload()
    del sidecar["virtualSafetyCar"]

    # Act / Assert: an absent status never forces the other status field.
    validate_instance(
        contract_bundle["schemas"]["pitLossEstimateSidecar"], sidecar, schema_registry,
    )
    assert "safetyCar" in sidecar
    assert "virtualSafetyCar" not in sidecar


def test_replay_contract_pit_loss_estimate_sidecar_unavailable_status_validates(
    contract_bundle, schema_registry
):
    # Arrange: both statuses occur but no eligible sample exists for either.
    sidecar = pit_loss_estimate_sidecar_payload()
    sidecar["safetyCar"] = {"status": "unavailable"}
    sidecar["virtualSafetyCar"] = {"status": "unavailable"}

    # Act / Assert: explicit unavailability is a valid status estimate.
    validate_instance(
        contract_bundle["schemas"]["pitLossEstimateSidecar"], sidecar, schema_registry,
    )


@pytest.mark.parametrize(
    ("instance_path", "value"),
    [
        (("method",), "other-v1"),
        (("contractVersion",), "v1"),
        (("trackId",), "Deterministic_Short_Loop"),
        (("fixtureId",), "Deterministic-Race"),
        (("race", "timeMs"), []),
        (("race", "estimatedLossMs"), [22_000, -1]),
        (("race", "observedSampleCount"), [0, 1.5]),
        (("safetyCar",), {"status": "normal"}),
        (("safetyCar",), {"status": "unavailable", "extra": True}),
        (("virtualSafetyCar",), {"timeMs": [0], "estimatedLossMs": [22_000]}),
    ],
)
def test_replay_contract_pit_loss_estimate_sidecar_rejects_invalid_payloads(
    contract_bundle, schema_registry, instance_path, value
):
    # Arrange: a structurally or semantically invalid sidecar payload.
    sidecar = pit_loss_estimate_sidecar_sc_vsc_payload()
    target = sidecar
    for key in instance_path[:-1]:
        target = target[key]
    target[instance_path[-1]] = value

    # Act / Assert: the sidecar schema fails closed on malformed estimates.
    with pytest.raises(ValidationError):
        validate_instance(
            contract_bundle["schemas"]["pitLossEstimateSidecar"], sidecar, schema_registry,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("airTempC", [0.0, 22.0, None]),
        ("airTempC", [-1.0, 22.0, None]),
        ("humidityPct", [101.0, 50.0, None]),
        ("humidityPct", [-1.0, 50.0, None]),
        ("pressureMbar", [0.0, 1012.0, None]),
        ("trackTempC", [0.0, 36.0, None]),
        ("windDirectionDeg", [360, 90, None]),
        ("windDirectionDeg", [-1, 90, None]),
        ("windSpeedMps", [-1.0, 2.5, None]),
    ],
)
def test_replay_contract_weather_sidecar_rejects_invalid_range_or_null_combinations(
    contract_bundle, schema_registry, field, value
):
    sidecar = weather_sidecar_payload()
    sidecar[field] = value

    with pytest.raises(ValidationError):
        validate_instance(contract_bundle["schemas"]["weatherSidecar"], sidecar, schema_registry)


def test_replay_contract_weather_sidecar_detects_bad_time_ordering(
    contract_bundle, schema_registry
):
    sidecar = weather_sidecar_payload()
    sidecar["timeMs"] = [1_000, 0, 2_000]

    # Unique non-negative times pass the schema, but the semantic ordering
    # contract rejects them (mirrors the publication-time audit).
    validate_instance(contract_bundle["schemas"]["weatherSidecar"], sidecar, schema_registry)
    with pytest.raises(AssertionError):
        assert_weather_sidecar_semantics(sidecar)


def test_replay_contract_weather_sidecar_detects_misaligned_arrays(
    contract_bundle, schema_registry
):
    sidecar = weather_sidecar_payload()
    sidecar["airTempC"] = [21.5]

    # The schema only enforces per-array minItems; cross-array alignment is
    # a semantic contract check.
    validate_instance(contract_bundle["schemas"]["weatherSidecar"], sidecar, schema_registry)
    with pytest.raises(AssertionError):
        assert_weather_sidecar_semantics(sidecar)


def test_replay_contract_weather_sidecar_rejects_non_finite_values(
    contract_bundle, schema_registry
):
    sidecar = weather_sidecar_payload()
    cast(list[object], sidecar["airTempC"])[0] = float("nan")

    # JSON Schema engines treat NaN as a nullable number, so the finite
    # guarantee is enforced semantically at the immutable-model boundary.
    validate_instance(contract_bundle["schemas"]["weatherSidecar"], sidecar, schema_registry)
    with pytest.raises(AssertionError):
        assert_weather_sidecar_semantics(sidecar)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schemaId", "urn:f1-cache-replay:schema:replay-data:unsupported:wrong"),
        ("path", "not-weather-sidecar.json"),
        ("sha256", "not-a-sha256"),
    ],
)
def test_replay_contract_weather_sidecar_reference_rejects_invalid_schema_or_digest(
    contract_bundle, schema_registry, field, value
):
    manifest = copy.deepcopy(contract_bundle["manifest"])
    reference = weather_sidecar_reference()
    reference[field] = value
    manifest["weatherSidecar"] = reference

    with pytest.raises(ValidationError):
        validate_instance(contract_bundle["schemas"]["manifest"], manifest, schema_registry)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("path", "pit-loss-model.json"),
        ("schemaId", "urn:f1-cache-replay:schema:replay-data:v2:wrong"),
        ("sha256", "not-a-sha256"),
    ],
)
def test_replay_contract_pit_loss_estimate_sidecar_reference_rejects_invalid_schema_or_digest(
    contract_bundle, schema_registry, field, value
):
    manifest = copy.deepcopy(contract_bundle["manifest"])
    reference = pit_loss_estimate_sidecar_reference()
    reference[field] = value
    manifest["pitLossEstimateSidecar"] = reference

    with pytest.raises(ValidationError):
        validate_instance(contract_bundle["schemas"]["manifest"], manifest, schema_registry)


def test_replay_contract_accepts_manifest_without_pit_loss_estimate_sidecar(
    contract_bundle, schema_registry
):
    # Arrange: a v2 manifest with the optional sidecar reference.
    manifest = copy.deepcopy(contract_bundle["manifest"])
    manifest["pitLossEstimateSidecar"] = pit_loss_estimate_sidecar_reference()
    validate_instance(contract_bundle["schemas"]["manifest"], manifest, schema_registry)

    # Act: remove the optional reference.
    del manifest["pitLossEstimateSidecar"]

    # Assert: the v2 manifest remains valid without the sidecar.
    validate_instance(contract_bundle["schemas"]["manifest"], manifest, schema_registry)


def test_browser_manifest_serializes_optional_timeline_summary_reference():
    manifest = BrowserManifest(
        "deterministic-race",
        "Deterministic Race",
        ({
            "id": "HAM",
            "displayName": "Lewis Hamilton",
            "teamName": "Mercedes",
            "colorHex": "#00D2BE",
            "carNumber": "44",
        },),
        session_mode="race",
        timeline_summary=BrowserTimelineSummaryReference(
            "timeline-summary.json",
            "urn:f1-cache-replay:schema:replay-data:v2:timeline-summary",
            "a" * 64,
        ),
    )

    assert manifest.as_dict()["timelineSummary"] == timeline_summary_reference()


def test_browser_manifest_serializes_optional_lap_sector_sidecar_reference():
    manifest = BrowserManifest(
        "deterministic-race",
        "Deterministic Race",
        ({
            "id": "HAM",
            "displayName": "Lewis Hamilton",
            "teamName": "Mercedes",
            "colorHex": "#00D2BE",
            "carNumber": "44",
        },),
        session_mode="race",
        lap_sector_sidecar=BrowserLapSectorSidecarReference(
            "lap-sector-sidecar.json",
            BROWSER_LAP_SECTOR_SIDECAR_SCHEMA_ID,
            "a" * 64,
        ),
    )

    assert manifest.as_dict()["lapSectorSidecar"] == lap_sector_sidecar_reference()


def test_browser_manifest_serializes_optional_penalty_sidecar_reference():
    manifest = BrowserManifest(
        "deterministic-race",
        "Deterministic Race",
        ({
            "id": "HAM",
            "displayName": "Lewis Hamilton",
            "teamName": "Mercedes",
            "colorHex": "#00D2BE",
            "carNumber": "44",
        },),
        session_mode="race",
        penalty_sidecar=BrowserPenaltySidecarReference(
            "penalty-sidecar.json", PENALTY_SIDECAR_SCHEMA_ID, "a" * 64,
        ),
    )

    assert manifest.as_dict()["penaltySidecar"] == {
        "path": "penalty-sidecar.json",
        "schemaId": PENALTY_SIDECAR_SCHEMA_ID,
        "sha256": "a" * 64,
    }


def test_browser_manifest_serializes_optional_weather_sidecar_reference():
    manifest = BrowserManifest(
        "deterministic-race",
        "Deterministic Race",
        ({
            "id": "HAM",
            "displayName": "Lewis Hamilton",
            "teamName": "Mercedes",
            "colorHex": "#00D2BE",
            "carNumber": "44",
        },),
        session_mode="race",
        weather_sidecar=BrowserWeatherSidecarReference(
            "weather-sidecar.json", WEATHER_SIDECAR_SCHEMA_ID, "a" * 64,
        ),
    )

    assert manifest.as_dict()["weatherSidecar"] == weather_sidecar_reference()


def test_browser_manifest_serializes_optional_pit_loss_estimate_sidecar_reference():
    manifest = BrowserManifest(
        "deterministic-race",
        "Deterministic Race",
        ({
            "id": "HAM",
            "displayName": "Lewis Hamilton",
            "teamName": "Mercedes",
            "colorHex": "#00D2BE",
            "carNumber": "44",
        },),
        session_mode="race",
        pit_loss_estimate_sidecar=BrowserPitLossEstimateSidecarReference(
            PIT_LOSS_ESTIMATE_SIDECAR_FILENAME,
            PIT_LOSS_ESTIMATE_SIDECAR_SCHEMA_ID,
            "a" * 64,
        ),
    )

    assert manifest.as_dict()["pitLossEstimateSidecar"] == pit_loss_estimate_sidecar_reference()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schemaId", "urn:f1-cache-replay:schema:replay-data:unsupported:wrong"),
        ("sha256", "not-a-sha256"),
    ],
)
def test_replay_contract_timeline_summary_reference_rejects_invalid_schema_or_digest(
    contract_bundle, schema_registry, field, value
):
    manifest = copy.deepcopy(contract_bundle["manifest"])
    reference = timeline_summary_reference()
    reference[field] = value
    manifest["timelineSummary"] = reference

    with pytest.raises(ValidationError):
        validate_instance(contract_bundle["schemas"]["manifest"], manifest, schema_registry)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schemaId", "urn:f1-cache-replay:schema:replay-data:unsupported:wrong"),
        ("sha256", "not-a-sha256"),
    ],
)
def test_replay_contract_lap_sector_sidecar_reference_rejects_invalid_schema_or_digest(
    contract_bundle, schema_registry, field, value
):
    manifest = copy.deepcopy(contract_bundle["manifest"])
    reference = lap_sector_sidecar_reference()
    reference[field] = value
    manifest["lapSectorSidecar"] = reference

    with pytest.raises(ValidationError):
        validate_instance(contract_bundle["schemas"]["manifest"], manifest, schema_registry)


@pytest.mark.parametrize(
    "invalid_summary",
    [
        {"kind": "yellow", "startMs": 1_000, "endMs": 1_000},
        {"kind": "red", "startMs": 2_000, "endMs": 1_000},
        {"kind": "blue", "startMs": 0, "endMs": 1_000},
    ],
)
def test_replay_contract_timeline_summary_detects_invalid_interval_bounds(
    contract_bundle, schema_registry, invalid_summary
):
    summary = timeline_summary_payload()
    summary["intervals"] = [invalid_summary]

    if invalid_summary["kind"] not in {"yellow", "sc", "red", "vsc"}:
        with pytest.raises(ValidationError):
            validate_instance(contract_bundle["schemas"]["timelineSummary"], summary, schema_registry)
    else:
        validate_instance(contract_bundle["schemas"]["timelineSummary"], summary, schema_registry)
        with pytest.raises(AssertionError):
            assert_timeline_summary_semantics(summary)


@pytest.mark.parametrize(
    "invalid_marker",
    [
        {"timeMs": 3_000},
        {"driverId": "ham", "timeMs": 3_000},
        {"driverId": "HAM", "timeMs": -1},
        {"driverId": "HAM", "timeMs": 3_000, "unexpected": True},
    ],
)
def test_replay_contract_timeline_summary_rejects_invalid_dnf_markers(
    contract_bundle, schema_registry, invalid_marker
):
    summary = timeline_summary_payload()
    summary["dnfMarkers"] = [invalid_marker]

    with pytest.raises(ValidationError):
        validate_instance(contract_bundle["schemas"]["timelineSummary"], summary, schema_registry)


def test_replay_contract_schema_accepts_a_general_ordered_three_chunk_manifest(
    contract_bundle, schema_registry
):
    # Arrange
    manifest = copy.deepcopy(contract_bundle["manifest"])
    manifest["fixtureId"] = "season-opener-2026"
    manifest["fixtureName"] = "Season Opener"
    manifest["chunks"].append(
        {
            "sequence": 3,
            "path": "chunks/chunk-003.json",
            "schemaId": "urn:f1-cache-replay:schema:replay-data:v2:chunk",
            "startMs": 4000,
            "endMs": 6000,
            "overlapWithPreviousMs": 500,
        }
    )

    # Act / Assert
    validate_instance(contract_bundle["schemas"]["manifest"], manifest, schema_registry)


def test_replay_contract_schema_accepts_general_chunk_identifiers_and_handoffs(
    contract_bundle, schema_registry
):
    # Arrange
    chunk = copy.deepcopy(contract_bundle["chunks"]["chunks/chunk-002.json"])
    chunk.update(
        {
            "fixtureId": "season-opener-2026",
            "chunkId": "chunk-1001",
            "sequence": 1001,
            "startMs": 4000,
            "endMs": 6000,
            "timeMs": [3500, 3600, 4000],
            "authoritativeStartIndex": 2,
            "overlap": {
                "kind": "handoff",
                "previousChunkPath": "chunks/chunk-1000.json",
                "range": {"startMs": 3500, "endMs": 4000},
                "authoritativeFromMs": 4000,
            },
            "events": [],
        }
    )

    # Act / Assert
    validate_instance(contract_bundle["schemas"]["chunk"], chunk, schema_registry)


def test_replay_contract_general_three_chunk_handoffs_preserve_order_and_ownership(
    contract_bundle,
):
    # Arrange
    bundle = copy.deepcopy(contract_bundle)
    fixture_id = "season-opener-2026"
    bundle["manifest"]["fixtureId"] = fixture_id
    bundle["track_assets"]["fixtureId"] = fixture_id
    for chunk in bundle["chunks"].values():
        chunk["fixtureId"] = fixture_id

    third_chunk_path = "chunks/chunk-003.json"
    third_chunk = copy.deepcopy(bundle["chunks"]["chunks/chunk-002.json"])
    third_chunk.update(
        {
            "fixtureId": fixture_id,
            "chunkId": "chunk-003",
            "sequence": 3,
            "startMs": 4000,
            "endMs": 6000,
            "timeMs": [3500, 4000, 5000],
            "authoritativeStartIndex": 1,
            "overlap": {
                "kind": "handoff",
                "previousChunkPath": "chunks/chunk-002.json",
                "range": {"startMs": 3500, "endMs": 4000},
                "authoritativeFromMs": 4000,
            },
            "events": [],
        }
    )
    bundle["chunks"][third_chunk_path] = third_chunk
    bundle["manifest"]["chunks"].append(
        {
            "sequence": 3,
            "path": third_chunk_path,
            "schemaId": "urn:f1-cache-replay:schema:replay-data:v2:chunk",
            "startMs": 4000,
            "endMs": 6000,
            "overlapWithPreviousMs": 500,
        }
    )

    # Act / Assert
    assert_manifest_semantics(bundle)


@pytest.mark.parametrize(
    ("instance_path", "value"),
    [
        (("fixtureId",), "season_opener"),
        (("chunkId",), "chunk-0"),
        (("sequence",), "3"),
        (("overlap", "range"), None),
    ],
)
def test_replay_contract_schema_rejects_malformed_general_chunk_values(
    contract_bundle, schema_registry, instance_path, value
):
    # Arrange
    invalid_chunk = copy.deepcopy(contract_bundle["chunks"]["chunks/chunk-002.json"])
    target = invalid_chunk
    for key in instance_path[:-1]:
        target = target[key]
    target[instance_path[-1]] = value

    # Act / Assert
    with pytest.raises(ValidationError):
        validate_instance(contract_bundle["schemas"]["chunk"], invalid_chunk, schema_registry)


def test_replay_contract_cross_file_semantics_hold_for_fixture(contract_bundle):
    # Arrange
    bundle = contract_bundle

    # Act / Assert
    assert_manifest_semantics(bundle)


def test_replay_contract_invalid_chunk_ordering_is_rejected(contract_bundle):
    # Arrange
    invalid_bundle = copy.deepcopy(contract_bundle)
    invalid_bundle["manifest"]["chunks"][1]["startMs"] = 1900

    # Act / Assert
    with pytest.raises(AssertionError):
        assert_manifest_semantics(invalid_bundle)


def test_replay_contract_misaligned_driver_column_is_rejected(contract_bundle):
    # Arrange
    invalid_bundle = copy.deepcopy(contract_bundle)
    invalid_bundle["chunks"]["chunks/chunk-001.json"]["drivers"]["HAM"]["speed"].pop()

    # Act / Assert
    with pytest.raises(AssertionError):
        assert_manifest_semantics(invalid_bundle)


def test_replay_contract_misaligned_global_column_is_rejected(contract_bundle):
    # Arrange
    invalid_bundle = copy.deepcopy(contract_bundle)
    invalid_bundle["chunks"]["chunks/chunk-002.json"]["weatherState"].pop()

    # Act / Assert
    with pytest.raises(AssertionError):
        assert_manifest_semantics(invalid_bundle)


def test_replay_contract_reference_index_cannot_be_authoritative(contract_bundle):
    # Arrange
    invalid_bundle = copy.deepcopy(contract_bundle)
    invalid_bundle["chunks"]["chunks/chunk-002.json"]["authoritativeStartIndex"] = 0

    # Act / Assert
    with pytest.raises(AssertionError):
        assert_manifest_semantics(invalid_bundle)


@pytest.mark.parametrize("snapshot", load_contract_bundle()["golden"]["snapshots"])
def test_replay_contract_golden_snapshots_match_expected_outputs(contract_bundle, snapshot):
    # Arrange
    bundle = contract_bundle

    # Act / Assert
    assert_snapshot_matches_expected(bundle, snapshot)


def test_replay_contract_overlap_reference_is_not_authoritative_owner(contract_bundle):
    # Arrange
    snapshot = next(
        item
        for item in contract_bundle["golden"]["snapshots"]
        if item["id"] == "overlap-ownership-at-1500"
    )

    # Act
    authoritative_chunk = contract_bundle["chunks"][snapshot["expectedSource"]["chunkPath"]]
    reference_chunk = contract_bundle["chunks"][snapshot["nonAuthoritativeMatch"]["chunkPath"]]

    # Assert
    assert snapshot["expectedSource"]["timeIndex"] >= authoritative_chunk["authoritativeStartIndex"]
    assert snapshot["nonAuthoritativeMatch"]["timeIndex"] < reference_chunk["authoritativeStartIndex"]
    assert reference_chunk["timeMs"][snapshot["nonAuthoritativeMatch"]["timeIndex"]] < reference_chunk["startMs"]
    assert snapshot["expectedSource"]["chunkPath"] != snapshot["nonAuthoritativeMatch"]["chunkPath"]


def test_replay_contract_exact_reads_use_the_golden_time_index(contract_bundle):
    # Arrange
    exact_snapshots = [
        snapshot
        for snapshot in contract_bundle["golden"]["snapshots"]
        if snapshot["expectationKind"] == "exact"
    ]

    # Act
    source_times = [
        contract_bundle["chunks"][snapshot["expectedSource"]["chunkPath"]]["timeMs"][
            snapshot["expectedSource"]["timeIndex"]
        ]
        for snapshot in exact_snapshots
    ]

    # Assert
    assert source_times == [snapshot["expectedSource"]["sampleTimeMs"] for snapshot in exact_snapshots]


def test_replay_contract_discrete_fields_use_step_semantics_not_linear_interpolation(contract_bundle):
    # Arrange
    snapshot = next(
        item
        for item in contract_bundle["golden"]["snapshots"]
        if item["id"] == "interpolated-sparse-event-at-2600"
    )
    previous_sample = find_sample(contract_bundle, "chunks/chunk-002.json", 2000)
    next_sample = find_sample(contract_bundle, "chunks/chunk-002.json", 3000)

    # Act
    actual = resolve_snapshot(contract_bundle, snapshot)
    linear_position = previous_sample["drivers"]["HAM"]["position"] + (
        (next_sample["drivers"]["HAM"]["position"] - previous_sample["drivers"]["HAM"]["position"])
        * 0.6
    )

    # Assert
    assert (
        actual["trackStatusCode"] == previous_sample["trackStatusCode"]
        and actual["leaderboardOrder"] == previous_sample["leaderboardOrder"]
        and actual["weatherState"] == previous_sample["weatherState"]
        and all(
            actual["drivers"]["HAM"][field] == previous_sample["drivers"]["HAM"][field]
            for field in STEP_DRIVER_FIELDS
        )
        and actual["drivers"]["HAM"]["position"] != pytest.approx(linear_position)
    )


def test_replay_contract_continuous_fields_use_linear_interpolation(contract_bundle):
    # Arrange
    snapshot = next(
        item
        for item in contract_bundle["golden"]["snapshots"]
        if item["id"] == "interpolated-mid-chunk-001"
    )
    previous_sample = find_sample(contract_bundle, "chunks/chunk-001.json", 0)
    next_sample = find_sample(contract_bundle, "chunks/chunk-001.json", 1000)

    # Act
    actual = resolve_snapshot(contract_bundle, snapshot)
    expected = {
        field: interpolate_value(previous_sample["drivers"]["HAM"][field], next_sample["drivers"]["HAM"][field], 0.5)
        for field in CONTINUOUS_DRIVER_FIELDS
    }

    # Assert
    assert {
        field: actual["drivers"]["HAM"][field]
        for field in CONTINUOUS_DRIVER_FIELDS
    } == pytest.approx(expected)


def test_replay_contract_sampled_current_leader_gap_is_normalized_to_zero(contract_bundle):
    snapshot = next(
        item
        for item in contract_bundle["golden"]["snapshots"]
        if item["id"] == "interpolated-sparse-event-at-2600"
    )

    actual = resolve_snapshot(contract_bundle, snapshot)

    assert actual["leaderboardOrder"][0] == "HAM"
    assert actual["drivers"]["HAM"]["position"] == 1
    assert actual["drivers"]["HAM"]["gapToLeaderMs"] == 0


def test_replay_contract_cross_boundary_interpolation_uses_authoritative_bounds(contract_bundle):
    # Arrange
    snapshot = next(
        item
        for item in contract_bundle["golden"]["snapshots"]
        if item["id"] == "cross-chunk-interpolation-before-boundary-owner-switch"
    )
    bounds = snapshot["interpolationBounds"]
    previous_chunk = contract_bundle["chunks"][bounds["previous"]["chunkPath"]]
    next_chunk = contract_bundle["chunks"][bounds["next"]["chunkPath"]]

    # Act
    actual = resolve_snapshot(contract_bundle, snapshot)

    # Assert
    assert (
        previous_chunk["timeMs"][bounds["previous"]["timeIndex"]]
        < snapshot["sessionTimeMs"]
        < next_chunk["timeMs"][bounds["next"]["timeIndex"]]
        and bounds["next"]["timeIndex"] >= next_chunk["authoritativeStartIndex"]
        and actual["drivers"]["HAM"]["x"] == pytest.approx(490)
    )


def test_replay_contract_sparse_events_remain_point_in_time_records(contract_bundle):
    # Arrange
    events_by_time = event_lookup(contract_bundle)

    # Act
    event_at_2600 = events_by_time.get(2600, [])
    event_at_2500 = events_by_time.get(2500, [])
    event_at_2700 = events_by_time.get(2700, [])

    # Assert
    assert len(event_at_2600) == 1
    assert event_at_2600[0]["eventType"] == "overtake_completed"
    assert event_at_2500 == []
    assert event_at_2700 == []


def test_v2_manifest_schema_requires_session_mode_field():
    # Arrange / Act: load the active v2 manifest schema.
    manifest_schema = load_json(SCHEMA_ROOT / "manifest.schema.json")

    # Assert: every active browser manifest identifies its session mode.
    assert manifest_schema["properties"]["contractVersion"]["const"] == "v2"
    assert "sessionMode" in manifest_schema["properties"]
    assert "sessionMode" in manifest_schema["required"]
    assert "sessionType" not in manifest_schema["properties"]
    assert "goldenSnapshots" in manifest_schema["properties"]
    assert manifest_schema["additionalProperties"] is False


def test_v2_contract_schemas_are_valid_json_schema():
    # Arrange / Act: load the v2 manifest and qualifying-summary schemas.
    schemas = {
        "manifest": load_json(V2_SCHEMA_ROOT / "manifest.schema.json"),
        "qualifying-summary": load_json(V2_SCHEMA_ROOT / "qualifying-summary.schema.json"),
    }

    # Assert: every v2 schema is valid draft 2020-12 with a v2 identity.
    for name, schema in schemas.items():
        Draft202012Validator.check_schema(schema)
        assert schema["$id"].startswith("urn:f1-cache-replay:schema:replay-data:v2:")
    assert schemas["manifest"]["$id"] == V2_MANIFEST_SCHEMA_ID
    assert schemas["qualifying-summary"]["$id"] == V2_QUALIFYING_SUMMARY_SCHEMA_ID


def v2_manifest_payload(*, session_mode: str = "qualifying", qualifying_summary: bool = True) -> dict:
    """Return a minimal schema-valid v2 manifest instance for mode semantics."""
    manifest = {
        "contractVersion": "v2",
        "formatVersion": "browser-delivery-v2",
        "sessionMode": session_mode,
        "fixtureId": "2026-australian-qualifying",
        "fixtureName": "Australian Grand Prix Qualifying",
        "schemas": {
            "manifest": V2_MANIFEST_SCHEMA_ID,
            "chunk": "urn:f1-cache-replay:schema:replay-data:v2:chunk",
            "trackAssets": "urn:f1-cache-replay:schema:replay-data:v2:track-assets",
        },
        "trackAssets": {
            "path": "track-assets.json",
            "schemaId": "urn:f1-cache-replay:schema:replay-data:v2:track-assets",
        },
        "chunks": [{
            "sequence": 1,
            "path": "chunks/chunk-001.json",
            "schemaId": "urn:f1-cache-replay:schema:replay-data:v2:chunk",
            "startMs": 0,
            "endMs": 600_000,
            "overlapWithPreviousMs": 0,
        }],
        "drivers": [{
            "id": "HAM",
            "displayName": "Lewis Hamilton",
            "teamName": "Ferrari",
            "colorHex": "#E8002D",
            "carNumber": "44",
        }],
        "goldenSnapshots": {"path": "golden-snapshots.json"},
    }
    if qualifying_summary:
        manifest["qualifyingSummary"] = {
            "path": "qualifying-summary.json",
            "schemaId": V2_QUALIFYING_SUMMARY_SCHEMA_ID,
            "sha256": "a" * 64,
        }
    return manifest


def test_v2_manifest_qualifying_summary_reference_is_restricted_to_qualifying_like_modes(
    v2_schema_registry,
):
    # Arrange: build the schema and qualifying-like instances with summaries.
    manifest_schema = load_json(V2_SCHEMA_ROOT / "manifest.schema.json")

    # Act / Assert: all qualifying-like modes permit a valid summary reference.
    for mode in ("qualifying", "sprint-qualifying", "sprint-shootout"):
        validate_instance(
            manifest_schema,
            v2_manifest_payload(session_mode=mode, qualifying_summary=True),
            v2_schema_registry,
        )

    # Assert: race-shaped sessions must not publish a qualifying summary.
    race_with_summary = v2_manifest_payload(session_mode="race", qualifying_summary=True)
    with pytest.raises(ValidationError):
        validate_instance(manifest_schema, race_with_summary, v2_schema_registry)

    # Assert: a qualifying manifest with an invalid summary digest is rejected.
    invalid_digest = v2_manifest_payload(session_mode="qualifying", qualifying_summary=True)
    invalid_digest["qualifyingSummary"]["sha256"] = "not-a-sha256"
    with pytest.raises(ValidationError):
        validate_instance(manifest_schema, invalid_digest, v2_schema_registry)


def test_v2_qualifying_summary_payload_validates_against_frozen_schema(v2_schema_registry):
    # Arrange: a representative qualifying summary with null and populated segments.
    schema = load_json(V2_SCHEMA_ROOT / "qualifying-summary.schema.json")
    payload = {
        "contractVersion": "v2",
        "fixtureId": "2026-australian-qualifying",
        "drivers": {
            "HAM": {
                "qualifyingPosition": [1],
                "q1TimeMs": [105_123],
                "q2TimeMs": [104_567],
                "q3TimeMs": [103_999],
                "bestLapNumber": [3],
                "bestLapTimeMs": [103_999],
            },
            "VER": {
                "qualifyingPosition": [2],
                "q1TimeMs": [105_200],
                "q2TimeMs": [105_000],
                "q3TimeMs": [None],
                "bestLapNumber": [1],
                "bestLapTimeMs": [105_200],
            },
        },
    }

    # Act / Assert: the populated payload validates against the v2 schema.
    validate_instance(schema, payload, v2_schema_registry)

    # Assert: a position below the declared minimum is rejected.
    payload["drivers"]["HAM"]["qualifyingPosition"] = [0]
    with pytest.raises(ValidationError):
        validate_instance(schema, payload, v2_schema_registry)


V2_BROWSER_LAP_SECTOR_SIDECAR_SCHEMA_ID = "urn:f1-cache-replay:schema:replay-data:v2:browser-lap-sector-sidecar"


def v2_browser_lap_sector_sidecar_payload(*, qualifying: bool = True) -> dict:
    """Return a schema-valid v2 lap-sector sidecar with aligned phase columns."""
    payload = {
        "contractVersion": "v2",
        "fixtureId": "2026-australian-qualifying",
        "phaseBoundaries": (
            [
                {"phase": "Q1", "startMs": 0},
                {"phase": "Q2", "startMs": 105_123},
                {"phase": "Q3", "startMs": 210_000},
            ]
            if qualifying
            else []
        ),
        "drivers": {
            "HAM": {
                "lapNumber": [1, 2, 3],
                "lapStartMs": [0, 105_123, 210_000],
                "lapEndMs": [105_123, 210_000, 313_999],
                "lapDurationMs": [105_123, 104_877, 103_999],
                "sector1DurationMs": [35_041, 34_959, 34_666],
                "sector2DurationMs": [35_041, 34_959, 34_666],
                "sector3DurationMs": [35_041, 34_959, 34_667],
                "sector1SessionTimeMs": [35_041, 140_164, 244_666],
                "sector2SessionTimeMs": [70_082, 175_123, 279_332],
                "sector3SessionTimeMs": [105_123, 210_000, 313_999],
                "qualifyingPhase": ["Q1", "Q2", "Q3"] if qualifying else [None, None, None],
            },
        },
    }
    return payload


def test_v2_browser_lap_sector_sidecar_schema_is_valid_and_frozen(v2_schema_registry):
    # Arrange / Act: load the v2 browser lap-sector sidecar schema.
    schema = load_json(V2_SCHEMA_ROOT / "browser-lap-sector-sidecar.schema.json")

    # Assert: the schema is valid draft 2020-12 with the v2 sidecar identity and
    # requires the qualifying phase column and boundary array.
    Draft202012Validator.check_schema(schema)
    assert schema["$id"] == V2_BROWSER_LAP_SECTOR_SIDECAR_SCHEMA_ID
    assert schema["properties"]["contractVersion"]["const"] == "v2"
    assert "phaseBoundaries" in schema["required"]
    assert "qualifyingPhase" in schema["$defs"]["columns"]["required"]


def test_v2_browser_lap_sector_sidecar_with_phase_data_validates(v2_schema_registry):
    # Arrange: a qualifying payload with per-lap phases and ordered boundaries.
    schema = load_json(V2_SCHEMA_ROOT / "browser-lap-sector-sidecar.schema.json")
    payload = v2_browser_lap_sector_sidecar_payload(qualifying=True)

    # Act / Assert: the authoritative Q-phase sidecar payload validates.
    validate_instance(schema, payload, v2_schema_registry)


def test_v2_browser_lap_sector_sidecar_accepts_backward_safe_non_qualifying_payload(
    v2_schema_registry,
):
    # Arrange: a race-shaped v2 sidecar with null phases and no boundaries.
    schema = load_json(V2_SCHEMA_ROOT / "browser-lap-sector-sidecar.schema.json")
    payload = v2_browser_lap_sector_sidecar_payload(qualifying=False)

    # Act / Assert: non-qualifying payloads remain schema-valid (backward safe).
    validate_instance(schema, payload, v2_schema_registry)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload["drivers"]["HAM"].update(qualifyingPhase=["Q4", "Q2", "Q3"]),
        lambda payload: payload["phaseBoundaries"].append({"phase": "Q4", "startMs": 400_000}),
        lambda payload: payload["phaseBoundaries"].append({"phase": "Q1", "startMs": -1}),
        lambda payload: payload["drivers"]["HAM"].pop("qualifyingPhase"),
        lambda payload: payload.pop("phaseBoundaries"),
    ],
)
def test_v2_browser_lap_sector_sidecar_rejects_invalid_phase_or_boundary_payloads(
    v2_schema_registry, mutation,
):
    # Arrange: a valid qualifying payload with a targeted structural defect.
    schema = load_json(V2_SCHEMA_ROOT / "browser-lap-sector-sidecar.schema.json")
    payload = v2_browser_lap_sector_sidecar_payload(qualifying=True)
    mutation(payload)

    # Act / Assert: the frozen v2 schema rejects the malformed phase payload.
    with pytest.raises(ValidationError):
        validate_instance(schema, payload, v2_schema_registry)


# ===========================================================================
# V2 qualifying timeline contract
# ===========================================================================

V2_QUALIFYING_TIMELINE_SCHEMA_ID = "urn:f1-cache-replay:schema:replay-data:v2:qualifying-timeline"


def qualifying_timeline_payload(
    *, intervals=None, markers=None, start_ms=0, end_ms=600_000,
) -> dict:
    """Return a schema-valid v2 qualifying timeline payload."""
    return {
        "contractVersion": "v2",
        "fixtureId": "2026-australian-qualifying",
        "startMs": start_ms,
        "endMs": end_ms,
        "intervals": intervals if intervals is not None else [
            {"kind": "yellow", "startMs": 10_000, "endMs": 20_000},
            {"kind": "red", "startMs": 30_000, "endMs": 40_000},
        ],
        "incidentMarkers": markers if markers is not None else [{
            "driverId": "HAM", "timeMs": 15_000,
            "source": "race-control-car-event", "rawMessage": "CAR 44 CRASH",
            "lapNumber": 7,
        }],
    }


def qualifying_timeline_reference() -> dict[str, str]:
    return {
        "path": "qualifying-timeline.json",
        "schemaId": V2_QUALIFYING_TIMELINE_SCHEMA_ID,
        "sha256": "a" * 64,
    }


def assert_qualifying_timeline_semantics(payload):
    """Semantic invariants the JSON schema cannot express directly."""
    start_ms, end_ms = payload["startMs"], payload["endMs"]
    assert start_ms < end_ms
    assert "dnfMarkers" not in payload
    assert "OUT" not in payload
    for interval in payload["intervals"]:
        assert start_ms <= interval["startMs"] < interval["endMs"] <= end_ms
    assert payload["intervals"] == sorted(
        payload["intervals"],
        key=lambda item: (item["startMs"], item["endMs"], item["kind"]),
    )
    markers = payload["incidentMarkers"]
    assert markers == sorted(
        markers, key=lambda item: (item["timeMs"], item["driverId"], item["rawMessage"])
    )
    for marker in markers:
        assert start_ms <= marker["timeMs"] < end_ms


def test_v2_qualifying_timeline_schema_is_valid_and_frozen():
    # Arrange / Act: load the v2 qualifying timeline schema.
    schema = load_json(V2_SCHEMA_ROOT / "qualifying-timeline.schema.json")

    # Assert: the schema is valid draft 2020-12 with the v2 identity, and it
    # never represents race DNF/OUT/finish semantics.
    Draft202012Validator.check_schema(schema)
    assert schema["$id"] == V2_QUALIFYING_TIMELINE_SCHEMA_ID
    assert schema["properties"]["contractVersion"]["const"] == "v2"
    assert {
        "contractVersion", "fixtureId", "startMs", "endMs",
        "intervals", "incidentMarkers",
    } <= set(schema["required"])
    assert "dnfMarkers" not in schema["properties"]
    assert "OUT" not in schema["properties"]


def test_v2_qualifying_timeline_payload_validates(v2_schema_registry):
    # Arrange
    schema = load_json(V2_SCHEMA_ROOT / "qualifying-timeline.schema.json")
    payload = qualifying_timeline_payload()

    # Act / Assert: the populated payload validates and satisfies semantics.
    validate_instance(schema, payload, v2_schema_registry)
    assert_qualifying_timeline_semantics(payload)

    # Assert: empty collections remain schema-valid; the artifact is omitted
    # entirely at publication when there is nothing to publish (fail closed).
    empty = qualifying_timeline_payload(intervals=[], markers=[])
    validate_instance(schema, empty, v2_schema_registry)
    assert_qualifying_timeline_semantics(empty)


def test_v2_qualifying_timeline_accepts_red_flag_position_freeze_source(
    v2_schema_registry,
):
    # Arrange: a schema-valid marker using the inferred freeze source.
    schema = load_json(V2_SCHEMA_ROOT / "qualifying-timeline.schema.json")
    payload = qualifying_timeline_payload(markers=[
        {"driverId": "VER", "timeMs": 40_000, "source": "red-flag-position-freeze",
         "rawMessage": "RED FLAG"},
    ])

    # Act / Assert: the new frozen source validates and passes semantics.
    validate_instance(schema, payload, v2_schema_registry)
    assert_qualifying_timeline_semantics(payload)


def test_v2_qualifying_timeline_rejects_unknown_marker_source(v2_schema_registry):
    # Arrange: a marker whose source is neither frozen value.
    schema = load_json(V2_SCHEMA_ROOT / "qualifying-timeline.schema.json")
    payload = qualifying_timeline_payload(markers=[
        {"driverId": "HAM", "timeMs": 15_000, "source": "dnf", "rawMessage": "CAR 44 CRASH"},
    ])

    # Act / Assert: unknown sources fail closed at the schema boundary.
    with pytest.raises(ValidationError):
        validate_instance(schema, payload, v2_schema_registry)


def test_v2_manifest_accepts_manifest_without_optional_qualifying_timeline(
    v2_schema_registry,
):
    # Arrange: a v2 manifest without the optional qualifying timeline
    # reference or schemas entry.
    manifest_schema = load_json(V2_SCHEMA_ROOT / "manifest.schema.json")
    legacy = v2_manifest_payload(session_mode="qualifying", qualifying_summary=True)
    assert "qualifyingTimeline" not in legacy

    # Act / Assert: the backward-compatible manifest remains valid, and the
    # optional artifact can be added without breaking the contract.
    validate_instance(manifest_schema, legacy, v2_schema_registry)
    with_timeline = dict(legacy)
    with_timeline["qualifyingTimeline"] = qualifying_timeline_reference()
    with_timeline["schemas"] = {
        **legacy["schemas"], "qualifyingTimeline": V2_QUALIFYING_TIMELINE_SCHEMA_ID,
    }
    validate_instance(manifest_schema, with_timeline, v2_schema_registry)


@pytest.mark.parametrize("mode", ["race", "sprint", "practice"])
def test_v2_manifest_rejects_qualifying_timeline_for_non_qualifying_modes(
    v2_schema_registry, mode,
):
    # Arrange: a non-qualifying manifest carrying a timeline reference.
    manifest_schema = load_json(V2_SCHEMA_ROOT / "manifest.schema.json")
    manifest = v2_manifest_payload(session_mode=mode, qualifying_summary=False)
    manifest["qualifyingTimeline"] = qualifying_timeline_reference()

    # Act / Assert: wrong-mode rejection at the schema boundary.
    with pytest.raises(ValidationError):
        validate_instance(manifest_schema, manifest, v2_schema_registry)

    # Assert: a schemas-registry leak is rejected outside qualifying-like modes.
    leaked = v2_manifest_payload(session_mode=mode, qualifying_summary=False)
    leaked["schemas"] = {
        **leaked["schemas"], "qualifyingTimeline": V2_QUALIFYING_TIMELINE_SCHEMA_ID,
    }
    with pytest.raises(ValidationError):
        validate_instance(manifest_schema, leaked, v2_schema_registry)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("path", "other-timeline.json"),
        ("schemaId", "urn:f1-cache-replay:schema:replay-data:unsupported:wrong"),
        ("sha256", "not-a-sha256"),
    ],
)
def test_v2_manifest_rejects_invalid_qualifying_timeline_reference(
    v2_schema_registry, field, value,
):
    # Arrange: a qualifying manifest with a structurally invalid reference.
    manifest_schema = load_json(V2_SCHEMA_ROOT / "manifest.schema.json")
    manifest = v2_manifest_payload(session_mode="qualifying", qualifying_summary=True)
    reference = qualifying_timeline_reference()
    reference[field] = value
    manifest["qualifyingTimeline"] = reference

    # Act / Assert: digest/path/schemaId validation fails closed.
    with pytest.raises(ValidationError):
        validate_instance(manifest_schema, manifest, v2_schema_registry)


def test_v2_qualifying_timeline_requires_deterministic_marker_ordering(
    v2_schema_registry,
):
    # Arrange: markers are schema-valid but out of deterministic order.
    schema = load_json(V2_SCHEMA_ROOT / "qualifying-timeline.schema.json")
    payload = qualifying_timeline_payload(markers=[
        {"driverId": "VER", "timeMs": 30_000, "source": "race-control-car-event",
         "rawMessage": "CAR 1 STOPS"},
        {"driverId": "HAM", "timeMs": 15_000, "source": "race-control-car-event",
         "rawMessage": "CAR 44 CRASH"},
        {"driverId": "HAM", "timeMs": 15_000, "source": "race-control-car-event",
         "rawMessage": "CAR 44 STALLED"},
    ])

    # Act / Assert: the JSON schema is order-agnostic...
    validate_instance(schema, payload, v2_schema_registry)
    # ...but the deterministic (timeMs, driverId, rawMessage) ordering is a
    # mandatory semantic enforced by the pipeline and consumers.
    with pytest.raises(AssertionError):
        assert_qualifying_timeline_semantics(payload)


def test_v2_qualifying_timeline_requires_deterministic_interval_ordering(
    v2_schema_registry,
):
    # Arrange: intervals are schema-valid but out of deterministic order.
    schema = load_json(V2_SCHEMA_ROOT / "qualifying-timeline.schema.json")
    payload = qualifying_timeline_payload(intervals=[
        {"kind": "red", "startMs": 30_000, "endMs": 40_000},
        {"kind": "yellow", "startMs": 10_000, "endMs": 20_000},
    ])

    # Act / Assert: schema-valid, but the deterministic interval ordering
    # (startMs, endMs, kind) semantic is mandatory.
    validate_instance(schema, payload, v2_schema_registry)
    with pytest.raises(AssertionError):
        assert_qualifying_timeline_semantics(payload)


# ===========================================================================
# V2-only contract/security boundary: frozen V1 identities cannot be selected
# ===========================================================================
#
# The V2-only decision in AGENTS.md is authoritative.  These focused tests
# prove that the active browser/manifest/weather boundaries accept only v2
# identities, that V1 identities (including the frozen v1 fixture material
# under contracts/replay-data/v1/) are rejected rather than selected, and
# that no V1 compatibility path is exposed by the active delivery models.
# Frozen V1 fixtures are read-only referenced here solely to assert
# rejection/non-selection; they are never activated or modified.

V1_SCHEMA_PREFIX = "urn:f1-cache-replay:schema:replay-data:v1:"


def test_active_browser_manifest_rejects_v1_contract_version():
    # Arrange: a v2-shaped manifest whose contract version claims v1.
    # Act / Assert: the active browser manifest model cannot select v1.
    with pytest.raises(ValueError, match="contract_version must be v2"):
        BrowserManifest(
            "deterministic-race",
            "Deterministic Race",
            (manifest_driver(),),
            session_mode="race",
            contract_version="v1",  # type: ignore[arg-type]
        )


def test_active_browser_manifest_rejects_v1_artifact_reference_identity():
    # Arrange: a weather sidecar reference carrying the frozen v1 schema identity.
    # Act / Assert: the manifest boundary fails closed instead of accepting v1.
    with pytest.raises(ValueError, match="schema_id is invalid"):
        BrowserManifest(
            "deterministic-race",
            "Deterministic Race",
            (manifest_driver(),),
            session_mode="race",
            weather_sidecar={
                "path": "weather-sidecar.json",
                "schemaId": f"{V1_SCHEMA_PREFIX}weather-sidecar",
                "sha256": "a" * 64,
            },
        )


@pytest.mark.parametrize(
    ("reference_type", "path", "v1_schema_id"),
    [
        (
            BrowserTimelineSummaryReference,
            "timeline-summary.json",
            f"{V1_SCHEMA_PREFIX}timeline-summary",
        ),
        (
            BrowserLapSectorSidecarReference,
            "lap-sector-sidecar.json",
            f"{V1_SCHEMA_PREFIX}browser-lap-sector-sidecar",
        ),
        (
            BrowserPenaltySidecarReference,
            "penalty-sidecar.json",
            f"{V1_SCHEMA_PREFIX}penalty-sidecar",
        ),
        (
            BrowserStintSummaryReference,
            "stint-summary.json",
            f"{V1_SCHEMA_PREFIX}stint-summary",
        ),
        (
            BrowserPitLossEstimateSidecarReference,
            PIT_LOSS_ESTIMATE_SIDECAR_FILENAME,
            f"{V1_SCHEMA_PREFIX}pit-loss-estimate-sidecar",
        ),
        (
            BrowserWeatherSidecarReference,
            "weather-sidecar.json",
            f"{V1_SCHEMA_PREFIX}weather-sidecar",
        ),
    ],
)
def test_active_browser_reference_models_reject_v1_schema_identities(
    reference_type, path, v1_schema_id,
):
    # Arrange: every active manifest reference type is bound to one exact v2
    # schema identity; the frozen v1 identity is a different namespace.
    # Act / Assert: constructing the reference with a v1 identity fails closed.
    with pytest.raises(ValueError, match="schema_id is invalid"):
        reference_type(path, v1_schema_id, "a" * 64)


def test_v2_manifest_schema_rejects_v1_contract_identity(v2_schema_registry):
    # Arrange: a schema-valid v2 manifest whose identity claims v1.
    manifest_schema = load_json(V2_SCHEMA_ROOT / "manifest.schema.json")
    manifest = v2_manifest_payload(session_mode="qualifying", qualifying_summary=True)
    manifest["contractVersion"] = "v1"

    # Act / Assert: the active v2 manifest schema rejects the v1 identity.
    with pytest.raises(ValidationError):
        validate_instance(manifest_schema, manifest, v2_schema_registry)


def test_v2_manifest_schema_rejects_v1_weather_sidecar_reference(v2_schema_registry):
    # Arrange: a v2 manifest carrying a weather sidecar reference with the v1 identity.
    manifest_schema = load_json(V2_SCHEMA_ROOT / "manifest.schema.json")
    manifest = v2_manifest_payload(session_mode="qualifying", qualifying_summary=True)
    manifest["weatherSidecar"] = {
        "path": "weather-sidecar.json",
        "schemaId": f"{V1_SCHEMA_PREFIX}weather-sidecar",
        "sha256": "a" * 64,
    }

    # Act / Assert: v1 weather references are invalid at the manifest boundary.
    with pytest.raises(ValidationError):
        validate_instance(manifest_schema, manifest, v2_schema_registry)


def test_replay_contract_weather_sidecar_rejects_v1_contract_identity(
    contract_bundle, schema_registry,
):
    # Arrange: a structurally valid weather sidecar claiming the v1 contract.
    sidecar = weather_sidecar_payload()
    sidecar["contractVersion"] = "v1"

    # Act / Assert: the active v2 weather schema fails closed on v1 identities.
    with pytest.raises(ValidationError):
        validate_instance(
            contract_bundle["schemas"]["weatherSidecar"], sidecar, schema_registry,
        )


def test_replay_contract_frozen_v1_fixture_manifest_is_not_selectable(
    v2_schema_registry,
):
    # Arrange: the frozen v1 fixture manifest, referenced read-only and never
    # activated; it remains historical material under contracts/replay-data/v1/.
    v1_manifest_path = (
        REPO_ROOT / "contracts" / "replay-data" / "v1" / "fixtures"
        / "deterministic-race" / "manifest.json"
    )
    v1_manifest = load_json(v1_manifest_path)
    assert v1_manifest["contractVersion"] == "v1"

    # Act / Assert: the active v2 manifest schema rejects the frozen v1 fixture.
    manifest_schema = load_json(V2_SCHEMA_ROOT / "manifest.schema.json")
    with pytest.raises(ValidationError):
        validate_instance(manifest_schema, v1_manifest, v2_schema_registry)


def test_active_browser_delivery_schema_identities_are_exclusively_v2():
    # Arrange: every schema identity the active browser delivery boundary exposes.
    active_schema_ids = (
        V2_MANIFEST_SCHEMA_ID,
        WEATHER_SIDECAR_SCHEMA_ID,
        TIMELINE_SUMMARY_SCHEMA_ID,
        BROWSER_LAP_SECTOR_SIDECAR_SCHEMA_ID,
        PENALTY_SIDECAR_SCHEMA_ID,
        STINT_SUMMARY_SCHEMA_ID,
        PIT_LOSS_MODEL_SCHEMA_ID,
        PIT_LOSS_ESTIMATE_SIDECAR_SCHEMA_ID,
    )

    # Act / Assert: no active identity carries the v1 contract namespace, so no
    # V1 compatibility path is exposed by the browser delivery models.
    for schema_id in active_schema_ids:
        assert schema_id.startswith("urn:f1-cache-replay:schema:replay-data:v2:")
        assert not schema_id.startswith(V1_SCHEMA_PREFIX)


def test_active_browser_manifest_serializes_only_v2_contract_identity():
    # Arrange: a v2 manifest bound to the active weather sidecar reference.
    manifest = BrowserManifest(
        "deterministic-race",
        "Deterministic Race",
        (manifest_driver(),),
        session_mode="race",
        weather_sidecar=BrowserWeatherSidecarReference(
            "weather-sidecar.json", WEATHER_SIDECAR_SCHEMA_ID, "a" * 64,
        ),
    )

    # Act
    payload = manifest.as_dict()

    # Assert: the emitted manifest identity is exclusively v2.
    assert payload["contractVersion"] == "v2"
    assert payload["formatVersion"] == "browser-delivery-v2"
    assert all(
        str(schema_id).startswith("urn:f1-cache-replay:schema:replay-data:v2:")
        for schema_id in cast(dict[str, object], payload["schemas"]).values()
    )
    assert cast(dict[str, object], payload["weatherSidecar"])["schemaId"] == WEATHER_SIDECAR_SCHEMA_ID
