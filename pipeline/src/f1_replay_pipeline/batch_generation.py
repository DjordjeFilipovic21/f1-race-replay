"""Pure, sequential season-generation orchestration.

This module deliberately knows nothing about FastF1.  Schedule and publication
work are injected so the batch state machine remains deterministic and offline
testable.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import stat
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Protocol, cast
from uuid import uuid4

from f1_replay_pipeline.browser_delivery_request import BrowserPublishRequest, BrowserPublishResult
from f1_replay_pipeline.dataset_manifest import parse_current_pointer, parse_manifest
from f1_replay_pipeline.generation_identity import GenerationIdentityError, validate_generation_id
from f1_replay_pipeline.generation_publication import (
    GenerationPublicationResult,
    PublicationDurabilityUncertainError,
    read_regular_file_no_follow,
    resolve_current_generation,
    verify_regular_file_identity,
)
from f1_replay_pipeline.orchestration import PipelineRequest, PipelineResult, RaceSelection

if TYPE_CHECKING:
    from f1_replay_pipeline.browser_delivery_publication import BrowserValidationProgress


@dataclass(frozen=True)
class ScheduledRace:
    """One ordinary championship race selected from a season schedule."""

    round_number: int
    event_name: str
    completed: bool

    def __post_init__(self) -> None:
        if type(self.round_number) is not int or self.round_number < 1:
            raise ValueError("round_number must be a positive integer")
        if not isinstance(self.event_name, str) or not self.event_name.strip():
            raise ValueError("event_name must be non-blank")


class ScheduleProvider(Protocol):
    def __call__(self, year: int, *, backend: str | None = None) -> Sequence[ScheduledRace]: ...


class PipelineService(Protocol):
    def __call__(self, request: PipelineRequest) -> PipelineResult: ...


class BrowserService(Protocol):
    def __call__(self, request: BrowserPublishRequest) -> BrowserPublishResult: ...


class GranularBrowserService(BrowserService, Protocol):
    """A browser publisher that reports actual delivery operation boundaries."""

    def publish_with_progress(
        self,
        request: BrowserPublishRequest,
        progress: Callable[[str | BrowserValidationProgress], None],
    ) -> BrowserPublishResult: ...


@dataclass(frozen=True)
class BatchProgressEvent:
    """An immutable rendering-independent report of batch activity."""

    year: int
    race_id: str | None
    race_index: int
    race_total: int
    phase: str
    detail: str | None = None
    outcome: str | None = None
    stage_index: int = 0
    stage_total: int = 0
    phase_completed: int | None = None
    phase_total: int | None = None


ProgressCallback = Callable[[BatchProgressEvent], None]


@dataclass(frozen=True)
class BatchRequest:
    year: int
    rounds: tuple[int, ...] | None
    all_rounds: bool
    session: str
    canonical_root: Path
    browser_root: Path
    schema_root: Path
    backend: str | None = None
    resume: bool = False
    force: bool = False
    continue_on_error: bool = False

    def __post_init__(self) -> None:
        if type(self.year) is not int or self.year < 1:
            raise ValueError("year must be a positive integer")
        if (self.rounds is None) != self.all_rounds:
            raise ValueError("select one or more rounds or all rounds")
        if self.rounds is not None and (not self.rounds or any(type(item) is not int or item < 1 for item in self.rounds)):
            raise ValueError("rounds must contain positive integers")
        if not self.session.strip():
            raise ValueError("session must be non-blank")
        if self.canonical_root.parent.absolute() != self.browser_root.parent.absolute():
            raise ValueError("canonical_root and browser_root must share a season parent")


@dataclass(frozen=True)
class BatchRaceResult:
    race_id: str
    round_number: int
    outcome: str
    generation_id: str | None = None
    delivery_version: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class BatchResult:
    request: BatchRequest
    races: tuple[BatchRaceResult, ...]

    @property
    def failed(self) -> bool:
        return any(race.outcome == "failed" for race in self.races)


def deterministic_race_id(year: int, round_number: int) -> str:
    return f"{year}-round-{round_number:02d}"


def deterministic_generation_id(year: int, round_number: int, session: str) -> str:
    return f"{deterministic_race_id(year, round_number)}-{session.casefold()}"


def event_folder_id(year: int, round_number: int, event_name: str) -> str:
    """Return a stable round identity with a readable schedule-derived event slug."""
    normalized = unicodedata.normalize("NFKD", event_name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.casefold()).strip("-")
    return f"{deterministic_race_id(year, round_number)}-{slug or 'event'}"


def run_batch(
    request: BatchRequest,
    *,
    schedule_provider: ScheduleProvider,
    pipeline_service: PipelineService,
    browser_service: BrowserService,
    progress: ProgressCallback | None = None,
) -> BatchResult:
    """Generate selected races sequentially; each race remains independently atomic."""
    emit = progress or (lambda _event: None)
    emit(BatchProgressEvent(request.year, None, 0, 0, "schedule_loading"))
    schedule = tuple(sorted(schedule_provider(request.year, backend=request.backend), key=lambda race: race.round_number))
    selected = _select_races(request, schedule)
    emit(BatchProgressEvent(request.year, None, 0, len(selected), "schedule_ready"))
    results: list[BatchRaceResult] = []
    for index, race in enumerate(selected, start=1):
        result = _run_race(request, race, index, len(selected), pipeline_service, browser_service, emit)
        results.append(result)
        if result.outcome == "failed" and not request.continue_on_error:
            break
    final = BatchResult(request, tuple(results))
    emit(BatchProgressEvent(request.year, None, len(results), len(selected), "catalog_revalidating_references"))
    publish_catalog(
        final,
        progress=lambda phase: emit(BatchProgressEvent(
            request.year, None, len(results), len(selected), phase,
        )),
    )
    emit(BatchProgressEvent(
        request.year, None, len(results), len(selected), "batch_completed",
        outcome="failed" if final.failed else "succeeded",
    ))
    return final


def _select_races(request: BatchRequest, schedule: tuple[ScheduledRace, ...]) -> tuple[ScheduledRace, ...]:
    if request.all_rounds:
        return schedule
    requested = set(request.rounds or ())
    selected = tuple(race for race in schedule if race.round_number in requested)
    available = {race.round_number for race in selected}
    missing = sorted(requested - available)
    if missing:
        rounds = ", ".join(str(round_number) for round_number in missing)
        raise ValueError(f"requested round(s) missing from the {request.year} schedule: {rounds}")
    return selected


def _run_race(request: BatchRequest, race: ScheduledRace, index: int, total: int, pipeline_service: PipelineService, browser_service: BrowserService, emit: ProgressCallback) -> BatchRaceResult:
    race_id = _race_folder_id(request, race)
    stage_total = 9 if _supports_granular_progress(browser_service) else 2
    last_stage = 0

    def event(
        phase: str,
        stage_index: int | None = None,
        *,
        detail: str | None = None,
        outcome: str | None = None,
        phase_completed: int | None = None,
        phase_total: int | None = None,
    ) -> None:
        nonlocal last_stage
        if stage_index is not None:
            last_stage = max(last_stage, stage_index)
        emit(BatchProgressEvent(
            request.year, race_id, index, total, phase, stage_index=last_stage,
            stage_total=stage_total, detail=detail, outcome=outcome,
            phase_completed=phase_completed, phase_total=phase_total,
        ))

    event("race_queued", detail=race.event_name)
    if not race.completed:
        event(
            "race_succeeded",
            stage_total,
            detail="scheduled race is not completed; skipped safely",
            outcome="skipped_unavailable",
        )
        return BatchRaceResult(race_id, race.round_number, "skipped_unavailable", detail="scheduled race is not completed")
    canonical = request.canonical_root / race_id
    browser = request.browser_root / race_id
    canonical_state = _canonical_state(canonical) if request.resume and not request.force else None
    if canonical_state is not None:
        if _browser_output_valid(canonical_state, browser):
            event("race_succeeded", stage_total, detail="validated existing artifacts", outcome="skipped_valid")
            return BatchRaceResult(
                race_id, race.round_number, "skipped_valid", canonical_state.generation_path.name,
            )
        try:
            browser_result = _publish_browser(
                browser_service,
                BrowserPublishRequest(canonical, browser, canonical_state.generation_path.name, request.schema_root),
                event,
                detail="reusing validated canonical generation",
            )
        except Exception as error:
            detail = _error_detail(error)
            event("race_failed", detail=detail, outcome="failed")
            return BatchRaceResult(
                race_id, race.round_number, "failed", canonical_state.generation_path.name, detail=detail,
            )
        event("race_succeeded", stage_total, outcome="generated")
        return BatchRaceResult(
            race_id, race.round_number, "generated", canonical_state.generation_path.name,
            browser_result.delivery_version,
        )
    generation_id = _generation_id(request, canonical, browser, race.round_number)
    try:
        event("canonical_generating", 1)
        canonical_result = pipeline_service(PipelineRequest(
            RaceSelection(request.year, round_number=race.round_number, session=request.session, backend=request.backend),
            canonical, generation_id=generation_id,
        ))
    except Exception as error:
        durability_warning = _find_durability_warning(error)
        canonical_state = _canonical_state(canonical) if durability_warning is not None else None
        if canonical_state is not None and canonical_state.generation_path.name == generation_id:
            try:
                browser_result = _publish_browser(
                    browser_service,
                    BrowserPublishRequest(canonical, browser, generation_id, request.schema_root),
                    event,
                    detail="canonical committed with uncertain durability",
                )
            except Exception as browser_error:
                detail = _error_detail(browser_error)
                event("race_failed", detail=detail, outcome="failed")
                return BatchRaceResult(
                    race_id, race.round_number, "failed", generation_id,
                    detail=f"canonical committed with uncertain durability; browser failed: {detail}",
                )
            event("race_succeeded", stage_total, detail=str(durability_warning), outcome="committed_with_durability_warning")
            return BatchRaceResult(
                race_id, race.round_number, "committed_with_durability_warning",
                generation_id, browser_result.delivery_version, str(durability_warning),
            )
        detail = _error_detail(error)
        event("race_failed", detail=detail, outcome="failed")
        return BatchRaceResult(race_id, race.round_number, "failed", detail=detail)
    try:
        browser_result = _publish_browser(
            browser_service,
            BrowserPublishRequest(canonical, browser, generation_id, request.schema_root),
            event,
        )
    except Exception as error:
        detail = _error_detail(error)
        event("race_failed", detail=detail, outcome="failed")
        return BatchRaceResult(
            race_id, race.round_number, "failed", canonical_result.generation_id, detail=detail,
        )
    event("race_succeeded", stage_total, outcome="generated")
    return BatchRaceResult(race_id, race.round_number, "generated", canonical_result.generation_id, browser_result.delivery_version)


def _race_folder_id(request: BatchRequest, race: ScheduledRace) -> str:
    """Prefer readable folders while retaining existing round-only artifacts."""
    legacy = deterministic_race_id(request.year, race.round_number)
    if (request.canonical_root / legacy).exists() or (request.browser_root / legacy).exists():
        return legacy
    return event_folder_id(request.year, race.round_number, race.event_name)


_BROWSER_STAGE_INDICES = {
    "canonical_snapshot_reading": 2,
    "track_assets_generating": 3,
    "browser_building": 4,
    "browser_payload_preparing": 5,
    "browser_contract_schema_loading": 6,
    "browser_schema_artifact_validating": 7,
    "browser_artifacts_staging": 8,
    "browser_pointer_committing_durability": 9,
    "browser_publishing": 2,
}


def _supports_granular_progress(browser_service: BrowserService) -> bool:
    return callable(getattr(browser_service, "publish_with_progress", None))


def _publish_browser(
    browser_service: BrowserService,
    request: BrowserPublishRequest,
    event: Callable[..., None],
    *,
    detail: str | None = None,
) -> BrowserPublishResult:
    """Use operation-level browser progress when the injected service supports it."""
    if not _supports_granular_progress(browser_service):
        event("browser_building", 2, detail=detail)
        return browser_service(request)

    def emit_browser_stage(update: object) -> None:
        phase = getattr(update, "phase", update)
        if not isinstance(phase, str):
            raise TypeError("browser progress phase must be a string")
        event(
            phase,
            _BROWSER_STAGE_INDICES[phase],
            detail=getattr(update, "detail", detail),
            phase_completed=getattr(update, "completed", None),
            phase_total=getattr(update, "total", None),
        )

    return cast(GranularBrowserService, browser_service).publish_with_progress(request, emit_browser_stage)


def _find_durability_warning(error: BaseException) -> PublicationDurabilityUncertainError | None:
    """Find a publication warning even when orchestration wrapped its cause."""
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        if isinstance(current, PublicationDurabilityUncertainError):
            return current
        visited.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def _error_detail(error: BaseException) -> str:
    """Expose an actionable exception chain without a noisy traceback."""
    messages: list[str] = []
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        message = str(current).strip()
        entry = f"{type(current).__name__}: {message}" if message else type(current).__name__
        if not messages or entry != messages[-1]:
            messages.append(entry)
        current = current.__cause__ or current.__context__
    return " <- ".join(messages)


def _outputs_valid(canonical: Path, browser: Path) -> bool:
    canonical_state = _canonical_state(canonical)
    return canonical_state is not None and _browser_output_valid(canonical_state, browser)


def _canonical_state(canonical: Path) -> GenerationPublicationResult | None:
    try:
        return resolve_current_generation(canonical)
    except Exception:
        return None


def _shallow_canonical_state(canonical: Path) -> GenerationPublicationResult | None:
    """Resolve only guarded canonical metadata; never open canonical tables."""
    try:
        _require_no_follow_directory(canonical, "canonical root")
        pointer_path = canonical / "current.json"
        pointer_file = read_regular_file_no_follow(pointer_path, "canonical current pointer")
        pointer = parse_current_pointer(pointer_file.data)
        generation_id = _safe_component(pointer.generation_id, "canonical generation_id")
        generations = canonical / "generations"
        _require_no_follow_directory(generations, "canonical generations")
        generation = generations / generation_id
        _require_no_follow_directory(generation, "canonical selected generation")
        manifest_path = generation / "manifest.json"
        manifest_file = read_regular_file_no_follow(manifest_path, "canonical manifest")
        if _sha256(manifest_file.data) != pointer.manifest_sha256:
            return None
        manifest = parse_manifest(manifest_file.data)
        if manifest.generation_id != generation_id:
            return None
        verify_regular_file_identity(manifest_path, manifest_file, "canonical manifest")
        verify_regular_file_identity(pointer_path, pointer_file, "canonical current pointer")
        return GenerationPublicationResult(generation, manifest_path, pointer_path, pointer.manifest_sha256)
    except Exception:
        return None


def _browser_output_valid(canonical: GenerationPublicationResult, browser: Path) -> bool:
    """Validate browser artifacts from guarded bytes bound to canonical state."""
    try:
        _require_no_follow_directory(browser, "browser root")
        pointer_path = browser / "browser-current.json"
        pointer_file = read_regular_file_no_follow(pointer_path, "browser current pointer")
        pointer = json.loads(pointer_file.data)
        version = _safe_component(pointer.get("deliveryVersion"), "deliveryVersion")
        if pointer.get("manifestPath") != f"generations/{version}/manifest.json":
            return False
        manifest_path = _browser_file(browser, ("generations", version, "manifest.json"), "browser manifest")
        manifest_file = read_regular_file_no_follow(manifest_path, "browser manifest")
        if pointer.get("manifestSha256") != _sha256(manifest_file.data):
            return False
        manifest = json.loads(manifest_file.data)
        if (
            manifest.get("deliveryVersion") != version
            or manifest.get("sourceGenerationId") != canonical.generation_path.name
            or manifest.get("sourceManifestSha256") != canonical.manifest_sha256
        ):
            return False
        chunks = manifest.get("chunks")
        if not isinstance(chunks, list):
            return False
        references = (manifest.get("trackAssets"), *chunks)
        if not all(_browser_reference_valid(manifest_path.parent, reference) for reference in references):
            return False
        verify_regular_file_identity(manifest_path, manifest_file, "browser manifest")
        verify_regular_file_identity(pointer_path, pointer_file, "browser current pointer")
        return True
    except Exception:
        return False


def _shallow_browser_output_valid(canonical: GenerationPublicationResult, browser: Path) -> bool:
    """Check pointer, manifest, safe references, and provenance without reading payloads."""
    try:
        _require_no_follow_directory(browser, "browser root")
        pointer_path = browser / "browser-current.json"
        pointer_file = read_regular_file_no_follow(pointer_path, "browser current pointer")
        pointer = json.loads(pointer_file.data)
        version = _safe_component(pointer.get("deliveryVersion"), "deliveryVersion")
        if pointer.get("manifestPath") != f"generations/{version}/manifest.json":
            return False
        manifest_path = _browser_file(browser, ("generations", version, "manifest.json"), "browser manifest")
        manifest_file = read_regular_file_no_follow(manifest_path, "browser manifest")
        if pointer.get("manifestSha256") != _sha256(manifest_file.data):
            return False
        manifest = json.loads(manifest_file.data)
        chunks = manifest.get("chunks")
        if (
            manifest.get("deliveryVersion") != version
            or manifest.get("sourceGenerationId") != canonical.generation_path.name
            or manifest.get("sourceManifestSha256") != canonical.manifest_sha256
            or not isinstance(chunks, list)
        ):
            return False
        for reference in (manifest.get("trackAssets"), *chunks):
            if not isinstance(reference, dict):
                return False
            _safe_relative_path(reference.get("path"))
            if not _is_sha256(reference.get("sha256")):
                return False
        verify_regular_file_identity(manifest_path, manifest_file, "browser manifest")
        verify_regular_file_identity(pointer_path, pointer_file, "browser current pointer")
        return True
    except Exception:
        return False


def _browser_reference_valid(generation: Path, reference: object) -> bool:
    if not isinstance(reference, dict):
        return False
    try:
        relative = _safe_relative_path(reference.get("path"))
        expected_sha256 = reference.get("sha256")
        if not _is_sha256(expected_sha256):
            return False
        artifact = _browser_file(generation, relative.parts, f"browser artifact {relative.as_posix()}")
        guarded = read_regular_file_no_follow(artifact, f"browser artifact {relative.as_posix()}")
        if _sha256(guarded.data) != expected_sha256:
            return False
        verify_regular_file_identity(artifact, guarded, f"browser artifact {relative.as_posix()}")
        return True
    except Exception:
        return False


def _browser_file(root: Path, components: tuple[str, ...], label: str) -> Path:
    if not components:
        raise ValueError(f"{label} requires a file component")
    path = root
    _require_no_follow_directory(path, f"{label} parent")
    for component in components[:-1]:
        path /= _safe_component(component, label)
        _require_no_follow_directory(path, f"{label} parent")
    return path / _safe_component(components[-1], label)


def _safe_component(value: object, label: str) -> str:
    try:
        component = validate_generation_id(value)
    except GenerationIdentityError as error:
        raise ValueError(f"{label} must be a safe path component") from error
    if component in {".", ".."}:
        raise ValueError(f"{label} must be a safe path component")
    return component


def _safe_relative_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("browser artifact path must be a safe relative POSIX path")
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in {".", ".."} for part in relative.parts):
        raise ValueError("browser artifact path escapes its delivery")
    for part in relative.parts:
        _safe_component(part, "browser artifact path")
    return relative


def _require_no_follow_directory(path: Path, label: str) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ValueError(f"{label} must be a directory")
    finally:
        os.close(descriptor)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _generation_id(request: BatchRequest, canonical: Path, browser: Path, round_number: int) -> str:
    """Choose a deterministic initial identity and a safe force-only successor."""
    base = deterministic_generation_id(request.year, round_number, request.session)
    if not request.force:
        return base
    version = 1
    while (canonical / "generations" / f"{base}-force-{version}").exists() or (browser / "generations" / f"{base}-force-{version}").exists():
        version += 1
    return f"{base}-force-{version}"


def publish_catalog(
    result: BatchResult,
    *,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Atomically publish deeply checked current races and shallowly retained references."""
    root = result.request.canonical_root.parent
    root.mkdir(parents=True, exist_ok=True)
    records = _retained_catalog_records(root, result.request)
    for race in result.races:
        valid = race.outcome in {"generated", "skipped_valid", "committed_with_durability_warning"} and _outputs_valid(
            result.request.canonical_root / race.race_id, result.request.browser_root / race.race_id,
        )
        record = {**asdict(race), "validated": valid}
        if valid:
            record["canonical"] = f"canonical/{race.race_id}/current.json"
            record["browser"] = f"browser/{race.race_id}/browser-current.json"
        records[race.race_id] = record
    payload = {
        "year": result.request.year,
        "atomicAcrossRaces": False,
        "races": [records[race_id] for race_id in sorted(records)],
    }
    emit = progress or (lambda _phase: None)
    emit("catalog_publishing")
    path = root / "catalog.json"
    _atomic_write_json(path, payload)
    return path


def verify_catalog(
    request: BatchRequest,
    *,
    progress: Callable[[str, str], None] | None = None,
) -> tuple[BatchRaceResult, ...]:
    """Deeply verify every catalog-referenced race without schedule or network access."""
    root = request.canonical_root.parent
    try:
        _require_no_follow_directory(root, "season catalog root")
        catalog_file = read_regular_file_no_follow(root / "catalog.json", "season catalog")
        catalog = json.loads(catalog_file.data)
        if catalog.get("year") != request.year or not isinstance(catalog.get("races"), list):
            raise ValueError("season catalog is malformed")
        records = tuple(sorted(catalog["races"], key=_catalog_record_sort_key))
        verify_regular_file_identity(root / "catalog.json", catalog_file, "season catalog")
    except Exception as error:
        raise ValueError("season catalog cannot be verified") from error
    emit = progress or (lambda _race_id, _phase: None)
    results: list[BatchRaceResult] = []
    for record in records:
        race_id = record.get("race_id") if isinstance(record, dict) else None
        display_id = race_id if isinstance(race_id, str) else "invalid"
        emit(display_id, "catalog_deep_verifying")
        try:
            if not _retained_record_valid(record, request):
                raise ValueError("catalog reference failed shallow integrity checks")
            canonical = _canonical_state(request.canonical_root / display_id)
            if canonical is None:
                raise ValueError("canonical generation failed deep validation")
            from f1_replay_pipeline.browser_delivery_publication import validate_complete_browser_delivery

            validate_complete_browser_delivery(
                request.browser_root / display_id,
                expected_generation_id=canonical.generation_path.name,
                expected_manifest_sha256=canonical.manifest_sha256,
                schema_root=request.schema_root,
            )
        except Exception as error:
            results.append(BatchRaceResult(display_id, _record_round_number(record), "invalid", detail=_error_detail(error)))
        else:
            results.append(BatchRaceResult(display_id, _record_round_number(record), "valid", canonical.generation_path.name))
    return tuple(results)


def _record_round_number(record: object) -> int:
    value = record.get("round_number") if isinstance(record, dict) else None
    return value if type(value) is int and value > 0 else 0


def _catalog_record_sort_key(record: object) -> str:
    return str(record.get("race_id", "")) if isinstance(record, dict) else ""


def _retained_catalog_records(root: Path, request: BatchRequest) -> dict[str, dict[str, object]]:
    """Keep prior records whose guarded pointers and manifests still agree.

    This intentionally does not read or hash canonical tables or browser chunks.
    """
    path = root / "catalog.json"
    try:
        _require_no_follow_directory(root, "season catalog root")
        catalog_file = read_regular_file_no_follow(path, "season catalog")
        catalog = json.loads(catalog_file.data)
        if catalog.get("year") != request.year or not isinstance(catalog.get("races"), list):
            return {}
        records = {
            record["race_id"]: record
            for record in catalog["races"]
            if _retained_record_valid(record, request)
        }
        verify_regular_file_identity(path, catalog_file, "season catalog")
        return records
    except Exception:
        return {}


def _retained_record_valid(record: object, request: BatchRequest) -> bool:
    if not isinstance(record, dict) or record.get("validated") is not True:
        return False
    race_id = record.get("race_id")
    if not isinstance(race_id, str):
        return False
    try:
        _safe_component(race_id, "catalog race_id")
    except ValueError:
        return False
    return (
        record.get("canonical") == f"canonical/{race_id}/current.json"
        and record.get("browser") == f"browser/{race_id}/browser-current.json"
        and (canonical := _shallow_canonical_state(request.canonical_root / race_id)) is not None
        and _shallow_browser_output_valid(canonical, request.browser_root / race_id)
    )


def _atomic_write_json(path: Path, value: object) -> None:
    """Replace the catalog only after its complete deterministic payload is written."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    root = path.parent
    _require_no_follow_directory(root, "season catalog root")
    temporary = root / f".catalog-{uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


__all__ = [
    "BatchProgressEvent", "BatchRaceResult", "BatchRequest", "BatchResult", "BrowserService",
    "PipelineService", "ScheduleProvider", "ScheduledRace", "deterministic_generation_id", "event_folder_id",
    "deterministic_race_id", "publish_catalog", "run_batch", "verify_catalog",
]
