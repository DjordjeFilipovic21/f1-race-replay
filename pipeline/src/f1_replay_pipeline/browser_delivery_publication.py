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

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry, Resource

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


class BrowserDeliveryPublicationError(RuntimeError):
    """Raised when browser artifacts cannot be safely validated or published."""


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
    payloads = _artifact_payloads(version, delivery)
    schemas, registry = _load_contract_schemas(schema_root)
    _validate_delivery_payloads(payloads, delivery, schemas, registry)
    return _publish_payloads(browser_parent, version, payloads)


def _artifact_payloads(version: str, delivery: BrowserDeliveryBuild) -> tuple[tuple[str, bytes], ...]:
    chunks = delivery.chunks
    _validate_chunks(chunks)
    fixture_id = delivery.manifest.fixture_id
    track_payload = _serialize_json(delivery.track_assets)
    chunk_payloads = tuple(
        (f"chunks/{chunk.chunk_id}.json", _serialize_json(_chunk_dict(chunk, fixture_id)))
        for chunk in chunks
    )
    digests = {
        path: hashlib.sha256(payload).hexdigest()
        for path, payload in (("track-assets.json", track_payload), *chunk_payloads)
    }
    manifest = delivery.manifest.as_dict()
    manifest.update({
        "formatVersion": _FORMAT_VERSION,
        "deliveryVersion": version,
        "sourceGenerationId": delivery.source.generation_id,
        "sourceManifestSha256": delivery.source.manifest_sha256,
        "trackAssets": {"path": "track-assets.json", "schemaId": _TRACK_SCHEMA, "sha256": digests["track-assets.json"]},
        "chunks": [_chunk_reference(chunk, digests[f"chunks/{chunk.chunk_id}.json"]) for chunk in chunks],
    })
    return (("manifest.json", _serialize_json(manifest)), ("track-assets.json", track_payload), *chunk_payloads)


def _validate_delivery_payloads(payloads, delivery: BrowserDeliveryBuild, schemas=None, registry=None) -> None:
    encoded = dict(payloads)
    try:
        manifest = json.loads(encoded["manifest.json"])
        track = json.loads(encoded["track-assets.json"])
    except (KeyError, json.JSONDecodeError) as error:
        raise BrowserDeliveryPublicationError("delivery metadata is incomplete") from error
    if manifest["sourceGenerationId"] != delivery.source.generation_id or manifest["sourceManifestSha256"] != delivery.source.manifest_sha256:
        raise BrowserDeliveryPublicationError("delivery provenance disagrees with its source snapshot")
    track_required = {"contractVersion", "fixtureId", "trackId", "trackName", "coordinateSpace", "circuitLengthMeters", "rotationDegrees", "startFinish", "centerLine", "innerBoundary", "outerBoundary"}
    if not track_required <= set(track) or track.get("contractVersion") != "v1" or track.get("fixtureId") != manifest["fixtureId"]:
        raise BrowserDeliveryPublicationError("track assets disagree with the manifest")
    if hashlib.sha256(encoded["track-assets.json"]).hexdigest() != manifest["trackAssets"]["sha256"]:
        raise BrowserDeliveryPublicationError("track asset digest disagrees")
    refs = manifest["chunks"]
    if len(refs) != len(delivery.chunks):
        raise BrowserDeliveryPublicationError("manifest chunk count disagrees")
    previous = None
    driver_ids = {driver["id"] for driver in manifest["drivers"]}
    expected_paths = {"manifest.json", "track-assets.json"}
    chunk_instances = []
    for sequence, (ref, expected_chunk) in enumerate(zip(refs, delivery.chunks, strict=True), start=1):
        path = ref["path"]
        expected_paths.add(path)
        if ref["sequence"] != sequence or path != f"chunks/chunk-{sequence:03d}.json" or ref["schemaId"] != _CHUNK_SCHEMA:
            raise BrowserDeliveryPublicationError("chunk references are not deterministic and contiguous")
        payload = encoded.get(path)
        if payload is None or hashlib.sha256(payload).hexdigest() != ref["sha256"]:
            raise BrowserDeliveryPublicationError("chunk digest disagrees")
        chunk = json.loads(payload)
        chunk_instances.append(chunk)
        _validate_chunk_contract(chunk, ref, driver_ids, previous)
        if chunk["chunkId"] != expected_chunk.chunk_id:
            raise BrowserDeliveryPublicationError("chunk payload disagrees with its immutable model")
        previous = ref
    if set(encoded) != expected_paths:
        raise BrowserDeliveryPublicationError("delivery contains unreferenced artifacts")
    if schemas is not None and registry is not None:
        _validate_schema_instance(schemas["manifest"], manifest, registry, "manifest")
        _validate_schema_instance(schemas["track-assets"], track, registry, "track assets")
        for chunk in chunk_instances:
            _validate_schema_instance(schemas["chunk"], chunk, registry, "chunk")


def _load_contract_schemas(schema_root: Path):
    if not isinstance(schema_root, Path):
        raise TypeError("schema_root must be a pathlib.Path")
    schemas = {}
    registry = Registry()
    try:
        for name in ("manifest", "chunk", "track-assets"):
            guarded = read_regular_file_no_follow(
                schema_root / f"{name}.schema.json", f"browser {name} schema"
            )
            schema = json.loads(guarded.data)
            Draft202012Validator.check_schema(schema)
            schemas[name] = schema
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    except (GenerationPublicationError, KeyError, json.JSONDecodeError, SchemaError, ValueError) as error:
        raise BrowserDeliveryPublicationError("invalid local replay contract schema registry") from error
    return schemas, registry


def _validate_schema_instance(schema, instance, registry, label: str) -> None:
    try:
        Draft202012Validator(
            schema,
            registry=registry,
            format_checker=Draft202012Validator.FORMAT_CHECKER,
        ).validate(instance)
    except (ValidationError, SchemaError) as error:
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
    if not times or times != sorted(set(times)) or not 0 <= index < len(times):
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


def _publish_payloads(browser_parent: Path, version: str, payloads) -> PublishedBrowserDelivery:
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
            for relative, payload in payloads:
                parent, name = (chunks_fd, relative.split("/", 1)[1]) if relative.startswith("chunks/") else (staging_fd, relative)
                _write_at(parent, name, payload)
            _validate_staged(staging_fd, chunks_fd, payloads)
        finally:
            os.close(chunks_fd)
        os.replace(staging_name, version, src_dir_fd=root_fd, dst_dir_fd=generations_fd)
        published = True
        manifest_payload = dict(payloads)["manifest.json"]
        pointer = _serialize_json({
            "formatVersion": _FORMAT_VERSION,
            "deliveryVersion": version,
            "manifestPath": f"generations/{version}/manifest.json",
            "manifestSha256": hashlib.sha256(manifest_payload).hexdigest(),
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
    digests = {path: hashlib.sha256(payload).hexdigest() for path, payload in payloads}
    chunk_paths = tuple(generation / path for path, _ in payloads if path.startswith("chunks/"))
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


def _validate_staged(staging_fd: int, chunks_fd: int, payloads) -> None:
    for relative, expected in payloads:
        parent, name = (chunks_fd, relative.split("/", 1)[1]) if relative.startswith("chunks/") else (staging_fd, relative)
        descriptor = os.open(name, os.O_RDONLY | _NO_FOLLOW, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            actual = source.read()
        if not stat.S_ISREG(metadata.st_mode) or actual != expected:
            raise BrowserDeliveryPublicationError("staged artifact differs from its validated bytes")


def _validate_chunks(chunks: tuple[BrowserChunk, ...]) -> None:
    if not chunks or tuple(chunk.sequence for chunk in chunks) != tuple(range(1, len(chunks) + 1)):
        raise BrowserDeliveryPublicationError("chunks must be a non-empty contiguous sequence")


def _chunk_reference(chunk: BrowserChunk, digest: str) -> dict[str, object]:
    overlap_ms = 0 if chunk.overlap.range_start_ms is None else chunk.start_ms - chunk.overlap.range_start_ms
    return {"sequence": chunk.sequence, "path": f"chunks/{chunk.chunk_id}.json", "schemaId": _CHUNK_SCHEMA, "startMs": chunk.start_ms, "endMs": chunk.end_ms, "overlapWithPreviousMs": overlap_ms, "sha256": digest}


def _chunk_dict(chunk: BrowserChunk, fixture_id: str) -> dict[str, object]:
    return {"contractVersion": "v1", "fixtureId": fixture_id, "chunkId": chunk.chunk_id, "sequence": chunk.sequence, "startMs": chunk.start_ms, "endMs": chunk.end_ms, "overlap": {"kind": chunk.overlap.kind, "previousChunkPath": chunk.overlap.previous_chunk_path, "range": None if chunk.overlap.range_start_ms is None else {"startMs": chunk.overlap.range_start_ms, "endMs": chunk.overlap.range_end_ms}, "authoritativeFromMs": chunk.overlap.authoritative_from_ms}, "timeMs": chunk.time_ms, "authoritativeStartIndex": chunk.authoritative_start_index, "drivers": {driver_id: _driver_dict(fields) for driver_id, fields in chunk.drivers.items()}, "leaderboardOrder": chunk.leaderboard_order, "trackStatusCode": chunk.track_status_code, "weatherState": chunk.weather_state, "events": [_event_dict(event) for event in chunk.events]}


def _driver_dict(fields: BrowserDriverFields) -> dict[str, object]:
    return {"x": fields.x, "y": fields.y, "trackDistanceMeters": fields.track_distance_meters, "speed": fields.speed, "throttle": fields.throttle, "brake": fields.brake, "gapToLeaderMs": fields.gap_to_leader_ms, "lap": fields.lap, "position": fields.position, "gear": fields.gear, "drs": fields.drs, "tyreCompound": fields.tyre_compound, "status": fields.status, "isInPitLane": fields.is_in_pit_lane}


def _event_dict(event: BrowserEvent) -> dict[str, object]:
    value = {"sessionTimeMs": event.session_time_ms, "eventType": event.event_type, "description": event.description, "driverId": event.driver_id}
    if event.payload is not None:
        value["payload"] = event.payload
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
