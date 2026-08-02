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
from typing import Literal, cast

import jsonschema_rs

from f1_replay_pipeline.delivery.browser.browser_chunk_builder import BrowserChunk, BrowserEvent
from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    BrowserDriverFields,
    BrowserQualifyingLapStatusSidecar,
    BROWSER_LAP_SECTOR_SIDECAR_SCHEMA_ID,
    MAX_INT64,
    PENALTY_SIDECAR_SCHEMA_ID,
    PIT_LOSS_MODEL_SCHEMA_ID,
    STINT_SUMMARY_SCHEMA_ID,
    TIMELINE_SUMMARY_SCHEMA_ID,
    V2_BROWSER_LAP_SECTOR_SIDECAR_SCHEMA_ID,
    V2_CHUNK_SCHEMA_ID,
    V2_MANIFEST_SCHEMA_ID,
    V2_PIT_LOSS_MODEL_SCHEMA_ID,
    V2_PENALTY_SIDECAR_SCHEMA_ID,
    V2_STINT_SUMMARY_SCHEMA_ID,
    V2_TIMELINE_SUMMARY_SCHEMA_ID,
    V2_TRACK_ASSETS_SCHEMA_ID,
    V2_BROWSER_QUALIFYING_LAP_STATUS_SCHEMA_ID,
    QUALIFYING_SUMMARY_SCHEMA_ID,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_orchestration import BrowserDeliveryBuild
from f1_replay_pipeline.delivery.browser.browser_lap_status import (
    build_qualifying_lap_status_sidecar,
    has_qualifying_lap_status_messages,
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
from f1_replay_pipeline.domain.session_modes import SessionMode, normalize_session_mode


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
_PENALTY_SIDECAR_SCHEMA = PENALTY_SIDECAR_SCHEMA_ID
_POINTER_FIELDS = frozenset({"formatVersion", "deliveryVersion", "manifestPath", "manifestSha256"})
_UNSUPPORTED_DIRECTORY_FSYNC = {errno.EINVAL, errno.ENOTSUP, errno.EBADF}

_make_contract_validator = jsonschema_rs.Draft202012Validator


@dataclass(frozen=True)
class _BrowserContractSpec:
    """Version-specific identities kept together at the publication boundary."""

    version: Literal["v1", "v2"]
    format_version: str
    schema_ids: Mapping[str, str]


_CONTRACT_SPECS = {
    "v1": _BrowserContractSpec("v1", "browser-delivery-v1", {
        "manifest": "urn:f1-cache-replay:schema:replay-data:v1:manifest",
        "chunk": "urn:f1-cache-replay:schema:replay-data:v1:chunk",
        "track-assets": "urn:f1-cache-replay:schema:replay-data:v1:track-assets",
        "timeline-summary": TIMELINE_SUMMARY_SCHEMA_ID,
        "browser-lap-sector-sidecar": BROWSER_LAP_SECTOR_SIDECAR_SCHEMA_ID,
        "stint-summary": STINT_SUMMARY_SCHEMA_ID,
        "pit-loss-model": PIT_LOSS_MODEL_SCHEMA_ID,
        "penalty-sidecar": PENALTY_SIDECAR_SCHEMA_ID,
        "qualifying-summary": QUALIFYING_SUMMARY_SCHEMA_ID,
    }),
    "v2": _BrowserContractSpec("v2", "browser-delivery-v2", {
        "manifest": V2_MANIFEST_SCHEMA_ID,
        "chunk": V2_CHUNK_SCHEMA_ID,
        "track-assets": V2_TRACK_ASSETS_SCHEMA_ID,
        "timeline-summary": V2_TIMELINE_SUMMARY_SCHEMA_ID,
        "browser-lap-sector-sidecar": V2_BROWSER_LAP_SECTOR_SIDECAR_SCHEMA_ID,
        "stint-summary": V2_STINT_SUMMARY_SCHEMA_ID,
        "pit-loss-model": V2_PIT_LOSS_MODEL_SCHEMA_ID,
        "penalty-sidecar": V2_PENALTY_SIDECAR_SCHEMA_ID,
        "qualifying-summary": QUALIFYING_SUMMARY_SCHEMA_ID,
        "qualifying-lap-status": V2_BROWSER_QUALIFYING_LAP_STATUS_SCHEMA_ID,
    }),
}


def _resolve_contract_spec(
    schema_root: Path, contract_version: Literal["v1", "v2"] | None,
) -> _BrowserContractSpec:
    if not isinstance(schema_root, Path):
        raise TypeError("schema_root must be a pathlib.Path")
    inferred: Literal["v1", "v2"] = "v2" if schema_root.parent.name == "v2" else "v1"
    selected = inferred if contract_version is None else contract_version
    if selected not in _CONTRACT_SPECS:
        raise BrowserDeliveryPublicationError("browser contract version must be v1 or v2")
    return _CONTRACT_SPECS[selected]


def _contract_version_from_format(value: object) -> Literal["v1", "v2"]:
    for spec in _CONTRACT_SPECS.values():
        if value == spec.format_version:
            return spec.version
    raise BrowserDeliveryPublicationError("browser current pointer format version is unsupported")


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
    qualifying_summary_path: Path | None = None
    qualifying_lap_status_path: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "chunk_paths", tuple(self.chunk_paths))
        object.__setattr__(self, "artifact_digests", MappingProxyType(dict(self.artifact_digests)))


def publish_browser_delivery(
    *, browser_parent: Path, delivery_version: str, delivery: BrowserDeliveryBuild,
    schema_root: Path, contract_version: Literal["v1", "v2"] | None = None,
    progress: ProgressCallback | None = None,
) -> PublishedBrowserDelivery:
    """Validate, stage, and atomically select artifacts from one bound snapshot."""
    emit = progress or (lambda _stage: None)
    if not isinstance(delivery, BrowserDeliveryBuild):
        raise TypeError("delivery must be a BrowserDeliveryBuild")
    version = _safe_delivery_version(delivery_version)
    spec = _resolve_contract_spec(schema_root, contract_version)
    emit("browser_payload_preparing")
    emit("browser_contract_schema_loading")
    schemas, registry = _load_contract_schemas(
        schema_root,
        include_qualifying_summary=spec.version == "v2",
        include_qualifying_lap_status=spec.version == "v2",
    )
    validators = _contract_validators(schemas, registry)
    artifacts = _prepared_artifacts(version, delivery, validators, spec=spec, progress=emit)
    return _publish_payloads(browser_parent, version, artifacts, spec=spec, progress=emit)


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
        _safe_delivery_version(expected_generation_id)
        if not isinstance(expected_manifest_sha256, str) or len(expected_manifest_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in expected_manifest_sha256
        ):
            raise ValueError("expected canonical manifest checksum must be a lowercase SHA-256 digest")
        _require_safe_directory(browser_parent, "browser delivery root")
        selected_pointer_path = pointer_path or (browser_parent / "browser-current.json")
        _validate_browser_pointer_path(browser_parent, selected_pointer_path)
        pointer_file = read_regular_file_no_follow(selected_pointer_path, "browser current pointer")
        pointer = json.loads(pointer_file.data)
        spec = _resolve_contract_spec(schema_root, _contract_version_from_format(pointer.get("formatVersion")))
        version = validate_browser_delivery_pointer(pointer, contract_version=spec.version)
        generation = browser_parent / "generations" / version
        _require_safe_directory(browser_parent / "generations", "browser generations")
        _require_safe_directory(generation, "browser selected delivery")
        manifest_path = generation / "manifest.json"
        manifest_file = read_regular_file_no_follow(manifest_path, "browser manifest")
        if pointer.get("manifestSha256") != hashlib.sha256(manifest_file.data).hexdigest():
            raise ValueError("browser pointer manifest checksum disagrees")
        manifest = json.loads(manifest_file.data)
        if (
            manifest.get("formatVersion") != spec.format_version
            or manifest.get("contractVersion") != spec.version
            or manifest.get("deliveryVersion") != version
            or manifest.get("sourceGenerationId") != expected_generation_id
            or manifest.get("sourceManifestSha256") != expected_manifest_sha256
        ):
            raise ValueError("browser delivery provenance disagrees with canonical generation")
        timeline_reference = manifest.get("timelineSummary")
        sidecar_reference = manifest.get("lapSectorSidecar")
        stint_reference = manifest.get("stintSummary")
        pit_loss_reference = manifest.get("pitLossModel")
        penalty_reference = manifest.get("penaltySidecar")
        qualifying_reference = manifest.get("qualifyingSummary")
        qualifying_lap_status_reference = manifest.get("qualifyingLapStatus")
        references = (
            manifest.get("trackAssets"),
            *((timeline_reference,) if timeline_reference is not None else ()),
            *((sidecar_reference,) if sidecar_reference is not None else ()),
            *((stint_reference,) if stint_reference is not None else ()),
            *((pit_loss_reference,) if pit_loss_reference is not None else ()),
            *((penalty_reference,) if penalty_reference is not None else ()),
            *((qualifying_reference,) if qualifying_reference is not None else ()),
            *((qualifying_lap_status_reference,) if qualifying_lap_status_reference is not None else ()),
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
        schemas, registry = _load_contract_schemas(
            schema_root,
            include_qualifying_summary=spec.version == "v2",
            include_qualifying_lap_status=spec.version == "v2",
        )
        _validate_stored_delivery_payloads(payloads, _contract_validators(schemas, registry), emit, spec=spec)
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


def _validate_browser_pointer_path(browser_parent: Path, pointer_path: Path) -> None:
    """Allow only the root pointer or one session-indexed pointer below root."""
    if not isinstance(pointer_path, Path):
        raise ValueError("browser pointer path must be a pathlib.Path")
    if any(part in {".", ".."} or "\\" in part or "\x00" in part for part in pointer_path.parts):
        raise ValueError("browser pointer path contains an unsafe component")
    root = Path(os.path.abspath(browser_parent))
    candidate = Path(os.path.abspath(pointer_path))
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise ValueError("browser pointer path must remain below its delivery root") from error
    parts = relative.parts
    if parts == ("browser-current.json",):
        return
    if (
        len(parts) == 3
        and parts[0] == "sessions"
        and parts[2] == "browser-current.json"
    ):
        _safe_delivery_version(parts[1])
        return
    raise ValueError("browser pointer path is not an expected relative pointer")


def validate_browser_delivery_pointer(
    pointer: object, *, contract_version: Literal["v1", "v2"] = "v2",
) -> str:
    """Return the selected safe version only for an exact current-pointer shape."""
    if not isinstance(pointer, dict) or set(pointer) != _POINTER_FIELDS:
        raise BrowserDeliveryPublicationError("browser current pointer has an invalid shape")
    spec = _CONTRACT_SPECS[contract_version]
    if pointer["formatVersion"] != spec.format_version:
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


def _validate_stored_delivery_payloads(
    payloads, validators, emit: ProgressCallback, *, spec: _BrowserContractSpec = _CONTRACT_SPECS["v1"],
) -> None:
    encoded = dict(payloads)
    manifest = json.loads(encoded["manifest.json"])
    track = json.loads(encoded["track-assets.json"])
    if not isinstance(manifest, dict) or not isinstance(track, dict):
        raise BrowserDeliveryPublicationError("delivery metadata must be JSON objects")
    _validate_schema_instance(validators["manifest"], manifest, "manifest", spec)
    chunk_refs = manifest.get("chunks")
    if not isinstance(chunk_refs, list):
        raise BrowserDeliveryPublicationError("manifest chunks must be an array")
    track_reference = manifest.get("trackAssets")
    if not isinstance(track_reference, dict) or track_reference.get("path") != "track-assets.json":
        raise BrowserDeliveryPublicationError("manifest track asset reference is invalid")
    if hashlib.sha256(encoded["track-assets.json"]).hexdigest() != track_reference.get("sha256"):
        raise BrowserDeliveryPublicationError("track asset digest disagrees")
    track_required = {"contractVersion", "fixtureId", "trackId", "trackName", "coordinateSpace", "circuitLengthMeters", "rotationDegrees", "startFinish", "centerLine", "innerBoundary", "outerBoundary"}
    if not track_required <= set(track) or track.get("contractVersion") != spec.version or track.get("fixtureId") != manifest.get("fixtureId"):
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
    penalty_reference = manifest.get("penaltySidecar")
    if penalty_reference is not None:
        if not isinstance(penalty_reference, dict) or penalty_reference.get("path") != "penalty-sidecar.json":
            raise BrowserDeliveryPublicationError("manifest penalty sidecar reference is invalid")
        expected_paths.add("penalty-sidecar.json")
    qualifying_reference = manifest.get("qualifyingSummary")
    if qualifying_reference is not None:
        if not isinstance(qualifying_reference, dict) or qualifying_reference.get("path") != "qualifying-summary.json":
            raise BrowserDeliveryPublicationError("manifest qualifying summary reference is invalid")
        expected_paths.add("qualifying-summary.json")
    qualifying_lap_status_reference = manifest.get("qualifyingLapStatus")
    if qualifying_lap_status_reference is not None:
        if (
            not isinstance(qualifying_lap_status_reference, dict)
            or qualifying_lap_status_reference.get("path") != "qualifying-lap-status.json"
        ):
            raise BrowserDeliveryPublicationError(
                "manifest qualifying lap status reference is invalid"
            )
        expected_paths.add("qualifying-lap-status.json")
    total = (
        len(chunk_refs) + 2
        + (1 if timeline_reference is not None else 0)
        + (1 if sidecar_reference is not None else 0)
        + (1 if stint_reference is not None else 0)
        + (1 if pit_loss_reference is not None else 0)
        + (1 if penalty_reference is not None else 0)
        + (1 if qualifying_reference is not None else 0)
        + (1 if qualifying_lap_status_reference is not None else 0)
    )
    _emit_validation_progress(emit, 1, total, "manifest schema")
    _validate_lap_starts(manifest.get("lapStarts", []), chunk_refs)
    _validate_schema_instance(validators["track-assets"], track, "track assets", spec)
    _emit_validation_progress(emit, 2, total, "track assets schema")
    completed = 2
    if timeline_reference is not None:
        timeline = json.loads(encoded["timeline-summary.json"])
        if not isinstance(timeline, dict):
            raise BrowserDeliveryPublicationError("timeline summary must be a JSON object")
        _validate_schema_instance(
            validators["timeline-summary"], timeline, "timeline summary", spec,
        )
        _validate_timeline_summary_contract(timeline, manifest, spec)
        completed += 1
        _emit_validation_progress(emit, completed, total, "timeline summary schema")
    if sidecar_reference is not None:
        sidecar = json.loads(encoded["lap-sector-sidecar.json"])
        if not isinstance(sidecar, dict):
            raise BrowserDeliveryPublicationError("lap sector sidecar must be a JSON object")
        _validate_schema_instance(
            validators["browser-lap-sector-sidecar"], sidecar, "lap sector sidecar", spec,
        )
        _validate_lap_sector_sidecar_contract(sidecar, manifest, spec)
        completed += 1
        _emit_validation_progress(emit, completed, total, "lap sector sidecar schema")
    if stint_reference is not None:
        stint_summary = json.loads(encoded["stint-summary.json"])
        if not isinstance(stint_summary, dict):
            raise BrowserDeliveryPublicationError("stint summary must be a JSON object")
        _validate_schema_instance(
            validators["stint-summary"], stint_summary, "stint summary", spec,
        )
        _validate_stint_summary_contract(stint_summary, manifest, spec)
        completed += 1
        _emit_validation_progress(emit, completed, total, "stint summary schema")
    if pit_loss_reference is not None:
        pit_loss_model = json.loads(encoded["pit-loss-model.json"])
        if not isinstance(pit_loss_model, dict):
            raise BrowserDeliveryPublicationError("pit loss model must be a JSON object")
        _validate_schema_instance(
            validators["pit-loss-model"], pit_loss_model, "pit loss model", spec,
        )
        _validate_pit_loss_model_contract(pit_loss_model, manifest, spec)
        completed += 1
        _emit_validation_progress(emit, completed, total, "pit loss model schema")
    if penalty_reference is not None:
        penalty_sidecar = json.loads(encoded["penalty-sidecar.json"])
        if not isinstance(penalty_sidecar, dict):
            raise BrowserDeliveryPublicationError("penalty sidecar must be a JSON object")
        _validate_schema_instance(
            validators["penalty-sidecar"], penalty_sidecar, "penalty sidecar", spec,
        )
        _validate_penalty_sidecar_contract(penalty_sidecar, manifest, spec)
        completed += 1
        _emit_validation_progress(emit, completed, total, "penalty sidecar schema")
    previous = None
    for sequence, reference in enumerate(chunk_refs, start=1):
        path = reference["path"]
        expected_paths.add(path)
        chunk = json.loads(encoded[path])
        if reference["sequence"] != sequence or path != f"chunks/chunk-{sequence:03d}.json" or reference["schemaId"] != spec.schema_ids["chunk"]:
            raise BrowserDeliveryPublicationError("chunk references are not deterministic and contiguous")
        _validate_chunk_contract(chunk, reference, driver_ids, previous, spec)
        _validate_schema_instance(validators["chunk"], chunk, "chunk", spec)
        previous = reference
        _emit_validation_progress(emit, completed + sequence, total, f"chunk schema {sequence}/{len(chunk_refs)}")
    if qualifying_reference is not None:
        qualifying = json.loads(encoded["qualifying-summary.json"])
        _validate_schema_instance(validators["qualifying-summary"], qualifying, "qualifying summary", spec)
        if qualifying.get("contractVersion") != spec.version or qualifying.get("fixtureId") != manifest.get("fixtureId"):
            raise BrowserDeliveryPublicationError("qualifying summary disagrees with the manifest")
        completed += 1
        _emit_validation_progress(emit, completed, total, "qualifying summary schema")
    if qualifying_lap_status_reference is not None:
        qualifying_lap_status = json.loads(encoded["qualifying-lap-status.json"])
        if not isinstance(qualifying_lap_status, dict):
            raise BrowserDeliveryPublicationError(
                "qualifying lap status sidecar must be a JSON object"
            )
        _validate_schema_instance(
            validators["qualifying-lap-status"],
            qualifying_lap_status,
            "qualifying lap status sidecar",
            spec,
        )
        _validate_qualifying_lap_status_contract(
            qualifying_lap_status, manifest, spec,
        )
        completed += 1
        _emit_validation_progress(
            emit, completed, total, "qualifying lap status sidecar schema",
        )
    if set(encoded) != expected_paths:
        raise BrowserDeliveryPublicationError("delivery contains unreferenced artifacts")


def _prepared_artifacts(
    version: str,
    delivery: BrowserDeliveryBuild,
    validators: Mapping[str, jsonschema_rs.Draft202012Validator],
    *,
    spec: _BrowserContractSpec = _CONTRACT_SPECS["v1"],
    progress: ProgressCallback | None = None,
) -> tuple[PreparedArtifact, ...]:
    emit = progress or (lambda _update: None)
    chunks = delivery.chunks
    _validate_chunks(chunks)
    mode = _delivery_session_mode(delivery) if spec.version == "v2" else None
    race_semantics = mode in {"race", "sprint"}
    summary = delivery.timeline_summary if spec.version == "v1" or race_semantics else None
    sidecar = delivery.lap_sector_sidecar
    stint_summary = delivery.stint_summary
    pit_loss_model = delivery.pit_loss_model if spec.version == "v1" or race_semantics else None
    penalty_sidecar = delivery.penalty_sidecar
    qualifying_like = spec.version == "v2" and mode in {
        "qualifying", "sprint-qualifying", "sprint-shootout",
    }
    qualifying_lap_status = _build_qualifying_lap_status(
        delivery, enabled=qualifying_like,
    )
    total = (
        2 * len(chunks) + 4
        + (2 if summary is not None else 0)
        + (2 if sidecar is not None else 0)
        + (2 if stint_summary is not None else 0)
        + (2 if pit_loss_model is not None else 0)
        + (2 if penalty_sidecar is not None else 0)
        + (
            2
            if qualifying_like
            else 0
        )
        + (2 if qualifying_lap_status is not None else 0)
    )
    fixture_id = delivery.manifest.fixture_id
    manifest = _manifest_contract(delivery, spec, mode)
    schema_track_assets = _versioned_payload(delivery.track_assets, spec)
    _validate_track_contract(schema_track_assets, manifest, spec)
    _emit_validation_progress(emit, 1, total, "track assets")
    _validate_schema_instance(validators["track-assets"], schema_track_assets, "track assets", spec)
    _emit_validation_progress(emit, 2, total, "track assets schema")
    track = _prepare_artifact("track-assets.json", schema_track_assets)

    timeline_artifact: PreparedArtifact | None = None
    timeline_contract = None
    completed = 2
    if summary is not None:
        timeline_contract = _versioned_payload(summary.as_dict(), spec)
        _validate_schema_instance(
            validators["timeline-summary"], timeline_contract, "timeline summary", spec,
        )
        timeline_artifact = _prepare_artifact("timeline-summary.json", timeline_contract)
        completed += 1
        _emit_validation_progress(emit, completed, total, "timeline summary")
        completed += 1
        _emit_validation_progress(emit, completed, total, "timeline summary schema")

    sidecar_artifact: PreparedArtifact | None = None
    sidecar_contract = None
    if sidecar is not None:
        sidecar_contract = _versioned_payload(sidecar.as_dict(), spec)
        _validate_schema_instance(
            validators["browser-lap-sector-sidecar"], sidecar_contract, "lap sector sidecar", spec,
        )
        sidecar_artifact = _prepare_artifact("lap-sector-sidecar.json", sidecar_contract)
        completed += 1
        _emit_validation_progress(emit, completed, total, "lap sector sidecar")
        completed += 1
        _emit_validation_progress(emit, completed, total, "lap sector sidecar schema")

    stint = stint_summary
    stint_artifact: PreparedArtifact | None = None
    stint_contract = None
    if stint is not None:
        stint_contract = _versioned_payload(stint.as_dict(), spec)
        _validate_schema_instance(
            validators["stint-summary"], stint_contract, "stint summary", spec,
        )
        stint_artifact = _prepare_artifact("stint-summary.json", stint_contract)
        completed += 1
        _emit_validation_progress(emit, completed, total, "stint summary")
        completed += 1
        _emit_validation_progress(emit, completed, total, "stint summary schema")

    pit_loss_artifact: PreparedArtifact | None = None
    pit_loss_contract = None
    if pit_loss_model is not None:
        pit_loss_contract = _versioned_payload(pit_loss_model.as_dict(), spec)
        _validate_schema_instance(
            validators["pit-loss-model"], pit_loss_contract, "pit loss model", spec,
        )
        pit_loss_artifact = _prepare_artifact("pit-loss-model.json", pit_loss_contract)
        completed += 1
        _emit_validation_progress(emit, completed, total, "pit loss model")
        completed += 1
        _emit_validation_progress(emit, completed, total, "pit loss model schema")

    penalty_artifact: PreparedArtifact | None = None
    penalty_contract = None
    if penalty_sidecar is not None:
        penalty_contract = _versioned_payload(penalty_sidecar.as_dict(), spec)
        _validate_schema_instance(
            validators["penalty-sidecar"], penalty_contract, "penalty sidecar", spec,
        )
        penalty_artifact = _prepare_artifact("penalty-sidecar.json", penalty_contract)
        completed += 1
        _emit_validation_progress(emit, completed, total, "penalty sidecar")
        completed += 1
        _emit_validation_progress(emit, completed, total, "penalty sidecar schema")

    qualifying_lap_status_artifact: PreparedArtifact | None = None
    qualifying_lap_status_contract = None
    if qualifying_lap_status is not None:
        qualifying_lap_status_contract = _versioned_payload(
            qualifying_lap_status.as_dict(), spec,
        )
        _validate_schema_instance(
            validators["qualifying-lap-status"],
            qualifying_lap_status_contract,
            "qualifying lap status sidecar",
            spec,
        )
        qualifying_lap_status_artifact = _prepare_artifact(
            "qualifying-lap-status.json", qualifying_lap_status_contract,
        )
        completed += 1
        _emit_validation_progress(
            emit, completed, total, "qualifying lap status sidecar",
        )
        completed += 1
        _emit_validation_progress(
            emit, completed, total, "qualifying lap status sidecar schema",
        )

    previous = None
    chunk_artifacts = []
    references = []
    driver_ids = {driver["id"] for driver in cast(list[Mapping[str, str]], manifest["drivers"])}
    for chunk in chunks:
        contract = _chunk_dict(chunk, fixture_id, spec)
        reference = _chunk_reference(chunk, "", spec)
        _validate_chunk_contract(contract, reference, driver_ids, previous, spec)
        chunk_completed = completed + 2 * (chunk.sequence - 1) + 1
        _emit_validation_progress(emit, chunk_completed, total, f"chunk {chunk.sequence}/{len(chunks)}")
        _validate_schema_instance(validators["chunk"], contract, "chunk", spec)
        _emit_validation_progress(emit, chunk_completed + 1, total, f"chunk schema {chunk.sequence}/{len(chunks)}")
        artifact = _prepare_artifact(f"chunks/{chunk.chunk_id}.json", contract)
        reference = _chunk_reference(chunk, artifact.sha256, spec)
        chunk_artifacts.append(artifact)
        references.append(reference)
        previous = reference

    manifest.update({
        "formatVersion": spec.format_version,
        "deliveryVersion": version,
        "sourceGenerationId": delivery.source.generation_id,
        "sourceManifestSha256": delivery.source.manifest_sha256,
        "trackAssets": {"path": "track-assets.json", "schemaId": spec.schema_ids["track-assets"], "sha256": track.sha256},
        "chunks": references,
    })
    if timeline_artifact is not None:
        manifest["timelineSummary"] = {
            "path": timeline_artifact.path,
            "schemaId": spec.schema_ids["timeline-summary"],
            "sha256": timeline_artifact.sha256,
        }
    elif "timelineSummary" in manifest:
        raise BrowserDeliveryPublicationError(
            "manifest timeline summary reference has no summary payload"
        )
    if sidecar_artifact is not None:
        manifest["lapSectorSidecar"] = {
            "path": sidecar_artifact.path,
            "schemaId": spec.schema_ids["browser-lap-sector-sidecar"],
            "sha256": sidecar_artifact.sha256,
        }
        if spec.version == "v2":
            cast(dict[str, object], manifest["schemas"])["lapSectorSidecar"] = (
                spec.schema_ids["browser-lap-sector-sidecar"]
            )
    elif "lapSectorSidecar" in manifest:
        raise BrowserDeliveryPublicationError(
            "manifest lap sector sidecar reference has no sidecar payload"
        )
    if stint_artifact is not None:
        manifest["stintSummary"] = {
            "path": stint_artifact.path,
            "schemaId": spec.schema_ids["stint-summary"],
            "sha256": stint_artifact.sha256,
        }
        if spec.version == "v2":
            cast(dict[str, object], manifest["schemas"])["stintSummary"] = (
                spec.schema_ids["stint-summary"]
            )
    elif "stintSummary" in manifest:
        raise BrowserDeliveryPublicationError(
            "manifest stint summary reference has no stint summary payload"
        )
    if pit_loss_artifact is not None:
        manifest["pitLossModel"] = {
            "path": pit_loss_artifact.path,
            "schemaId": spec.schema_ids["pit-loss-model"],
            "sha256": pit_loss_artifact.sha256,
        }
    elif "pitLossModel" in manifest:
        raise BrowserDeliveryPublicationError(
            "manifest pit loss model reference has no pit loss model payload"
        )
    if penalty_artifact is not None:
        manifest["penaltySidecar"] = {
            "path": penalty_artifact.path,
            "schemaId": spec.schema_ids["penalty-sidecar"],
            "sha256": penalty_artifact.sha256,
        }
    elif "penaltySidecar" in manifest:
        raise BrowserDeliveryPublicationError(
            "manifest penalty sidecar reference has no penalty sidecar payload"
        )
    if qualifying_lap_status_artifact is not None:
        manifest["qualifyingLapStatus"] = {
            "path": qualifying_lap_status_artifact.path,
            "schemaId": spec.schema_ids["qualifying-lap-status"],
            "sha256": qualifying_lap_status_artifact.sha256,
        }
        cast(dict[str, object], manifest["schemas"])["qualifyingLapStatus"] = (
            spec.schema_ids["qualifying-lap-status"]
        )
    elif "qualifyingLapStatus" in manifest:
        raise BrowserDeliveryPublicationError(
            "manifest qualifying lap status reference has no sidecar payload"
        )
    if timeline_contract is not None:
        _validate_timeline_summary_contract(timeline_contract, manifest, spec)
    if sidecar_contract is not None:
        _validate_lap_sector_sidecar_contract(sidecar_contract, manifest, spec)
    if stint_contract is not None:
        _validate_stint_summary_contract(stint_contract, manifest, spec)
    if pit_loss_contract is not None:
        _validate_pit_loss_model_contract(pit_loss_contract, manifest, spec)
    if penalty_contract is not None:
        _validate_penalty_sidecar_contract(penalty_contract, manifest, spec)
    if qualifying_lap_status_contract is not None:
        _validate_qualifying_lap_status_contract(
            qualifying_lap_status_contract, manifest, spec,
        )
    qualifying_artifact = _qualifying_artifact(delivery, manifest, mode, spec)
    if qualifying_artifact is not None:
        _validate_schema_instance(
            validators["qualifying-summary"],
            json.loads(qualifying_artifact.payload),
            "qualifying summary", spec,
        )
        manifest["qualifyingSummary"] = {
            "path": qualifying_artifact.path,
            "schemaId": spec.schema_ids["qualifying-summary"],
            "sha256": qualifying_artifact.sha256,
        }
        schemas = cast(dict[str, object], manifest["schemas"])
        schemas["qualifyingSummary"] = spec.schema_ids["qualifying-summary"]
    _validate_manifest_contract(
        manifest, delivery, references, timeline_artifact, sidecar_artifact, stint_artifact,
        pit_loss_artifact, penalty_artifact, qualifying_artifact,
        qualifying_lap_status_artifact, spec=spec,
    )
    _emit_validation_progress(emit, total - 1, total, "manifest")
    _validate_schema_instance(validators["manifest"], manifest, "manifest", spec)
    _emit_validation_progress(emit, total, total, "manifest schema")
    return (
        _prepare_artifact("manifest.json", manifest),
        track,
        *((timeline_artifact,) if timeline_artifact is not None else ()),
        *((sidecar_artifact,) if sidecar_artifact is not None else ()),
        *((stint_artifact,) if stint_artifact is not None else ()),
        *((pit_loss_artifact,) if pit_loss_artifact is not None else ()),
        *((penalty_artifact,) if penalty_artifact is not None else ()),
        *((qualifying_artifact,) if qualifying_artifact is not None else ()),
        *((qualifying_lap_status_artifact,) if qualifying_lap_status_artifact is not None else ()),
        *chunk_artifacts,
    )


def _delivery_session_mode(delivery: BrowserDeliveryBuild) -> SessionMode:
    try:
        value = delivery.source.frames["session_metadata"].row(0, named=True)["session_mode"]
        mode = normalize_session_mode(value)
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise BrowserDeliveryPublicationError(
            "v2 browser publication requires normalized canonical session_mode metadata"
        ) from error
    return mode


def _manifest_contract(
    delivery: BrowserDeliveryBuild,
    spec: _BrowserContractSpec,
    mode: SessionMode | None,
) -> dict[str, object]:
    manifest = cast(dict[str, object], delivery.manifest.as_dict())
    if spec.version == "v1":
        return manifest
    assert mode is not None
    manifest.update({
        "contractVersion": "v2",
        "formatVersion": spec.format_version,
        "sessionMode": mode,
        "schemas": {
            "manifest": spec.schema_ids["manifest"],
            "chunk": spec.schema_ids["chunk"],
            "trackAssets": spec.schema_ids["track-assets"],
        },
    })
    return manifest


def _versioned_payload(value: object, spec: _BrowserContractSpec) -> object:
    payload = cast(dict[str, object], _schema_compatible_value(value))
    if spec.version == "v2":
        payload = dict(payload)
        payload["contractVersion"] = "v2"
    return payload


def _build_qualifying_lap_status(
    delivery: BrowserDeliveryBuild, *, enabled: bool,
) -> BrowserQualifyingLapStatusSidecar | None:
    if not enabled:
        return None
    if delivery.qualifying_lap_status_sidecar is not None:
        return delivery.qualifying_lap_status_sidecar
    frame_names = frozenset(delivery.source.frames)
    required = frozenset({"drivers", "laps", "race_control_messages"})
    if not required <= frame_names:
        if not (frame_names & {"drivers", "race_control_messages"}):
            return None
        raise BrowserDeliveryPublicationError(
            "qualifying lap-status inputs are incomplete"
        )
    if not has_qualifying_lap_status_messages(
        delivery.source.frames["race_control_messages"]
    ):
        return None
    try:
        return build_qualifying_lap_status_sidecar(delivery.source)
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise BrowserDeliveryPublicationError(
            "qualifying lap-status sidecar cannot be reconciled"
        ) from error


def _qualifying_artifact(
    delivery: BrowserDeliveryBuild,
    manifest: Mapping[str, object],
    mode: SessionMode | None,
    spec: _BrowserContractSpec,
) -> PreparedArtifact | None:
    if spec.version != "v2" or mode not in {
        "qualifying", "sprint-qualifying", "sprint-shootout",
    }:
        return None
    results = delivery.source.frames["results"].to_dicts()
    laps = delivery.source.frames["laps"].to_dicts()
    rows: dict[str, object] = {}
    for result in results:
        driver_id = cast(str, result["driver_id"])
        position = _nullable_positive_int(result.get("classified_position"))
        best = _best_lap(laps, driver_id)
        rows[driver_id] = {
            "qualifyingPosition": [position],
            "q1TimeMs": [result.get("q1_time_ms")],
            "q2TimeMs": [result.get("q2_time_ms")],
            "q3TimeMs": [result.get("q3_time_ms")],
            "bestLapNumber": [None if best is None else best[0]],
            "bestLapTimeMs": [None if best is None else best[1]],
        }
    if set(rows) != {cast(str, driver["id"]) for driver in cast(tuple[Mapping[str, object], ...], manifest["drivers"])}:
        raise BrowserDeliveryPublicationError("qualifying summary driver IDs disagree with the manifest")
    return _prepare_artifact("qualifying-summary.json", {
        "contractVersion": "v2", "fixtureId": manifest["fixtureId"], "drivers": rows,
    })


def _nullable_positive_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip().isdigit():
        return None
    try:
        number = int(cast(str | int, value))
    except (TypeError, ValueError) as error:
        raise BrowserDeliveryPublicationError("qualifying position is not an integer") from error
    return number if number >= 1 else None


def _best_lap(rows: list[dict[str, object]], driver_id: str) -> tuple[int, int] | None:
    candidates = tuple(
        (cast(int, row["lap_number"]), cast(int, duration))
        for row in rows
        if row.get("driver_id") == driver_id
        and type(row.get("lap_number")) is int
        and type(row.get("lap_duration_ms")) is int
        and (duration := row.get("lap_duration_ms")) is not None
        and cast(int, duration) >= 0
        and row.get("deleted") is not True
    )
    return min(candidates, key=lambda value: (value[1], value[0])) if candidates else None


def _artifact_payloads(
    version: str,
    delivery: BrowserDeliveryBuild,
    schema_root: Path,
) -> tuple[PreparedArtifact, ...]:
    """Prepare fully validated artifacts for focused tests."""
    spec = _resolve_contract_spec(schema_root, None)
    schemas, registry = _load_contract_schemas(
        schema_root,
        include_qualifying_summary=spec.version == "v2",
        include_qualifying_lap_status=spec.version == "v2",
    )
    return _prepared_artifacts(version, delivery, _contract_validators(schemas, registry), spec=spec)


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
    spec = _CONTRACT_SPECS["v2"] if b'"contractVersion":"v2"' in manifest.payload else _CONTRACT_SPECS["v1"]
    expected_paths = {
        "manifest.json", "track-assets.json",
        *(f"chunks/{chunk.chunk_id}.json" for chunk in delivery.chunks),
    }
    mode = _delivery_session_mode(delivery) if spec.version == "v2" else None
    race_semantics = mode in {"race", "sprint"}
    if delivery.timeline_summary is not None and (spec.version == "v1" or race_semantics):
        expected_paths.add("timeline-summary.json")
    if delivery.lap_sector_sidecar is not None:
        expected_paths.add("lap-sector-sidecar.json")
    if delivery.stint_summary is not None:
        expected_paths.add("stint-summary.json")
    if delivery.pit_loss_model is not None and (spec.version == "v1" or race_semantics):
        expected_paths.add("pit-loss-model.json")
    if delivery.penalty_sidecar is not None:
        expected_paths.add("penalty-sidecar.json")
    if spec.version == "v2" and mode in {
        "qualifying", "sprint-qualifying", "sprint-shootout",
    }:
        expected_paths.add("qualifying-summary.json")
        if {"drivers", "laps", "race_control_messages"} <= set(delivery.source.frames):
            expected_paths.add("qualifying-lap-status.json")
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
    qualifying_artifact: PreparedArtifact | None = None,
    qualifying_lap_status_artifact: PreparedArtifact | None = None,
    *,
    spec: _BrowserContractSpec = _CONTRACT_SPECS["v1"],
) -> None:
    if manifest["sourceGenerationId"] != delivery.source.generation_id or manifest["sourceManifestSha256"] != delivery.source.manifest_sha256:
        raise BrowserDeliveryPublicationError("delivery provenance disagrees with its source snapshot")
    if len(refs) != len(delivery.chunks):
        raise BrowserDeliveryPublicationError("manifest chunk count disagrees")
    timeline_reference = manifest.get("timelineSummary")
    if timeline_artifact is None:
        if timeline_reference is not None or (spec.version == "v1" and delivery.timeline_summary is not None):
            raise BrowserDeliveryPublicationError("timeline summary reference disagrees with its payload")
    elif timeline_reference != {
        "path": timeline_artifact.path,
        "schemaId": spec.schema_ids["timeline-summary"],
        "sha256": timeline_artifact.sha256,
    }:
        raise BrowserDeliveryPublicationError("timeline summary reference disagrees with its payload")
    sidecar_reference = manifest.get("lapSectorSidecar")
    if sidecar_artifact is None:
        if sidecar_reference is not None or (spec.version == "v1" and delivery.lap_sector_sidecar is not None):
            raise BrowserDeliveryPublicationError("lap sector sidecar reference disagrees with its payload")
    elif sidecar_reference != {
        "path": sidecar_artifact.path,
        "schemaId": spec.schema_ids["browser-lap-sector-sidecar"],
        "sha256": sidecar_artifact.sha256,
    }:
        raise BrowserDeliveryPublicationError("lap sector sidecar reference disagrees with its payload")
    stint_reference = manifest.get("stintSummary")
    if stint_artifact is None:
        if stint_reference is not None or (spec.version == "v1" and delivery.stint_summary is not None):
            raise BrowserDeliveryPublicationError("manifest stint summary reference disagrees with its payload")
    elif stint_reference != {
        "path": stint_artifact.path,
        "schemaId": spec.schema_ids["stint-summary"],
        "sha256": stint_artifact.sha256,
    }:
        raise BrowserDeliveryPublicationError("stint summary reference disagrees with its payload")
    pit_loss_reference = manifest.get("pitLossModel")
    if pit_loss_artifact is None:
        if pit_loss_reference is not None or (spec.version == "v1" and delivery.pit_loss_model is not None):
            raise BrowserDeliveryPublicationError("manifest pit loss model reference disagrees with its payload")
    elif pit_loss_reference != {
        "path": pit_loss_artifact.path,
        "schemaId": spec.schema_ids["pit-loss-model"],
        "sha256": pit_loss_artifact.sha256,
    }:
        raise BrowserDeliveryPublicationError("pit loss model reference disagrees with its payload")
    penalty_reference = manifest.get("penaltySidecar")
    if penalty_artifact is None:
        if penalty_reference is not None or delivery.penalty_sidecar is not None:
            raise BrowserDeliveryPublicationError("manifest penalty sidecar reference disagrees with its payload")
    elif penalty_reference != {
        "path": penalty_artifact.path,
        "schemaId": spec.schema_ids["penalty-sidecar"],
        "sha256": penalty_artifact.sha256,
    }:
        raise BrowserDeliveryPublicationError("penalty sidecar reference disagrees with its payload")
    qualifying_reference = manifest.get("qualifyingSummary")
    if qualifying_artifact is None:
        if qualifying_reference is not None:
            raise BrowserDeliveryPublicationError("qualifying summary reference has no summary payload")
    elif qualifying_reference != {
        "path": qualifying_artifact.path,
        "schemaId": spec.schema_ids["qualifying-summary"],
        "sha256": qualifying_artifact.sha256,
    }:
        raise BrowserDeliveryPublicationError("qualifying summary reference disagrees with its payload")
    qualifying_lap_status_reference = manifest.get("qualifyingLapStatus")
    if qualifying_lap_status_artifact is None:
        if qualifying_lap_status_reference is not None:
            raise BrowserDeliveryPublicationError(
                "qualifying lap status reference has no sidecar payload"
            )
    elif qualifying_lap_status_reference != {
        "path": qualifying_lap_status_artifact.path,
        "schemaId": spec.schema_ids["qualifying-lap-status"],
        "sha256": qualifying_lap_status_artifact.sha256,
    }:
        raise BrowserDeliveryPublicationError(
            "qualifying lap status reference disagrees with its payload"
        )
    if qualifying_lap_status_artifact is not None and (
        spec.version != "v2"
        or manifest.get("sessionMode") not in {
            "qualifying", "sprint-qualifying", "sprint-shootout",
        }
    ):
        raise BrowserDeliveryPublicationError(
            "qualifying lap status sidecar is valid only for qualifying-like modes"
        )
    _validate_lap_starts(manifest.get("lapStarts", []), refs)
    for sequence, (ref, expected_chunk) in enumerate(zip(refs, delivery.chunks, strict=True), start=1):
        path = ref["path"]
        if ref["sequence"] != sequence or path != f"chunks/chunk-{sequence:03d}.json" or ref["schemaId"] != spec.schema_ids["chunk"]:
            raise BrowserDeliveryPublicationError("chunk references are not deterministic and contiguous")
        if ref["path"] != f"chunks/{expected_chunk.chunk_id}.json":
            raise BrowserDeliveryPublicationError("chunk payload disagrees with its immutable model")
def _emit_validation_progress(
    emit: ProgressCallback, completed: int, total: int, detail: str,
) -> None:
    emit(BrowserValidationProgress(
        "browser_schema_artifact_validating", completed, total, detail,
    ))


def _validate_track_contract(
    track, manifest, spec: _BrowserContractSpec = _CONTRACT_SPECS["v1"],
) -> None:
    required = {"contractVersion", "fixtureId", "trackId", "trackName", "coordinateSpace", "circuitLengthMeters", "rotationDegrees", "startFinish", "centerLine", "innerBoundary", "outerBoundary"}
    if not required <= set(track) or track.get("contractVersion") != spec.version or track.get("fixtureId") != manifest["fixtureId"]:
        raise BrowserDeliveryPublicationError("track assets disagree with the manifest")


def _validate_timeline_summary_contract(
    summary, manifest, spec: _BrowserContractSpec = _CONTRACT_SPECS["v1"],
) -> None:
    """Apply semantic checks not expressible in the compact JSON schema."""
    if summary.get("contractVersion") != spec.version or summary.get("fixtureId") != manifest.get("fixtureId"):
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


def _validate_lap_sector_sidecar_contract(
    sidecar, manifest, spec: _BrowserContractSpec = _CONTRACT_SPECS["v1"],
) -> None:
    """Apply semantic checks not expressible in the compact JSON schema."""
    if sidecar.get("contractVersion") != spec.version or sidecar.get("fixtureId") != manifest.get("fixtureId"):
        raise BrowserDeliveryPublicationError("lap sector sidecar disagrees with the manifest")
    driver_ids = {driver["id"] for driver in manifest.get("drivers", ())}
    if set(sidecar.get("drivers", {})) != driver_ids:
        raise BrowserDeliveryPublicationError("lap sector sidecar drivers disagree with the manifest")


def _validate_penalty_sidecar_contract(
    sidecar, manifest, spec: _BrowserContractSpec = _CONTRACT_SPECS["v1"],
) -> None:
    """Ensure issued-penalty identities belong to the published replay."""
    if sidecar.get("contractVersion") != spec.version or sidecar.get("fixtureId") != manifest.get("fixtureId"):
        raise BrowserDeliveryPublicationError("penalty sidecar disagrees with the manifest")
    driver_ids = {driver["id"] for driver in manifest.get("drivers", ())}
    issuances = sidecar.get("penaltyIssuances")
    if not isinstance(issuances, list) or any(
        not isinstance(issuance, dict) or issuance.get("driverId") not in driver_ids
        for issuance in issuances
    ):
        raise BrowserDeliveryPublicationError("penalty sidecar driver IDs disagree with the manifest")


def _validate_qualifying_lap_status_contract(
    sidecar, manifest, spec: _BrowserContractSpec = _CONTRACT_SPECS["v1"],
) -> None:
    """Validate immutable identity, aligned columns, and causal event order."""
    qualifying_modes = {"qualifying", "sprint-qualifying", "sprint-shootout"}
    if (
        spec.version != "v2"
        or manifest.get("sessionMode") not in qualifying_modes
        or sidecar.get("contractVersion") != "v2"
        or sidecar.get("fixtureId") != manifest.get("fixtureId")
    ):
        raise BrowserDeliveryPublicationError(
            "qualifying lap status sidecar disagrees with the manifest"
        )
    schemas = manifest.get("schemas")
    if not isinstance(schemas, dict) or schemas.get("qualifyingLapStatus") != spec.schema_ids[
        "qualifying-lap-status"
    ]:
        raise BrowserDeliveryPublicationError(
            "qualifying lap status schema registry entry is invalid"
        )
    drivers = sidecar.get("drivers")
    manifest_drivers = manifest.get("drivers")
    if not isinstance(drivers, dict) or not isinstance(manifest_drivers, list):
        raise BrowserDeliveryPublicationError(
            "qualifying lap status sidecar drivers are invalid"
        )
    driver_ids = {driver.get("id") for driver in manifest_drivers if isinstance(driver, dict)}
    if set(drivers) != driver_ids:
        raise BrowserDeliveryPublicationError(
            "qualifying lap status sidecar drivers disagree with the manifest"
        )
    if list(drivers) != sorted(drivers):
        raise BrowserDeliveryPublicationError(
            "qualifying lap status drivers are not deterministically ordered"
        )
    lap_numbers: dict[str, set[int]] = {}
    for driver_id, record in drivers.items():
        if not isinstance(driver_id, str) or not isinstance(record, dict):
            raise BrowserDeliveryPublicationError(
                "qualifying lap status driver record is invalid"
            )
        column_values = tuple(
            record.get(name)
            for name in ("lapNumber", "lapStartMs", "lapEndMs", "status", "deletedReason")
        )
        if any(not isinstance(column, list) for column in column_values):
            raise BrowserDeliveryPublicationError(
                "qualifying lap status driver columns are invalid"
            )
        columns = cast(
            tuple[list[object], list[object], list[object], list[object], list[object]],
            column_values,
        )
        if len({len(column) for column in columns}) != 1:
            raise BrowserDeliveryPublicationError(
                "qualifying lap status driver columns are not aligned"
            )
        laps, starts, ends, statuses, reasons = columns
        if any(type(lap) is not int or lap < 1 for lap in laps):
            raise BrowserDeliveryPublicationError(
                "qualifying lap status lap numbers are not deterministically ordered"
            )
        lap_values = cast(list[int], laps)
        if lap_values != sorted(set(lap_values)):
            raise BrowserDeliveryPublicationError(
                "qualifying lap status lap numbers are not deterministically ordered"
            )
        start_values = cast(list[int], starts)
        end_values = cast(list[int], ends)
        if any(
            type(value) is not int or not 0 <= value <= MAX_INT64
            for values in (starts, ends) for value in values
        ) or any(end <= start for start, end in zip(start_values, end_values, strict=True)):
            raise BrowserDeliveryPublicationError(
                "qualifying lap status lap bounds are invalid"
            )
        if any(status not in {"valid", "deleted"} for status in statuses):
            raise BrowserDeliveryPublicationError(
                "qualifying lap status values are invalid"
            )
        if any(
            reason is not None and (not isinstance(reason, str) or not reason.strip())
            for reason in reasons
        ) or any(status == "valid" and reason is not None for status, reason in zip(statuses, reasons, strict=True)):
            raise BrowserDeliveryPublicationError(
                "qualifying lap status deleted reasons are invalid"
            )
        lap_numbers[driver_id] = set(lap_values)

    events = sidecar.get("events")
    if not isinstance(events, list):
        raise BrowserDeliveryPublicationError("qualifying lap status events are invalid")
    for event in events:
        if not isinstance(event, dict):
            raise BrowserDeliveryPublicationError("qualifying lap status event is invalid")
        driver_id, lap_number, event_time = (
            event.get("driverId"), event.get("lapNumber"), event.get("eventTimeMs")
        )
        if (
            not isinstance(driver_id, str)
            or driver_id not in lap_numbers
            or type(lap_number) is not int
            or lap_number not in lap_numbers[driver_id]
            or type(event_time) is not int
            or not 0 <= event_time <= MAX_INT64
            or event.get("status") not in {"deleted", "reinstated"}
            or not isinstance(event.get("rawMessage"), str)
            or not event["rawMessage"].strip()
        ):
            raise BrowserDeliveryPublicationError(
                "qualifying lap status event identity is invalid"
            )
        reason = event.get("reason")
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            raise BrowserDeliveryPublicationError(
                "qualifying lap status event reason is invalid"
            )
    semantic_event_keys = tuple(
        (
            event["driverId"], event["lapNumber"], event["eventTimeMs"],
            event["status"], event.get("reason"),
        )
        for event in events
    )
    if len(set(semantic_event_keys)) != len(semantic_event_keys):
        raise BrowserDeliveryPublicationError(
            "qualifying lap status events contain semantic duplicates"
        )
    same_time_statuses: dict[tuple[str, int, int], set[str]] = {}
    for event in events:
        same_time_statuses.setdefault(
            (event["driverId"], event["lapNumber"], event["eventTimeMs"]),
            set(),
        ).add(cast(str, event["status"]))
    if any(len(statuses) > 1 for statuses in same_time_statuses.values()):
        raise BrowserDeliveryPublicationError(
            "qualifying lap status events contain contradictory same-time statuses"
        )
    event_key = lambda event: (
        event["eventTimeMs"], event["driverId"], event["lapNumber"],
        event["status"], event.get("reason") or "", event["rawMessage"],
    )
    if events != sorted(events, key=event_key):
        raise BrowserDeliveryPublicationError(
            "qualifying lap status events are not deterministically ordered"
        )
    effective_status = {
        (driver_id, lap_number): "valid"
        for driver_id, laps in lap_numbers.items()
        for lap_number in laps
    }
    for event in events:
        effective_status[(event["driverId"], event["lapNumber"])] = (
            "deleted" if event["status"] == "deleted" else "valid"
        )
    for driver_id, record in drivers.items():
        for lap_number, status in zip(
            cast(list[object], record["lapNumber"]),
            cast(list[object], record["status"]),
            strict=True,
        ):
            if effective_status[(driver_id, cast(int, lap_number))] != status:
                raise BrowserDeliveryPublicationError(
                    "qualifying lap status events disagree with final status"
                )


def _validate_stint_summary_contract(
    summary, manifest, spec: _BrowserContractSpec = _CONTRACT_SPECS["v1"],
) -> None:
    """Apply semantic checks not expressible in the compact JSON schema."""
    if summary.get("contractVersion") != spec.version or summary.get("fixtureId") != manifest.get("fixtureId"):
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


def _validate_pit_loss_model_contract(
    model, manifest, spec: _BrowserContractSpec = _CONTRACT_SPECS["v1"],
) -> None:
    """Apply the causal timeline guarantees not expressible in JSON Schema."""
    if (
        model.get("contractVersion") != spec.version
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
    *,
    include_qualifying_summary: bool = False,
    include_qualifying_lap_status: bool = False,
) -> tuple[dict[str, Mapping[str, object]], jsonschema_rs.Registry]:
    if not isinstance(schema_root, Path):
        raise TypeError("schema_root must be a pathlib.Path")
    schemas: dict[str, Mapping[str, object]] = {}
    try:
        names = [
            "manifest", "chunk", "track-assets", "timeline-summary",
            "browser-lap-sector-sidecar", "penalty-sidecar", "stint-summary", "pit-loss-model",
        ]
        if include_qualifying_summary:
            names.append("qualifying-summary")
        if include_qualifying_lap_status:
            names.append("qualifying-lap-status")
        for name in names:
            filename = (
                "browser-qualifying-lap-status"
                if name == "qualifying-lap-status" else name
            )
            guarded = read_regular_file_no_follow(
                schema_root / f"{filename}.schema.json", f"browser {name} schema"
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
    spec: _BrowserContractSpec = _CONTRACT_SPECS["v1"],
) -> None:
    try:
        validator.validate(instance)
    except (jsonschema_rs.ValidationError, jsonschema_rs.ReferencingError, ValueError, TypeError) as error:
        raise BrowserDeliveryPublicationError(
            f"{label} fails replay-data {spec.version} schema validation"
        ) from error


def _validate_chunk_contract(
    chunk, ref, driver_ids, previous, spec: _BrowserContractSpec = _CONTRACT_SPECS["v1"],
) -> None:
    required = {"contractVersion", "fixtureId", "chunkId", "sequence", "startMs", "endMs", "overlap", "timeMs", "authoritativeStartIndex", "drivers", "leaderboardOrder", "trackStatusCode", "weatherState", "events"}
    if not required <= set(chunk) or chunk["contractVersion"] != spec.version:
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
    spec: _BrowserContractSpec = _CONTRACT_SPECS["v1"],
    progress: ProgressCallback,
) -> PublishedBrowserDelivery:
    _validate_staging_artifact_paths(artifacts)
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
            "formatVersion": spec.format_version,
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
            if _pointer_selects(root_fd, version, manifest.sha256, spec):
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
        generation / "qualifying-summary.json" if "qualifying-summary.json" in digests else None,
        generation / "qualifying-lap-status.json" if "qualifying-lap-status.json" in digests else None,
    )


def _close_descriptor(descriptor: int | None, cleanup_errors: list[BaseException]) -> None:
    if descriptor is None:
        return
    try:
        os.close(descriptor)
    except BaseException as error:
        cleanup_errors.append(error)


def _pointer_selects(
    root_fd: int, version: str, manifest_sha256: str,
    spec: _BrowserContractSpec = _CONTRACT_SPECS["v1"],
) -> bool:
    descriptor: int | None = None
    try:
        descriptor = os.open("browser-current.json", os.O_RDONLY | _NO_FOLLOW, dir_fd=root_fd)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            return False
        payload = bytearray()
        while block := os.read(descriptor, 64 * 1024):
            payload.extend(block)
        pointer = json.loads(bytes(payload))
        return (
            validate_browser_delivery_pointer(pointer, contract_version=spec.version) == version
            and pointer["manifestSha256"] == manifest_sha256
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
    if (
        not isinstance(name, str)
        or not name
        or name in {".", ".."}
        or Path(name).name != name
        or "\x00" in name
    ):
        raise BrowserDeliveryPublicationError("browser staged artifact name must be one safe component")
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


def _validate_staging_artifact_paths(artifacts: tuple[PreparedArtifact, ...]) -> None:
    """Validate artifact paths before creating the publication root."""
    for artifact in artifacts:
        relative = _safe_delivery_path(artifact.path)
        if relative.startswith("chunks/"):
            if relative.count("/") != 1:
                raise BrowserDeliveryPublicationError("browser chunk path must have one safe child")
        elif "/" in relative:
            raise BrowserDeliveryPublicationError("browser artifact path must be a direct child")


def _validate_chunks(chunks: tuple[BrowserChunk, ...]) -> None:
    if not chunks or tuple(chunk.sequence for chunk in chunks) != tuple(range(1, len(chunks) + 1)):
        raise BrowserDeliveryPublicationError("chunks must be a non-empty contiguous sequence")


def _chunk_reference(
    chunk: BrowserChunk, digest: str, spec: _BrowserContractSpec = _CONTRACT_SPECS["v1"],
) -> dict[str, object]:
    overlap_ms = 0 if chunk.overlap.range_start_ms is None else chunk.start_ms - chunk.overlap.range_start_ms
    return {"sequence": chunk.sequence, "path": f"chunks/{chunk.chunk_id}.json", "schemaId": spec.schema_ids["chunk"], "startMs": chunk.start_ms, "endMs": chunk.end_ms, "overlapWithPreviousMs": overlap_ms, "sha256": digest}


def _prepare_artifact(path: str, contract: object) -> PreparedArtifact:
    payload = _serialize_json(contract)
    return PreparedArtifact(path, payload, hashlib.sha256(payload).hexdigest())


def _chunk_dict(
    chunk: BrowserChunk, fixture_id: str, spec: _BrowserContractSpec = _CONTRACT_SPECS["v1"],
) -> dict[str, object]:
    return {"contractVersion": spec.version, "fixtureId": fixture_id, "chunkId": chunk.chunk_id, "sequence": chunk.sequence, "startMs": chunk.start_ms, "endMs": chunk.end_ms, "overlap": {"kind": chunk.overlap.kind, "previousChunkPath": chunk.overlap.previous_chunk_path, "range": None if chunk.overlap.range_start_ms is None else {"startMs": chunk.overlap.range_start_ms, "endMs": chunk.overlap.range_end_ms}, "authoritativeFromMs": chunk.overlap.authoritative_from_ms}, "timeMs": chunk.time_ms, "authoritativeStartIndex": chunk.authoritative_start_index, "drivers": {driver_id: _driver_dict(fields) for driver_id, fields in chunk.drivers.items()}, "leaderboardOrder": chunk.leaderboard_order, "trackStatusCode": chunk.track_status_code, "weatherState": chunk.weather_state, "events": [_event_dict(event) for event in chunk.events]}


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
