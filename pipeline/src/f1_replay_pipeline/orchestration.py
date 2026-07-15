"""Import-safe contracts for canonical pipeline orchestration.

Selection validation is deliberately complete before a resolver, publisher, or
filesystem boundary is reachable.  Concrete FastF1 and Parquet integration is
supplied by callers through the protocols below.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, TypeAlias, runtime_checkable

from f1_replay_pipeline.car_telemetry_adapter import adapt_car_telemetry
from f1_replay_pipeline.generation_identity import validate_generation_id
from f1_replay_pipeline.laps_stints_adapter import adapt_laps, adapt_stints
from f1_replay_pipeline.messages_results_adapter import adapt_race_control_messages, adapt_results
from f1_replay_pipeline.parquet_io import validate_canonical_frames
from f1_replay_pipeline.position_telemetry_adapter import adapt_position_telemetry
from f1_replay_pipeline.session_metadata_adapter import adapt_drivers, adapt_session_metadata
from f1_replay_pipeline.weather_status_adapter import adapt_track_status_intervals, adapt_weather


class SelectionError(ValueError):
    """Raised when a race or testing selector is incomplete or ambiguous."""


class PipelineRequestError(ValueError):
    """Raised when an orchestration request cannot safely be executed."""


class SessionResolutionError(RuntimeError):
    """Raised when a validated selection cannot be resolved to a session."""


class PublicationError(RuntimeError):
    """Raised when an injected publisher cannot publish a completed pipeline."""


class NormalizationError(RuntimeError):
    """Raised when one named canonical normalization stage fails."""


class PipelineValidationError(RuntimeError):
    """Raised when the assembled canonical generation fails validation."""


@dataclass(frozen=True)
class RaceSelection:
    """An exact race-event selector; testing fields are rejected explicitly."""

    year: int
    session: str
    round_number: int | None = None
    event_name: str | None = None
    backend: str | None = None
    test_number: int | None = None
    test_session_number: int | None = None

    def __post_init__(self) -> None:
        _validate_year(self.year)
        _validate_nonblank_string(self.session, "session")
        object.__setattr__(self, "backend", _normalize_backend(self.backend, _RACE_BACKENDS))
        if self.test_number is not None or self.test_session_number is not None:
            raise SelectionError("race selection cannot include testing fields")
        _validate_race_event_selector(self.round_number, self.event_name)


@dataclass(frozen=True)
class TestingSelection:
    """An explicit testing-session selector; race fields are rejected explicitly."""

    year: int
    test_number: int
    session_number: int
    backend: str | None = None
    round_number: int | None = None
    event_name: str | None = None
    session: str | None = None

    def __post_init__(self) -> None:
        _validate_year(self.year)
        _validate_positive_integer(self.test_number, "test_number")
        _validate_positive_integer(self.session_number, "session_number")
        object.__setattr__(self, "backend", _normalize_backend(self.backend, _TESTING_BACKENDS))
        if any(value is not None for value in (self.round_number, self.event_name, self.session)):
            raise SelectionError("testing selection cannot include race selector fields")


SessionSelection: TypeAlias = RaceSelection | TestingSelection


@runtime_checkable
class SessionResolver(Protocol):
    """Resolve one already-validated selection without exposing FastF1 here."""

    def __call__(self, selection: SessionSelection) -> object: ...


@runtime_checkable
class GenerationIdGenerator(Protocol):
    """Produce a deterministic safe generation identifier when injected."""

    def __call__(self) -> str: ...


@runtime_checkable
class Publisher(Protocol):
    """Publish canonical frames to the request's explicit output directory."""

    def __call__(
        self,
        *,
        frames: Mapping[str, object],
        target_parent: Path,
        generation_id: str,
    ) -> object: ...


@dataclass(frozen=True)
class PipelineRequest:
    """Immutable execution input with an explicit output and identity policy."""

    selection: SessionSelection
    output_directory: Path
    generation_id: str | None = None
    generation_id_generator: GenerationIdGenerator | None = field(
        default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        _validate_selection(self.selection)
        if not isinstance(self.output_directory, Path):
            raise PipelineRequestError("output_directory must be a pathlib.Path")
        if (self.generation_id is None) == (self.generation_id_generator is None):
            raise PipelineRequestError(
                "provide exactly one of generation_id or generation_id_generator"
            )
        if self.generation_id is not None:
            _validate_generation_id(self.generation_id)
        if self.generation_id_generator is not None and not callable(self.generation_id_generator):
            raise PipelineRequestError("generation_id_generator must be callable")


@dataclass(frozen=True)
class PipelineResult:
    """Immutable outcome metadata returned after an injected publisher succeeds."""

    request: PipelineRequest
    generation_id: str
    publication: object


def resolve_generation_id(request: PipelineRequest) -> str:
    """Return the caller identity or validate the value from its injected generator."""
    _validate_request(request)
    supplied_generation_id = request.generation_id
    if supplied_generation_id is not None:
        return supplied_generation_id
    assert request.generation_id_generator is not None
    try:
        return _validate_generation_id(request.generation_id_generator())
    except Exception as error:
        raise PipelineRequestError("generation_id_generator returned an invalid generation ID") from error


def resolve_session(request: PipelineRequest, resolver: SessionResolver) -> object:
    """Resolve a validated request while preserving resolver failures as causes."""
    _validate_request(request)
    if not callable(resolver):
        raise PipelineRequestError("resolver must be callable")
    try:
        return resolver(request.selection)
    except SessionResolutionError:
        raise
    except Exception as error:
        raise SessionResolutionError(
            f"could not resolve {_selection_context(request.selection)}; "
            "verify the event schedule, backend, and cache"
        ) from error


def publish_frames(
    request: PipelineRequest, frames: Mapping[str, object], publisher: Publisher,
) -> PipelineResult:
    """Validate all frames before delegating their publication to the publisher."""
    _validate_request(request)
    if not isinstance(frames, Mapping):
        raise PipelineRequestError("frames must be a mapping")
    if not callable(publisher):
        raise PipelineRequestError("publisher must be callable")
    try:
        validate_canonical_frames(frames)
    except Exception as error:
        raise PipelineValidationError(
            f"canonical validation failed for {_selection_context(request.selection)}"
        ) from error
    generation_id = resolve_generation_id(request)
    try:
        publication = publisher(
            frames=frames,
            target_parent=request.output_directory,
            generation_id=generation_id,
        )
    except Exception as error:
        raise PublicationError(
            f"publication failed for {_selection_context(request.selection)} "
            f"to output_directory={request.output_directory!s}"
        ) from error
    return PipelineResult(request=request, generation_id=generation_id, publication=publication)


def run_pipeline(
    request: PipelineRequest,
    resolver: SessionResolver,
    publisher: Publisher,
) -> PipelineResult:
    """Resolve, normalize, validate, then publish one complete canonical generation.

    The resolver owns the single FastF1 loading boundary.  Every later adapter
    receives that same loaded session and the same immutable source-driver map.
    """
    session = resolve_session(request, resolver)
    frames = normalize_session(session, request.selection)
    return publish_frames(request, frames, publisher)


def normalize_session(session: object, selection: SessionSelection) -> Mapping[str, object]:
    """Produce exactly the canonical frames, preserving native telemetry streams."""
    metadata = _run_normalization_stage("session_metadata", selection, adapt_session_metadata, session)
    session_id = _metadata_session_id(metadata, selection)
    drivers = _run_normalization_stage("drivers", selection, adapt_drivers, session, session_id)
    driver_ids = _source_driver_ids(drivers, selection)
    car_telemetry = _run_normalization_stage(
        "car_telemetry", selection, adapt_car_telemetry, session, session_id, driver_ids
    )
    position_telemetry = _run_normalization_stage(
        "position_telemetry", selection, adapt_position_telemetry, session, session_id, driver_ids
    )
    laps = _run_normalization_stage("laps", selection, adapt_laps, session, session_id, driver_ids)
    stints = _run_normalization_stage("stints", selection, adapt_stints, session, session_id, driver_ids, laps)
    weather = _run_normalization_stage("weather", selection, adapt_weather, session, session_id)
    track_status_intervals = _run_normalization_stage(
        "track_status_intervals", selection, adapt_track_status_intervals, session, session_id
    )
    race_control_messages = _run_normalization_stage(
        "race_control_messages", selection, adapt_race_control_messages, session, driver_ids, session_id
    )
    results = _run_normalization_stage("results", selection, adapt_results, session, driver_ids, session_id)
    frames = {
        "session_metadata": metadata,
        "drivers": drivers,
        "car_telemetry": car_telemetry,
        "position_telemetry": position_telemetry,
        "laps": laps,
        "stints": stints,
        "weather": weather,
        "track_status_intervals": track_status_intervals,
        "race_control_messages": race_control_messages,
        "results": results,
    }
    return MappingProxyType(frames)


def _source_driver_ids(drivers: object, selection: SessionSelection) -> Mapping[str, str]:
    try:
        select = getattr(drivers, "select")
        selected = select("source_driver_key", "driver_id")
        records = selected.to_dicts()
        mapping = {record["source_driver_key"]: record["driver_id"] for record in records}
        if len(mapping) != len(records) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in mapping.items()
        ):
            raise ValueError("drivers must contain a one-to-one string source-driver mapping")
        return MappingProxyType(mapping)
    except Exception as error:
        raise NormalizationError(
            f"normalization failed during drivers mapping for {_selection_context(selection)}"
        ) from error


def _metadata_session_id(metadata: object, selection: SessionSelection) -> str:
    try:
        session_id = getattr(metadata, "item")(0, "session_id")
    except Exception as error:
        raise NormalizationError(
            f"normalization failed during session_metadata identity for {_selection_context(selection)}"
        ) from error
    if not isinstance(session_id, str) or not session_id.strip():
        raise NormalizationError(
            f"normalization failed during session_metadata identity for {_selection_context(selection)}"
        )
    return session_id


def _run_normalization_stage(
    stage: str, selection: SessionSelection, adapter: object, *args: object,
) -> object:
    try:
        return adapter(*args)  # type: ignore[operator]
    except Exception as error:
        raise NormalizationError(
            f"normalization failed during {stage} for {_selection_context(selection)}"
        ) from error


def _validate_request(request: PipelineRequest) -> None:
    if not isinstance(request, PipelineRequest):
        raise PipelineRequestError("request must be a PipelineRequest")
    _validate_selection(request.selection)


def _validate_selection(selection: SessionSelection) -> None:
    if isinstance(selection, RaceSelection):
        selection.__post_init__()
    elif isinstance(selection, TestingSelection):
        selection.__post_init__()
    else:
        raise PipelineRequestError("selection must be a RaceSelection or TestingSelection")


def _validate_race_event_selector(round_number: int | None, event_name: str | None) -> None:
    if (round_number is None) == (event_name is None):
        raise SelectionError("race selection requires exactly one of round_number or event_name")
    if round_number is not None:
        _validate_positive_integer(round_number, "round_number")
    if event_name is not None:
        _validate_nonblank_string(event_name, "event_name")


def _validate_year(year: int) -> None:
    _validate_positive_integer(year, "year")


def _validate_positive_integer(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SelectionError(f"{name} must be a positive integer")


def _validate_nonblank_string(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise SelectionError(f"{name} must be a non-empty string")


_RACE_BACKENDS = frozenset({"fastf1", "f1timing", "ergast"})
_TESTING_BACKENDS = frozenset({"fastf1", "f1timing"})


def _normalize_backend(backend: str | None, supported_backends: frozenset[str]) -> str | None:
    if backend is None:
        return None
    _validate_nonblank_string(backend, "backend")
    normalized = backend.strip().casefold()
    if normalized not in supported_backends:
        choices = ", ".join(sorted(supported_backends))
        raise SelectionError(f"backend must be one of: {choices}")
    return normalized


def _validate_generation_id(generation_id: str) -> str:
    try:
        return validate_generation_id(generation_id)
    except ValueError as error:
        raise PipelineRequestError(str(error)) from error


def _selection_context(selection: SessionSelection) -> str:
    if isinstance(selection, RaceSelection):
        event = f"round={selection.round_number}" if selection.round_number else f"event={selection.event_name!r}"
        return f"race year={selection.year} {event} session={selection.session!r} backend={selection.backend!r}"
    return (
        f"testing year={selection.year} test_number={selection.test_number} "
        f"session_number={selection.session_number} backend={selection.backend!r}"
    )


__all__ = [
    "GenerationIdGenerator", "PipelineRequest", "PipelineRequestError", "PipelineResult",
    "NormalizationError", "PipelineValidationError", "PublicationError", "Publisher", "RaceSelection",
    "SelectionError", "SessionResolver", "SessionResolutionError", "SessionSelection", "TestingSelection",
    "normalize_session", "publish_frames", "resolve_generation_id", "resolve_session", "run_pipeline",
]
