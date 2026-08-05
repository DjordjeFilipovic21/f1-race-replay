"""Secure deterministic publication for one immutable browser delivery build."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import cast

import jsonschema_rs

from f1_replay_pipeline.delivery.browser.browser_chunk_builder import BrowserChunk, BrowserEvent
from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    BrowserDriverFields,
    BROWSER_LAP_SECTOR_SIDECAR_SCHEMA_ID,
    CURATED_BASELINE_METHOD,
    LEGACY_PIT_LOSS_ESTIMATE_METHOD,
    MAX_INT64,
    PENALTY_SIDECAR_SCHEMA_ID,
    PIT_LOSS_MODEL_SCHEMA_ID,
    PIT_LOSS_ESTIMATE_SIDECAR_FILENAME,
    PIT_LOSS_ESTIMATE_SIDECAR_SCHEMA_ID,
    STINT_SUMMARY_SCHEMA_ID,
    TIMELINE_SUMMARY_SCHEMA_ID,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_orchestration import BrowserDeliveryBuild
from f1_replay_pipeline.delivery.browser.browser_pit_loss_track_identity import (
    TrackIdentityLookupError,
    resolve_binding_identity,
)
from f1_replay_pipeline.domain.dataset_manifest import ManifestValidationError, serialize_deterministic_json
from f1_replay_pipeline.domain.generation_identity import GenerationIdentityError, validate_generation_id
from f1_replay_pipeline.storage.generation_publication import (
    GenerationPublicationError,
    LocalRecoveryLock,
    RecoveryOwnershipError,
    _attach_cleanup_errors,
    _open_directory_no_follow,
    _remove_owned_file_at,
    _remove_owned_tree_at,
    _require_safe_directory,
    _require_safe_existing_ancestors,
    read_regular_file_no_follow,
    verify_regular_file_identity,
)


_FORMAT_VERSION = "browser-delivery-v1"
_STAGING_PREFIX = ".browser-delivery-staging-"
_NO_FOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_CHUNK_SCHEMA = "urn:f1-cache-replay:schema:replay-data:v1:chunk"
_TRACK_SCHEMA = "urn:f1-cache-replay:schema:replay-data:v1:track-assets"
_TIMELINE_SUMMARY_SCHEMA = TIMELINE_SUMMARY_SCHEMA_ID
_BROWSER_LAP_SECTOR_SIDECAR_SCHEMA = BROWSER_LAP_SECTOR_SIDECAR_SCHEMA_ID
_STINT_SUMMARY_SCHEMA = STINT_SUMMARY_SCHEMA_ID
_PIT_LOSS_MODEL_SCHEMA = PIT_LOSS_MODEL_SCHEMA_ID
_PIT_LOSS_ESTIMATE_SIDECAR_SCHEMA = PIT_LOSS_ESTIMATE_SIDECAR_SCHEMA_ID
_PENALTY_SIDECAR_SCHEMA = PENALTY_SIDECAR_SCHEMA_ID
_POINTER_FIELDS = frozenset({"formatVersion", "deliveryVersion", "manifestPath", "manifestSha256"})
_UNSUPPORTED_DIRECTORY_FSYNC = {errno.EINVAL, errno.ENOTSUP, errno.EBADF}

_make_contract_validator = jsonschema_rs.Draft202012Validator


class BrowserDeliveryPublicationError(RuntimeError):
    """Raised when browser artifacts cannot be safely validated or published."""


class BrowserDeliveryDurabilityUncertainError(BrowserDeliveryPublicationError):
    """The browser pointer committed, but final directory durability is unknown."""

    def __init__(self, result: "PublishedBrowserDelivery", cause: BaseException) -> None:
        super().__init__("browser pointer was replaced, but post-commit durability is uncertain")
        self.result = result
        self.committed = True
        self.durability_confirmed = False
        self.cause = cause


class BrowserDeliveryCommittedError(BrowserDeliveryPublicationError):
    """The browser commit and requested durability completed before a later failure."""

    def __init__(self, result: "PublishedBrowserDelivery", cause: BaseException) -> None:
        super().__init__("browser pointer was replaced and durably synced, but publication completed with an error")
        self.result = result
        self.committed = True
        self.durability_confirmed = True
        self.cause = cause


class BrowserDeliveryCleanupError(BrowserDeliveryPublicationError):
    """Publication completed, but temporary cleanup or ownership release failed."""

    def __init__(
        self,
        cleanup_errors: tuple[BaseException, ...],
        result: "PublishedBrowserDelivery | None" = None,
    ) -> None:
        super().__init__("browser publication completed with cleanup failures")
        self.cleanup_errors = cleanup_errors
        self.result = result
        self.committed = result is not None
        self.durability_confirmed = result is not None


@dataclass(frozen=True)
class BrowserValidationProgress:
    """One completed validation unit within browser delivery verification."""

    phase: str
    completed: int
    total: int
    detail: str


ProgressCallback = Callable[[str | BrowserValidationProgress], None]


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
    timeline_summary_path: Path | None = None
    lap_sector_sidecar_path: Path | None = None
    stint_summary_path: Path | None = None
    pit_loss_model_path: Path | None = None
    penalty_sidecar_path: Path | None = None
    pit_loss_estimate_sidecar_path: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "chunk_paths", tuple(self.chunk_paths))
        object.__setattr__(self, "artifact_digests", MappingProxyType(dict(self.artifact_digests)))


def publish_browser_delivery(
    *, browser_parent: Path, delivery_version: str, delivery: BrowserDeliveryBuild,
    schema_root: Path, progress: ProgressCallback | None = None,
) -> PublishedBrowserDelivery:
    """Validate, stage, and atomically select artifacts from one bound snapshot."""
    emit = progress or (lambda _stage: None)
    if not isinstance(delivery, BrowserDeliveryBuild):
        raise TypeError("delivery must be a BrowserDeliveryBuild")
    version = _safe_delivery_version(delivery_version)
    emit("browser_payload_preparing")
    emit("browser_contract_schema_loading")
    schemas, registry = _load_contract_schemas(schema_root)
    validators = _contract_validators(schemas, registry)
    artifacts = _prepared_artifacts(version, delivery, validators, progress=emit)
    return _publish_payloads(browser_parent, version, artifacts, progress=emit)


def validate_complete_browser_delivery(
    browser_parent: Path,
    *,
    expected_generation_id: str,
    expected_manifest_sha256: str,
    schema_root: Path,
    pointer_path: Path | None = None,
    progress: ProgressCallback | None = None,
) -> None:
    """Deeply validate the pointer-selected browser delivery and all payloads."""
    emit = progress or (lambda _stage: None)
    try:
        _require_safe_directory(browser_parent, "browser delivery root")
        selected_pointer_path = pointer_path or (browser_parent / "browser-current.json")
        pointer_file = read_regular_file_no_follow(selected_pointer_path, "browser current pointer")
        pointer = json.loads(pointer_file.data)
        version = validate_browser_delivery_pointer(pointer)
        generation = browser_parent / "generations" / version
        _require_safe_directory(browser_parent / "generations", "browser generations")
        _require_safe_directory(generation, "browser selected delivery")
        manifest_path = generation / "manifest.json"
        manifest_file = read_regular_file_no_follow(manifest_path, "browser manifest")
        if pointer.get("manifestSha256") != hashlib.sha256(manifest_file.data).hexdigest():
            raise ValueError("browser pointer manifest checksum disagrees")
        manifest = json.loads(manifest_file.data)
        if (
            manifest.get("deliveryVersion") != version
            or manifest.get("sourceGenerationId") != expected_generation_id
            or manifest.get("sourceManifestSha256") != expected_manifest_sha256
        ):
            raise ValueError("browser delivery provenance disagrees with canonical generation")
        timeline_reference = manifest.get("timelineSummary")
        sidecar_reference = manifest.get("lapSectorSidecar")
        stint_reference = manifest.get("stintSummary")
        pit_loss_reference = manifest.get("pitLossModel")
        penalty_reference = manifest.get("penaltySidecar")
        pit_loss_estimate_reference = manifest.get("pitLossEstimateSidecar")
        references = (
            manifest.get("trackAssets"),
            *((timeline_reference,) if timeline_reference is not None else ()),
            *((sidecar_reference,) if sidecar_reference is not None else ()),
            *((stint_reference,) if stint_reference is not None else ()),
            *((pit_loss_reference,) if pit_loss_reference is not None else ()),
            *((penalty_reference,) if penalty_reference is not None else ()),
            *((pit_loss_estimate_reference,) if pit_loss_estimate_reference is not None else ()),
            *(manifest.get("chunks") or ()),
        )
        payloads = [("manifest.json", manifest_file.data)]
        for reference in references:
            if not isinstance(reference, dict):
                raise ValueError("browser artifact reference must be an object")
            relative = _safe_delivery_path(reference.get("path"))
            artifact = _stored_delivery_file(generation, relative)
            guarded = read_regular_file_no_follow(artifact, f"browser artifact {relative}")
            if hashlib.sha256(guarded.data).hexdigest() != reference.get("sha256"):
                raise ValueError(f"browser artifact checksum disagrees for {relative}")
            verify_regular_file_identity(artifact, guarded, f"browser artifact {relative}")
            payloads.append((relative, guarded.data))
        emit("browser_contract_schema_loading")
        schemas, registry = _load_contract_schemas(schema_root)
        _validate_stored_delivery_payloads(payloads, _contract_validators(schemas, registry), emit)
        verify_regular_file_identity(manifest_path, manifest_file, "browser manifest")
        verify_regular_file_identity(selected_pointer_path, pointer_file, "browser current pointer")
    except (GenerationPublicationError, BrowserDeliveryPublicationError, OSError, TypeError, ValueError, KeyError, AttributeError, json.JSONDecodeError) as error:
        raise BrowserDeliveryPublicationError("browser delivery validation failed") from error


def _safe_delivery_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("browser artifact path must be a safe relative POSIX path")
    parts = value.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise ValueError("browser artifact path escapes its delivery")
    for part in parts:
        _safe_delivery_version(part)
    return value


def validate_browser_delivery_pointer(pointer: object) -> str:
    """Return the selected safe version only for an exact current-pointer shape."""
    if not isinstance(pointer, dict) or set(pointer) != _POINTER_FIELDS:
        raise BrowserDeliveryPublicationError("browser current pointer has an invalid shape")
    if pointer["formatVersion"] != _FORMAT_VERSION:
        raise BrowserDeliveryPublicationError("browser current pointer format version disagrees")
    version = _safe_delivery_version(pointer["deliveryVersion"])
    if pointer["manifestPath"] != f"generations/{version}/manifest.json":
        raise BrowserDeliveryPublicationError("browser pointer manifest path disagrees")
    manifest_sha256 = pointer["manifestSha256"]
    if not isinstance(manifest_sha256, str) or len(manifest_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in manifest_sha256
    ):
        raise BrowserDeliveryPublicationError("browser pointer manifest checksum must be a lowercase SHA-256 digest")
    return version


def _stored_delivery_file(generation: Path, relative: str) -> Path:
    path = generation
    _require_safe_directory(path, "browser selected delivery")
    parts = relative.split("/")
    for part in parts[:-1]:
        path /= part
        _require_safe_directory(path, "browser artifact parent")
    return path / parts[-1]


def _validate_stored_delivery_payloads(payloads, validators, emit: ProgressCallback) -> None:
    encoded = dict(payloads)
    manifest = json.loads(encoded["manifest.json"])
    track = json.loads(encoded["track-assets.json"])
    if not isinstance(manifest, dict) or not isinstance(track, dict):
        raise BrowserDeliveryPublicationError("delivery metadata must be JSON objects")
    _validate_schema_instance(validators["manifest"], manifest, "manifest")
    chunk_refs = manifest.get("chunks")
    if not isinstance(chunk_refs, list):
        raise BrowserDeliveryPublicationError("manifest chunks must be an array")
    track_reference = manifest.get("trackAssets")
    if not isinstance(track_reference, dict) or track_reference.get("path") != "track-assets.json":
        raise BrowserDeliveryPublicationError("manifest track asset reference is invalid")
    if hashlib.sha256(encoded["track-assets.json"]).hexdigest() != track_reference.get("sha256"):
        raise BrowserDeliveryPublicationError("track asset digest disagrees")
    track_required = {"contractVersion", "fixtureId", "trackId", "trackName", "coordinateSpace", "circuitLengthMeters", "rotationDegrees", "startFinish", "centerLine", "innerBoundary", "outerBoundary"}
    if not track_required <= set(track) or track.get("contractVersion") != "v1" or track.get("fixtureId") != manifest.get("fixtureId"):
        raise BrowserDeliveryPublicationError("track assets disagree with the manifest")
    driver_ids = {driver["id"] for driver in manifest["drivers"]}
    expected_paths = {"manifest.json", "track-assets.json"}
    timeline_reference = manifest.get("timelineSummary")
    if timeline_reference is not None:
        if not isinstance(timeline_reference, dict) or timeline_reference.get("path") != "timeline-summary.json":
            raise BrowserDeliveryPublicationError("manifest timeline summary reference is invalid")
        expected_paths.add("timeline-summary.json")
    sidecar_reference = manifest.get("lapSectorSidecar")
    if sidecar_reference is not None:
        if not isinstance(sidecar_reference, dict) or sidecar_reference.get("path") != "lap-sector-sidecar.json":
            raise BrowserDeliveryPublicationError("manifest lap sector sidecar reference is invalid")
        expected_paths.add("lap-sector-sidecar.json")
    stint_reference = manifest.get("stintSummary")
    if stint_reference is not None:
        if not isinstance(stint_reference, dict) or stint_reference.get("path") != "stint-summary.json":
            raise BrowserDeliveryPublicationError("manifest stint summary reference is invalid")
        expected_paths.add("stint-summary.json")
    pit_loss_reference = manifest.get("pitLossModel")
    if pit_loss_reference is not None:
        if not isinstance(pit_loss_reference, dict) or pit_loss_reference.get("path") != "pit-loss-model.json":
            raise BrowserDeliveryPublicationError("manifest pit loss model reference is invalid")
        expected_paths.add("pit-loss-model.json")
    pit_loss_estimate_reference = manifest.get("pitLossEstimateSidecar")
    if pit_loss_estimate_reference is not None:
        if (
            not isinstance(pit_loss_estimate_reference, dict)
            or pit_loss_estimate_reference.get("path") != PIT_LOSS_ESTIMATE_SIDECAR_FILENAME
        ):
            raise BrowserDeliveryPublicationError(
                "manifest pit loss estimate sidecar reference is invalid",
            )
        expected_paths.add(PIT_LOSS_ESTIMATE_SIDECAR_FILENAME)
    penalty_reference = manifest.get("penaltySidecar")
    if penalty_reference is not None:
        if not isinstance(penalty_reference, dict) or penalty_reference.get("path") != "penalty-sidecar.json":
            raise BrowserDeliveryPublicationError("manifest penalty sidecar reference is invalid")
        expected_paths.add("penalty-sidecar.json")
    total = (
        len(chunk_refs) + 2
        + (1 if timeline_reference is not None else 0)
        + (1 if sidecar_reference is not None else 0)
        + (1 if stint_reference is not None else 0)
        + (1 if pit_loss_reference is not None else 0)
        + (1 if penalty_reference is not None else 0)
        + (1 if pit_loss_estimate_reference is not None else 0)
    )
    _emit_validation_progress(emit, 1, total, "manifest schema")
    _validate_lap_starts(manifest.get("lapStarts", []), chunk_refs)
    _validate_schema_instance(validators["track-assets"], track, "track assets")
    _emit_validation_progress(emit, 2, total, "track assets schema")
    completed = 2
    if timeline_reference is not None:
        timeline = json.loads(encoded["timeline-summary.json"])
        if not isinstance(timeline, dict):
            raise BrowserDeliveryPublicationError("timeline summary must be a JSON object")
        _validate_schema_instance(
            validators["timeline-summary"], timeline, "timeline summary",
        )
        _validate_timeline_summary_contract(timeline, manifest)
        completed += 1
        _emit_validation_progress(emit, completed, total, "timeline summary schema")
    if sidecar_reference is not None:
        sidecar = json.loads(encoded["lap-sector-sidecar.json"])
        if not isinstance(sidecar, dict):
            raise BrowserDeliveryPublicationError("lap sector sidecar must be a JSON object")
        _validate_schema_instance(
            validators["browser-lap-sector-sidecar"], sidecar, "lap sector sidecar",
        )
        _validate_lap_sector_sidecar_contract(sidecar, manifest)
        completed += 1
        _emit_validation_progress(emit, completed, total, "lap sector sidecar schema")
    if stint_reference is not None:
        stint_summary = json.loads(encoded["stint-summary.json"])
        if not isinstance(stint_summary, dict):
            raise BrowserDeliveryPublicationError("stint summary must be a JSON object")
        _validate_schema_instance(
            validators["stint-summary"], stint_summary, "stint summary",
        )
        _validate_stint_summary_contract(stint_summary, manifest)
        completed += 1
        _emit_validation_progress(emit, completed, total, "stint summary schema")
    if pit_loss_reference is not None:
        pit_loss_model = json.loads(encoded["pit-loss-model.json"])
        if not isinstance(pit_loss_model, dict):
            raise BrowserDeliveryPublicationError("pit loss model must be a JSON object")
        _validate_schema_instance(
            validators["pit-loss-model"], pit_loss_model, "pit loss model",
        )
        _validate_pit_loss_model_contract(pit_loss_model, manifest)
        completed += 1
        _emit_validation_progress(emit, completed, total, "pit loss model schema")
    if penalty_reference is not None:
        penalty_sidecar = json.loads(encoded["penalty-sidecar.json"])
        if not isinstance(penalty_sidecar, dict):
            raise BrowserDeliveryPublicationError("penalty sidecar must be a JSON object")
        _validate_schema_instance(
            validators["penalty-sidecar"], penalty_sidecar, "penalty sidecar",
        )
        _validate_penalty_sidecar_contract(penalty_sidecar, manifest)
        completed += 1
        _emit_validation_progress(emit, completed, total, "penalty sidecar schema")
    if pit_loss_estimate_reference is not None:
        pit_loss_estimate_sidecar = json.loads(encoded[PIT_LOSS_ESTIMATE_SIDECAR_FILENAME])
        if not isinstance(pit_loss_estimate_sidecar, dict):
            raise BrowserDeliveryPublicationError(
                "pit loss estimate sidecar must be a JSON object",
            )
        _validate_schema_instance(
            validators["pit-loss-estimate-sidecar"],
            pit_loss_estimate_sidecar,
            "pit loss estimate sidecar",
        )
        _validate_pit_loss_estimate_sidecar_contract(
            pit_loss_estimate_sidecar,
            manifest,
            track,
            tuple(
                status
                for reference in chunk_refs
                for status in json.loads(encoded[reference["path"]]).get(
                    "trackStatusCode", []
                )
            ),
        )
        completed += 1
        _emit_validation_progress(
            emit, completed, total, "pit loss estimate sidecar schema",
        )
    previous = None
    for sequence, reference in enumerate(chunk_refs, start=1):
        path = reference["path"]
        expected_paths.add(path)
        chunk = json.loads(encoded[path])
        if reference["sequence"] != sequence or path != f"chunks/chunk-{sequence:03d}.json" or reference["schemaId"] != _CHUNK_SCHEMA:
            raise BrowserDeliveryPublicationError("chunk references are not deterministic and contiguous")
        _validate_chunk_contract(chunk, reference, driver_ids, previous)
        _validate_schema_instance(validators["chunk"], chunk, "chunk")
        previous = reference
        _emit_validation_progress(emit, completed + sequence, total, f"chunk schema {sequence}/{len(chunk_refs)}")
    if set(encoded) != expected_paths:
        raise BrowserDeliveryPublicationError("delivery contains unreferenced artifacts")


def _prepared_artifacts(
    version: str,
    delivery: BrowserDeliveryBuild,
    validators: Mapping[str, jsonschema_rs.Draft202012Validator],
    *,
    progress: ProgressCallback | None = None,
) -> tuple[PreparedArtifact, ...]:
    emit = progress or (lambda _update: None)
    chunks = delivery.chunks
    _validate_chunks(chunks)
    summary = delivery.timeline_summary
    sidecar = delivery.lap_sector_sidecar
    total = (
        2 * len(chunks) + 4
        + (2 if summary is not None else 0)
        + (2 if sidecar is not None else 0)
        + (2 if delivery.stint_summary is not None else 0)
        + (2 if delivery.pit_loss_model is not None else 0)
        + (2 if delivery.penalty_sidecar is not None else 0)
        + (2 if delivery.pit_loss_estimate_sidecar is not None else 0)
    )
    fixture_id = delivery.manifest.fixture_id
    manifest = delivery.manifest.as_dict()
    schema_track_assets = _schema_compatible_value(delivery.track_assets)
    _validate_track_contract(schema_track_assets, manifest)
    _emit_validation_progress(emit, 1, total, "track assets")
    _validate_schema_instance(validators["track-assets"], schema_track_assets, "track assets")
    _emit_validation_progress(emit, 2, total, "track assets schema")
    track = _prepare_artifact("track-assets.json", delivery.track_assets)

    timeline_artifact: PreparedArtifact | None = None
    timeline_contract = None
    completed = 2
    if summary is not None:
        timeline_contract = _schema_compatible_value(summary.as_dict())
        _validate_schema_instance(
            validators["timeline-summary"], timeline_contract, "timeline summary",
        )
        timeline_artifact = _prepare_artifact("timeline-summary.json", timeline_contract)
        completed += 1
        _emit_validation_progress(emit, completed, total, "timeline summary")
        completed += 1
        _emit_validation_progress(emit, completed, total, "timeline summary schema")

    sidecar_artifact: PreparedArtifact | None = None
    sidecar_contract = None
    if sidecar is not None:
        sidecar_contract = _schema_compatible_value(sidecar.as_dict())
        _validate_schema_instance(
            validators["browser-lap-sector-sidecar"], sidecar_contract, "lap sector sidecar",
        )
        sidecar_artifact = _prepare_artifact("lap-sector-sidecar.json", sidecar_contract)
        completed += 1
        _emit_validation_progress(emit, completed, total, "lap sector sidecar")
        completed += 1
        _emit_validation_progress(emit, completed, total, "lap sector sidecar schema")

    stint = delivery.stint_summary
    stint_artifact: PreparedArtifact | None = None
    stint_contract = None
    if stint is not None:
        stint_contract = _schema_compatible_value(stint.as_dict())
        _validate_schema_instance(
            validators["stint-summary"], stint_contract, "stint summary",
        )
        stint_artifact = _prepare_artifact("stint-summary.json", stint_contract)
        completed += 1
        _emit_validation_progress(emit, completed, total, "stint summary")
        completed += 1
        _emit_validation_progress(emit, completed, total, "stint summary schema")

    pit_loss_model = delivery.pit_loss_model
    pit_loss_artifact: PreparedArtifact | None = None
    pit_loss_contract = None
    if pit_loss_model is not None:
        pit_loss_contract = _schema_compatible_value(pit_loss_model.as_dict())
        _validate_schema_instance(
            validators["pit-loss-model"], pit_loss_contract, "pit loss model",
        )
        pit_loss_artifact = _prepare_artifact("pit-loss-model.json", pit_loss_contract)
        completed += 1
        _emit_validation_progress(emit, completed, total, "pit loss model")
        completed += 1
        _emit_validation_progress(emit, completed, total, "pit loss model schema")

    penalty_sidecar = delivery.penalty_sidecar
    penalty_artifact: PreparedArtifact | None = None
    penalty_contract = None
    if penalty_sidecar is not None:
        penalty_contract = _schema_compatible_value(penalty_sidecar.as_dict())
        _validate_schema_instance(
            validators["penalty-sidecar"], penalty_contract, "penalty sidecar",
        )
        penalty_artifact = _prepare_artifact("penalty-sidecar.json", penalty_contract)
        completed += 1
        _emit_validation_progress(emit, completed, total, "penalty sidecar")
        completed += 1
        _emit_validation_progress(emit, completed, total, "penalty sidecar schema")

    pit_loss_estimate_sidecar = delivery.pit_loss_estimate_sidecar
    pit_loss_estimate_artifact: PreparedArtifact | None = None
    pit_loss_estimate_contract = None
    if pit_loss_estimate_sidecar is not None:
        pit_loss_estimate_contract = _schema_compatible_value(
            pit_loss_estimate_sidecar.as_dict(),
        )
        _validate_schema_instance(
            validators["pit-loss-estimate-sidecar"],
            pit_loss_estimate_contract,
            "pit loss estimate sidecar",
        )
        pit_loss_estimate_artifact = _prepare_artifact(
            PIT_LOSS_ESTIMATE_SIDECAR_FILENAME, pit_loss_estimate_contract,
        )
        completed += 1
        _emit_validation_progress(
            emit, completed, total, "pit loss estimate sidecar",
        )
        completed += 1
        _emit_validation_progress(
            emit, completed, total, "pit loss estimate sidecar schema",
        )

    previous = None
    chunk_artifacts = []
    references = []
    driver_ids = {driver["id"] for driver in cast(list[Mapping[str, str]], manifest["drivers"])}
    for chunk in chunks:
        contract = _chunk_dict(chunk, fixture_id)
        reference = _chunk_reference(chunk, "")
        _validate_chunk_contract(contract, reference, driver_ids, previous)
        chunk_completed = completed + 2 * (chunk.sequence - 1) + 1
        _emit_validation_progress(emit, chunk_completed, total, f"chunk {chunk.sequence}/{len(chunks)}")
        _validate_schema_instance(validators["chunk"], contract, "chunk")
        _emit_validation_progress(emit, chunk_completed + 1, total, f"chunk schema {chunk.sequence}/{len(chunks)}")
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
    if timeline_artifact is not None:
        manifest["timelineSummary"] = {
            "path": timeline_artifact.path,
            "schemaId": _TIMELINE_SUMMARY_SCHEMA,
            "sha256": timeline_artifact.sha256,
        }
    elif "timelineSummary" in manifest:
        raise BrowserDeliveryPublicationError(
            "manifest timeline summary reference has no summary payload"
        )
    if sidecar_artifact is not None:
        manifest["lapSectorSidecar"] = {
            "path": sidecar_artifact.path,
            "schemaId": _BROWSER_LAP_SECTOR_SIDECAR_SCHEMA,
            "sha256": sidecar_artifact.sha256,
        }
    elif "lapSectorSidecar" in manifest:
        raise BrowserDeliveryPublicationError(
            "manifest lap sector sidecar reference has no sidecar payload"
        )
    if stint_artifact is not None:
        manifest["stintSummary"] = {
            "path": stint_artifact.path,
            "schemaId": _STINT_SUMMARY_SCHEMA,
            "sha256": stint_artifact.sha256,
        }
    elif "stintSummary" in manifest:
        raise BrowserDeliveryPublicationError(
            "manifest stint summary reference has no stint summary payload"
        )
    if pit_loss_artifact is not None:
        manifest["pitLossModel"] = {
            "path": pit_loss_artifact.path,
            "schemaId": _PIT_LOSS_MODEL_SCHEMA,
            "sha256": pit_loss_artifact.sha256,
        }
    elif "pitLossModel" in manifest:
        raise BrowserDeliveryPublicationError(
            "manifest pit loss model reference has no pit loss model payload"
        )
    if pit_loss_estimate_artifact is not None:
        manifest["pitLossEstimateSidecar"] = {
            "path": pit_loss_estimate_artifact.path,
            "schemaId": _PIT_LOSS_ESTIMATE_SIDECAR_SCHEMA,
            "sha256": pit_loss_estimate_artifact.sha256,
        }
    elif "pitLossEstimateSidecar" in manifest:
        raise BrowserDeliveryPublicationError(
            "manifest pit loss estimate sidecar reference has no sidecar payload",
        )
    if penalty_artifact is not None:
        manifest["penaltySidecar"] = {
            "path": penalty_artifact.path,
            "schemaId": _PENALTY_SIDECAR_SCHEMA,
            "sha256": penalty_artifact.sha256,
        }
    elif "penaltySidecar" in manifest:
        raise BrowserDeliveryPublicationError(
            "manifest penalty sidecar reference has no penalty sidecar payload"
        )
    if timeline_contract is not None:
        _validate_timeline_summary_contract(timeline_contract, manifest)
    if sidecar_contract is not None:
        _validate_lap_sector_sidecar_contract(sidecar_contract, manifest)
    if stint_contract is not None:
        _validate_stint_summary_contract(stint_contract, manifest)
    if pit_loss_contract is not None:
        _validate_pit_loss_model_contract(pit_loss_contract, manifest)
    if pit_loss_estimate_contract is not None:
        _validate_pit_loss_estimate_sidecar_contract(
            pit_loss_estimate_contract,
            manifest,
            schema_track_assets,
            tuple(status for chunk in chunks for status in chunk.track_status_code),
        )
    if penalty_contract is not None:
        _validate_penalty_sidecar_contract(penalty_contract, manifest)
    _validate_manifest_contract(
        manifest, delivery, references, timeline_artifact, sidecar_artifact, stint_artifact,
        pit_loss_artifact, penalty_artifact, pit_loss_estimate_artifact,
    )
    _emit_validation_progress(emit, total - 1, total, "manifest")
    _validate_schema_instance(validators["manifest"], manifest, "manifest")
    _emit_validation_progress(emit, total, total, "manifest schema")
    return (
        _prepare_artifact("manifest.json", manifest),
        track,
        *((timeline_artifact,) if timeline_artifact is not None else ()),
        *((sidecar_artifact,) if sidecar_artifact is not None else ()),
        *((stint_artifact,) if stint_artifact is not None else ()),
        *((pit_loss_artifact,) if pit_loss_artifact is not None else ()),
        *((penalty_artifact,) if penalty_artifact is not None else ()),
        *((pit_loss_estimate_artifact,) if pit_loss_estimate_artifact is not None else ()),
        *chunk_artifacts,
    )


def _artifact_payloads(
    version: str,
    delivery: BrowserDeliveryBuild,
    schema_root: Path,
) -> tuple[PreparedArtifact, ...]:
    """Prepare fully validated artifacts for focused tests."""
    schemas, registry = _load_contract_schemas(schema_root)
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
    expected_paths = {
        "manifest.json", "track-assets.json",
        *(f"chunks/{chunk.chunk_id}.json" for chunk in delivery.chunks),
    }
    if delivery.timeline_summary is not None:
        expected_paths.add("timeline-summary.json")
    if delivery.lap_sector_sidecar is not None:
        expected_paths.add("lap-sector-sidecar.json")
    if delivery.stint_summary is not None:
        expected_paths.add("stint-summary.json")
    if delivery.pit_loss_model is not None:
        expected_paths.add("pit-loss-model.json")
    if delivery.pit_loss_estimate_sidecar is not None:
        expected_paths.add(PIT_LOSS_ESTIMATE_SIDECAR_FILENAME)
    if delivery.penalty_sidecar is not None:
        expected_paths.add("penalty-sidecar.json")
    if set(encoded) != expected_paths or any(hashlib.sha256(artifact.payload).hexdigest() != artifact.sha256 for artifact in encoded.values()):
        raise BrowserDeliveryPublicationError("prepared artifact digest disagrees")


def _validate_manifest_contract(
    manifest,
    delivery: BrowserDeliveryBuild,
    refs,
    timeline_artifact: PreparedArtifact | None = None,
    sidecar_artifact: PreparedArtifact | None = None,
    stint_artifact: PreparedArtifact | None = None,
    pit_loss_artifact: PreparedArtifact | None = None,
    penalty_artifact: PreparedArtifact | None = None,
    pit_loss_estimate_artifact: PreparedArtifact | None = None,
) -> None:
    if manifest["sourceGenerationId"] != delivery.source.generation_id or manifest["sourceManifestSha256"] != delivery.source.manifest_sha256:
        raise BrowserDeliveryPublicationError("delivery provenance disagrees with its source snapshot")
    if len(refs) != len(delivery.chunks):
        raise BrowserDeliveryPublicationError("manifest chunk count disagrees")
    timeline_reference = manifest.get("timelineSummary")
    if timeline_artifact is None:
        if timeline_reference is not None or delivery.timeline_summary is not None:
            raise BrowserDeliveryPublicationError("timeline summary reference disagrees with its payload")
    elif timeline_reference != {
        "path": timeline_artifact.path,
        "schemaId": _TIMELINE_SUMMARY_SCHEMA,
        "sha256": timeline_artifact.sha256,
    }:
        raise BrowserDeliveryPublicationError("timeline summary reference disagrees with its payload")
    sidecar_reference = manifest.get("lapSectorSidecar")
    if sidecar_artifact is None:
        if sidecar_reference is not None or delivery.lap_sector_sidecar is not None:
            raise BrowserDeliveryPublicationError("lap sector sidecar reference disagrees with its payload")
    elif sidecar_reference != {
        "path": sidecar_artifact.path,
        "schemaId": _BROWSER_LAP_SECTOR_SIDECAR_SCHEMA,
        "sha256": sidecar_artifact.sha256,
    }:
        raise BrowserDeliveryPublicationError("lap sector sidecar reference disagrees with its payload")
    stint_reference = manifest.get("stintSummary")
    if stint_artifact is None:
        if stint_reference is not None or delivery.stint_summary is not None:
            raise BrowserDeliveryPublicationError("manifest stint summary reference disagrees with its payload")
    elif stint_reference != {
        "path": stint_artifact.path,
        "schemaId": _STINT_SUMMARY_SCHEMA,
        "sha256": stint_artifact.sha256,
    }:
        raise BrowserDeliveryPublicationError("stint summary reference disagrees with its payload")
    pit_loss_reference = manifest.get("pitLossModel")
    if pit_loss_artifact is None:
        if pit_loss_reference is not None or delivery.pit_loss_model is not None:
            raise BrowserDeliveryPublicationError("manifest pit loss model reference disagrees with its payload")
    elif pit_loss_reference != {
        "path": pit_loss_artifact.path,
        "schemaId": _PIT_LOSS_MODEL_SCHEMA,
        "sha256": pit_loss_artifact.sha256,
    }:
        raise BrowserDeliveryPublicationError("pit loss model reference disagrees with its payload")
    penalty_reference = manifest.get("penaltySidecar")
    if penalty_artifact is None:
        if penalty_reference is not None or delivery.penalty_sidecar is not None:
            raise BrowserDeliveryPublicationError("manifest penalty sidecar reference disagrees with its payload")
    elif penalty_reference != {
        "path": penalty_artifact.path,
        "schemaId": _PENALTY_SIDECAR_SCHEMA,
        "sha256": penalty_artifact.sha256,
    }:
        raise BrowserDeliveryPublicationError("penalty sidecar reference disagrees with its payload")
    pit_loss_estimate_reference = manifest.get("pitLossEstimateSidecar")
    if pit_loss_estimate_artifact is None:
        if (
            pit_loss_estimate_reference is not None
            or delivery.pit_loss_estimate_sidecar is not None
        ):
            raise BrowserDeliveryPublicationError(
                "manifest pit loss estimate sidecar reference disagrees with its payload",
            )
    elif pit_loss_estimate_reference != {
        "path": pit_loss_estimate_artifact.path,
        "schemaId": _PIT_LOSS_ESTIMATE_SIDECAR_SCHEMA,
        "sha256": pit_loss_estimate_artifact.sha256,
    }:
        raise BrowserDeliveryPublicationError(
            "pit loss estimate sidecar reference disagrees with its payload",
        )
    _validate_lap_starts(manifest.get("lapStarts", []), refs)
    for sequence, (ref, expected_chunk) in enumerate(zip(refs, delivery.chunks, strict=True), start=1):
        path = ref["path"]
        if ref["sequence"] != sequence or path != f"chunks/chunk-{sequence:03d}.json" or ref["schemaId"] != _CHUNK_SCHEMA:
            raise BrowserDeliveryPublicationError("chunk references are not deterministic and contiguous")
        if ref["path"] != f"chunks/{expected_chunk.chunk_id}.json":
            raise BrowserDeliveryPublicationError("chunk payload disagrees with its immutable model")
def _emit_validation_progress(
    emit: ProgressCallback, completed: int, total: int, detail: str,
) -> None:
    emit(BrowserValidationProgress(
        "browser_schema_artifact_validating", completed, total, detail,
    ))


def _validate_track_contract(track, manifest) -> None:
    required = {"contractVersion", "fixtureId", "trackId", "trackName", "coordinateSpace", "circuitLengthMeters", "rotationDegrees", "startFinish", "centerLine", "innerBoundary", "outerBoundary"}
    if not required <= set(track) or track.get("contractVersion") != "v1" or track.get("fixtureId") != manifest["fixtureId"]:
        raise BrowserDeliveryPublicationError("track assets disagree with the manifest")


def _validate_timeline_summary_contract(summary, manifest) -> None:
    """Apply semantic checks not expressible in the compact JSON schema."""
    if summary.get("contractVersion") != "v1" or summary.get("fixtureId") != manifest.get("fixtureId"):
        raise BrowserDeliveryPublicationError("timeline summary disagrees with the manifest")
    start_ms, end_ms = summary.get("startMs"), summary.get("endMs")
    if any(type(value) is not int or value < 0 for value in (start_ms, end_ms)):
        raise BrowserDeliveryPublicationError("timeline summary bounds are invalid")
    start_ms, end_ms = cast(int, start_ms), cast(int, end_ms)
    if start_ms >= end_ms:
        raise BrowserDeliveryPublicationError("timeline summary bounds are invalid")
    chunks = manifest.get("chunks")
    if (
        not isinstance(chunks, list)
        or not chunks
        or start_ms != chunks[0].get("startMs")
        or end_ms != chunks[-1].get("endMs")
    ):
        raise BrowserDeliveryPublicationError("timeline summary bounds disagree with replay bounds")
    intervals = summary.get("intervals")
    markers = summary.get("dnfMarkers")
    if not isinstance(intervals, list) or not isinstance(markers, list):
        raise BrowserDeliveryPublicationError("timeline summary collections are invalid")
    for interval in intervals:
        if not isinstance(interval, dict):
            raise BrowserDeliveryPublicationError("timeline summary interval is invalid")
        interval_start, interval_end = interval.get("startMs"), interval.get("endMs")
        if (
            interval.get("kind") not in {"yellow", "sc", "red", "vsc"}
            or any(type(value) is not int for value in (interval_start, interval_end))
        ):
            raise BrowserDeliveryPublicationError("timeline summary interval bounds are invalid")
        interval_start, interval_end = cast(int, interval_start), cast(int, interval_end)
        if not start_ms <= interval_start < interval_end <= end_ms:
            raise BrowserDeliveryPublicationError("timeline summary interval bounds are invalid")
    if intervals != sorted(intervals, key=lambda value: (value["startMs"], value["endMs"], value["kind"])):
        raise BrowserDeliveryPublicationError("timeline summary intervals are not deterministically ordered")
    driver_ids = {driver["id"] for driver in manifest.get("drivers", ())}
    marker_ids = set()
    for marker in markers:
        if not isinstance(marker, dict):
            raise BrowserDeliveryPublicationError("timeline summary DNF marker is invalid")
        driver_id, time_ms = marker.get("driverId"), marker.get("timeMs")
        if (
            not isinstance(driver_id, str)
            or driver_id not in driver_ids
            or type(time_ms) is not int
            or not start_ms <= time_ms < end_ms
            or driver_id in marker_ids
        ):
            raise BrowserDeliveryPublicationError("timeline summary DNF markers are invalid")
        marker_ids.add(driver_id)
    if markers != sorted(markers, key=lambda value: (value["timeMs"], value["driverId"])):
        raise BrowserDeliveryPublicationError("timeline summary DNF markers are not deterministically ordered")


def _validate_lap_sector_sidecar_contract(sidecar, manifest) -> None:
    """Apply semantic checks not expressible in the compact JSON schema."""
    if sidecar.get("contractVersion") != "v1" or sidecar.get("fixtureId") != manifest.get("fixtureId"):
        raise BrowserDeliveryPublicationError("lap sector sidecar disagrees with the manifest")
    driver_ids = {driver["id"] for driver in manifest.get("drivers", ())}
    if set(sidecar.get("drivers", {})) != driver_ids:
        raise BrowserDeliveryPublicationError("lap sector sidecar drivers disagree with the manifest")


def _validate_penalty_sidecar_contract(sidecar, manifest) -> None:
    """Ensure issued-penalty identities belong to the published replay."""
    if sidecar.get("contractVersion") != "v1" or sidecar.get("fixtureId") != manifest.get("fixtureId"):
        raise BrowserDeliveryPublicationError("penalty sidecar disagrees with the manifest")
    driver_ids = {driver["id"] for driver in manifest.get("drivers", ())}
    issuances = sidecar.get("penaltyIssuances")
    if not isinstance(issuances, list) or any(
        not isinstance(issuance, dict) or issuance.get("driverId") not in driver_ids
        for issuance in issuances
    ):
        raise BrowserDeliveryPublicationError("penalty sidecar driver IDs disagree with the manifest")


def _validate_stint_summary_contract(summary, manifest) -> None:
    """Apply semantic checks not expressible in the compact JSON schema."""
    if summary.get("contractVersion") != "v1" or summary.get("fixtureId") != manifest.get("fixtureId"):
        raise BrowserDeliveryPublicationError("stint summary disagrees with the manifest")
    drivers = summary.get("drivers")
    if not isinstance(drivers, dict):
        raise BrowserDeliveryPublicationError("stint summary drivers are invalid")
    driver_ids = {driver["id"] for driver in manifest.get("drivers", ())}
    if set(drivers) != driver_ids:
        raise BrowserDeliveryPublicationError("stint summary drivers disagree with the manifest")
    for columns in drivers.values():
        if not isinstance(columns, dict):
            raise BrowserDeliveryPublicationError("stint summary driver columns are invalid")
        column_names = (
            "stintNumber", "compound", "startLap", "endLap", "startTimeMs",
            "endTimeMs", "tyreLifeAtStart", "isFreshTyre", "pitInTimeMs", "pitOutTimeMs",
        )
        if any(not isinstance(columns.get(name), list) for name in column_names):
            raise BrowserDeliveryPublicationError("stint summary driver columns are invalid")
        arrays = tuple(cast(list[object], columns[name]) for name in column_names)
        if len({len(values) for values in arrays}) != 1:
            raise BrowserDeliveryPublicationError("stint summary driver columns are not aligned")
        stint_numbers = cast(list[int], columns["stintNumber"])
        if stint_numbers != sorted(stint_numbers) or len(set(stint_numbers)) != len(stint_numbers):
            raise BrowserDeliveryPublicationError("stint summary stints are not deterministically ordered")


def _validate_pit_loss_model_contract(model, manifest) -> None:
    """Apply the causal timeline guarantees not expressible in JSON Schema."""
    if (
        model.get("contractVersion") != "v1"
        or model.get("fixtureId") != manifest.get("fixtureId")
        or model.get("method") != "global-prior-weighted-mean-v1"
    ):
        raise BrowserDeliveryPublicationError("pit loss model identity disagrees with the manifest")
    baseline_ms, prior_weight = model.get("baselineMs"), model.get("priorWeight")
    if any(
        type(value) is not int or not 1 <= value <= MAX_INT64
        for value in (baseline_ms, prior_weight)
    ):
        raise BrowserDeliveryPublicationError("pit loss model baseline and prior weight are invalid")

    time_ms, estimates, counts = (
        model.get("timeMs"), model.get("estimatedLossMs"), model.get("observedSampleCount"),
    )
    arrays = (time_ms, estimates, counts)
    if any(not isinstance(values, list) for values in arrays):
        raise BrowserDeliveryPublicationError("pit loss model timeline arrays are invalid")
    if not time_ms or any(len(values) != len(time_ms) for values in arrays):
        raise BrowserDeliveryPublicationError("pit loss model timeline arrays are not aligned")
    if any(
        type(value) is not int or not 0 <= value <= MAX_INT64
        for values in arrays for value in values
    ):
        raise BrowserDeliveryPublicationError("pit loss model timeline values are invalid")

    chunks = manifest.get("chunks")
    if (
        not isinstance(chunks, list)
        or not chunks
        or any(not isinstance(chunk, dict) for chunk in chunks)
    ):
        raise BrowserDeliveryPublicationError("pit loss model replay bounds are invalid")
    replay_start_ms, replay_end_ms = chunks[0].get("startMs"), chunks[-1].get("endMs")
    if (
        type(replay_start_ms) is not int
        or type(replay_end_ms) is not int
        or not 0 <= replay_start_ms < replay_end_ms <= MAX_INT64
    ):
        raise BrowserDeliveryPublicationError("pit loss model replay bounds are invalid")
    if time_ms[0] != replay_start_ms or any(
        not replay_start_ms <= value < replay_end_ms for value in time_ms
    ):
        raise BrowserDeliveryPublicationError("pit loss model timestamps are outside replay bounds")
    if any(following <= current for current, following in zip(time_ms, time_ms[1:], strict=False)):
        raise BrowserDeliveryPublicationError("pit loss model timestamps must be strictly increasing")
    if estimates[0] != baseline_ms or counts[0] != 0:
        raise BrowserDeliveryPublicationError("pit loss model initial sample is invalid")
    if any(following <= current for current, following in zip(counts, counts[1:], strict=False)):
        raise BrowserDeliveryPublicationError("pit loss model sample counts must strictly increase")


def _validate_pit_loss_estimate_sidecar_contract(
    sidecar, manifest, track, status_codes: tuple[object, ...],
) -> None:
    """Validate identity and generation-time timeline rules for the sidecar."""
    if (
        sidecar.get("contractVersion") != "v1"
        or sidecar.get("fixtureId") != manifest.get("fixtureId")
        or sidecar.get("trackId") != track.get("trackId")
        or sidecar.get("method") not in (
            LEGACY_PIT_LOSS_ESTIMATE_METHOD,
            CURATED_BASELINE_METHOD,
        )
    ):
        raise BrowserDeliveryPublicationError(
            "pit loss estimate sidecar identity disagrees with its delivery",
        )
    if sidecar.get("method") == CURATED_BASELINE_METHOD:
        _validate_curated_pit_loss_estimate_sidecar(sidecar, manifest)
        _validate_curated_sidecar_identity(sidecar, manifest, track)
        return

    _validate_status_estimate_availability(sidecar, status_codes)
    for name in ("race", "safetyCar", "virtualSafetyCar"):
        value = sidecar.get(name)
        if value is None:
            continue
        if value == {"status": "unavailable"}:
            continue
        _validate_pit_loss_estimate_timeline(
            value, manifest, name, require_observation=name != "race",
        )


def _validate_curated_pit_loss_estimate_sidecar(sidecar, manifest) -> None:
    """Validate immutable curated values independently of race observations."""
    for name in ("race", "safetyCar", "virtualSafetyCar"):
        value = sidecar.get(name)
        if value is None or value == {"status": "unavailable"}:
            raise BrowserDeliveryPublicationError(
                f"curated pit loss {name} value must be available",
            )
        _validate_pit_loss_estimate_timeline(
            value, manifest, name, allow_missing_observed_sample_count=True,
            require_single_point=True,
        )
def _validate_curated_sidecar_identity(sidecar, manifest, track) -> None:
    """Reject a curated sidecar whose fixture/track binding is malformed.

    The digest-bound sidecar is validated as a whole after schema validation:
    its fixture and track identity must deterministically resolve to one
    physical circuit through the identity map.  The track asset's ``trackName``
    is included so the generator's actual binding (for example ``2026-01-race``
    with a ``-telemetry-layout-v1`` asset) publishes when the name establishes
    the circuit.  An unknown or ambiguous binding fails closed instead of being
    published as if it were catalog-backed.  The check runs only for the
    curated method so legacy ``track-status-median-v1`` sidecars remain
    publishable unchanged.
    """
    fixture_id = sidecar.get("fixtureId")
    track_id = sidecar.get("trackId")
    if not isinstance(fixture_id, str) or not isinstance(track_id, str):
        raise BrowserDeliveryPublicationError(
            "curated pit loss sidecar binding is invalid",
        )
    track_name = track.get("trackName")
    if track_name is not None and not isinstance(track_name, str):
        raise BrowserDeliveryPublicationError(
            "curated pit loss sidecar binding is invalid",
        )
    try:
        resolve_binding_identity(
            fixture_id=fixture_id, track_id=track_id, track_name=track_name,
        )
    except TrackIdentityLookupError as error:
        raise BrowserDeliveryPublicationError(
            "curated pit loss sidecar binding is not identity-consistent",
        ) from error


def _validate_status_estimate_availability(sidecar, status_codes: tuple[object, ...]) -> None:
    expected = {
        "safetyCar": any(status == 4 for status in status_codes),
        "virtualSafetyCar": any(status in {6, 7} for status in status_codes),
    }
    for field, occurs in expected.items():
        if (field in sidecar) != occurs:
            raise BrowserDeliveryPublicationError(
                f"pit loss estimate {field} availability disagrees with track status",
            )


def _validate_pit_loss_estimate_timeline(
    timeline, manifest, label: str, *, require_observation: bool = False,
    allow_missing_observed_sample_count: bool = False,
    require_single_point: bool = False,
) -> None:
    if not isinstance(timeline, dict):
        raise BrowserDeliveryPublicationError(f"{label} pit loss estimate is invalid")
    time_ms = timeline.get("timeMs")
    estimates = timeline.get("estimatedLossMs")
    counts = timeline.get("observedSampleCount")
    arrays = (time_ms, estimates)
    if any(not isinstance(values, list) for values in arrays) or (
        counts is not None and not isinstance(counts, list)
    ):
        raise BrowserDeliveryPublicationError(f"{label} pit loss estimate arrays are invalid")
    time_values = cast(list[object], time_ms)
    estimate_values = cast(list[object], estimates)
    count_values = None if counts is None else cast(list[object], counts)
    if count_values is not None and allow_missing_observed_sample_count:
        # Curated timelines are immutable catalog evidence, never current-race
        # observations: an observedSampleCount array would mislabel the value
        # as race-derived even when the catalog entry is fixture-less.  The
        # explicit rejection keeps the semantic layer fail-closed independent
        # of the JSON schema's additionalProperties guard.
        raise BrowserDeliveryPublicationError(
            f"{label} curated pit loss timeline cannot carry observedSampleCount",
        )
    if count_values is None and not allow_missing_observed_sample_count:
        raise BrowserDeliveryPublicationError(
            f"{label} pit loss estimate observed sample counts are required",
        )
    if not time_values or len(estimate_values) != len(time_values) or (
        count_values is not None and len(count_values) != len(time_values)
    ):
        raise BrowserDeliveryPublicationError(
            f"{label} pit loss estimate arrays are not aligned",
        )
    if require_single_point and len(time_values) != 1:
        raise BrowserDeliveryPublicationError(
            f"{label} curated pit loss estimate must contain one replay-start point",
        )
    values_to_validate = (time_values, estimate_values)
    if count_values is not None:
        values_to_validate += (count_values,)
    if any(
        type(value) is not int or not 0 <= value <= MAX_INT64
        for values in values_to_validate for value in values
    ):
        raise BrowserDeliveryPublicationError(
            f"{label} pit loss estimate values are invalid",
        )
    time_values_int = cast(list[int], time_values)
    count_values_int = None if count_values is None else cast(list[int], count_values)
    chunks = manifest.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        raise BrowserDeliveryPublicationError("pit loss estimate replay bounds are invalid")
    replay_start_ms, replay_end_ms = chunks[0].get("startMs"), chunks[-1].get("endMs")
    if (
        type(replay_start_ms) is not int
        or type(replay_end_ms) is not int
        or not 0 <= replay_start_ms < replay_end_ms <= MAX_INT64
    ):
        raise BrowserDeliveryPublicationError("pit loss estimate replay bounds are invalid")
    if time_values_int[0] != replay_start_ms or any(
        not replay_start_ms <= value < replay_end_ms for value in time_values_int
    ):
        raise BrowserDeliveryPublicationError(
            f"{label} pit loss estimate timestamps are outside replay bounds",
        )
    if any(
        following <= current
        for current, following in zip(time_values_int, time_values_int[1:], strict=False)
    ):
        raise BrowserDeliveryPublicationError(
            f"{label} pit loss estimate timestamps must be strictly increasing",
        )
    if count_values_int is not None and len(count_values_int) > 1 and (
        count_values_int[0] != 0 or any(
            following <= current
            for current, following in zip(count_values_int, count_values_int[1:], strict=False)
        )
    ):
        raise BrowserDeliveryPublicationError(
            f"{label} pit loss estimate sample counts must strictly increase",
        )
    if require_observation and (count_values_int is None or count_values_int[-1] == 0):
        raise BrowserDeliveryPublicationError(
            f"{label} pit loss estimate without observations must be unavailable",
        )


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
        for name in (
            "manifest", "chunk", "track-assets", "timeline-summary",
            "browser-lap-sector-sidecar", "penalty-sidecar", "stint-summary", "pit-loss-model",
            "pit-loss-estimate-sidecar",
        ):
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


def _publish_payloads(
    browser_parent: Path,
    version: str,
    artifacts: tuple[PreparedArtifact, ...],
    *,
    progress: ProgressCallback,
) -> PublishedBrowserDelivery:
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
    generations_fd = staging_fd = chunks_fd = None
    staging_name = f"{_STAGING_PREFIX}{uuid.uuid4().hex}"
    staging_identity = pointer_identity = None
    pointer_temp = f"{_STAGING_PREFIX}pointer-{uuid.uuid4().hex}"
    generation_committed = False
    result: PublishedBrowserDelivery | None = None
    primary_error: BaseException | None = None
    cleanup_errors: list[BaseException] = []
    durability_confirmed = False
    candidate = _delivery_result(root, version, artifacts)
    try:
        generations_created = False
        try:
            os.mkdir("generations", mode=0o700, dir_fd=root_fd)
            generations_created = True
        except FileExistsError:
            pass
        if generations_created:
            _fsync_directory_fd(root_fd)
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
        progress("browser_artifacts_staging")
        for artifact in artifacts:
            relative, payload = artifact.path, artifact.payload
            parent, name = (chunks_fd, relative.split("/", 1)[1]) if relative.startswith("chunks/") else (staging_fd, relative)
            _write_at(parent, name, payload)
        _validate_staged(staging_fd, chunks_fd, artifacts)
        # File fsyncs protect payload bytes; these directory fsyncs protect the
        # entries before the staging tree is renamed into the selected set.
        _fsync_directory_fd(chunks_fd)
        _fsync_directory_fd(staging_fd)
        os.replace(staging_name, version, src_dir_fd=root_fd, dst_dir_fd=generations_fd)
        _fsync_directory_fd(generations_fd)
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
        progress("browser_pointer_committing_durability")
        try:
            os.replace(pointer_temp, "browser-current.json", src_dir_fd=root_fd, dst_dir_fd=root_fd)
        except BaseException:
            if _pointer_selects(root_fd, version, manifest.sha256):
                result = candidate
                generation_committed = True
                pointer_identity = None
            raise
        result = candidate
        generation_committed = True
        pointer_identity = None
        durability_confirmed = _fsync_directory_fd(root_fd)
    except BaseException as error:
        primary_error = error
    finally:
        _close_descriptor(chunks_fd, cleanup_errors)
        _close_descriptor(staging_fd, cleanup_errors)
        if not generation_committed and staging_identity is not None:
            try:
                _remove_owned_tree_at(root_fd, staging_name, staging_identity)
            except FileNotFoundError:
                pass
            except BaseException as error:
                cleanup_errors.append(error)
        if pointer_identity is not None:
            try:
                _remove_owned_file_at(root_fd, pointer_temp, pointer_identity)
            except FileNotFoundError:
                pass
            except BaseException as error:
                cleanup_errors.append(error)
        _close_descriptor(generations_fd, cleanup_errors)
        _close_descriptor(root_fd, cleanup_errors)
        try:
            lease.release()
        except BaseException as error:
            cleanup_errors.append(
                error if isinstance(error, RecoveryOwnershipError) else RecoveryOwnershipError(
                    "unable to release verifiable browser publication ownership"
                )
            )
    if primary_error is not None:
        _attach_cleanup_errors(primary_error, cleanup_errors)
        if result is None:
            if isinstance(primary_error, BrowserDeliveryPublicationError):
                raise primary_error
            publication_error = BrowserDeliveryPublicationError("secure browser publication failed")
            _attach_cleanup_errors(publication_error, cleanup_errors)
            raise publication_error from primary_error
        committed_error: BrowserDeliveryPublicationError
        if durability_confirmed:
            committed_error = BrowserDeliveryCommittedError(result, primary_error)
        else:
            committed_error = BrowserDeliveryDurabilityUncertainError(result, primary_error)
        _attach_cleanup_errors(committed_error, cleanup_errors)
        raise committed_error from primary_error
    if cleanup_errors:
        if result is not None and not durability_confirmed:
            committed_error = BrowserDeliveryDurabilityUncertainError(result, cleanup_errors[0])
            _attach_cleanup_errors(committed_error, cleanup_errors)
            raise committed_error from cleanup_errors[0]
        raise BrowserDeliveryCleanupError(tuple(cleanup_errors), result) from cleanup_errors[0]
    if result is None:
        raise AssertionError("browser publication did not return or raise")
    return result


def _fsync_directory_fd(descriptor: int) -> bool:
    """Sync a directory descriptor, tolerating filesystems without support."""
    try:
        os.fsync(descriptor)
    except OSError as error:
        if error.errno in _UNSUPPORTED_DIRECTORY_FSYNC:
            return False
        raise
    return True


def _delivery_result(
    root: Path, version: str, artifacts: tuple[PreparedArtifact, ...],
) -> PublishedBrowserDelivery:
    generation = root / "generations" / version
    digests = {artifact.path: artifact.sha256 for artifact in artifacts}
    return PublishedBrowserDelivery(
        version,
        generation,
        generation / "manifest.json",
        root / "browser-current.json",
        generation / "track-assets.json",
        tuple(generation / artifact.path for artifact in artifacts if artifact.path.startswith("chunks/")),
        digests,
        generation / "timeline-summary.json" if "timeline-summary.json" in digests else None,
        generation / "lap-sector-sidecar.json" if "lap-sector-sidecar.json" in digests else None,
        generation / "stint-summary.json" if "stint-summary.json" in digests else None,
        generation / "pit-loss-model.json" if "pit-loss-model.json" in digests else None,
        generation / "penalty-sidecar.json" if "penalty-sidecar.json" in digests else None,
        generation / PIT_LOSS_ESTIMATE_SIDECAR_FILENAME
        if PIT_LOSS_ESTIMATE_SIDECAR_FILENAME in digests else None,
    )


def _close_descriptor(descriptor: int | None, cleanup_errors: list[BaseException]) -> None:
    if descriptor is None:
        return
    try:
        os.close(descriptor)
    except BaseException as error:
        cleanup_errors.append(error)


def _pointer_selects(root_fd: int, version: str, manifest_sha256: str) -> bool:
    descriptor: int | None = None
    try:
        descriptor = os.open("browser-current.json", os.O_RDONLY | _NO_FOLLOW, dir_fd=root_fd)
        payload = os.read(descriptor, 1_048_576)
        pointer = json.loads(payload)
        return (
            pointer.get("formatVersion") == _FORMAT_VERSION
            and pointer.get("deliveryVersion") == version
            and pointer.get("manifestPath") == f"generations/{version}/manifest.json"
            and pointer.get("manifestSha256") == manifest_sha256
        )
    except (OSError, TypeError, ValueError, KeyError, AttributeError, json.JSONDecodeError):
        return False
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


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
    return {"x": fields.x, "y": fields.y, "trackDistanceMeters": fields.track_distance_meters, "speed": fields.speed, "rpm": fields.rpm, "throttle": fields.throttle, "brake": fields.brake, "gapToLeaderMs": fields.gap_to_leader_ms, "lap": fields.lap, "position": fields.position, "gear": fields.gear, "drs": fields.drs, "tyreCompound": fields.tyre_compound, "tyreAge": fields.tyre_age, "status": fields.status, "isInPitLane": fields.is_in_pit_lane, "isFinished": fields.is_finished}


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


__all__ = [
    "BrowserDeliveryCleanupError", "BrowserDeliveryCommittedError",
    "BrowserDeliveryDurabilityUncertainError", "BrowserDeliveryPublicationError",
    "BrowserValidationProgress",
    "PublishedBrowserDelivery", "publish_browser_delivery", "validate_browser_delivery_pointer",
    "validate_complete_browser_delivery",
]
