from __future__ import annotations

import sys
from dataclasses import dataclass, field

import pytest

from fake_fastf1_session import build_complete_session
from f1_replay_pipeline.fastf1_resolver import FastF1SessionResolver
from f1_replay_pipeline.orchestration import RaceSelection, SessionResolutionError
from f1_replay_pipeline.orchestration import TestingSelection as PipelineTestingSelection


@dataclass
class FakeEvent:
    session: object
    requested_identifiers: list[str] = field(default_factory=list)

    def get_session(self, identifier: str) -> object:
        self.requested_identifiers.append(identifier)
        return self.session


@dataclass
class FakeFastF1Module:
    event: FakeEvent
    testing_session: object
    event_calls: list[tuple[int, int | str, str | None, bool]] = field(default_factory=list)
    testing_calls: list[tuple[int, int, int, str | None]] = field(default_factory=list)

    def get_event(
        self, year: int, gp: int | str, *, backend: str | None = None, exact_match: bool = False
    ) -> FakeEvent:
        self.event_calls.append((year, gp, backend, exact_match))
        return self.event

    def get_testing_session(
        self, year: int, test_number: int, session_number: int, *, backend: str | None = None
    ) -> object:
        self.testing_calls.append((year, test_number, session_number, backend))
        return self.testing_session


def test_race_event_name_uses_exact_lookup_and_loads_once() -> None:
    session = build_complete_session()
    module = FakeFastF1Module(FakeEvent(session), session)

    resolved = FastF1SessionResolver(lambda: module)(
        RaceSelection(year=2026, event_name="Australian Grand Prix", session="R", backend="fastf1")
    )

    assert resolved is session
    assert module.event_calls == [(2026, "Australian Grand Prix", "fastf1", True)]
    assert module.event.requested_identifiers == ["R"]
    assert session.load_calls == [{"laps": True, "telemetry": True, "weather": True, "messages": True}]


def test_numeric_round_uses_the_numeric_event_selector() -> None:
    session = build_complete_session()
    module = FakeFastF1Module(FakeEvent(session), session)

    resolved = FastF1SessionResolver(lambda: module)(RaceSelection(year=2026, round_number=3, session="r"))

    assert resolved is session
    assert module.event_calls == [(2026, 3, None, False)]


@pytest.mark.parametrize("session_name", ["FP1", "QUALIFYING", "Sprint Shootout", "r", "Race"])
def test_supported_race_session_aliases_are_case_insensitive(session_name: str) -> None:
    session = build_complete_session()
    module = FakeFastF1Module(FakeEvent(session), session)

    assert FastF1SessionResolver(lambda: module)(
        RaceSelection(year=2026, round_number=1, session=session_name)
    ) is session


def test_exact_event_none_reports_an_actionable_resolution_reason() -> None:
    session = build_complete_session()
    module = FakeFastF1Module(FakeEvent(session), session)
    module.get_event = lambda *args, **kwargs: None  # type: ignore[method-assign]

    with pytest.raises(SessionResolutionError, match="no exact event found.*event='Australian Grand Prix'"):
        FastF1SessionResolver(lambda: module)(
            RaceSelection(year=2026, event_name="Australian Grand Prix", session="R")
        )


def test_event_lookup_failure_reports_schedule_context_without_cause_details() -> None:
    def failing_event(*args: object, **kwargs: object) -> object:
        raise LookupError("private cache path")

    module = FakeFastF1Module(FakeEvent(build_complete_session()), build_complete_session())
    module.get_event = failing_event  # type: ignore[method-assign]

    with pytest.raises(SessionResolutionError, match="event schedule.*backend and cache") as raised:
        FastF1SessionResolver(lambda: module)(RaceSelection(year=2026, round_number=1, session="R"))

    assert "private cache path" not in str(raised.value)


def test_session_lookup_failure_reports_session_context_without_cause_details() -> None:
    def failing_session(identifier: str) -> object:
        raise LookupError("private schedule data")

    event = FakeEvent(build_complete_session())
    event.get_session = failing_session  # type: ignore[method-assign]
    module = FakeFastF1Module(event, build_complete_session())

    with pytest.raises(SessionResolutionError, match="session='R'.*session alias") as raised:
        FastF1SessionResolver(lambda: module)(RaceSelection(year=2026, round_number=1, session="R"))

    assert "private schedule data" not in str(raised.value)


def test_testing_uses_dedicated_api_and_loads_once() -> None:
    session = build_complete_session()
    module = FakeFastF1Module(FakeEvent(session), session)

    resolved = FastF1SessionResolver(lambda: module)(
        PipelineTestingSelection(year=2026, test_number=1, session_number=2, backend="f1timing")
    )

    assert resolved is session
    assert module.testing_calls == [(2026, 1, 2, "f1timing")]
    assert module.event_calls == []
    assert session.load_calls == [{"laps": True, "telemetry": True, "weather": True, "messages": True}]


def test_unsupported_race_session_fails_before_module_factory_call() -> None:
    selection = RaceSelection(year=2026, round_number=1, session="warmup")

    with pytest.raises(SessionResolutionError, match="unsupported session"):
        FastF1SessionResolver(lambda: pytest.fail("FastF1 must not be loaded"))(selection)


def test_resolver_preserves_load_failure_cause_and_selection_context() -> None:
    session = build_complete_session()
    module = FakeFastF1Module(FakeEvent(session), session)

    def failing_loader(**_: object) -> object:
        raise LookupError("cache unavailable")

    with pytest.raises(SessionResolutionError, match="could not load.*year=2026.*round=1.*backend='fastf1'") as raised:
        FastF1SessionResolver(lambda: module, failing_loader)(
            RaceSelection(year=2026, round_number=1, session="Race", backend="fastf1")
        )

    assert isinstance(raised.value.__cause__, LookupError)


def test_importing_resolver_does_not_import_fastf1() -> None:
    sys.modules.pop("fastf1", None)
    sys.modules.pop("f1_replay_pipeline.fastf1_resolver", None)

    __import__("f1_replay_pipeline.fastf1_resolver")

    assert "fastf1" not in sys.modules
