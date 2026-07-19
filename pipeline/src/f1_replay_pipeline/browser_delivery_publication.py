"""Secure deterministic publication for one immutable browser delivery build."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import cast

import jsonschema_rs

from f1_replay_pipeline.browser_chunk_builder import BrowserChunk, BrowserEvent
from f1_replay_pipeline.browser_delivery_models import BrowserDriverFields
from f1_replay_pipeline.browser_delivery_orchestration import BrowserDeliveryBuild
from f1_replay_pipeline.dataset_manifest import ManifestValidationError, serialize_deterministic_json
from f1_replay_pipeline.generation_identity import GenerationIdentityError, validate_generation_id
from f1_replay_pipeline.generation_publication import (
    GenerationPublicationError,
    LocalRecoveryLock,
    _open_directory_no_follow,
    _remove_owned_file_at,
    _remove_owned_tree_at,
    _require_safe_directory,
    _require_safe_existing_ancestors,
    read_regular_file_no_follow,
)


_FORMAT_VERSION = "browser-delivery-v1"
_STAGING_PREFIX = ".browser-delivery-staging-"
_NO_FOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_CHUNK_SCHEMA = "urn:f1-cache-replay:schema:replay-data:v1:chunk"
_TRACK_SCHEMA = "urn:f1-cache-replay:schema:replay-data:v1:track-assets"

_make_contract_validator = jsonschema_rs.Draft202012Validator


class BrowserDeliveryPublicationError(RuntimeError):
    """Raised when browser artifacts cannot be safely validated or published."""


@dataclass(frozen=True)
class PreparedArtifact:
    """Validated deterministic bytes and their one authoritative digest."""

    path: str
    payload: bytes
    sha256: str


@dataclass(frozen=True)
class PublishedBrowserDelivery:
    delivery_version: str
    generation_path: Path
    manifest_path: Path
    pointer_path: Path
    track_assets_path: Path
    chunk_paths: tuple[Path, ...]
    artifact_digests: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "chunk_paths", tuple(self.chunk_paths))
        object.__setattr__(self, "artifact_digests", MappingProxyType(dict(self.artifact_digests)))


def publish_browser_delivery(
    *, browser_parent: Path, delivery_version: str, delivery: BrowserDeliveryBuild,
    schema_root: Path,
) -> PublishedBrowserDelivery:
    """Validate, stage, and atomically select artifacts from one bound snapshot."""
    if not isinstance(delivery, BrowserDeliveryBuild):
        raise TypeError("delivery must be a BrowserDeliveryBuild")
    version = _safe_delivery_version(delivery_version)
    schemas, registry = _load_contract_schemas(schema_root)
    validators = _contract_validators(schemas, registry)
    artifacts = _prepared_artifacts(version, delivery, validators)
    return _publish_payloads(browser_parent, version, artifacts)


def _prepared_artifacts(version: str, delivery: BrowserDeliveryBuild, validators) -> tuple[PreparedArtifact, ...]:
    chunks = delivery.chunks
    _validate_chunks(chunks)
    fixture_id = delivery.manifest.fixture_id
    manifest = delivery.manifest.as_dict()
    schema_track_assets = _schema_compatible_value(delivery.track_assets)
    _validate_track_contract(schema_track_assets, manifest)
    _validate_schema_instance(validators["track-assets"], schema_track_assets, "track assets")
    track = _prepare_artifact("track-assets.json", delivery.track_assets)

    previous = None
    chunk_artifacts = []
    references = []
    driver_ids = {driver["id"] for driver in cast(list[Mapping[str, str]], manifest["drivers"])}
    for chunk in chunks:
        contract = _chunk_dict(chunk, fixture_id)
        reference = _chunk_reference(chunk, "")
        _validate_chunk_contract(contract, reference, driver_ids, previous)
        _validate_schema_instance(validators["chunk"], contract, "chunk")
        artifact = _prepare_artifact(f"chunks/{chunk.chunk_id}.json", contract)
        reference = _chunk_reference(chunk, artifact.sha256)
        chunk_artifacts.append(artifact)
        references.append(reference)
        previous = reference

    manifest = delivery.manifest.as_dict()
    manifest.update({
        "formatVersion": _FORMAT_VERSION,
        "deliveryVersion": version,
        "sourceGenerationId": delivery.source.generation_id,
        "sourceManifestSha256": delivery.source.manifest_sha256,
        "trackAssets": {"path": "track-assets.json", "schemaId": _TRACK_SCHEMA, "sha256": track.sha256},
        "chunks": references,
    })
    _validate_manifest_contract(manifest, delivery, references)
    _validate_schema_instance(validators["manifest"], manifest, "manifest")
    return (_prepare_artifact("manifest.json", manifest), track, *chunk_artifacts)


def _artifact_payloads(version: str, delivery: BrowserDeliveryBuild) -> tuple[PreparedArtifact, ...]:
    """Prepare fully validated artifacts for focused tests."""
    schemas, registry = _load_contract_schemas(Path(__file__).parents[3] / "contracts" / "replay-data" / "v1" / "schemas")
    return _prepared_artifacts(version, delivery, _contract_validators(schemas, registry))


def _validate_delivery_payloads(artifacts, delivery: BrowserDeliveryBuild, schemas=None, registry=None) -> None:
    """Verify prepared bytes remain bound to their preparation digest.

    Production validation occurs before serialization on direct immutable contract
    objects; this focused helper protects mutation tests without reparsing JSON.
    """
    encoded = {artifact.path: artifact for artifact in artifacts}
    try:
        manifest = encoded["manifest.json"]
        track = encoded["track-assets.json"]
    except KeyError as error:
        raise BrowserDeliveryPublicationError("delivery metadata is incomplete") from error
    if hashlib.sha256(manifest.payload).hexdigest() != manifest.sha256 or hashlib.sha256(track.payload).hexdigest() != track.sha256:
        raise BrowserDeliveryPublicationError("prepared artifact digest disagrees")
    expected_paths = {"manifest.json", "track-assets.json", *(f"chunks/{chunk.chunk_id}.json" for chunk in delivery.chunks)}
    if set(encoded) != expected_paths or any(hashlib.sha256(artifact.payload).hexdigest() != artifact.sha256 for artifact in encoded.values()):
        raise BrowserDeliveryPublicationError("prepared artifact digest disagrees")


def _validate_manifest_contract(manifest, delivery: BrowserDeliveryBuild, refs) -> None:
    if manifest["sourceGenerationId"] != delivery.source.generation_id or manifest["sourceManifestSha256"] != delivery.source.manifest_sha256:
        raise BrowserDeliveryPublicationError("delivery provenance disagrees with its source snapshot")
    if len(refs) != len(delivery.chunks):
        raise BrowserDeliveryPublicationError("manifest chunk count disagrees")
    _validate_lap_starts(manifest.get("lapStarts", []), refs)
    for sequence, (ref, expected_chunk) in enumerate(zip(refs, delivery.chunks, strict=True), start=1):
        path = ref["path"]
        if ref["sequence"] != sequence or path != f"chunks/chunk-{sequence:03d}.json" or ref["schemaId"] != _CHUNK_SCHEMA:
            raise BrowserDeliveryPublicationError("chunk references are not deterministic and contiguous")
        if ref["path"] != f"chunks/{expected_chunk.chunk_id}.json":
            raise BrowserDeliveryPublicationError("chunk payload disagrees with its immutable model")


def _validate_track_contract(track, manifest) -> None:
    required = {"contractVersion", "fixtureId", "trackId", "trackName", "coordinateSpace", "circuitLengthMeters", "rotationDegrees", "startFinish", "centerLine", "innerBoundary", "outerBoundary"}
    if not required <= set(track) or track.get("contractVersion") != "v1" or track.get("fixtureId") != manifest["fixtureId"]:
        raise BrowserDeliveryPublicationError("track assets disagree with the manifest")


def _validate_lap_starts(markers, refs) -> None:
    if any(
        following["lap"] <= current["lap"] or following["startMs"] < current["startMs"]
        for current, following in zip(markers, markers[1:], strict=False)
    ):
        raise BrowserDeliveryPublicationError("manifest lap starts must be ordered")
    if any(marker["startMs"] < refs[0]["startMs"] or marker["startMs"] >= refs[-1]["endMs"] for marker in markers):
        raise BrowserDeliveryPublicationError("manifest lap starts must be within replay bounds")


def _load_contract_schemas(
    schema_root: Path,
) -> tuple[dict[str, Mapping[str, object]], jsonschema_rs.Registry]:
    if not isinstance(schema_root, Path):
        raise TypeError("schema_root must be a pathlib.Path")
    schemas: dict[str, Mapping[str, object]] = {}
    try:
        for name in ("manifest", "chunk", "track-assets"):
            guarded = read_regular_file_no_follow(
                schema_root / f"{name}.schema.json", f"browser {name} schema"
            )
            schema = cast(Mapping[str, object], json.loads(guarded.data))
            schemas[name] = schema
        registry = jsonschema_rs.Registry(
            [(cast(str, schema["$id"]), dict(schema)) for schema in schemas.values()],
            draft=jsonschema_rs.Draft202012,
        )
    except (
        GenerationPublicationError, KeyError, json.JSONDecodeError,
        jsonschema_rs.ValidationError, jsonschema_rs.ReferencingError, ValueError, TypeError,
    ) as error:
        raise BrowserDeliveryPublicationError("invalid local replay contract schema registry") from error
    return schemas, registry


def _contract_validators(
    schemas: Mapping[str, Mapping[str, object]], registry: jsonschema_rs.Registry,
) -> dict[str, jsonschema_rs.Draft202012Validator]:
    """Compile one local-only Rust validator per artifact type for a publication."""
    try:
        return {
            name: _make_contract_validator(
                dict(schema), registry=registry, validate_formats=True,
                ignore_unknown_formats=False,
            )
            for name, schema in schemas.items()
        }
    except (jsonschema_rs.ValidationError, jsonschema_rs.ReferencingError, ValueError, TypeError) as error:
        raise BrowserDeliveryPublicationError("invalid local replay contract schema registry") from error


def _validate_schema_instance(
    validator: jsonschema_rs.Draft202012Validator, instance: object, label: str,
) -> None:
    try:
        validator.validate(instance)
    except (jsonschema_rs.ValidationError, jsonschema_rs.ReferencingError, ValueError, TypeError) as error:
        raise BrowserDeliveryPublicationError(
            f"{label} fails replay-data v1 schema validation"
        ) from error


def _validate_chunk_contract(chunk, ref, driver_ids, previous) -> None:
    required = {"contractVersion", "fixtureId", "chunkId", "sequence", "startMs", "endMs", "overlap", "timeMs", "authoritativeStartIndex", "drivers", "leaderboardOrder", "trackStatusCode", "weatherState", "events"}
    if not required <= set(chunk) or chunk["contractVersion"] != "v1":
        raise BrowserDeliveryPublicationError("chunk structure is incomplete")
    if (chunk["sequence"], chunk["startMs"], chunk["endMs"]) != (ref["sequence"], ref["startMs"], ref["endMs"]):
        raise BrowserDeliveryPublicationError("chunk metadata disagrees with its reference")
    times, index = chunk["timeMs"], chunk["authoritativeStartIndex"]
    if not times or tuple(times) != tuple(sorted(set(times))) or not 0 <= index < len(times):
        raise BrowserDeliveryPublicationError("chunk timeline or authority index is invalid")
    if any(not chunk["startMs"] <= value < chunk["endMs"] for value in times[index:]) or any(value >= chunk["startMs"] for value in times[:index]):
        raise BrowserDeliveryPublicationError("chunk ownership is invalid")
    overlap = chunk["overlap"]
    if previous is None:
        if overlap != {"kind": "none", "previousChunkPath": None, "range": None, "authoritativeFromMs": None} or ref["overlapWithPreviousMs"] != 0:
            raise BrowserDeliveryPublicationError("first chunk overlap is invalid")
    elif previous["endMs"] != ref["startMs"] or overlap["previousChunkPath"] != previous["path"] or overlap["authoritativeFromMs"] != ref["startMs"]:
        raise BrowserDeliveryPublicationError("chunk handoff is invalid")
    if set(chunk["drivers"]) != driver_ids:
        raise BrowserDeliveryPublicationError("chunk drivers disagree with the manifest")
    aligned = (chunk["leaderboardOrder"], chunk["trackStatusCode"], chunk["weatherState"])
    aligned += tuple(column for fields in chunk["drivers"].values() for column in fields.values())
    if any(len(column) != len(times) for column in aligned):
        raise BrowserDeliveryPublicationError("chunk columns are not aligned")
    if any(not chunk["startMs"] <= event["sessionTimeMs"] < chunk["endMs"] for event in chunk["events"]):
        raise BrowserDeliveryPublicationError("event is outside its owning chunk")


def _publish_payloads(browser_parent: Path, version: str, artifacts: tuple[PreparedArtifact, ...]) -> PublishedBrowserDelivery:
    root = Path(os.path.abspath(browser_parent))
    try:
        _require_safe_existing_ancestors(root, "browser publication root")
        root.mkdir(parents=True, exist_ok=True)
        _require_safe_directory(root, "browser publication root")
        root_fd = _open_directory_no_follow(root)
    except (GenerationPublicationError, OSError) as error:
        raise BrowserDeliveryPublicationError("browser publication root must not traverse symlinks") from error
    try:
        lease = LocalRecoveryLock().acquire(root)
    except GenerationPublicationError as error:
        os.close(root_fd)
        raise BrowserDeliveryPublicationError("unable to acquire browser publication ownership") from error
    generations_fd = staging_fd = None
    staging_name = f"{_STAGING_PREFIX}{uuid.uuid4().hex}"
    staging_identity = pointer_identity = None
    pointer_temp = f"{_STAGING_PREFIX}pointer-{uuid.uuid4().hex}"
    published = False
    try:
        try:
            os.mkdir("generations", mode=0o700, dir_fd=root_fd)
        except FileExistsError:
            pass
        generations_fd = os.open("generations", os.O_RDONLY | _DIRECTORY | _NO_FOLLOW, dir_fd=root_fd)
        try:
            os.stat(version, dir_fd=generations_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise BrowserDeliveryPublicationError("refusing to overwrite an existing browser delivery")
        os.mkdir(staging_name, mode=0o700, dir_fd=root_fd)
        staging_fd = os.open(staging_name, os.O_RDONLY | _DIRECTORY | _NO_FOLLOW, dir_fd=root_fd)
        metadata = os.fstat(staging_fd)
        staging_identity = (metadata.st_dev, metadata.st_ino)
        os.mkdir("chunks", mode=0o700, dir_fd=staging_fd)
        chunks_fd = os.open("chunks", os.O_RDONLY | _DIRECTORY | _NO_FOLLOW, dir_fd=staging_fd)
        try:
            for artifact in artifacts:
                relative, payload = artifact.path, artifact.payload
                parent, name = (chunks_fd, relative.split("/", 1)[1]) if relative.startswith("chunks/") else (staging_fd, relative)
                _write_at(parent, name, payload)
            _validate_staged(staging_fd, chunks_fd, artifacts)
        finally:
            os.close(chunks_fd)
        os.replace(staging_name, version, src_dir_fd=root_fd, dst_dir_fd=generations_fd)
        published = True
        manifest = next(artifact for artifact in artifacts if artifact.path == "manifest.json")
        pointer = _serialize_json({
            "formatVersion": _FORMAT_VERSION,
            "deliveryVersion": version,
            "manifestPath": f"generations/{version}/manifest.json",
            "manifestSha256": manifest.sha256,
        })
        pointer_fd = os.open(pointer_temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NO_FOLLOW, 0o600, dir_fd=root_fd)
        try:
            pointer_metadata = os.fstat(pointer_fd)
            pointer_identity = (pointer_metadata.st_dev, pointer_metadata.st_ino)
            _write_open(pointer_fd, pointer)
        finally:
            os.close(pointer_fd)
        os.replace(pointer_temp, "browser-current.json", src_dir_fd=root_fd, dst_dir_fd=root_fd)
        pointer_identity = None
        os.fsync(generations_fd)
        os.fsync(root_fd)
    except (BrowserDeliveryPublicationError, OSError, TypeError) as error:
        if isinstance(error, BrowserDeliveryPublicationError):
            raise
        raise BrowserDeliveryPublicationError("secure browser publication failed") from error
    finally:
        if staging_fd is not None:
            os.close(staging_fd)
        if not published and staging_identity is not None:
            try:
                _remove_owned_tree_at(root_fd, staging_name, staging_identity)
            except FileNotFoundError:
                pass
        if pointer_identity is not None:
            try:
                _remove_owned_file_at(root_fd, pointer_temp, pointer_identity)
            except FileNotFoundError:
                pass
        if generations_fd is not None:
            os.close(generations_fd)
        lease.release()
        os.close(root_fd)
    generation = root / "generations" / version
    digests = {artifact.path: artifact.sha256 for artifact in artifacts}
    chunk_paths = tuple(generation / artifact.path for artifact in artifacts if artifact.path.startswith("chunks/"))
    return PublishedBrowserDelivery(version, generation, generation / "manifest.json", root / "browser-current.json", generation / "track-assets.json", chunk_paths, digests)


def _write_at(directory_fd: int, name: str, payload: bytes) -> None:
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NO_FOLLOW, 0o600, dir_fd=directory_fd)
    try:
        _write_open(descriptor, payload)
    finally:
        os.close(descriptor)


def _write_open(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write while staging browser artifact")
        view = view[written:]
    os.fsync(descriptor)


def _validate_staged(staging_fd: int, chunks_fd: int, artifacts: tuple[PreparedArtifact, ...]) -> None:
    for artifact in artifacts:
        relative = artifact.path
        parent, name = (chunks_fd, relative.split("/", 1)[1]) if relative.startswith("chunks/") else (staging_fd, relative)
        descriptor = os.open(name, os.O_RDONLY | _NO_FOLLOW, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as source:
            before = os.fstat(source.fileno())
            digest = hashlib.sha256()
            while block := source.read(64 * 1024):
                digest.update(block)
            after = os.fstat(source.fileno())
        stable_identity = (
            before.st_dev, before.st_ino, before.st_mode, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns,
        ) == (
            after.st_dev, after.st_ino, after.st_mode, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns,
        )
        if not stable_identity or not stat.S_ISREG(after.st_mode) or after.st_size != len(artifact.payload) or digest.hexdigest() != artifact.sha256:
            raise BrowserDeliveryPublicationError("staged artifact differs from its validated bytes")


def _validate_chunks(chunks: tuple[BrowserChunk, ...]) -> None:
    if not chunks or tuple(chunk.sequence for chunk in chunks) != tuple(range(1, len(chunks) + 1)):
        raise BrowserDeliveryPublicationError("chunks must be a non-empty contiguous sequence")


def _chunk_reference(chunk: BrowserChunk, digest: str) -> dict[str, object]:
    overlap_ms = 0 if chunk.overlap.range_start_ms is None else chunk.start_ms - chunk.overlap.range_start_ms
    return {"sequence": chunk.sequence, "path": f"chunks/{chunk.chunk_id}.json", "schemaId": _CHUNK_SCHEMA, "startMs": chunk.start_ms, "endMs": chunk.end_ms, "overlapWithPreviousMs": overlap_ms, "sha256": digest}


def _prepare_artifact(path: str, contract: object) -> PreparedArtifact:
    payload = _serialize_json(contract)
    return PreparedArtifact(path, payload, hashlib.sha256(payload).hexdigest())


def _chunk_dict(chunk: BrowserChunk, fixture_id: str) -> dict[str, object]:
    return {"contractVersion": "v1", "fixtureId": fixture_id, "chunkId": chunk.chunk_id, "sequence": chunk.sequence, "startMs": chunk.start_ms, "endMs": chunk.end_ms, "overlap": {"kind": chunk.overlap.kind, "previousChunkPath": chunk.overlap.previous_chunk_path, "range": None if chunk.overlap.range_start_ms is None else {"startMs": chunk.overlap.range_start_ms, "endMs": chunk.overlap.range_end_ms}, "authoritativeFromMs": chunk.overlap.authoritative_from_ms}, "timeMs": chunk.time_ms, "authoritativeStartIndex": chunk.authoritative_start_index, "drivers": {driver_id: _driver_dict(fields) for driver_id, fields in chunk.drivers.items()}, "leaderboardOrder": chunk.leaderboard_order, "trackStatusCode": chunk.track_status_code, "weatherState": chunk.weather_state, "events": [_event_dict(event) for event in chunk.events]}


def _driver_dict(fields: BrowserDriverFields) -> dict[str, object]:
    return {"x": fields.x, "y": fields.y, "trackDistanceMeters": fields.track_distance_meters, "speed": fields.speed, "throttle": fields.throttle, "brake": fields.brake, "gapToLeaderMs": fields.gap_to_leader_ms, "lap": fields.lap, "position": fields.position, "gear": fields.gear, "drs": fields.drs, "tyreCompound": fields.tyre_compound, "status": fields.status, "isInPitLane": fields.is_in_pit_lane}


def _event_dict(event: BrowserEvent) -> dict[str, object]:
    value = {"sessionTimeMs": event.session_time_ms, "eventType": event.event_type, "description": event.description, "driverId": event.driver_id}
    if event.payload is not None:
        value["payload"] = _schema_compatible_value(event.payload)
    return value


def _schema_compatible_value(value: object) -> object:
    """Copy small immutable metadata containers into Rust-supported JSON containers."""
    if type(value) is dict:
        converted = None
        for key, entry in value.items():
            normalized = _schema_compatible_value(entry)
            if converted is None and normalized is not entry:
                converted = dict(value)
            if converted is not None:
                converted[key] = normalized
        return value if converted is None else converted
    if isinstance(value, Mapping):
        return {key: _schema_compatible_value(entry) for key, entry in value.items()}
    if isinstance(value, tuple):
        converted = None
        for index, entry in enumerate(value):
            normalized = _schema_compatible_value(entry)
            if converted is None and normalized is not entry:
                converted = list(value[:index])
            if converted is not None:
                converted.append(normalized)
        return value if converted is None else tuple(converted)
    if isinstance(value, list):
        converted = None
        for index, entry in enumerate(value):
            normalized = _schema_compatible_value(entry)
            if converted is None and normalized is not entry:
                converted = value[:index]
            if converted is not None:
                converted.append(normalized)
        return value if converted is None else converted
    return value


def _serialize_json(value: object) -> bytes:
    try:
        return serialize_deterministic_json(value)
    except ManifestValidationError as error:
        if "NaN or infinity" in str(error):
            raise ValueError("value must contain only finite numbers") from error
        raise


def _safe_delivery_version(value: object) -> str:
    try:
        return validate_generation_id(value)
    except GenerationIdentityError as error:
        raise BrowserDeliveryPublicationError(str(error)) from error


__all__ = ["BrowserDeliveryPublicationError", "PublishedBrowserDelivery", "publish_browser_delivery"]
