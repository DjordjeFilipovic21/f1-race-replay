"""Focused behavior coverage for the import-safe argparse boundary."""

from __future__ import annotations

from pathlib import Path
import runpy
import sys
import tomllib

import pytest

import f1_replay_pipeline.app.cli as cli
from f1_replay_pipeline.app.cli import build_parser, main
from f1_replay_pipeline.delivery.browser.browser_delivery_request import (
    BrowserDeliveryServiceError,
    BrowserPublishRequest,
    BrowserPublishResult,
)
from f1_replay_pipeline.app.orchestration import (
    PipelineRequest,
    PipelineResult,
    RaceSelection,
    TestingSelection as PipelineTestingSelection,
)


def test_race_command_builds_request_and_prints_generation_id(capsys: pytest.CaptureFixture[str]) -> None:
    received: list[PipelineRequest] = []

    def service(request: PipelineRequest) -> PipelineResult:
        received.append(request)
        return PipelineResult(request=request, generation_id="race-001", publication=object())

    status = main(
        ["race", "--year", "2026", "--round", "3", "--session", "R", "--output", "artifacts"],
        service=service,
    )

    assert status == 0
    assert received[0].selection == RaceSelection(year=2026, round_number=3, session="R")
    assert received[0].output_directory == Path("artifacts")
    assert capsys.readouterr().out == "generation_id=race-001\n"


def test_testing_command_uses_explicit_testing_selection() -> None:
    received: list[PipelineRequest] = []

    def service(request: PipelineRequest) -> PipelineResult:
        received.append(request)
        return PipelineResult(request=request, generation_id="test-001", publication=object())

    assert main(
        ["testing", "--year", "2026", "--test-number", "1", "--session-number", "2", "--output", "artifacts"],
        service=service,
    ) == 0
    assert received[0].selection == PipelineTestingSelection(year=2026, test_number=1, session_number=2)


def test_browser_command_builds_request_and_prints_delivery_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    received = []

    def browser_service(request: BrowserPublishRequest) -> BrowserPublishResult:
        received.append(request)
        return BrowserPublishResult(request, request.delivery_version, object())

    status = main([
        "browser", "--canonical", "artifacts/canonical", "--output", "artifacts/browser",
        "--delivery-version", "bahrain-v1", "--schema-root", "contracts/replay-data/v1/schemas",
    ], browser_service=browser_service)

    assert status == 0
    assert received == [BrowserPublishRequest(
        Path("artifacts/canonical"), Path("artifacts/browser"), "bahrain-v1",
        Path("contracts/replay-data/v1/schemas"),
    )]
    assert capsys.readouterr().out == "delivery_version=bahrain-v1\n"


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ("--round 0".split(), "positive integer"),
        ("--test-number 0".split(), "positive integer"),
        ("--session-number 0".split(), "positive integer"),
        ("--backend invalid".split(), "invalid choice"),
        (["--backend", "   "], "non-blank"),
        (["--event", "   "], "non-blank"),
        (["--session", "   "], "non-blank"),
        (["--generation-id", "../unsafe"], "generation_id"),
    ],
)
def test_semantic_cli_errors_exit_two_without_calling_service(
    arguments: list[str], expected: str, capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[PipelineRequest] = []
    mode = "testing" if arguments[0] in {"--test-number", "--session-number"} else "race"
    base = [mode, "--year", "2026", "--output", "artifacts"]
    if mode == "race":
        if arguments[0] != "--event":
            base.extend(["--round", "1"])
        base.extend(["--session", "R"])
    else:
        base.extend(["--test-number", "1", "--session-number", "1"])

    with pytest.raises(SystemExit) as raised:
        main(base + arguments, service=lambda request: calls.append(request))  # type: ignore[arg-type]

    assert raised.value.code == 2
    assert calls == []
    assert expected in capsys.readouterr().err


def test_cli_normalizes_backend_before_calling_service() -> None:
    received: list[PipelineRequest] = []

    def service(request: PipelineRequest) -> PipelineResult:
        received.append(request)
        return PipelineResult(request=request, generation_id="race-001", publication=object())

    assert main(
        ["race", "--year", "2026", "--round", "3", "--session", "R", "--backend", "FastF1", "--output", "artifacts"],
        service=service,
    ) == 0

    assert received[0].selection == RaceSelection(
        year=2026, round_number=3, session="R", backend="fastf1",
    )


def test_parser_rejects_abbreviated_or_incomplete_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as abbreviated:
        build_parser().parse_args(["race", "--ye", "2026"])
    with pytest.raises(SystemExit) as incomplete:
        build_parser().parse_args(["race", "--year", "2026", "--round", "1", "--session", "R"])

    assert abbreviated.value.code == 2
    assert incomplete.value.code == 2
    assert "error:" in capsys.readouterr().err


def test_browser_parser_rejects_unsafe_version_without_calling_service() -> None:
    calls = []

    with pytest.raises(SystemExit) as raised:
        main([
            "browser", "--canonical", "canonical", "--output", "browser",
            "--delivery-version", "../unsafe", "--schema-root", "schemas",
        ], browser_service=lambda request: calls.append(request))  # type: ignore[arg-type]

    assert raised.value.code == 2
    assert calls == []


def test_expected_browser_failure_returns_one_visible_error_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def failing_service(request: BrowserPublishRequest) -> BrowserPublishResult:
        del request
        raise BrowserDeliveryServiceError("canonical current pointer is invalid")

    status = main([
        "browser", "--canonical", "canonical", "--output", "browser",
        "--delivery-version", "delivery-v1", "--schema-root", "schemas",
    ], browser_service=failing_service)

    assert status == 1
    assert capsys.readouterr().err == "error: canonical current pointer is invalid\n"


def test_unexpected_browser_failure_is_not_rendered_by_cli(capsys: pytest.CaptureFixture[str]) -> None:
    def failing_service(request: BrowserPublishRequest) -> BrowserPublishResult:
        del request
        raise ValueError("unexpected browser defect")

    with pytest.raises(ValueError, match="unexpected browser defect"):
        main([
            "browser", "--canonical", "canonical", "--output", "browser",
            "--delivery-version", "delivery-v1", "--schema-root", "schemas",
        ], browser_service=failing_service)

    assert capsys.readouterr().err == ""


def test_expected_application_failure_returns_visible_reason_without_traceback(capsys: pytest.CaptureFixture[str]) -> None:
    def failing_service(request: PipelineRequest) -> PipelineResult:
        from f1_replay_pipeline.app.orchestration import SessionResolutionError

        raise SessionResolutionError("no exact event found for race year=2026")

    status = main(
        ["race", "--year", "2026", "--event", "British Grand Prix", "--session", "R", "--output", "artifacts"],
        service=failing_service,
    )

    assert status == 1
    assert capsys.readouterr().err == "error: no exact event found for race year=2026\n"


def test_unexpected_application_failure_is_not_rendered_by_the_cli(capsys: pytest.CaptureFixture[str]) -> None:
    def failing_service(request: PipelineRequest) -> PipelineResult:
        raise RuntimeError("sensitive unexpected detail")

    with pytest.raises(RuntimeError, match="sensitive unexpected detail"):
        main(
            ["race", "--year", "2026", "--round", "1", "--session", "R", "--output", "artifacts"],
            service=failing_service,
        )

    assert capsys.readouterr().err == ""


def test_help_writes_usage_to_stdout_and_exits_successfully(capsys: pytest.CaptureFixture[str]) -> None:
    # Arrange
    parser = build_parser()

    # Act
    with pytest.raises(SystemExit) as raised:
        parser.parse_args(["--help"])
    captured = capsys.readouterr()

    # Assert
    assert raised.value.code == 0
    assert "Publish canonical or browser Formula 1 replay generations." in captured.out
    assert captured.err == ""


def test_module_entry_point_invokes_cli_main_and_propagates_its_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    calls: list[None] = []
    monkeypatch.setattr(cli, "main", lambda: calls.append(None) or 17)

    # Act
    with pytest.raises(SystemExit) as raised:
        runpy.run_module("f1_replay_pipeline.__main__", run_name="__main__")

    # Assert
    assert raised.value.code == 17
    assert calls == [None]


def test_console_script_is_wired_to_the_cli_main() -> None:
    # Arrange
    pyproject_path = Path(__file__).parents[2] / "pyproject.toml"

    # Act
    with pyproject_path.open("rb") as pyproject_file:
        configuration = tomllib.load(pyproject_file)

    # Assert
    assert configuration["project"]["scripts"]["f1-replay-pipeline"] == "f1_replay_pipeline.app.cli:main"


def test_importing_cli_does_not_import_fastf1_or_the_canonical_writer() -> None:
    # Arrange
    sys.modules.pop("fastf1", None)
    sys.modules.pop("f1_replay_pipeline.storage.canonical_writer", None)
    sys.modules.pop("f1_replay_pipeline.app.cli", None)

    # Act
    __import__("f1_replay_pipeline.app.cli")

    # Assert
    assert "fastf1" not in sys.modules
    assert "f1_replay_pipeline.storage.canonical_writer" not in sys.modules
