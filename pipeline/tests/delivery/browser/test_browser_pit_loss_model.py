"""Focused contract tests for the immutable browser pit-loss model."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    BrowserManifest,
    BrowserPitLossModel,
    BrowserPitLossModelReference,
    MAX_INT64,
    PIT_LOSS_MODEL_SCHEMA_ID,
)


DRIVERS = ({
    "id": "HAM",
    "displayName": "Hamilton",
    "teamName": "Team",
    "colorHex": "#000000",
    "carNumber": "44",
},)


def _model(**changes: object) -> BrowserPitLossModel:
    values: dict[str, object] = {
        "fixture_id": "race-01",
        "method": "global-prior-weighted-mean-v1",
        "baseline_ms": 22_000,
        "prior_weight": 2,
        "time_ms": (0, 1_000, 2_000),
        "estimated_loss_ms": (22_000, 21_900, 22_100),
        "observed_sample_count": (0, 1, 2),
    }
    values.update(changes)
    return BrowserPitLossModel(**values)  # type: ignore[arg-type]


def _reference(**changes: object) -> BrowserPitLossModelReference:
    values: dict[str, object] = {
        "path": "pit-loss-model.json",
        "schema_id": PIT_LOSS_MODEL_SCHEMA_ID,
        "sha256": "a" * 64,
    }
    values.update(changes)
    return BrowserPitLossModelReference(**values)  # type: ignore[arg-type]


def test_model_is_immutable_and_serializes_camel_case() -> None:
    model = _model()

    assert model.time_ms == (0, 1_000, 2_000)
    assert model.as_dict() == {
        "contractVersion": "v1",
        "fixtureId": "race-01",
        "method": "global-prior-weighted-mean-v1",
        "baselineMs": 22_000,
        "priorWeight": 2,
        "timeMs": [0, 1_000, 2_000],
        "estimatedLossMs": [22_000, 21_900, 22_100],
        "observedSampleCount": [0, 1, 2],
    }
    with pytest.raises(FrozenInstanceError):
        model.baseline_ms = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("estimated_loss_ms", (22_000,)),
        ("observed_sample_count", (0, 1)),
        ("time_ms", (0, 1)),
    ],
)
def test_model_rejects_misaligned_arrays(field: str, value: object) -> None:
    with pytest.raises(ValueError, match="aligned"):
        _model(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("baseline_ms", MAX_INT64 + 1),
        ("prior_weight", True),
        ("time_ms", (0, 1_000, MAX_INT64 + 1)),
        ("estimated_loss_ms", (22_000, -1, 22_100)),
        ("observed_sample_count", (0, MAX_INT64 + 1, 2)),
    ],
)
def test_model_rejects_non_int64_values(field: str, value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        _model(**{field: value})


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("method", "other-v1", "method"),
        ("prior_weight", 0, "prior_weight"),
        ("time_ms", (0, 0, 2_000), "strictly increasing"),
        ("estimated_loss_ms", (21_999, 21_900, 22_100), "first estimated"),
        ("observed_sample_count", (1, 1, 2), "first observed"),
        ("observed_sample_count", (0, 1, 1), "strictly increase"),
    ],
)
def test_model_rejects_invalid_semantics(
    field: str, value: object, message: str,
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        _model(**{field: value})


def test_model_requires_non_empty_arrays() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        _model(time_ms=(), estimated_loss_ms=(), observed_sample_count=())


@pytest.mark.parametrize(
    ("path", "schema_id"),
    [
        ("wrong.json", PIT_LOSS_MODEL_SCHEMA_ID),
        ("pit-loss-model.json", "urn:wrong"),
    ],
)
def test_reference_enforces_path_and_schema(path: str, schema_id: str) -> None:
    with pytest.raises(ValueError):
        _reference(path=path, schema_id=schema_id)


def test_manifest_accepts_reference_or_mapping_and_omits_unset_value() -> None:
    without_model = BrowserManifest("race-01", "Race", DRIVERS)
    assert "pitLossModel" not in without_model.as_dict()

    reference = _reference()
    with_model = BrowserManifest("race-01", "Race", DRIVERS, pit_loss_model=reference)
    assert with_model.pit_loss_model == reference
    assert with_model.as_dict()["pitLossModel"] == reference.as_dict()

    from_mapping = BrowserManifest(
        "race-01", "Race", DRIVERS, pit_loss_model=reference.as_dict(),
    )
    assert from_mapping.pit_loss_model == reference
