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
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Protocol, cast
from uuid import uuid4

from f1_replay_pipeline.delivery.browser.browser_delivery_request import BrowserPublishRequest, BrowserPublishResult
from f1_replay_pipeline.delivery.browser.browser_delivery_publication import (
    BrowserDeliveryDurabilityUncertainError,
    BrowserDeliveryPublicationError,
    validate_browser_delivery_pointer,
)
from f1_replay_pipeline.domain.dataset_manifest import parse_current_pointer, parse_manifest
from f1_replay_pipeline.domain.generation_identity import (
    GenerationIdentityError,
    build_v2_generation_id,
    validate_generation_id,
)
from f1_replay_pipeline.domain.session_modes import normalize_session_identity
from f1_replay_pipeline.storage.generation_publication import (
    GenerationPublicationResult,
    PublicationCommittedError,
    PublicationDurabilityUncertainError,
    read_regular_file_no_follow,
    resolve_current_generation,
    verify_regular_file_identity,
)
from f1_replay_pipeline.app.catalog_v2_schema import (
    CATALOG_SCHEMA_VERSION,
    CatalogV2Payload,
    CatalogV2RaceRecord,
    CatalogV2SessionRecord,
    session_code_from_generation_id,
    validate_active_catalog,
)
from f1_replay_pipeline.app.catalog_visuals import create_circuit_preview, resolve_venue_coordinates
from f1_replay_pipeline.app.session_pointer_publication import (
    browser_session_pointer_path,
    canonical_session_pointer_path,
    promote_session_canonical_pointer,
    read_session_browser_pointer,
    read_session_canonical_pointer,
    write_session_browser_pointer,
    write_session_canonical_pointer,
)
from f1_replay_pipeline.app.orchestration import PipelineRequest, PipelineResult, RaceSelection

if TYPE_CHECKING:
    from f1_replay_pipeline.delivery.browser.browser_delivery_publication import BrowserValidationProgress
    from f1_replay_pipeline.delivery.browser.browser_delivery_reader import BrowserReadProgress


@dataclass(frozen=True)
class ScheduledRace:
    """One ordinary championship race selected from a season schedule."""

    round_number: int
    event_name: str
    completed: bool
    country: str | None = None
    location: str | None = None
    event_date: str | None = None

    def __post_init__(self) -> None:
        if type(self.round_number) is not int or self.round_number < 1:
            raise ValueError("round_number must be a positive integer")
        if not isinstance(self.event_name, str) or not self.event_name.strip():
            raise ValueError("event_name must be non-blank")
        for value, label in ((self.country, "country"), (self.location, "location"), (self.event_date, "event_date")):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{label} must be non-blank when provided")


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
        progress: Callable[[str | BrowserReadProgress | BrowserValidationProgress], None],
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
    session_code: str | None = None
    session_name: str | None = None
    event_name: str | None = None
    country: str | None = None
    location: str | None = None
    event_date: str | None = None


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
    return build_v2_generation_id(year, round_number, session)


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
    session_code = _session_code(request)
    session_name = _session_name(session_code)
    stage_total = 9 if _supports_granular_progress(browser_service) else 2
    last_stage = 0

    def result(outcome: str, generation_id: str | None = None, delivery_version: str | None = None, detail: str | None = None) -> BatchRaceResult:
        return BatchRaceResult(
            race_id, race.round_number, outcome, generation_id, delivery_version, detail,
            session_code, session_name, race.event_name, race.country, race.location, race.event_date,
        )

    def event(
        phase: str, stage_index: int | None = None, *, detail: str | None = None,
        outcome: str | None = None, phase_completed: int | None = None, phase_total: int | None = None,
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
        event("race_succeeded", stage_total, detail="scheduled race is not completed; skipped safely", outcome="skipped_unavailable")
        return result("skipped_unavailable", detail="scheduled race is not completed")
    canonical = request.canonical_root / race_id
    browser = request.browser_root / race_id

    # Resume is indexed by the requested session, never by the race alias.
    canonical_state = _session_canonical_state(canonical, session_code) if request.resume and not request.force else None
    if canonical_state is not None:
        if _session_outputs_valid(canonical, browser, session_code):
            delivery = read_session_browser_pointer(browser, session_code).delivery_version
            event("race_succeeded", stage_total, detail="validated existing session artifacts", outcome="skipped_valid")
            return result("skipped_valid", canonical_state.generation_path.name, delivery)
        try:
            promote_session_canonical_pointer(canonical, session_code)
            _write_session_canonical_reference(canonical, session_code, canonical_state.generation_path.name)
            delivery_version = _browser_delivery_version(browser, canonical_state.generation_path.name)
            browser_result = _publish_browser(
                browser_service, BrowserPublishRequest(canonical, browser, delivery_version, request.schema_root),
                event, detail="reusing validated canonical session generation",
            )
            _write_session_references(canonical, browser, session_code, canonical_state.generation_path.name, browser_result.delivery_version)
        except Exception as error:
            browser_outcome = _committed_browser_outcome(error)
            if browser_outcome is not None:
                outcome, delivery_version, detail = browser_outcome
                try:
                    _write_session_browser_reference(browser, session_code, delivery_version)
                except Exception:
                    pass
                event("race_succeeded", stage_total, detail=detail, outcome=outcome)
                return result(outcome, canonical_state.generation_path.name, delivery_version, detail)
            detail = _error_detail(error)
            event("race_failed", detail=detail, outcome="failed")
            return result("failed", canonical_state.generation_path.name, detail=detail)
        event("race_succeeded", stage_total, outcome="generated")
        return result("generated", canonical_state.generation_path.name, browser_result.delivery_version)

    generation_id = _generation_id(request, canonical, browser, race.round_number)
    try:
        event("canonical_generating", 1)
        canonical_result = pipeline_service(PipelineRequest(
            RaceSelection(request.year, round_number=race.round_number, session=request.session, backend=request.backend),
            canonical, generation_id=generation_id,
        ))
    except Exception as error:
        committed_publication = _find_committed_publication(error)
        canonical_state = _canonical_state(canonical) if committed_publication is not None else None
        if canonical_state is not None and canonical_state.generation_path.name == generation_id:
            canonical_warning = isinstance(committed_publication, PublicationDurabilityUncertainError)
            canonical_detail = "canonical committed with uncertain durability" if canonical_warning else "canonical committed durably"
            try:
                _write_session_canonical_reference(canonical, session_code, generation_id)
                browser_result = _publish_browser(
                    browser_service, BrowserPublishRequest(canonical, browser, generation_id, request.schema_root),
                    event, detail=canonical_detail,
                )
                _write_session_references(canonical, browser, session_code, generation_id, browser_result.delivery_version)
            except Exception as browser_error:
                browser_outcome = _committed_browser_outcome(browser_error)
                if browser_outcome is not None:
                    browser_outcome_name, delivery_version, browser_detail = browser_outcome
                    try:
                        _write_session_browser_reference(browser, session_code, delivery_version)
                    except Exception:
                        pass
                    outcome = "committed_with_durability_warning" if canonical_warning or browser_outcome_name == "committed_with_durability_warning" else "generated"
                    detail = f"{canonical_detail}; browser: {browser_detail}"
                    event("race_succeeded", stage_total, detail=detail, outcome=outcome)
                    return result(outcome, generation_id, delivery_version, detail)
                detail = _error_detail(browser_error)
                event("race_failed", detail=detail, outcome="failed")
                return result("failed", generation_id, detail=f"{canonical_detail}; browser failed: {detail}")
            outcome = "committed_with_durability_warning" if canonical_warning else "generated"
            event("race_succeeded", stage_total, detail=str(committed_publication), outcome=outcome)
            return result(outcome, generation_id, browser_result.delivery_version, str(committed_publication))
        detail = _error_detail(error)
        event("race_failed", detail=detail, outcome="failed")
        return result("failed", detail=detail)

    try:
        _write_session_canonical_reference(canonical, session_code, generation_id)
        browser_result = _publish_browser(
            browser_service, BrowserPublishRequest(canonical, browser, generation_id, request.schema_root), event,
        )
        _write_session_references(canonical, browser, session_code, canonical_result.generation_id, browser_result.delivery_version)
    except Exception as error:
        browser_outcome = _committed_browser_outcome(error)
        if browser_outcome is not None:
            outcome, delivery_version, detail = browser_outcome
            try:
                _write_session_browser_reference(browser, session_code, delivery_version)
            except Exception:
                pass
            event("race_succeeded", stage_total, detail=detail, outcome=outcome)
            return result(outcome, canonical_result.generation_id, delivery_version, detail)
        detail = _error_detail(error)
        event("race_failed", detail=detail, outcome="failed")
        return result("failed", canonical_result.generation_id, detail=detail)
    event("race_succeeded", stage_total, outcome="generated")
    return result("generated", canonical_result.generation_id, browser_result.delivery_version)


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
    committed = _find_committed_publication(error)
    return committed if isinstance(committed, PublicationDurabilityUncertainError) else None


def _find_committed_publication(
    error: BaseException,
) -> PublicationDurabilityUncertainError | PublicationCommittedError | None:
    """Find a canonical commit result even when orchestration wrapped its cause."""
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        if isinstance(current, (PublicationDurabilityUncertainError, PublicationCommittedError)):
            return current
        visited.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def _committed_browser_outcome(error: BaseException) -> tuple[str, str, str] | None:
    """Recover a committed browser result from service and publication wrappers."""
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        if isinstance(current, BrowserDeliveryPublicationError):
            result = getattr(current, "result", None)
            delivery_version = getattr(result, "delivery_version", None)
            if getattr(current, "committed", False) and isinstance(delivery_version, str):
                outcome = (
                    "committed_with_durability_warning"
                    if isinstance(current, BrowserDeliveryDurabilityUncertainError)
                    else "generated"
                )
                return outcome, delivery_version, _error_detail(error)
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


def _session_code(request: BatchRequest) -> str:
    identity = normalize_session_identity(request.session)
    return {
        "practice-1": "fp1",
        "practice-2": "fp2",
        "practice-3": "fp3",
        "qualifying": "q",
        "race": "r",
        "sprint": "s",
        "sprint-qualifying": "sq",
        "sprint-shootout": "ss",
        "testing": "testing",
    }[identity]


def _session_name(session_code: str) -> str:
    names = {
        "fp1": "Practice 1", "fp2": "Practice 2", "fp3": "Practice 3",
        "q": "Qualifying", "qualifying": "Qualifying", "s": "Sprint", "sprint": "Sprint",
        "ss": "Sprint Shootout",
        "sq": "Sprint Qualifying", "r": "Race",
        "race": "Race",
    }
    return names.get(session_code, session_code.upper())


def _session_canonical_state(canonical: Path, session_code: str) -> GenerationPublicationResult | None:
    try:
        return read_session_canonical_pointer(canonical, session_code)
    except Exception:
        return None


def _session_outputs_valid(canonical: Path, browser: Path, session_code: str) -> bool:
    canonical_state = _session_canonical_state(canonical, session_code)
    return canonical_state is not None and _browser_output_valid(
        canonical_state, browser, pointer_path=browser_session_pointer_path(browser, session_code),
    )


def _write_session_references(
    canonical: Path, browser: Path, session_code: str, generation_id: str, delivery_version: str | None,
) -> None:
    """Snapshot both race-level pointers after their publication commits."""
    _write_session_canonical_reference(canonical, session_code, generation_id)
    _write_session_browser_reference(browser, session_code, delivery_version)


def _write_session_canonical_reference(canonical: Path, session_code: str, generation_id: str) -> None:
    if not (canonical / "current.json").is_file():
        return
    canonical_state = _canonical_state(canonical)
    if canonical_state is None or canonical_state.generation_path.name != generation_id:
        return
    write_session_canonical_pointer(canonical, session_code, generation_id, canonical_state.manifest_sha256)


def _write_session_browser_reference(browser: Path, session_code: str, delivery_version: str | None) -> None:
    if delivery_version is None or not (browser / "browser-current.json").is_file():
        return
    browser_pointer = browser / "browser-current.json"
    guarded = read_regular_file_no_follow(browser_pointer, "browser current pointer")
    pointer = json.loads(guarded.data)
    version = validate_browser_delivery_pointer(pointer)
    if version != delivery_version:
        raise ValueError("published browser delivery version disagrees with result")
    manifest_path = browser / "generations" / version / "manifest.json"
    manifest_file = read_regular_file_no_follow(manifest_path, "browser manifest")
    manifest_sha256 = _sha256(manifest_file.data)
    verify_regular_file_identity(manifest_path, manifest_file, "browser manifest")
    write_session_browser_pointer(browser, session_code, delivery_version, manifest_sha256)


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


def _browser_output_valid(
    canonical: GenerationPublicationResult, browser: Path, *, pointer_path: Path | None = None,
) -> bool:
    """Validate browser artifacts from guarded bytes bound to canonical state."""
    try:
        _require_no_follow_directory(browser, "browser root")
        selected_pointer_path = pointer_path or (browser / "browser-current.json")
        pointer_file = read_regular_file_no_follow(selected_pointer_path, "browser current pointer")
        pointer = json.loads(pointer_file.data)
        version = validate_browser_delivery_pointer(pointer)
        manifest_path = _browser_file(browser, ("generations", version, "manifest.json"), "browser manifest")
        manifest_file = read_regular_file_no_follow(manifest_path, "browser manifest")
        if pointer.get("manifestSha256") != _sha256(manifest_file.data):
            return False
        manifest = json.loads(manifest_file.data)
        if (
            manifest.get("formatVersion") != "browser-delivery-v2"
            or manifest.get("contractVersion") != "v2"
            or manifest.get("deliveryVersion") != version
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
        verify_regular_file_identity(selected_pointer_path, pointer_file, "browser current pointer")
        return True
    except Exception:
        return False


def _shallow_browser_output_valid(
    canonical: GenerationPublicationResult, browser: Path, *, pointer_path: Path | None = None,
) -> bool:
    """Check pointer, manifest, safe references, and provenance without reading payloads."""
    try:
        _require_no_follow_directory(browser, "browser root")
        selected_pointer_path = pointer_path or (browser / "browser-current.json")
        pointer_file = read_regular_file_no_follow(selected_pointer_path, "browser current pointer")
        pointer = json.loads(pointer_file.data)
        version = validate_browser_delivery_pointer(pointer)
        manifest_path = _browser_file(browser, ("generations", version, "manifest.json"), "browser manifest")
        manifest_file = read_regular_file_no_follow(manifest_path, "browser manifest")
        if pointer.get("manifestSha256") != _sha256(manifest_file.data):
            return False
        manifest = json.loads(manifest_file.data)
        chunks = manifest.get("chunks")
        if (
            manifest.get("formatVersion") != "browser-delivery-v2"
            or manifest.get("contractVersion") != "v2"
            or manifest.get("deliveryVersion") != version
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
        verify_regular_file_identity(selected_pointer_path, pointer_file, "browser current pointer")
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


def _open_no_follow_directory(path: Path, label: str) -> int:
    """Open a directory and each ancestor without following symlinks."""
    absolute = Path(os.path.abspath(path))
    descriptor = os.open(absolute.anchor, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        for component in absolute.parts[1:]:
            child = os.open(
                component,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ValueError(f"{label} must be a directory")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _browser_delivery_version(browser: Path, generation_id: str) -> str:
    """Retain an occupied delivery and choose a deterministic browser-only successor."""
    try:
        _require_no_follow_directory(browser, "browser root")
        generations = browser / "generations"
        _require_no_follow_directory(generations, "browser generations")
    except FileNotFoundError:
        return generation_id
    except OSError:
        return generation_id
    descriptor = os.open(generations, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not _directory_entry_exists(descriptor, generation_id):
            return generation_id
        suffix = 1
        while True:
            candidate = _browser_successor_version(generation_id, suffix)
            if not _directory_entry_exists(descriptor, candidate):
                return candidate
            suffix += 1
    finally:
        os.close(descriptor)


def _directory_entry_exists(directory_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _browser_successor_version(generation_id: str, suffix: int) -> str:
    candidate = f"{generation_id}-browser-{suffix}"
    try:
        return validate_generation_id(candidate)
    except GenerationIdentityError:
        digest = hashlib.sha256(generation_id.encode("utf-8")).hexdigest()[:24]
        return f"browser-{digest}-{suffix}"


def _generation_id(request: BatchRequest, canonical: Path, browser: Path, round_number: int) -> str:
    """Choose a deterministic initial identity and a safe force-only successor."""
    base = deterministic_generation_id(request.year, round_number, request.session)
    if not request.force:
        return base
    version = 1
    while (canonical / "generations" / f"{base}-force-{version}").exists() or (browser / "generations" / f"{base}-force-{version}").exists():
        version += 1
    return validate_generation_id(f"{base}-force-{version}")


def publish_catalog(
    result: BatchResult,
    *,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Atomically publish a complete v2 catalog with independently merged sessions."""
    root = result.request.canonical_root.parent
    root.mkdir(parents=True, exist_ok=True)
    records = _retained_catalog_records(root, result.request)
    for race in result.races:
        code = race.session_code or _session_code(result.request)
        if race.session_code is None and race.generation_id is not None:
            try:
                code = session_code_from_generation_id(race.generation_id, race.race_id)
            except ValueError:
                code = _session_code(result.request)
        valid = race.outcome in {"generated", "skipped_valid", "committed_with_durability_warning"} and _session_outputs_valid(
            result.request.canonical_root / race.race_id, result.request.browser_root / race.race_id, code,
        )
        browser_pointer = f"browser/{race.race_id}/sessions/{code}/browser-current.json" if valid else None
        session = CatalogV2SessionRecord(
            code, race.session_name or _session_name(code), race.generation_id, race.delivery_version,
            race.outcome, valid, None, browser_pointer,
        )
        prior = records.get(race.race_id)
        prior_sessions = prior.get("sessions", []) if isinstance(prior, dict) else []
        if not isinstance(prior_sessions, list):
            prior_sessions = []
        sessions = {
            item["session_code"]: item
            for item in prior_sessions
            if isinstance(item, dict) and isinstance(item.get("session_code"), str)
        }
        if valid or code not in sessions:
            sessions[code] = session.to_dict()
        record: dict[str, object] = {
            "race_id": race.race_id,
            "round_number": race.round_number,
            "event_name": race.event_name or (prior.get("event_name", race.race_id) if prior else race.race_id),
            "sessions": [sessions[key] for key in sorted(sessions)],
        }
        for name, value in (("country", race.country), ("location", race.location), ("event_date", race.event_date)):
            if value is not None:
                record[name] = value
            elif prior and name in prior:
                record[name] = prior[name]
        record["visual"] = _publish_race_visual_metadata(
            root, result.request, race, valid, code, prior,
            country=cast(str | None, record.get("country")),
            location=cast(str | None, record.get("location")),
        )
        if record["visual"] is None:
            record.pop("visual")
        records[race.race_id] = record
    payload = CatalogV2Payload(
        result.request.year,
        tuple(_race_model(record) for record in records.values()),
    ).to_dict()
    # Validate the complete merged payload before it becomes the active
    # discovery boundary. Retained records must not smuggle historical or
    # malformed pointers into a schemaVersion 2 catalog.
    validate_active_catalog(payload)
    emit = progress or (lambda _phase: None)
    emit("catalog_publishing")
    path = root / "catalog.json"
    _atomic_write_json(path, payload)
    return path


def _publish_race_visual_metadata(
    root: Path,
    request: BatchRequest,
    race: BatchRaceResult,
    valid: bool,
    session_code: str,
    prior: dict[str, object] | None,
    *,
    country: str | None,
    location: str | None,
) -> dict[str, object] | None:
    """Resolve coordinates and best-effort publish a validated circuit preview."""
    coordinates = resolve_venue_coordinates(country, location)
    if coordinates is None:
        prior_visual = prior.get("visual") if prior is not None else None
        return prior_visual if isinstance(prior_visual, dict) else None

    visual: dict[str, object] = {
        "latitude": coordinates.latitude,
        "longitude": coordinates.longitude,
    }
    prior_visual = prior.get("visual") if prior is not None else None
    prior_preview = prior_visual.get("circuitPreview") if isinstance(prior_visual, dict) else None
    if isinstance(prior_preview, str):
        visual["circuitPreview"] = prior_preview
    if valid:
        preview = _read_circuit_preview_source(request.browser_root / race.race_id, session_code)
        if preview is not None:
            try:
                preview_payload = create_circuit_preview(preview)
                if preview_payload is not None:
                    preview_path = _circuit_preview_path(root, race.race_id)
                    _atomic_write_json(preview_path, preview_payload)
                    visual["circuitPreview"] = preview_path.relative_to(root).as_posix()
            except Exception:
                # Coordinates remain useful when the optional asset is bad or
                # cannot be durably staged; catalog publication must continue.
                pass
    return visual


def _read_circuit_preview_source(browser_root: Path, session_code: str) -> dict[str, object] | None:
    """Read track-assets only after rechecking its guarded browser reference."""
    try:
        browser_pointer = read_session_browser_pointer(browser_root, session_code)
        manifest_file = read_regular_file_no_follow(browser_pointer.manifest_path, "browser manifest")
        verify_regular_file_identity(browser_pointer.manifest_path, manifest_file, "browser manifest")
        manifest = json.loads(manifest_file.data)
        reference = manifest.get("trackAssets") if isinstance(manifest, dict) else None
        if not _browser_reference_valid(browser_pointer.manifest_path.parent, reference):
            return None
        if not isinstance(reference, dict):
            return None
        relative = _safe_relative_path(reference.get("path"))
        asset_path = _browser_file(browser_pointer.manifest_path.parent, relative.parts, "track assets")
        asset_file = read_regular_file_no_follow(asset_path, "track assets")
        if _sha256(asset_file.data) != reference.get("sha256"):
            return None
        verify_regular_file_identity(asset_path, asset_file, "track assets")
        asset = json.loads(asset_file.data)
        return asset if isinstance(asset, dict) else None
    except Exception:
        return None


def _circuit_preview_path(root: Path, race_id: str) -> Path:
    safe_race_id = _safe_component(race_id, "catalog race_id")
    _ensure_safe_child_directory(root, "visuals", "catalog visuals directory")
    _ensure_safe_child_directory(root / "visuals", safe_race_id, "catalog race visuals directory")
    return root / "visuals" / safe_race_id / "circuit-preview.json"


def _ensure_safe_child_directory(parent: Path, name: str, label: str) -> None:
    """Create one directory component without following a symlink."""
    _require_no_follow_directory(parent, f"{label} parent")
    descriptor = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        try:
            os.mkdir(name, mode=0o700, dir_fd=descriptor)
            os.fsync(descriptor)
        except FileExistsError:
            pass
        child = os.open(name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=descriptor)
        try:
            if not stat.S_ISDIR(os.fstat(child).st_mode):
                raise ValueError(f"{label} must be a directory")
        finally:
            os.close(child)
    finally:
        os.close(descriptor)


def verify_catalog(
    request: BatchRequest,
    *,
    progress: Callable[[str, str], None] | None = None,
) -> tuple[BatchRaceResult, ...]:
    """Deeply verify every catalog-referenced session without schedule access."""
    root = request.canonical_root.parent
    try:
        _require_no_follow_directory(root, "season catalog root")
        catalog_file = read_regular_file_no_follow(root / "catalog.json", "season catalog")
        catalog = json.loads(catalog_file.data)
        if catalog.get("schemaVersion") != CATALOG_SCHEMA_VERSION:
            raise ValueError("season catalog must use schemaVersion 2; migrate the v1 catalog first")
        parsed_catalog = validate_active_catalog(catalog)
        if parsed_catalog.year != request.year:
            raise ValueError("season catalog is malformed v2")
        if not isinstance(catalog.get("races"), list):
            raise ValueError("season catalog is malformed v2")
        records = tuple(sorted(catalog["races"], key=_catalog_record_sort_key))
        verify_regular_file_identity(root / "catalog.json", catalog_file, "season catalog")
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("season catalog cannot be verified") from error
    emit = progress or (lambda _race_id, _phase: None)
    results: list[BatchRaceResult] = []
    for record in records:
        race_id = record.get("race_id") if isinstance(record, dict) else None
        display_id = race_id if isinstance(race_id, str) else "invalid"
        sessions = record.get("sessions") if isinstance(record, dict) else None
        if not isinstance(sessions, list) or not sessions:
            raise ValueError(f"catalog race {display_id} has no sessions")
        for session in sorted(sessions, key=lambda value: str(value.get("session_code", "")) if isinstance(value, dict) else ""):
            code = session.get("session_code") if isinstance(session, dict) else None
            emit(display_id, "catalog_deep_verifying")
            try:
                if not isinstance(code, str) or not _retained_session_valid(record, session, request):
                    raise ValueError("catalog session reference failed shallow integrity checks")
                canonical = read_session_canonical_pointer(request.canonical_root / display_id, code)
                from f1_replay_pipeline.storage.canonical_generation_validation import validate_complete_canonical_generation
                validate_complete_canonical_generation(
                    canonical.generation_path,
                    expected_generation_id=canonical.generation_path.name,
                    expected_manifest_sha256=canonical.manifest_sha256,
                )
                from f1_replay_pipeline.delivery.browser.browser_delivery_publication import validate_complete_browser_delivery
                validate_complete_browser_delivery(
                    request.browser_root / display_id,
                    expected_generation_id=canonical.generation_path.name,
                    expected_manifest_sha256=canonical.manifest_sha256,
                    schema_root=request.schema_root,
                    pointer_path=browser_session_pointer_path(request.browser_root / display_id, code),
                )
            except Exception as error:
                results.append(BatchRaceResult(display_id, _record_round_number(record), "invalid", detail=_error_detail(error), session_code=code if isinstance(code, str) else None, session_name=session.get("session_name") if isinstance(session, dict) else None))
            else:
                results.append(BatchRaceResult(display_id, _record_round_number(record), "valid", canonical.generation_path.name, session.get("delivery_version") if isinstance(session, dict) else None, session_code=code, session_name=session.get("session_name") if isinstance(session, dict) else None))
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
        os.lstat(path)
    except FileNotFoundError:
        return {}
    _require_no_follow_directory(root, "season catalog root")
    catalog_file = read_regular_file_no_follow(path, "season catalog")
    catalog = json.loads(catalog_file.data)
    if (
        not isinstance(catalog, dict)
        or catalog.get("schemaVersion") != CATALOG_SCHEMA_VERSION
        or catalog.get("year") != request.year
        or not isinstance(catalog.get("races"), list)
    ):
        raise ValueError("existing season catalog is malformed or belongs to another season")
    records = {}
    for record in catalog["races"]:
        retained = _retained_record_with_valid_sessions(record, request)
        if retained is not None:
            records[retained["race_id"]] = retained
    verify_regular_file_identity(path, catalog_file, "season catalog")
    return records


def _retained_record_valid(record: object, request: BatchRequest) -> bool:
    return _retained_record_with_valid_sessions(record, request) is not None


def _retained_record_with_valid_sessions(record: object, request: BatchRequest) -> dict[str, object] | None:
    if not isinstance(record, dict) or not isinstance(record.get("sessions"), list):
        return None
    race_id = record.get("race_id")
    if (
        not isinstance(race_id, str)
        or type(record.get("round_number")) is not int
        or cast(int, record.get("round_number")) < 1
        or not isinstance(record.get("event_name"), str)
    ):
        return None
    try:
        _safe_component(race_id, "catalog race_id")
    except ValueError:
        return None
    sessions = [
        {**session, "canonical_pointer": None}
        for session in record["sessions"]
        if isinstance(session, dict) and _retained_session_valid(record, session, request)
    ]
    if not sessions:
        return None
    retained = {key: value for key, value in record.items() if key not in {"sessions"}}
    retained["sessions"] = sorted(sessions, key=lambda value: value["session_code"])
    return retained


def _retained_session_valid(record: object, session: object, request: BatchRequest) -> bool:
    if not isinstance(record, dict) or not isinstance(session, dict) or session.get("validated") is not True:
        return False
    race_id = record.get("race_id")
    code = session.get("session_code")
    generation_id = session.get("generation_id")
    if (
        not isinstance(race_id, str) or not isinstance(code, str)
        or not isinstance(generation_id, str)
        or not isinstance(session.get("session_name"), str)
        or not isinstance(session.get("outcome"), str)
    ):
        return False
    expected_canonical = f"canonical/{race_id}/sessions/{code}/current.json"
    expected_browser = f"browser/{race_id}/sessions/{code}/browser-current.json"
    if (
        session.get("canonical_pointer") not in (None, expected_canonical)
        or session.get("browser_pointer") != expected_browser
    ):
        return False
    try:
        if session_code_from_generation_id(generation_id, race_id) != code:
            return False
        canonical = read_session_canonical_pointer(request.canonical_root / race_id, code)
        if canonical.generation_path.name != generation_id:
            return False
        browser_pointer = read_session_browser_pointer(request.browser_root / race_id, code)
        if session.get("delivery_version") != browser_pointer.delivery_version:
            return False
        return _shallow_browser_output_valid(
            canonical, request.browser_root / race_id,
            pointer_path=browser_session_pointer_path(request.browser_root / race_id, code),
        )
    except Exception:
        return False


def _race_model(record: dict[str, object]) -> CatalogV2RaceRecord:
    raw_sessions = record.get("sessions", ())
    sessions = tuple(
        CatalogV2SessionRecord(**session)
        for session in raw_sessions
        if isinstance(session, dict)
    ) if isinstance(raw_sessions, list) else ()
    visual = record.get("visual")
    visual_values: dict[str, object] = {}
    if visual is not None:
        if (
            not isinstance(visual, dict)
            or set(visual) - {"latitude", "longitude", "circuitPreview"}
            or not {"latitude", "longitude"}.issubset(visual)
        ):
            raise ValueError("catalog race visual metadata is malformed")
        visual_values = visual
    return CatalogV2RaceRecord(
        cast(str, record["race_id"]), cast(int, record["round_number"]),
        cast(str, record["event_name"]), sessions,
        cast(str | None, record.get("country")), cast(str | None, record.get("location")),
        cast(str | None, record.get("event_date")),
        cast(float | None, visual_values.get("latitude")),
        cast(float | None, visual_values.get("longitude")),
        cast(str | None, visual_values.get("circuitPreview")),
    )


def _atomic_write_json(path: Path, value: object) -> None:
    """Replace the catalog only after its complete deterministic payload is written."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    root = path.parent
    directory_descriptor = _open_no_follow_directory(root, "season catalog root")
    temporary_name = f".catalog-{uuid4().hex}.tmp"
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_descriptor,
        )
        with os.fdopen(descriptor, "wb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        os.fsync(directory_descriptor)
    except Exception:
        try:
            os.unlink(temporary_name, dir_fd=directory_descriptor)
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(directory_descriptor)


__all__ = [
    "BatchProgressEvent", "BatchRaceResult", "BatchRequest", "BatchResult", "BrowserService",
    "PipelineService", "ScheduleProvider", "ScheduledRace", "deterministic_generation_id", "event_folder_id",
    "deterministic_race_id", "publish_catalog", "run_batch", "verify_catalog",
]
