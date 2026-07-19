"""Thin, import-safe command-line boundary for the canonical pipeline."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from math import exp
from pathlib import Path
import sys
from threading import Event, Lock, Thread
from time import monotonic
from typing import TYPE_CHECKING, Protocol

from f1_replay_pipeline.batch_generation import (
    BatchProgressEvent, BatchRequest, BatchResult, BrowserService as BatchBrowserService,
    PipelineService as BatchPipelineService, ScheduleProvider, run_batch, verify_catalog,
)
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

if TYPE_CHECKING:
    from f1_replay_pipeline.browser_delivery_publication import BrowserValidationProgress


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

    def publish_with_progress(
        self,
        request: BrowserPublishRequest,
        progress: Callable[[str | BrowserValidationProgress], None],
    ) -> BrowserPublishResult:
        from f1_replay_pipeline.browser_delivery_service import publish_browser_delivery_from_canonical

        return publish_browser_delivery_from_canonical(request, progress=progress)


@dataclass(frozen=True)
class DefaultBatchScheduleProvider:
    """Lazy composition ensures importing the CLI never imports FastF1."""

    def __call__(self, year: int, *, backend: str | None = None):
        from f1_replay_pipeline.fastf1_resolver import FastF1ScheduleProvider

        return FastF1ScheduleProvider()(year, backend=backend)


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
    _add_generate_parser(commands)
    _add_verify_parser(commands)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    service: PipelineService | None = None,
    browser_service: BrowserService | None = None,
    schedule_provider: ScheduleProvider | None = None,
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
    if namespace.mode == "generate":
        season_root = Path("artifacts") / "seasons" / str(namespace.year)
        request = BatchRequest(
            year=namespace.year, rounds=None if namespace.all_rounds else tuple(namespace.rounds),
            all_rounds=namespace.all_rounds, session=namespace.session,
            canonical_root=namespace.output or season_root / "canonical",
            browser_root=namespace.browser_output or season_root / "browser",
            schema_root=namespace.schema_root, backend=namespace.backend,
            resume=namespace.resume, force=namespace.force, continue_on_error=namespace.continue_on_error,
        )
        renderer = _terminal_progress_renderer()
        batch_result: BatchResult | None = None
        failure: Exception | None = None
        cancelled = False
        try:
            with _suppress_fastf1_info():
                batch_result = run_batch(
                    request, schedule_provider=schedule_provider or DefaultBatchScheduleProvider(),
                    pipeline_service=service or DefaultPipelineService(),
                    browser_service=browser_service or DefaultBrowserService(), progress=renderer,
                )
        except KeyboardInterrupt:
            cancelled = True
        except Exception as error:
            failure = error
        finally:
            renderer.close()
        if cancelled:
            for race_id, detail in renderer.failures:
                print(f"failure: race_id={race_id} detail={detail}", file=sys.stderr)
            print("Generation cancelled safely. Resume with --resume.", file=sys.stderr)
            return 130
        if failure is not None:
            print(f"error: {failure}", file=sys.stderr)
            return 1
        assert batch_result is not None
        for race in batch_result.races:
            fields = [f"race_id={race.race_id}", f"outcome={race.outcome}"]
            if race.generation_id is not None:
                fields.append(f"generation_id={race.generation_id}")
            if race.delivery_version is not None:
                fields.append(f"delivery_version={race.delivery_version}")
            print(" ".join(fields))
        for race in batch_result.races:
            if race.outcome == "failed" and race.detail:
                print(f"failure: race_id={race.race_id} detail={race.detail}", file=sys.stderr)
        return 1 if batch_result.failed else 0
    if namespace.mode == "verify":
        season_root = Path("artifacts") / "seasons" / str(namespace.year)
        try:
            request = BatchRequest(
                year=namespace.year, rounds=None, all_rounds=True, session="R",
                canonical_root=namespace.output or season_root / "canonical",
                browser_root=namespace.browser_output or season_root / "browser",
                schema_root=namespace.schema_root,
            )
            results = verify_catalog(
                request,
                progress=lambda race_id, phase: print(
                    f"progress: race_id={race_id} phase={phase}", file=sys.stderr,
                ),
            )
        except Exception as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        for race in results:
            print(f"race_id={race.race_id} outcome={race.outcome}")
        return 1 if any(race.outcome != "valid" for race in results) else 0
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


def _add_generate_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    generate = commands.add_parser("generate", help="Generate canonical and browser artifacts for season races.", allow_abbrev=False)
    generate.add_argument("--year", type=_positive_integer, required=True, help="Formula 1 season year.")
    selection = generate.add_mutually_exclusive_group(required=True)
    selection.add_argument("--round", dest="rounds", action="append", type=_positive_integer, help="Race round; repeat to select multiple rounds.")
    selection.add_argument("--all", dest="all_rounds", action="store_true", help="Select every ordinary championship round.")
    generate.add_argument("--session", type=_nonblank_text, default="R", help="FastF1 session alias (default: R).")
    generate.add_argument("--output", type=Path, help="Canonical season parent (default: artifacts/seasons/<year>/canonical).")
    generate.add_argument("--browser-output", type=Path, help="Browser season parent (default: artifacts/seasons/<year>/browser).")
    generate.add_argument("--schema-root", type=Path, default=Path("contracts/replay-data/v1/schemas"), help="Local replay-data v1 schema directory.")
    _add_backend_option(generate, _RACE_BACKENDS)
    generate.add_argument("--resume", action="store_true", help="Skip only races with validated existing outputs.")
    generate.add_argument("--force", action="store_true", help="Regenerate selected races even when resume would skip them.")
    generate.add_argument("--continue-on-error", action="store_true", help="Continue after a failed race; final status remains nonzero.")


def _add_verify_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    verify = commands.add_parser("verify", help="Deeply verify all catalog-referenced season artifacts.", allow_abbrev=False)
    verify.add_argument("--year", type=_positive_integer, required=True, help="Formula 1 season year.")
    verify.add_argument("--output", type=Path, help="Canonical season parent (default: artifacts/seasons/<year>/canonical).")
    verify.add_argument("--browser-output", type=Path, help="Browser season parent (default: artifacts/seasons/<year>/browser).")
    verify.add_argument("--schema-root", type=Path, default=Path("contracts/replay-data/v1/schemas"), help="Local replay-data v1 schema directory.")


class _TerminalProgressRenderer:
    """Render a live bar on terminals and stable progress lines when redirected."""

    def __init__(self) -> None:
        self._started = monotonic()
        self._interactive = sys.stderr.isatty()
        self._latest: BatchProgressEvent | None = None
        self._lock = Lock()
        self._stop = Event()
        self._thread = Thread(target=self._refresh, daemon=True) if self._interactive else None
        self._refresh_count = 0
        self._timed_event: BatchProgressEvent | None = None
        self._phase_started = self._started
        self._activity_durations: list[tuple[str, str, float]] = []
        self._failures: list[tuple[str, str]] = []
        if self._interactive:
            sys.stderr.write("\033[?1049h\033[H\033[2J")
            sys.stderr.flush()
        if self._thread is not None:
            self._thread.start()

    def __call__(self, event: BatchProgressEvent) -> None:
        now = monotonic()
        with self._lock:
            if self._timed_event is not None:
                self._activity_durations.append((
                    self._timed_event.race_id or "finishing",
                    self._timed_event.phase,
                    now - self._phase_started,
                ))
            self._timed_event = (
                event
                if event.phase not in {
                    "schedule_loading", "schedule_ready", "race_queued", "race_succeeded",
                    "race_failed", "batch_completed",
                }
                else None
            )
            self._phase_started = now
            self._latest = event
            if event.phase == "race_failed" and event.detail:
                self._failures.append((event.race_id or "unknown", event.detail))
        self._render(event, final=event.phase == "batch_completed")

    @property
    def failures(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._failures)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._interactive:
            sys.stderr.write("\033[?1049l")
            sys.stderr.flush()
        aggregated: dict[tuple[str, str], float] = {}
        for race_id, phase, duration in self._activity_durations:
            key = (race_id, phase)
            aggregated[key] = aggregated.get(key, 0.0) + duration
        for (race_id, phase), duration in aggregated.items():
            print(f"timing: race_id={race_id} phase={phase} duration={duration:.3f}s", file=sys.stderr)
        print(f"timing: total_duration={monotonic() - self._started:.3f}s", file=sys.stderr)

    def _refresh(self) -> None:
        while not self._stop.wait(0.25):
            with self._lock:
                event = self._latest
            if event is not None:
                self._render(event, final=False)

    def _render(self, event: BatchProgressEvent, *, final: bool) -> None:
        suffix = ""
        if self._interactive and not final:
            suffix = "." * (self._refresh_count % 3 + 1)
            self._refresh_count += 1
        now = monotonic()
        text = _format_progress(
            event,
            now - self._started,
            phase_elapsed=max(0.0, now - self._phase_started),
            suffix=suffix,
        )
        if self._interactive:
            sys.stderr.write(f"\033[H\033[2J{text}\n")
            sys.stderr.flush()
        else:
            print(text, file=sys.stderr)


def _format_progress(
    event: BatchProgressEvent,
    elapsed: float,
    *,
    phase_elapsed: float = 0.0,
    suffix: str = "",
) -> str:
    completed = (
        event.race_index
        if event.phase in {"race_succeeded", "race_failed", "batch_completed"}
        else max(0, event.race_index - 1)
    )
    total = event.race_total
    width = 24
    percent = _progress_percent(event, phase_elapsed=phase_elapsed)
    filled = round(width * percent / 100)
    bar = "█" * filled + "░" * (width - filled)
    identity = event.race_id or "finishing"
    detail = f" | {event.detail}" if event.detail else ""
    minutes, seconds = divmod(int(elapsed), 60)
    return f"[{bar}] progress {percent:02d}% | race {completed}/{total} | {minutes:02d}:{seconds:02d} | {identity} | {event.phase}{suffix}{detail}"


_PHASE_SECONDS = {
    "canonical_generating": 68.011,
    "canonical_snapshot_reading": 29.962,
    "track_assets_generating": 0.010,
    "browser_building": 65.431,
    "browser_payload_preparing": 10.033,
    "browser_contract_schema_loading": 0.043,
    "browser_schema_artifact_validating": 202.407,
    "browser_artifacts_staging": 2.482,
    "browser_pointer_committing_durability": 0.175,
}
_PHASE_WEIGHT_TOTAL = sum(_PHASE_SECONDS.values())
_PHASE_ORDER = tuple(_PHASE_SECONDS)


def _progress_percent(event: BatchProgressEvent, *, phase_elapsed: float = 0.0) -> int:
    """Return wizard-style estimated progress; only completion may report 100%."""
    if event.phase == "batch_completed":
        return 100
    if event.phase in {"catalog_revalidating_references", "catalog_publishing"}:
        return 99
    if event.race_total == 0:
        return 0
    completed = event.race_index if event.phase in {"race_succeeded", "race_failed"} else max(0, event.race_index - 1)
    fraction = completed / event.race_total
    if event.race_id is not None:
        prior_weight = sum(
            seconds for phase, seconds in _PHASE_SECONDS.items() if _phase_precedes(phase, event.phase)
        )
        current_weight = _PHASE_SECONDS.get(event.phase, 0.0)
        phase_fraction = _phase_fraction(event, phase_elapsed, current_weight)
        fraction += (prior_weight + current_weight * phase_fraction) / _PHASE_WEIGHT_TOTAL / event.race_total
    return min(99, int(fraction * 100))


def _phase_precedes(candidate: str, phase: str) -> bool:
    return _PHASE_ORDER.index(candidate) < _PHASE_ORDER.index(phase) if phase in _PHASE_SECONDS else False


def _phase_fraction(event: BatchProgressEvent, elapsed: float, expected_seconds: float) -> float:
    if event.phase_completed is not None and event.phase_total not in (None, 0):
        return min(1.0, event.phase_completed / event.phase_total)
    if expected_seconds <= 0.0:
        return 0.0
    # Setup wizards usually interpolate inside weighted tasks and cap the estimate.
    # The next real phase transition completes the remaining weight immediately.
    return min(0.95, 1.0 - exp(-max(0.0, elapsed) / expected_seconds))


@contextmanager
def _suppress_fastf1_info() -> Iterator[None]:
    """Keep generate-mode progress readable without hiding CLI error reporting."""
    logger = logging.getLogger("fastf1")
    previous_level = logger.level
    logger.setLevel(logging.WARNING)
    try:
        yield
    finally:
        logger.setLevel(previous_level)


def _terminal_progress_renderer() -> _TerminalProgressRenderer:
    return _TerminalProgressRenderer()


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
    "BrowserService", "DefaultBatchScheduleProvider", "DefaultBrowserService", "DefaultPipelineService", "PipelineService",
    "build_parser", "main",
]
