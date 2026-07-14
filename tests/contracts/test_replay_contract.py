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
