import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry, Resource


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = REPO_ROOT / "contracts" / "replay-data" / "v1"
SCHEMA_ROOT = CONTRACT_ROOT / "schemas"
FIXTURE_ROOT = CONTRACT_ROOT / "fixtures" / "deterministic-race"

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

    assert manifest["contractVersion"] == "v1"
    assert manifest["fixtureId"] == track_assets["fixtureId"]
    assert len(manifest["chunks"]) == 2

    manifest_driver_ids = {driver["id"] for driver in manifest["drivers"]}
    assert manifest_driver_ids == {"HAM", "RUS"}

    previous_ref = None
    for chunk_ref in manifest["chunks"]:
        chunk = chunks[chunk_ref["path"]]

        assert chunk_ref["sequence"] == chunk["sequence"]
        assert chunk_ref["startMs"] == chunk["startMs"]
        assert chunk_ref["endMs"] == chunk["endMs"]
        assert manifest["fixtureId"] == chunk["fixtureId"]

        sample_times = [sample["sessionTimeMs"] for sample in chunk["samples"]]
        assert sample_times == sorted(sample_times)

        for sample in chunk["samples"]:
            if sample["sampleRole"] == "authoritative":
                assert chunk["startMs"] <= sample["sessionTimeMs"] < chunk["endMs"]
            else:
                overlap = chunk["overlap"]
                assert overlap["kind"] == "handoff"
                assert overlap["range"]["startMs"] <= sample["sessionTimeMs"] < overlap["range"]["endMs"]
                assert sample["sessionTimeMs"] < chunk["startMs"]

            assert set(sample["leaderboardOrder"]) == manifest_driver_ids
            assert set(sample["drivers"]) == manifest_driver_ids

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

        previous_ref = chunk_ref

    for marker in track_assets["distanceMarkersMeters"]:
        assert marker <= track_assets["circuitLengthMeters"]

    for zone in track_assets["drsZones"]:
        assert zone["startMeters"] < zone["endMeters"] <= track_assets["circuitLengthMeters"]


def sample_lookup(chunk):
    return {sample["sessionTimeMs"]: sample for sample in chunk["samples"]}


def event_lookup(bundle):
    events = {}

    for chunk_path, chunk in bundle["chunks"].items():
        for event in chunk["events"]:
            events.setdefault(event["sessionTimeMs"], []).append(copy.deepcopy(event))

    return events


def find_sample(bundle, chunk_path, session_time_ms):
    chunk = bundle["chunks"][chunk_path]
    return sample_lookup(chunk)[session_time_ms]


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
    authoritative_sample = find_sample(
        contract_bundle,
        snapshot["expectedSource"]["chunkPath"],
        snapshot["expectedSource"]["sampleTimeMs"],
    )
    overlap_reference = find_sample(
        contract_bundle,
        snapshot["nonAuthoritativeMatch"]["chunkPath"],
        snapshot["nonAuthoritativeMatch"]["sampleTimeMs"],
    )

    # Assert
    assert authoritative_sample["sampleRole"] == "authoritative"
    assert overlap_reference["sampleRole"] == "overlap_reference"
    assert snapshot["expectedSource"]["chunkPath"] != snapshot["nonAuthoritativeMatch"]["chunkPath"]


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
    assert actual["trackStatusCode"] == previous_sample["trackStatusCode"]
    assert actual["drivers"]["HAM"]["position"] == previous_sample["drivers"]["HAM"]["position"]
    assert actual["drivers"]["HAM"]["position"] != pytest.approx(linear_position)
    assert actual["drivers"]["HAM"]["status"] == previous_sample["drivers"]["HAM"]["status"]


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
