"""Fail-closed publication of validated browser deliveries to Cloudflare R2."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Protocol, cast
from urllib.parse import urlparse

from f1_replay_pipeline.app.catalog_v2_schema import (
    CatalogV2Payload,
    CatalogV2RaceRecord,
    CatalogV2SessionRecord,
    validate_active_catalog,
)
from f1_replay_pipeline.app.session_pointer_publication import (
    browser_session_pointer_path,
    read_session_browser_pointer,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_publication import (
    validate_complete_browser_delivery,
)
from f1_replay_pipeline.storage.generation_publication import (
    read_regular_file_no_follow,
    verify_regular_file_identity,
)
from f1_replay_pipeline.domain.generation_identity import validate_generation_id


IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
VISUAL_CACHE_CONTROL = "public, max-age=86400, must-revalidate"
MUTABLE_CACHE_CONTROL = "public, max-age=0, must-revalidate"
_REFERENCE_FIELDS = (
    "trackAssets",
    "timelineSummary",
    "lapSectorSidecar",
    "stintSummary",
    "pitLossModel",
    "penaltySidecar",
)


class R2PublicationError(RuntimeError):
    """Raised when a safe R2 publication cannot be completed."""


class R2ObjectClient(Protocol):
    """The small S3-compatible surface used by the publisher."""

    def head_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]: ...

    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: bytes,
        ContentType: str,
        CacheControl: str,
        Metadata: Mapping[str, str],
    ) -> Mapping[str, object]: ...


DeliveryValidator = Callable[..., None]


@dataclass(frozen=True)
class R2PublicationSource:
    """Local season inputs from which a public browser-only catalog is built."""

    year: int
    season_root: Path
    schema_root: Path
    key_prefix: str = "seasons"

    def __post_init__(self) -> None:
        if type(self.year) is not int or self.year < 1:
            raise ValueError("year must be a positive integer")
        if not isinstance(self.season_root, Path) or not isinstance(self.schema_root, Path):
            raise TypeError("season_root and schema_root must be pathlib.Path values")
        _safe_relative_path(self.key_prefix, "R2 key prefix")


@dataclass(frozen=True)
class R2PublicationConfig:
    endpoint_url: str
    bucket: str

    def __post_init__(self) -> None:
        parsed = urlparse(self.endpoint_url)
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.hostname is None
            or not parsed.hostname.endswith(".r2.cloudflarestorage.com")
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "R2_ENDPOINT_URL must be an HTTPS r2.cloudflarestorage.com origin"
            )
        if (
            not isinstance(self.bucket, str)
            or not self.bucket
            or "/" in self.bucket
            or "\\" in self.bucket
            or "\x00" in self.bucket
        ):
            raise ValueError("R2_BUCKET must be a non-empty bucket name")

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "R2PublicationConfig":
        values = os.environ if environ is None else environ
        endpoint = values.get("R2_ENDPOINT_URL", "").strip()
        bucket = values.get("R2_BUCKET", "").strip()
        missing = [
            name
            for name, value in (("R2_ENDPOINT_URL", endpoint), ("R2_BUCKET", bucket))
            if not value
        ]
        if missing:
            raise R2PublicationError(
                f"missing R2 publication environment variable(s): {', '.join(missing)}"
            )
        try:
            return cls(endpoint, bucket)
        except ValueError as error:
            raise R2PublicationError(str(error)) from error


@dataclass(frozen=True)
class R2ObjectSpec:
    key: str
    cache_control: str
    expected_sha256: str
    local_path: Path | None = None
    payload: bytes | None = field(default=None, repr=False)
    immutable: bool = False

    def __post_init__(self) -> None:
        _safe_relative_path(self.key, "R2 object key")
        if (self.local_path is None) == (self.payload is None):
            raise ValueError("R2 object requires exactly one local source")
        if (
            len(self.expected_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.expected_sha256)
        ):
            raise ValueError("R2 object SHA-256 must be lowercase hexadecimal")


@dataclass(frozen=True)
class R2PublicationPlan:
    immutable: tuple[R2ObjectSpec, ...]
    visuals: tuple[R2ObjectSpec, ...]
    pointers: tuple[R2ObjectSpec, ...]
    catalog: R2ObjectSpec

    @property
    def objects(self) -> tuple[R2ObjectSpec, ...]:
        return (*self.immutable, *self.visuals, *self.pointers, self.catalog)


@dataclass(frozen=True)
class R2PublicationResult:
    uploaded: int
    reused: int
    catalog_key: str


@dataclass(frozen=True)
class R2ProgressEvent:
    """One rendering-independent R2 validation or publication update."""

    phase: str
    completed: int
    total: int
    uploaded: int = 0
    reused: int = 0
    key: str | None = None

    def __post_init__(self) -> None:
        if not self.phase:
            raise ValueError("R2 progress phase must be non-empty")
        if min(self.completed, self.total, self.uploaded, self.reused) < 0:
            raise ValueError("R2 progress counters must be non-negative")
        if self.completed > self.total:
            raise ValueError("R2 progress completed count exceeds total")


R2ProgressCallback = Callable[[R2ProgressEvent], None]


def build_r2_publication_plan(
    source: R2PublicationSource,
    *,
    validate_delivery: DeliveryValidator = validate_complete_browser_delivery,
    progress: R2ProgressCallback | None = None,
) -> R2PublicationPlan:
    """Validate local browser data and build an immutable-first publication plan."""
    emit = progress or (lambda _event: None)
    catalog_path = source.season_root / "catalog.json"
    catalog = _read_json_object(catalog_path, "season catalog")
    try:
        parsed_catalog = validate_active_catalog(catalog)
    except (TypeError, ValueError) as error:
        raise R2PublicationError(
            "season catalog is malformed, mixed-version, or belongs to another season"
        ) from error
    if parsed_catalog.year != source.year:
        raise R2PublicationError("season catalog is malformed or belongs to another season")
    raw_races = catalog.get("races")
    if not isinstance(raw_races, list):
        raise R2PublicationError("season catalog races must be an array")

    immutable: list[R2ObjectSpec] = []
    pointers: list[R2ObjectSpec] = []
    visuals: list[R2ObjectSpec] = []
    public_races: list[CatalogV2RaceRecord] = []
    seen_keys: set[str] = set()
    validation_total = _validated_session_count(raw_races)
    validation_completed = 0
    emit(R2ProgressEvent("r2_validating", 0, validation_total))

    for record in raw_races:
        race, race_immutable, race_pointers = _plan_public_race(
            source, record, validate_delivery=validate_delivery,
        )
        if race is None:
            continue
        for session in race.sessions:
            validation_completed += 1
            emit(R2ProgressEvent(
                "r2_validating",
                validation_completed,
                validation_total,
                key=f"{race.race_id}/{session.session_code}",
            ))
        public_races.append(race)
        _extend_unique(immutable, race_immutable, seen_keys)
        _extend_unique(pointers, race_pointers, seen_keys)
        visual = _visual_spec(source, race)
        if visual is not None:
            _extend_unique(visuals, (visual,), seen_keys)

    if not public_races:
        raise R2PublicationError(
            "no validated browser sessions are available for R2 publication"
        )
    payload = CatalogV2Payload(source.year, tuple(public_races)).to_json_bytes()
    catalog_spec = _payload_spec(
        _object_key(source, PurePosixPath("catalog.json")),
        payload,
        MUTABLE_CACHE_CONTROL,
    )
    if catalog_spec.key in seen_keys:
        raise R2PublicationError(f"duplicate R2 object key: {catalog_spec.key}")
    return R2PublicationPlan(
        tuple(immutable), tuple(visuals), tuple(pointers), catalog_spec,
    )


def publish_r2_plan(
    plan: R2PublicationPlan,
    *,
    client: R2ObjectClient,
    bucket: str,
    progress: R2ProgressCallback | None = None,
) -> R2PublicationResult:
    """Publish all dependencies before committing mutable discovery objects."""
    emit = progress or (lambda _event: None)
    uploaded = 0
    reused = 0
    for phase, objects in (
        ("r2_uploading_immutable", plan.immutable),
        ("r2_uploading_visuals", plan.visuals),
        ("r2_uploading_pointers", plan.pointers),
        ("r2_committing_catalog", (plan.catalog,)),
    ):
        emit(R2ProgressEvent(phase, 0, len(objects), uploaded, reused))
        for completed, spec in enumerate(objects, start=1):
            try:
                was_uploaded = _publish_object(spec, client=client, bucket=bucket)
            except R2PublicationError:
                raise
            except Exception as error:
                raise R2PublicationError(f"R2 publication failed for {spec.key}") from error
            uploaded += int(was_uploaded)
            reused += int(not was_uploaded)
            emit(R2ProgressEvent(
                phase, completed, len(objects), uploaded, reused, spec.key,
            ))
    emit(R2ProgressEvent(
        "r2_completed", len(plan.objects), len(plan.objects),
        uploaded, reused, plan.catalog.key,
    ))
    return R2PublicationResult(uploaded, reused, plan.catalog.key)


def publish_r2_from_environment(
    source: R2PublicationSource,
    progress: R2ProgressCallback | None = None,
) -> R2PublicationResult:
    """Compose the default boto3 client only when publication was requested."""
    config = R2PublicationConfig.from_environment()
    plan = build_r2_publication_plan(source, progress=progress)
    client = _create_boto3_client(config)
    return publish_r2_plan(
        plan, client=client, bucket=config.bucket, progress=progress,
    )


def _validated_session_count(races: object) -> int:
    if not isinstance(races, list):
        return 0
    return sum(
        1
        for race in races
        if isinstance(race, dict) and isinstance(race.get("sessions"), list)
        for session in race["sessions"]
        if isinstance(session, dict) and session.get("validated") is True
    )


def _plan_public_race(
    source: R2PublicationSource,
    record: object,
    *,
    validate_delivery: DeliveryValidator,
) -> tuple[
    CatalogV2RaceRecord | None,
    tuple[R2ObjectSpec, ...],
    tuple[R2ObjectSpec, ...],
]:
    if not isinstance(record, dict) or not isinstance(record.get("sessions"), list):
        raise R2PublicationError("catalog race must contain a sessions array")
    race_id = record.get("race_id")
    if not isinstance(race_id, str):
        raise R2PublicationError("catalog race_id must be a string")
    try:
        validate_generation_id(race_id)
    except ValueError as error:
        raise R2PublicationError("catalog race_id must be a safe path component") from error

    sessions: list[CatalogV2SessionRecord] = []
    immutable: list[R2ObjectSpec] = []
    pointers: list[R2ObjectSpec] = []
    for value in record["sessions"]:
        if not isinstance(value, dict):
            raise R2PublicationError(f"catalog race {race_id} contains a malformed session")
        if value.get("validated") is not True:
            continue
        session, session_objects, pointer = _plan_public_session(
            source, race_id, value, validate_delivery=validate_delivery,
        )
        sessions.append(session)
        immutable.extend(session_objects)
        pointers.append(pointer)
    if not sessions:
        return None, (), ()

    visual = record.get("visual")
    latitude = visual.get("latitude") if isinstance(visual, dict) else None
    longitude = visual.get("longitude") if isinstance(visual, dict) else None
    preview = visual.get("circuitPreview") if isinstance(visual, dict) else None
    try:
        race = CatalogV2RaceRecord(
            race_id=race_id,
            round_number=cast(int, record.get("round_number")),
            event_name=cast(str, record.get("event_name")),
            sessions=tuple(sessions),
            country=record.get("country"),
            location=record.get("location"),
            event_date=record.get("event_date"),
            latitude=latitude,
            longitude=longitude,
            circuit_preview=preview,
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise R2PublicationError(f"catalog race {race_id} is malformed") from error
    return race, tuple(immutable), tuple(pointers)


def _plan_public_session(
    source: R2PublicationSource,
    race_id: str,
    value: Mapping[str, object],
    *,
    validate_delivery: DeliveryValidator,
) -> tuple[CatalogV2SessionRecord, tuple[R2ObjectSpec, ...], R2ObjectSpec]:
    try:
        session_code = value.get("session_code")
        session_name = value.get("session_name")
        generation_id = value.get("generation_id")
        delivery_version = value.get("delivery_version")
        outcome = value.get("outcome")
        browser_pointer = value.get("browser_pointer")
        session = CatalogV2SessionRecord(
            session_code=session_code,  # type: ignore[arg-type]
            session_name=session_name,  # type: ignore[arg-type]
            generation_id=generation_id,  # type: ignore[arg-type]
            delivery_version=delivery_version,  # type: ignore[arg-type]
            outcome=outcome,  # type: ignore[arg-type]
            validated=True,
            canonical_pointer=None,
            browser_pointer=browser_pointer,  # type: ignore[arg-type]
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise R2PublicationError(f"validated catalog session for {race_id} is malformed") from error
    assert session.delivery_version is not None
    assert session.generation_id is not None
    assert session.browser_pointer is not None

    expected_pointer = (
        f"browser/{race_id}/sessions/{session.session_code}/browser-current.json"
    )
    if session.browser_pointer != expected_pointer:
        raise R2PublicationError(
            f"validated catalog session for {race_id} has an unexpected browser pointer"
        )
    browser_parent = source.season_root / "browser" / race_id
    pointer_path = browser_session_pointer_path(browser_parent, session.session_code)
    try:
        pointer = read_session_browser_pointer(browser_parent, session.session_code)
        manifest_file = read_regular_file_no_follow(pointer.manifest_path, "browser manifest")
        manifest_sha256 = hashlib.sha256(manifest_file.data).hexdigest()
        manifest = json.loads(manifest_file.data)
    except Exception as error:
        raise R2PublicationError(
            f"validated browser session for {race_id}/{session.session_code} cannot be read"
        ) from error
    if (
        pointer.delivery_version != session.delivery_version
        or manifest_sha256 != pointer.manifest_sha256
        or not isinstance(manifest, dict)
        or manifest.get("formatVersion") != "browser-delivery-v2"
        or manifest.get("contractVersion") != "v2"
        or manifest.get("deliveryVersion") != session.delivery_version
        or manifest.get("sourceGenerationId") != session.generation_id
        or not _is_sha256(manifest.get("sourceManifestSha256"))
    ):
        raise R2PublicationError(
            f"validated browser session for {race_id}/{session.session_code} disagrees with its catalog"
        )
    try:
        validate_delivery(
            browser_parent,
            expected_generation_id=session.generation_id,
            expected_manifest_sha256=manifest["sourceManifestSha256"],
            schema_root=source.schema_root,
            pointer_path=pointer_path,
        )
    except Exception as error:
        raise R2PublicationError(
            f"browser delivery validation failed for {race_id}/{session.session_code}"
        ) from error

    generation_root = pointer.manifest_path.parent
    generation_relative = PurePosixPath(
        "browser", race_id, "generations", session.delivery_version,
    )
    objects = [
        R2ObjectSpec(
            key=_object_key(source, generation_relative / "manifest.json"),
            local_path=pointer.manifest_path,
            expected_sha256=manifest_sha256,
            cache_control=IMMUTABLE_CACHE_CONTROL,
            immutable=True,
        )
    ]
    chunks = manifest.get("chunks")
    if not isinstance(chunks, list):
        raise R2PublicationError("browser manifest chunks must be an array")
    track_assets = manifest.get("trackAssets")
    if not isinstance(track_assets, dict):
        raise R2PublicationError("browser manifest trackAssets reference is required")
    references = [manifest.get(field) for field in _REFERENCE_FIELDS]
    references.extend(chunks)
    for index, reference in enumerate(references):
        if reference is None:
            if index >= len(_REFERENCE_FIELDS):
                raise R2PublicationError("browser manifest chunk reference must be an object")
            continue
        if not isinstance(reference, dict):
            raise R2PublicationError("browser manifest artifact reference must be an object")
        relative = _safe_relative_path(reference.get("path"), "browser artifact path")
        digest = reference.get("sha256")
        if not _is_sha256(digest):
            raise R2PublicationError("browser manifest artifact SHA-256 must be lowercase hexadecimal")
        expected_digest = cast(str, digest)
        artifact_path = generation_root.joinpath(*relative.parts)
        artifact_payload = _read_guarded(artifact_path, f"R2 source {relative}")
        if hashlib.sha256(artifact_payload).hexdigest() != expected_digest:
            raise R2PublicationError(f"browser manifest artifact checksum disagrees: {relative}")
        objects.append(R2ObjectSpec(
            key=_object_key(source, generation_relative / relative),
            local_path=artifact_path,
            expected_sha256=expected_digest,
            cache_control=IMMUTABLE_CACHE_CONTROL,
            immutable=True,
        ))

    pointer_spec = _local_spec(
        source,
        PurePosixPath(expected_pointer),
        pointer_path,
        MUTABLE_CACHE_CONTROL,
    )
    return session, tuple(objects), pointer_spec


def _visual_spec(
    source: R2PublicationSource,
    race: CatalogV2RaceRecord,
) -> R2ObjectSpec | None:
    if race.circuit_preview is None:
        return None
    relative = _safe_relative_path(race.circuit_preview, "catalog circuit preview")
    return _local_spec(
        source,
        relative,
        source.season_root.joinpath(*relative.parts),
        VISUAL_CACHE_CONTROL,
    )


def _local_spec(
    source: R2PublicationSource,
    relative: PurePosixPath,
    path: Path,
    cache_control: str,
) -> R2ObjectSpec:
    guarded = _read_guarded(path, f"R2 source {relative}")
    return R2ObjectSpec(
        key=_object_key(source, relative),
        local_path=path,
        expected_sha256=hashlib.sha256(guarded).hexdigest(),
        cache_control=cache_control,
    )


def _payload_spec(key: str, payload: bytes, cache_control: str) -> R2ObjectSpec:
    return R2ObjectSpec(
        key=key,
        payload=payload,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        cache_control=cache_control,
    )


def _publish_object(
    spec: R2ObjectSpec,
    *,
    client: R2ObjectClient,
    bucket: str,
) -> bool:
    payload = spec.payload
    if payload is None:
        assert spec.local_path is not None
        payload = _read_guarded(spec.local_path, f"R2 source {spec.key}")
    digest = hashlib.sha256(payload).hexdigest()
    if digest != spec.expected_sha256:
        raise R2PublicationError(f"local object changed before R2 upload: {spec.key}")

    existing = _head_or_none(client, bucket, spec.key)
    if existing is not None and spec.immutable:
        _require_identical_immutable(existing, spec, payload)
        return False

    client.put_object(
        Bucket=bucket,
        Key=spec.key,
        Body=payload,
        ContentType="application/json",
        CacheControl=spec.cache_control,
        Metadata={"sha256": digest},
    )
    uploaded = _head_or_none(client, bucket, spec.key)
    if uploaded is None:
        raise R2PublicationError(f"R2 upload cannot be verified: {spec.key}")
    _require_uploaded_identity(uploaded, spec, len(payload))
    return True


def _head_or_none(
    client: R2ObjectClient,
    bucket: str,
    key: str,
) -> Mapping[str, object] | None:
    try:
        return client.head_object(Bucket=bucket, Key=key)
    except Exception as error:
        response = getattr(error, "response", None)
        details = response.get("Error") if isinstance(response, dict) else None
        code = details.get("Code") if isinstance(details, dict) else None
        if str(code) in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise


def _require_identical_immutable(
    head: Mapping[str, object],
    spec: R2ObjectSpec,
    payload: bytes,
) -> None:
    if (
        head.get("ContentLength") != len(payload)
        or head.get("ContentType") != "application/json"
        or head.get("CacheControl") != spec.cache_control
    ):
        raise R2PublicationError(f"immutable R2 object collision: {spec.key}")
    metadata = head.get("Metadata")
    remote_sha256 = metadata.get("sha256") if isinstance(metadata, Mapping) else None
    if remote_sha256 == spec.expected_sha256:
        return
    etag = head.get("ETag")
    remote_etag = etag.strip('"') if isinstance(etag, str) else ""
    local_md5 = hashlib.md5(payload, usedforsecurity=False).hexdigest()
    if "-" not in remote_etag and remote_etag == local_md5:
        return
    raise R2PublicationError(f"immutable R2 object collision: {spec.key}")


def _require_uploaded_identity(
    head: Mapping[str, object],
    spec: R2ObjectSpec,
    size: int,
) -> None:
    metadata = head.get("Metadata")
    remote_sha256 = metadata.get("sha256") if isinstance(metadata, Mapping) else None
    if (
        head.get("ContentLength") != size
        or head.get("ContentType") != "application/json"
        or head.get("CacheControl") != spec.cache_control
        or remote_sha256 != spec.expected_sha256
    ):
        raise R2PublicationError(f"R2 upload identity disagrees: {spec.key}")


def _read_json_object(path: Path, label: str) -> dict[str, object]:
    try:
        guarded = read_regular_file_no_follow(path, label)
        payload = json.loads(guarded.data)
        verify_regular_file_identity(path, guarded, label)
    except Exception as error:
        raise R2PublicationError(f"{label} cannot be read safely") from error
    if not isinstance(payload, dict):
        raise R2PublicationError(f"{label} must be a JSON object")
    return payload


def _read_guarded(path: Path, label: str) -> bytes:
    try:
        guarded = read_regular_file_no_follow(path, label)
        verify_regular_file_identity(path, guarded, label)
        return guarded.data
    except Exception as error:
        raise R2PublicationError(f"{label} cannot be read safely") from error


def _safe_relative_path(value: object, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise R2PublicationError(f"{label} must be a safe relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or "." in path.parts or ".." in path.parts:
        raise R2PublicationError(f"{label} must be a safe relative POSIX path")
    if any(not part or part in {".", ".."} for part in str(value).split("/")):
        raise R2PublicationError(f"{label} must be a safe relative POSIX path")
    return path


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _object_key(source: R2PublicationSource, relative: PurePosixPath) -> str:
    return (
        PurePosixPath(source.key_prefix) / str(source.year) / relative
    ).as_posix()


def _extend_unique(
    target: list[R2ObjectSpec],
    values: tuple[R2ObjectSpec, ...],
    seen: set[str],
) -> None:
    for value in values:
        if value.key in seen:
            raise R2PublicationError(f"duplicate R2 object key: {value.key}")
        seen.add(value.key)
        target.append(value)


def _create_boto3_client(config: R2PublicationConfig) -> R2ObjectClient:
    try:
        import boto3
    except ImportError as error:
        raise R2PublicationError("boto3 is required for --publish-r2") from error
    try:
        return boto3.client(
            service_name="s3",
            endpoint_url=config.endpoint_url,
            region_name="auto",
        )
    except Exception as error:
        raise R2PublicationError("R2 S3 client could not be configured") from error


__all__ = [
    "IMMUTABLE_CACHE_CONTROL",
    "MUTABLE_CACHE_CONTROL",
    "R2ObjectClient",
    "R2ObjectSpec",
    "R2PublicationConfig",
    "R2PublicationError",
    "R2PublicationPlan",
    "R2PublicationResult",
    "R2PublicationSource",
    "R2ProgressCallback",
    "R2ProgressEvent",
    "VISUAL_CACHE_CONTROL",
    "build_r2_publication_plan",
    "publish_r2_from_environment",
    "publish_r2_plan",
]
