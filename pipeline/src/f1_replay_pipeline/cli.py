"""Thin, import-safe command-line boundary for the canonical pipeline."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sys
from typing import Protocol

from f1_replay_pipeline.browser_delivery_request import (
    BrowserDeliveryServiceError,
    BrowserPublishRequest,
    BrowserPublishResult,
)
from f1_replay_pipeline.orchestration import (
    NormalizationError,
    PipelineRequest,
    PipelineRequestError,
    PipelineResult,
    PipelineValidationError,
    PublicationError,
    RaceSelection,
    SelectionError,
    SessionResolutionError,
    TestingSelection,
)
from f1_replay_pipeline.generation_identity import validate_generation_id


class PipelineService(Protocol):
    """Execute a prepared pipeline request without exposing CLI dependencies."""

    def __call__(self, request: PipelineRequest) -> PipelineResult: ...


class BrowserService(Protocol):
    """Publish browser artifacts without exposing CLI dependencies."""

    def __call__(self, request: BrowserPublishRequest) -> BrowserPublishResult: ...


GenerationIdGenerator = Callable[[], str]
_RACE_BACKENDS = ("fastf1", "f1timing", "ergast")
_TESTING_BACKENDS = ("fastf1", "f1timing")
_EXPECTED_FAILURES = (
    NormalizationError,
    PipelineRequestError,
    PipelineValidationError,
    PublicationError,
    SelectionError,
    SessionResolutionError,
)


def _generate_generation_id() -> str:
    """Create a safe identifier only when the caller did not provide one."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


@dataclass(frozen=True)
class DefaultPipelineService:
    """Application composition kept outside parsing and invoked only at runtime."""

    generation_id_generator: GenerationIdGenerator = _generate_generation_id

    def __call__(self, request: PipelineRequest) -> PipelineResult:
        from f1_replay_pipeline.canonical_writer import publish_canonical_generation
        from f1_replay_pipeline.fastf1_resolver import FastF1SessionResolver
        from f1_replay_pipeline.orchestration import run_pipeline

        return run_pipeline(request, FastF1SessionResolver(), publish_canonical_generation)


@dataclass(frozen=True)
class DefaultBrowserService:
    """Lazy browser publication composition with no FastF1 loading."""

    def __call__(self, request: BrowserPublishRequest) -> BrowserPublishResult:
        from f1_replay_pipeline.browser_delivery_service import publish_browser_delivery_from_canonical

        return publish_browser_delivery_from_canonical(request)


def build_parser() -> argparse.ArgumentParser:
    """Build the stable parser without reading arguments or initializing FastF1."""
    parser = argparse.ArgumentParser(
        prog="f1-replay-pipeline",
        description="Publish canonical or browser Formula 1 replay generations.",
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(dest="mode", required=True)
    _add_race_parser(commands)
    _add_testing_parser(commands)
    _add_browser_parser(commands)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    service: PipelineService | None = None,
    browser_service: BrowserService | None = None,
) -> int:
    """Parse one non-interactive command and return a conventional exit status."""
    namespace = build_parser().parse_args(argv)
    if namespace.mode == "browser":
        request = BrowserPublishRequest(
            namespace.canonical, namespace.output, namespace.delivery_version, namespace.schema_root,
        )
        try:
            result = (browser_service or DefaultBrowserService())(request)
        except BrowserDeliveryServiceError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        print(f"delivery_version={result.delivery_version}")
        return 0
    generation_id_generator = _generation_id_generator(service)
    request = _request_from_namespace(namespace, generation_id_generator)
    try:
        selected_service = service if service is not None else DefaultPipelineService()
        result = selected_service(request)
    except _EXPECTED_FAILURES as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"generation_id={result.generation_id}")
    return 0


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--year", type=_positive_integer, required=True, help="Formula 1 season year.")
    parser.add_argument("--output", type=Path, required=True, help="Output directory for canonical generations.")
    parser.add_argument("--generation-id", type=_generation_id, help="Optional safe generation identifier.")


def _add_race_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    race = commands.add_parser("race", help="Publish an ordinary race-event session.", allow_abbrev=False)
    _add_common_options(race)
    _add_backend_option(race, _RACE_BACKENDS)
    event = race.add_mutually_exclusive_group(required=True)
    event.add_argument("--round", dest="round_number", type=_positive_integer, help="Positive race round number.")
    event.add_argument("--event", dest="event_name", type=_nonblank_text, help="Exact event name.")
    race.add_argument("--session", type=_nonblank_text, required=True, help="FastF1 race session alias, such as R or Q.")


def _add_testing_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    testing = commands.add_parser("testing", help="Publish an explicit testing session.", allow_abbrev=False)
    _add_common_options(testing)
    _add_backend_option(testing, _TESTING_BACKENDS)
    testing.add_argument("--test-number", type=_positive_integer, required=True, help="Positive testing event number.")
    testing.add_argument("--session-number", type=_positive_integer, required=True, help="Positive testing session number.")


def _add_browser_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    browser = commands.add_parser(
        "browser", help="Publish browser artifacts from the selected canonical generation.",
        allow_abbrev=False,
    )
    browser.add_argument("--canonical", type=Path, required=True, help="Canonical parent containing current.json.")
    browser.add_argument("--output", type=Path, required=True, help="Output directory for browser generations.")
    browser.add_argument("--delivery-version", type=_generation_id, required=True, help="Safe browser delivery version.")
    browser.add_argument("--schema-root", type=Path, required=True, help="Local replay-data v1 schema directory.")


def _add_backend_option(parser: argparse.ArgumentParser, choices: tuple[str, ...]) -> None:
    parser.add_argument(
        "--backend",
        type=_backend_name,
        choices=choices,
        help="Optional FastF1 backend.",
    )


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _backend_name(value: str) -> str:
    normalized = value.strip().casefold()
    if not normalized:
        raise argparse.ArgumentTypeError("backend must be non-blank")
    return normalized


def _nonblank_text(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("must be non-blank")
    return value


def _generation_id(value: str) -> str:
    try:
        return validate_generation_id(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _generation_id_generator(service: PipelineService | None) -> GenerationIdGenerator:
    if isinstance(service, DefaultPipelineService):
        return service.generation_id_generator
    return _generate_generation_id


def _request_from_namespace(
    namespace: argparse.Namespace, generation_id_generator: GenerationIdGenerator,
) -> PipelineRequest:
    if namespace.mode == "race":
        selection = RaceSelection(
            year=namespace.year,
            round_number=namespace.round_number,
            event_name=namespace.event_name,
            session=namespace.session,
            backend=namespace.backend,
        )
    else:
        selection = TestingSelection(
            year=namespace.year,
            test_number=namespace.test_number,
            session_number=namespace.session_number,
            backend=namespace.backend,
        )
    return PipelineRequest(
        selection=selection,
        output_directory=namespace.output,
        generation_id=namespace.generation_id,
        generation_id_generator=None if namespace.generation_id else generation_id_generator,
    )


__all__ = [
    "BrowserService", "DefaultBrowserService", "DefaultPipelineService", "PipelineService",
    "build_parser", "main",
]
