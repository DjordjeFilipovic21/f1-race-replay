from __future__ import annotations

import pytest

from fake_fastf1_session import build_complete_session, build_session_factory
from f1_replay_pipeline.session_loader import SessionLoaderError, load_session


class FakeSession:
    def __init__(self, missing_data: str | None = None) -> None:
        self.load_calls: list[dict[str, bool]] = []
        for attribute in (
            "laps",
            "results",
            "car_data",
            "pos_data",
            "weather_data",
            "track_status",
            "race_control_messages",
        ):
            if attribute != missing_data:
                setattr(self, attribute, object())

    def load(
        self,
        *,
        laps: bool = True,
        telemetry: bool = True,
        weather: bool = True,
        messages: bool = True,
    ) -> None:
        self.load_calls.append(
            {
                "laps": laps,
                "telemetry": telemetry,
                "weather": weather,
                "messages": messages,
            }
        )


class SessionWithoutLoad:
    laps = object()
    results = object()
    car_data = object()
    pos_data = object()
    weather_data = object()
    track_status = object()
    race_control_messages = object()


def test_load_session_returns_preloaded_session_without_calling_load():
    # Arrange
    session = FakeSession()

    # Act
    loaded_session = load_session(session=session)

    # Assert
    assert loaded_session is session
    assert session.load_calls == []


def test_load_session_creates_session_and_passes_exact_fastf1_load_flags():
    # Arrange
    session = FakeSession()
    factory_calls: list[None] = []

    def session_factory() -> FakeSession:
        factory_calls.append(None)
        return session

    # Act
    loaded_session = load_session(session_factory=session_factory)

    # Assert
    assert loaded_session is session
    assert factory_calls == [None]
    assert session.load_calls == [
        {"laps": True, "telemetry": True, "weather": True, "messages": True}
    ]


def test_load_session_requires_factory_when_no_preloaded_session_is_supplied():
    with pytest.raises(SessionLoaderError, match="session factory"):
        load_session()


def test_load_session_requires_callable_load_capability_from_factory():
    with pytest.raises(SessionLoaderError, match="load capability"):
        load_session(session_factory=SessionWithoutLoad)


def test_load_session_identifies_missing_required_session_data():
    with pytest.raises(SessionLoaderError, match="required session data: pos_data"):
        load_session(session_factory=lambda: FakeSession(missing_data="pos_data"))


def test_load_session_accepts_public_shaped_fastf1_fixture_without_network_loading():
    # Arrange: an already-loaded public-shaped session has typed FastF1 tables and no factory.
    session = build_complete_session()

    # Act: validate the injected session boundary.
    loaded_session = load_session(session=session)

    # Assert: preloaded sessions are not loaded again.
    assert loaded_session is session
    assert session.load_calls == []


def test_load_session_factory_loads_public_shaped_fastf1_fixture_with_all_categories():
    # Arrange: the deterministic factory is the sole session creation dependency.
    session = build_complete_session()
    factory = build_session_factory(session)

    # Act: create and load the session through the public boundary.
    loaded_session = load_session(session_factory=factory)

    # Assert: loading requests every FastF1 category exactly once.
    assert loaded_session is session
    assert session.load_calls == [{"laps": True, "telemetry": True, "weather": True, "messages": True}]
