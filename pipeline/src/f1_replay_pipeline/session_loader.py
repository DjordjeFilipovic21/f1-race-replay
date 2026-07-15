"""Dependency-injected loading boundary for FastF1-compatible sessions.

This module deliberately does not import FastF1. Callers supply either a
pre-loaded session or a zero-argument factory that creates one.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeAlias


class SessionLoaderError(ValueError):
    """Raised when a supplied session cannot meet the loader contract."""


class LoadableSession(Protocol):
    """Minimal FastF1 session surface used by the loading boundary."""

    def load(
        self,
        *,
        laps: bool = True,
        telemetry: bool = True,
        weather: bool = True,
        messages: bool = True,
    ) -> None: ...


SessionFactory: TypeAlias = Callable[[], object]

_REQUIRED_SESSION_DATA = (
    "laps",
    "results",
    "car_data",
    "pos_data",
    "weather_data",
    "track_status",
    "race_control_messages",
)


def load_session(
    *,
    session: object | None = None,
    session_factory: SessionFactory | None = None,
) -> object:
    """Return a validated session, loading a factory-created session exactly once.

    Supplying ``session`` declares it already loaded, so its ``load`` method is
    never called. A factory is only required when no loaded session is supplied.
    """
    if session is not None:
        _validate_required_session_data(session)
        return session
    if session_factory is None:
        raise SessionLoaderError("a session factory is required when no loaded session is supplied")

    created_session = session_factory()
    _load_all_session_data(created_session)
    _validate_required_session_data(created_session)
    return created_session


def _load_all_session_data(session: object) -> None:
    load = getattr(session, "load", None)
    if not callable(load):
        raise SessionLoaderError("session is missing a callable load capability")
    load(laps=True, telemetry=True, weather=True, messages=True)


def _validate_required_session_data(session: object) -> None:
    missing_data = [
        attribute
        for attribute in _REQUIRED_SESSION_DATA
        if not hasattr(session, attribute) or getattr(session, attribute) is None
    ]
    if missing_data:
        raise SessionLoaderError(f"session is missing required session data: {', '.join(missing_data)}")


__all__ = ["LoadableSession", "SessionFactory", "SessionLoaderError", "load_session"]
