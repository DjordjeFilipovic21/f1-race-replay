from __future__ import annotations

from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import f1_replay_pipeline.orchestration as orchestration
from f1_replay_pipeline.orchestration import (
    NormalizationError, PipelineRequest, PipelineRequestError, PipelineResult,
    PipelineValidationError, PublicationError, RaceSelection, SelectionError,
    SessionResolutionError, TestingSelection as PipelineTestingSelection, resolve_generation_id,
    resolve_session, run_pipeline,
)
from fake_fastf1_session import build_complete_session


def test_race_selection_is_immutable_and_accepts_a_positive_round() -> None:
    selection = RaceSelection(year=2026, round_number=1, session="R")

    assert selection.round_number == 1
    with pytest.raises(FrozenInstanceError):
        selection.round_number = 2  # type: ignore[misc]


@pytest.mark.parametrize("round_number", [0, -1, True])
def test_race_selection_rejects_non_positive_or_boolean_rounds(round_number: int) -> None:
    with pytest.raises(SelectionError, match="round_number"):
        RaceSelection(year=2026, round_number=round_number, session="Race")


def test_race_selection_rejects_mixed_testing_fields_before_resolution() -> None:
    with pytest.raises(SelectionError, match="testing fields"):
        RaceSelection(year=2026, event_name="British Grand Prix", session="R", test_number=1)


def test_testing_selection_rejects_a_race_event_selector() -> None:
    with pytest.raises(SelectionError, match="race selector"):
        PipelineTestingSelection(year=2026, test_number=1, session_number=1, round_number=1)


def test_request_requires_explicit_output_and_one_generation_identity() -> None:
    selection = RaceSelection(year=2026, round_number=1, session="R")

    with pytest.raises(PipelineRequestError, match="exactly one"):
        PipelineRequest(selection=selection, output_directory=Path("artifacts"))


def test_resolver_failure_preserves_its_original_cause() -> None:
    request = PipelineRequest(
        selection=PipelineTestingSelection(year=2026, test_number=1, session_number=1),
        output_directory=Path("artifacts"),
        generation_id_generator=lambda: "deterministic",
    )

    def failing_resolver(selection: object) -> object:
        raise LookupError("missing test")

    with pytest.raises(SessionResolutionError) as raised:
        resolve_session(request, failing_resolver)

    assert isinstance(raised.value.__cause__, LookupError)
    assert resolve_generation_id(request) == "deterministic"


def test_existing_session_resolution_error_is_not_rewrapped() -> None:
    request = PipelineRequest(
        selection=RaceSelection(year=2026, round_number=1, session="R"),
        output_directory=Path("artifacts"),
        generation_id_generator=lambda: "deterministic",
    )
    resolution_error = SessionResolutionError("no exact event found")

    with pytest.raises(SessionResolutionError) as raised:
        resolve_session(request, lambda selection: (_ for _ in ()).throw(resolution_error))

    assert raised.value is resolution_error


@pytest.mark.parametrize("backend", ["FastF1", " f1TIMING ", "ERGAST"])
def test_race_selection_normalizes_supported_backends(backend: str) -> None:
    selection = RaceSelection(year=2026, round_number=1, session="R", backend=backend)

    assert selection.backend == backend.strip().casefold()


@pytest.mark.parametrize("backend", ["FastF1", " f1TIMING "])
def test_testing_selection_normalizes_supported_backends(backend: str) -> None:
    selection = PipelineTestingSelection(year=2026, test_number=1, session_number=1, backend=backend)

    assert selection.backend == backend.strip().casefold()


@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        (lambda: RaceSelection(year=2026, round_number=1, session="R", backend="unknown"), "backend"),
        (lambda: PipelineTestingSelection(year=2026, test_number=1, session_number=1, backend="ergast"), "backend"),
    ],
)
def test_selection_rejects_unsupported_backends(selection: object, expected: str) -> None:
    with pytest.raises(SelectionError, match=expected):
        selection()  # type: ignore[operator]


def _request(*, generation_id: str | None = "generation-001") -> PipelineRequest:
    return PipelineRequest(
        selection=RaceSelection(year=2026, round_number=3, session="R"),
        output_directory=Path("artifacts"),
        generation_id=generation_id,
        generation_id_generator=None if generation_id is not None else lambda: "generated-001",
    )


def test_pipeline_normalizes_in_canonical_stage_order_and_publishes_exact_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    stage_results: dict[str, object] = {}
    stage_arguments: dict[str, tuple[object, ...]] = {}
    stage_names = (
        "adapt_session_metadata", "adapt_drivers", "adapt_car_telemetry",
        "adapt_position_telemetry", "adapt_laps", "adapt_stints", "adapt_weather",
        "adapt_track_status_intervals", "adapt_race_control_messages", "adapt_results",
    )
    for name in stage_names:
        original = getattr(orchestration, name)

        def recorded(*args: object, _name: str = name, _original: object = original, **kwargs: object) -> object:
            calls.append(_name)
            stage_arguments[_name] = args
            result = _original(*args, **kwargs)  # type: ignore[operator]
            stage_results[_name] = result
            return result

        monkeypatch.setattr(orchestration, name, recorded)
    published: list[dict[str, object]] = []

    def publisher(*, frames: Mapping[str, object], target_parent: Path, generation_id: str) -> str:
        published.append({"frames": frames, "target_parent": target_parent, "generation_id": generation_id})
        return "published"

    result = run_pipeline(_request(), lambda selection: build_complete_session(), publisher)

    assert calls == list(stage_names)
    assert stage_arguments["adapt_stints"][-1] is stage_results["adapt_laps"]
    frames = published[0]["frames"]
    assert isinstance(frames, Mapping)
    assert tuple(frames) == (
        "session_metadata", "drivers", "car_telemetry", "position_telemetry", "laps", "stints",
        "weather", "track_status_intervals", "race_control_messages", "results",
    )
    assert result.generation_id == "generation-001"
    assert published[0]["target_parent"] == Path("artifacts")
    with pytest.raises(TypeError):
        frames["extra"] = object()  # type: ignore[index]


def test_invalid_source_driver_mapping_does_not_call_publisher_and_preserves_stage_cause() -> None:
    session = build_complete_session()
    session.car_data["999"] = [{"SessionTime": 1, "Speed": 1.0}]
    calls: list[object] = []

    with pytest.raises(NormalizationError, match="car_telemetry") as raised:
        run_pipeline(_request(), lambda selection: session, lambda **kwargs: calls.append(kwargs))

    assert calls == []
    assert raised.value.__cause__ is not None


def test_validation_failure_prevents_publishing_and_preserves_cause(monkeypatch: pytest.MonkeyPatch) -> None:
    validation_error = ValueError("invalid generation")
    monkeypatch.setattr(orchestration, "validate_canonical_frames", lambda frames: (_ for _ in ()).throw(validation_error))
    calls: list[object] = []

    with pytest.raises(PipelineValidationError) as raised:
        run_pipeline(_request(), lambda selection: build_complete_session(), lambda **kwargs: calls.append(kwargs))

    assert calls == []
    assert raised.value.__cause__ is validation_error


def test_pipeline_uses_injected_generation_id_seam_and_returns_an_immutable_result() -> None:
    generated: list[bool] = []
    request = _request(generation_id=None)
    request = PipelineRequest(
        selection=request.selection,
        output_directory=request.output_directory,
        generation_id_generator=lambda: generated.append(True) or "generated-001",
    )
    received: list[str] = []

    result = run_pipeline(
        request, lambda selection: build_complete_session(),
        lambda **kwargs: received.append(kwargs["generation_id"]) or "published",
    )

    assert generated == [True]
    assert received == ["generated-001"]
    assert isinstance(result, PipelineResult)
    with pytest.raises(FrozenInstanceError):
        result.generation_id = "other"  # type: ignore[misc]


def test_publisher_failure_preserves_cause_and_request_context() -> None:
    failure = OSError("disk full")

    def failing_publisher(**kwargs: object) -> object:
        raise failure

    with pytest.raises(PublicationError, match="year=2026") as raised:
        run_pipeline(_request(), lambda selection: build_complete_session(), failing_publisher)

    assert raised.value.__cause__ is failure
