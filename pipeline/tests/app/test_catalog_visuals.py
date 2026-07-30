"""Focused coverage for catalog visual population and pure preview helpers."""

from __future__ import annotations

import json
from pathlib import Path

from f1_replay_pipeline.app.batch_generation import BatchRaceResult, BatchRequest, BatchResult, publish_catalog
from f1_replay_pipeline.app.catalog_visuals import create_circuit_preview, resolve_venue_coordinates


def _request(tmp_path: Path) -> BatchRequest:
    return BatchRequest(
        2024, (1,), False, "R", tmp_path / "canonical", tmp_path / "browser", tmp_path / "schemas",
    )


def test_coordinate_registry_resolves_template_venue_and_rejects_unknown() -> None:
    coordinates = resolve_venue_coordinates("United Kingdom", "Silverstone")

    assert coordinates is not None
    assert coordinates.latitude > 0
    assert coordinates.longitude < 0
    assert resolve_venue_coordinates("Unknown", "Somewhere") is None


def test_circuit_preview_is_normalized_bounded_and_deterministic() -> None:
    assets = {
        "rotationDegrees": 0,
        "centerLine": [{"x": 0, "y": 0}, {"x": 10, "y": 20}, {"x": 20, "y": 0}, {"x": 0, "y": 0}],
    }

    preview = create_circuit_preview(assets)

    assert preview == {"pathData": "M 0 0 L 10 -20 L 20 0 L 0 0 Z", "viewBox": "0 -20 20 20"}


def test_circuit_preview_uses_workspace_rotation_and_preserves_point_order() -> None:
    assets = {
        "rotationDegrees": 90,
        "centerLine": [{"x": 0, "y": 0}, {"x": 0, "y": 20}, {"x": 10, "y": 40}, {"x": 0, "y": 0}],
    }

    preview = create_circuit_preview(assets)

    assert preview == {"pathData": "M 0 0 L 20 0 L 40 10 L 0 0 Z", "viewBox": "0 0 40 10"}


def test_circuit_preview_rejects_invalid_point_data() -> None:
    assert create_circuit_preview({"centerLine": [{"x": 0, "y": 0}] * 3}) is None
    assert create_circuit_preview({
        "rotationDegrees": 0,
        "centerLine": [{"x": 0, "y": 0}, {"x": float("nan"), "y": 1}] * 2,
    }) is None


def test_publish_catalog_populates_preview_and_retains_unrelated_visuals(tmp_path: Path, monkeypatch) -> None:
    request = _request(tmp_path)
    result = BatchResult(request, (
        BatchRaceResult(
            "2024-round-01-silverstone", 1, "generated", "2024-round-01-r", "2024-round-01-r",
            session_code="r", session_name="Race", event_name="British Grand Prix",
            country="United Kingdom", location="Silverstone",
        ),
    ))
    monkeypatch.setattr("f1_replay_pipeline.app.batch_generation._session_outputs_valid", lambda *_args: True)
    monkeypatch.setattr(
        "f1_replay_pipeline.app.batch_generation._read_circuit_preview_source",
        lambda *_args: {
            "rotationDegrees": 0,
            "centerLine": [{"x": 0, "y": 0}, {"x": 10, "y": 20}, {"x": 20, "y": 0}, {"x": 0, "y": 0}],
        },
    )

    publish_catalog(result)
    monkeypatch.setattr("f1_replay_pipeline.app.batch_generation._retained_session_valid", lambda *_args: True)
    publish_catalog(BatchResult(request, ()))

    catalog = json.loads((tmp_path / "catalog.json").read_bytes())
    race = catalog["races"][0]
    assert race["visual"]["latitude"] > 0
    assert race["visual"]["circuitPreview"] == "visuals/2024-round-01-silverstone/circuit-preview.json"
    assert (tmp_path / race["visual"]["circuitPreview"]).is_file()


def test_publish_catalog_keeps_prior_preview_when_replacement_is_unavailable(tmp_path: Path, monkeypatch) -> None:
    request = _request(tmp_path)
    race_id = "2024-round-01-silverstone"
    result = BatchResult(request, (
        BatchRaceResult(
            race_id, 1, "generated", "2024-round-01-r", "2024-round-01-r",
            session_code="r", session_name="Race", event_name="British Grand Prix",
            country="United Kingdom", location="Silverstone",
        ),
    ))
    monkeypatch.setattr("f1_replay_pipeline.app.batch_generation._session_outputs_valid", lambda *_args: True)
    monkeypatch.setattr(
        "f1_replay_pipeline.app.batch_generation._read_circuit_preview_source",
        lambda *_args: {
            "rotationDegrees": 0,
            "centerLine": [{"x": 0, "y": 0}, {"x": 10, "y": 20}, {"x": 20, "y": 0}, {"x": 0, "y": 0}],
        },
    )
    publish_catalog(result)
    monkeypatch.setattr("f1_replay_pipeline.app.batch_generation._retained_session_valid", lambda *_args: True)
    monkeypatch.setattr(
        "f1_replay_pipeline.app.batch_generation._read_circuit_preview_source",
        lambda *_args: None,
    )

    publish_catalog(BatchResult(request, (
        BatchRaceResult(
            race_id, 1, "generated", "2024-round-01-q", "2024-round-01-q",
            session_code="q", session_name="Qualifying", event_name="British Grand Prix",
            country="United Kingdom", location="Silverstone",
        ),
    )))

    race = json.loads((tmp_path / "catalog.json").read_bytes())["races"][0]
    assert race["visual"]["circuitPreview"] == f"visuals/{race_id}/circuit-preview.json"
