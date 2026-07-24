"""Shared deterministic FastF1-shaped fixtures for pipeline adapter tests."""

import pytest

from fixtures.fake_fastf1_session import (
    SESSION_TABLE_NAMES,
    FakeFastF1Session,
    FakeFastF1SessionFactory,
    build_complete_session,
    build_empty_session,
    build_permuted_session,
    build_session_factory,
    build_session_with_empty_table,
    build_session_with_missing_table,
    build_testing_event_schedule,
)


@pytest.fixture
def fake_fastf1_session() -> FakeFastF1Session:
    """Provide a fresh already-loaded complete session without external dependencies."""
    return build_complete_session()


@pytest.fixture
def fake_fastf1_session_factory() -> FakeFastF1SessionFactory:
    """Provide a fresh injectable factory for the complete fake session."""
    return build_session_factory()


@pytest.fixture
def fake_fastf1_empty_session() -> FakeFastF1Session:
    """Provide a session whose every in-scope table is typed and empty."""
    return build_empty_session()


@pytest.fixture(params=SESSION_TABLE_NAMES)
def fake_fastf1_session_with_missing_table(request: pytest.FixtureRequest) -> FakeFastF1Session:
    """Provide one case per absent in-scope session table."""
    return build_session_with_missing_table(request.param)


@pytest.fixture(params=SESSION_TABLE_NAMES)
def fake_fastf1_session_with_empty_table(request: pytest.FixtureRequest) -> FakeFastF1Session:
    """Provide one case per empty in-scope session table."""
    return build_session_with_empty_table(request.param)


@pytest.fixture
def fake_fastf1_permuted_session() -> FakeFastF1Session:
    """Provide a complete session with input ordering deliberately permuted."""
    return build_permuted_session()


@pytest.fixture
def fake_fastf1_testing_event_schedule():
    """Provide the supported public lookup shape for FastF1 testing events."""
    return build_testing_event_schedule()
