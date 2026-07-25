"""Regression coverage for the production dependency composition roots."""

import importlib
from pathlib import Path
import socket
import urllib.request

import pytest

from f1_replay_pipeline.app.cli import (
    DefaultBatchScheduleProvider,
    DefaultBrowserService,
    DefaultPipelineService,
)
from f1_replay_pipeline.app.orchestration import PipelineRequest, RaceSelection
from f1_replay_pipeline.delivery.browser.browser_delivery_request import (
    BrowserPublishRequest,
    BrowserPublishResult,
)


@pytest.fixture(autouse=True)
def reject_network(monkeypatch) -> None:
    def fail_network(*_args: object, **_keywords: object) -> None:
        raise AssertionError("network access is forbidden in composition tests")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(socket.socket, "connect", fail_network)
    monkeypatch.setattr(socket.socket, "connect_ex", fail_network)
    monkeypatch.setattr(urllib.request, "urlopen", fail_network)


def test_default_pipeline_service_resolves_normalizes_and_publishes_through_production_imports(
    fake_fastf1_session: object,
    monkeypatch,
) -> None:
    publication = object()
    published: list[dict[str, object]] = []
    resolver_module = importlib.import_module(
        "f1_replay_pipeline.adapters.fastf1.resolver",
    )
    writer_module = importlib.import_module(
        "f1_replay_pipeline.storage.canonical_writer",
    )

    monkeypatch.setattr(
        resolver_module.FastF1SessionResolver,
        "__call__",
        lambda _resolver, _selection: fake_fastf1_session,
    )

    def publisher(**keywords: object) -> object:
        published.append(keywords)
        return publication

    monkeypatch.setattr(writer_module, "publish_canonical_generation", publisher)
    request = PipelineRequest(
        RaceSelection(2026, "R", round_number=3),
        Path("canonical"),
        generation_id="generation-v1",
    )

    result = DefaultPipelineService()(request)

    assert result.publication is publication
    assert published[0]["target_parent"] == Path("canonical")
    assert published[0]["generation_id"] == "generation-v1"


def test_default_browser_service_uses_production_browser_import(monkeypatch) -> None:
    service_module = importlib.import_module(
        "f1_replay_pipeline.delivery.browser.browser_delivery_service",
    )
    request = BrowserPublishRequest(
        Path("canonical"), Path("browser"), "delivery-v1", Path("schemas"),
    )
    expected = BrowserPublishResult(request, "delivery-v1", object())
    received: list[BrowserPublishRequest] = []

    def publish(value: BrowserPublishRequest, **_keywords: object) -> BrowserPublishResult:
        received.append(value)
        return expected

    monkeypatch.setattr(
        service_module,
        "publish_browser_delivery_from_canonical",
        publish,
    )

    assert DefaultBrowserService()(request) is expected
    assert received == [request]


def test_default_browser_service_forwards_progress_through_production_import(monkeypatch) -> None:
    service_module = importlib.import_module(
        "f1_replay_pipeline.delivery.browser.browser_delivery_service",
    )
    request = BrowserPublishRequest(
        Path("canonical"), Path("browser"), "delivery-v1", Path("schemas"),
    )
    expected = BrowserPublishResult(request, "delivery-v1", object())
    progress = lambda _event: None
    received: list[object] = []

    def publish(
        value: BrowserPublishRequest,
        *,
        progress: object,
    ) -> BrowserPublishResult:
        received.extend((value, progress))
        return expected

    monkeypatch.setattr(
        service_module,
        "publish_browser_delivery_from_canonical",
        publish,
    )

    assert DefaultBrowserService().publish_with_progress(request, progress) is expected
    assert received == [request, progress]


def test_default_schedule_provider_uses_production_resolver_import(monkeypatch) -> None:
    schedule = object()
    received: list[tuple[int, str | None]] = []
    resolver_module = importlib.import_module(
        "f1_replay_pipeline.adapters.fastf1.resolver",
    )

    def provide(_provider, year: int, *, backend: str | None = None) -> object:
        received.append((year, backend))
        return schedule

    monkeypatch.setattr(resolver_module.FastF1ScheduleProvider, "__call__", provide)

    assert DefaultBatchScheduleProvider()(2026, backend="ergast") is schedule
    assert received == [(2026, "ergast")]
