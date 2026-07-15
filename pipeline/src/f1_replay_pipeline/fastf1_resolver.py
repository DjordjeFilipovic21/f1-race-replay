"""Lazy, dependency-injected FastF1 session resolution.

FastF1 is deliberately imported only by the default module factory at
resolution time. Tests and application composition may instead inject a small
module-shaped fake and the existing loading seam.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from typing import Protocol

from f1_replay_pipeline.orchestration import (
    RaceSelection,
    SessionResolutionError,
    SessionSelection,
    TestingSelection,
    _selection_context,
)
from f1_replay_pipeline.session_loader import load_session


class FastF1Module(Protocol):
    """The small public FastF1 surface required by this resolver."""

    def get_event(self, year: int, gp: int | str, *, backend: str | None = None, exact_match: bool = False) -> object: ...

    def get_testing_session(
        self, year: int, test_number: int, session_number: int, *, backend: str | None = None
    ) -> object: ...


FastF1ModuleFactory = Callable[[], FastF1Module]
SessionLoader = Callable[..., object]

_SUPPORTED_SESSION_IDENTIFIERS = frozenset(
    {
        "fp1", "fp2", "fp3", "q", "s", "ss", "sq", "r",
        "practice 1", "practice 2", "practice 3", "qualifying", "sprint",
        "sprint shootout", "sprint qualifying", "race",
    }
)


def _import_fastf1() -> FastF1Module:
    """Import FastF1 only when a default resolver actually resolves a selection."""
    return import_module("fastf1")  # type: ignore[return-value]


@dataclass(frozen=True)
class FastF1SessionResolver:
    """Resolve and load one validated race or testing selection exactly once."""

    fastf1_module_factory: FastF1ModuleFactory = _import_fastf1
    session_loader: SessionLoader = load_session

    def __call__(self, selection: SessionSelection) -> object:
        _validate_selection_for_fastf1(selection)
        try:
            fastf1 = self.fastf1_module_factory()
        except SessionResolutionError:
            raise
        except Exception as error:
            raise SessionResolutionError(
                f"could not initialize FastF1 for {_selection_context(selection)}"
            ) from error
        try:
            session = _session_factory(fastf1, selection)()
        except SessionResolutionError:
            raise
        except Exception as error:
            raise SessionResolutionError(
                f"could not resolve {_selection_context(selection)}; "
                "verify the event schedule, backend, and cache"
            ) from error
        try:
            return self.session_loader(session_factory=lambda: session)
        except SessionResolutionError:
            raise
        except Exception as error:
            raise SessionResolutionError(
                f"could not load {_selection_context(selection)}; "
                "verify session availability and cached data"
            ) from error


def _validate_selection_for_fastf1(selection: SessionSelection) -> None:
    if isinstance(selection, RaceSelection):
        _validate_race_session_identifier(selection)
        return
    if isinstance(selection, TestingSelection):
        return
    raise SessionResolutionError("selection must be a RaceSelection or TestingSelection")


def _validate_race_session_identifier(selection: RaceSelection) -> None:
    if selection.session.strip().casefold() not in _SUPPORTED_SESSION_IDENTIFIERS:
        raise SessionResolutionError(
            f"unsupported session={selection.session!r} for {_selection_context(selection)}"
        )


def _session_factory(fastf1: FastF1Module, selection: SessionSelection) -> Callable[[], object]:
    if isinstance(selection, RaceSelection):
        return lambda: _resolve_race_session(fastf1, selection)
    return lambda: _resolve_testing_session(fastf1, selection)


def _resolve_testing_session(fastf1: FastF1Module, selection: TestingSelection) -> object:
    try:
        session = fastf1.get_testing_session(
            selection.year,
            selection.test_number,
            selection.session_number,
            backend=selection.backend,
        )
    except SessionResolutionError:
        raise
    except Exception as error:
        raise SessionResolutionError(
            f"could not resolve testing session for {_selection_context(selection)}; "
            "verify the event schedule, backend, and cache"
        ) from error
    if session is None:
        raise SessionResolutionError(f"no testing session found for {_selection_context(selection)}")
    return session


def _resolve_race_session(fastf1: FastF1Module, selection: RaceSelection) -> object:
    event_selector = selection.round_number if selection.round_number is not None else selection.event_name
    if event_selector is None:
        raise SessionResolutionError(f"race event is missing for {_selection_context(selection)}")
    try:
        event = fastf1.get_event(
            selection.year,
            event_selector,
            backend=selection.backend,
            exact_match=selection.event_name is not None,
        )
    except SessionResolutionError:
        raise
    except Exception as error:
        raise SessionResolutionError(
            f"could not resolve event schedule for {_selection_context(selection)}; "
            "verify the backend and cache"
        ) from error
    if event is None:
        raise SessionResolutionError(f"no exact event found for {_selection_context(selection)}")
    get_session = getattr(event, "get_session", None)
    if not callable(get_session):
        raise SessionResolutionError(f"FastF1 event cannot create session for {_selection_context(selection)}")
    try:
        session = get_session(selection.session)
    except SessionResolutionError:
        raise
    except Exception as error:
        raise SessionResolutionError(
            f"could not resolve session={selection.session!r} for {_selection_context(selection)}; "
            "verify the session alias and event schedule"
        ) from error
    if session is None:
        raise SessionResolutionError(f"no session found for {_selection_context(selection)}")
    return session


__all__ = ["FastF1Module", "FastF1ModuleFactory", "FastF1SessionResolver", "SessionLoader"]
