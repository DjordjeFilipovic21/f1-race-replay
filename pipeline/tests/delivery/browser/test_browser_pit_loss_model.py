"""Focused contract tests for the immutable browser pit-loss model."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    BrowserManifest,
    BrowserPitLossEstimateSidecarReference,
    BrowserPitLossModel,
    BrowserPitLossModelReference,
    MAX_INT64,
    PIT_LOSS_ESTIMATE_METHOD,
    PIT_LOSS_ESTIMATE_SIDECAR_FILENAME,
    PIT_LOSS_ESTIMATE_SIDECAR_SCHEMA_ID,
    PIT_LOSS_MODEL_SCHEMA_ID,
)
from f1_replay_pipeline.delivery.browser.browser_pit_loss_sidecar import (
    BrowserPitLossEstimateSidecar,
    BrowserPitLossEstimateTimeline,
    BrowserPitLossEstimateUnavailable,
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


# --- Status-aware estimate sidecar contract ----------------------------------


def _timeline(**changes: object) -> BrowserPitLossEstimateTimeline:
    values: dict[str, object] = {
        "time_ms": (0, 1_000, 2_000),
        "estimated_loss_ms": (22_000, 22_000, 22_500),
        "observed_sample_count": (0, 1, 2),
    }
    values.update(changes)
    return BrowserPitLossEstimateTimeline(**values)  # type: ignore[arg-type]


def _sidecar(**changes: object) -> BrowserPitLossEstimateSidecar:
    values: dict[str, object] = {
        "fixture_id": "race-01",
        "track_id": "track-01",
        "method": PIT_LOSS_ESTIMATE_METHOD,
        "race": _timeline(),
    }
    values.update(changes)
    return BrowserPitLossEstimateSidecar(**values)  # type: ignore[arg-type]


def _sidecar_reference(**changes: object) -> BrowserPitLossEstimateSidecarReference:
    values: dict[str, object] = {
        "path": PIT_LOSS_ESTIMATE_SIDECAR_FILENAME,
        "schema_id": PIT_LOSS_ESTIMATE_SIDECAR_SCHEMA_ID,
        "sha256": "a" * 64,
    }
    values.update(changes)
    return BrowserPitLossEstimateSidecarReference(**values)  # type: ignore[arg-type]


def test_estimate_timeline_is_immutable_and_serializes_camel_case() -> None:
    timeline = _timeline()

    assert timeline.as_dict() == {
        "timeMs": [0, 1_000, 2_000],
        "estimatedLossMs": [22_000, 22_000, 22_500],
        "observedSampleCount": [0, 1, 2],
    }
    with pytest.raises(FrozenInstanceError):
        timeline.time_ms = (0,)  # type: ignore[misc]


def test_estimate_timeline_coerces_list_arrays_to_tuples() -> None:
    timeline = BrowserPitLossEstimateTimeline(
        cast(tuple[int, ...], [0, 1_000, 2_000]),
        cast(tuple[int, ...], [22_000, 22_000, 22_500]),
        cast(tuple[int, ...], [0, 1, 2]),
    )

    assert timeline.time_ms == (0, 1_000, 2_000)
    assert isinstance(timeline.time_ms, tuple)
    assert isinstance(timeline.estimated_loss_ms, tuple)
    assert isinstance(timeline.observed_sample_count, tuple)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("time_ms", ()),
        ("time_ms", (0, 1_000, 2_000, 3_000)),
        ("estimated_loss_ms", (22_000,)),
        ("observed_sample_count", (0, 1)),
    ],
)
def test_estimate_timeline_rejects_misaligned_or_empty_arrays(
    field: str, value: object,
) -> None:
    with pytest.raises(ValueError, match="(aligned|non-empty)"):
        _timeline(**{field: value})


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("time_ms", (0, 0, 2_000), "strictly increasing"),
        ("observed_sample_count", (0, 1, 1), "strictly increasing"),
        ("observed_sample_count", (1, 2, 3), "first observed"),
        ("estimated_loss_ms", (22_000, MAX_INT64 + 1, 22_500), "Int64"),
        ("observed_sample_count", (0, -1, 2), "Int64"),
    ],
)
def test_estimate_timeline_rejects_invalid_semantics(
    field: str, value: object, message: str,
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        _timeline(**{field: value})


def test_estimate_unavailable_is_immutable_and_serializes() -> None:
    unavailable = BrowserPitLossEstimateUnavailable()

    assert unavailable.status == "unavailable"
    assert unavailable.as_dict() == {"status": "unavailable"}
    with pytest.raises(FrozenInstanceError):
        unavailable.status = "normal"  # type: ignore[misc]


def test_estimate_unavailable_rejects_other_status_values() -> None:
    with pytest.raises(ValueError, match="unavailable"):
        BrowserPitLossEstimateUnavailable("normal")  # type: ignore[arg-type]


def test_sidecar_is_immutable_and_omits_absent_status_fields() -> None:
    sidecar = _sidecar()

    assert sidecar.safety_car is None
    assert sidecar.virtual_safety_car is None
    assert sidecar.as_dict() == {
        "contractVersion": "v1",
        "fixtureId": "race-01",
        "trackId": "track-01",
        "method": PIT_LOSS_ESTIMATE_METHOD,
        "race": _timeline().as_dict(),
    }
    with pytest.raises(FrozenInstanceError):
        sidecar.race = _timeline()  # type: ignore[misc]


def test_sidecar_serializes_unavailable_and_timeline_status_estimates() -> None:
    sidecar = _sidecar(
        safety_car=BrowserPitLossEstimateUnavailable(),
        virtual_safety_car=BrowserPitLossEstimateTimeline((0, 500), (22_000, 23_000), (0, 1)),
    )

    payload = sidecar.as_dict()

    assert payload["safetyCar"] == {"status": "unavailable"}
    assert payload["virtualSafetyCar"] == {
        "timeMs": [0, 500],
        "estimatedLossMs": [22_000, 23_000],
        "observedSampleCount": [0, 1],
    }


def test_sidecar_coerces_status_estimates_from_mappings() -> None:
    sidecar = _sidecar(
        safety_car={"status": "unavailable"},
        virtual_safety_car={
            "timeMs": (0, 500),
            "estimatedLossMs": (22_000, 23_000),
            "observedSampleCount": (0, 1),
        },
    )

    assert isinstance(sidecar.safety_car, BrowserPitLossEstimateUnavailable)
    assert isinstance(sidecar.virtual_safety_car, BrowserPitLossEstimateTimeline)


@pytest.mark.parametrize(
    ("fixture_id", "track_id"),
    [
        ("Race-01", "track-01"),
        ("race_01", "track-01"),
        ("", "track-01"),
        ("race-01", "TRACK-01"),
        ("race-01", "track 01"),
    ],
)
def test_sidecar_rejects_invalid_identifiers(fixture_id: str, track_id: str) -> None:
    with pytest.raises(ValueError, match="identifier"):
        _sidecar(fixture_id=fixture_id, track_id=track_id)


def test_sidecar_rejects_invalid_method() -> None:
    with pytest.raises(ValueError, match="method"):
        _sidecar(method="other-v1")


def test_sidecar_rejects_invalid_race_and_status_estimates() -> None:
    with pytest.raises(ValueError, match="race"):
        _sidecar(race={"timeMs": (0,)})
    with pytest.raises(ValueError, match="unavailable"):
        _sidecar(safety_car={"status": "normal"})
    with pytest.raises(TypeError, match="safety_car"):
        _sidecar(safety_car=42)  # type: ignore[arg-type]


def test_legacy_sidecar_rejects_instantiated_race_timeline_without_observed_sample_count() -> None:
    # Arrange: a pre-instantiated causal timeline omitting the current-race
    # observation array must not slip through the legacy coercion boundary.
    with pytest.raises(ValueError, match="race must contain observedSampleCount"):
        _sidecar(race=BrowserPitLossEstimateTimeline((0,), (22_000,)))


def test_legacy_sidecar_rejects_instantiated_status_timeline_without_observed_sample_count() -> None:
    # Arrange: status estimates are coerced through the same legacy timeline
    # contract, so an instantiated Safety Car timeline without observation
    # counts is rejected as well.
    with pytest.raises(ValueError, match="safety_car must contain observedSampleCount"):
        _sidecar(
            safety_car=BrowserPitLossEstimateTimeline((0,), (22_000,)),
        )


def test_legacy_sidecar_unavailable_status_remains_accepted_without_observed_sample_count() -> None:
    # Arrange / Act: an unavailable status value never carries an observation
    # array and must stay a valid legacy shape while instantiated race
    # timelines keep their required counts.
    sidecar = _sidecar(
        race=BrowserPitLossEstimateTimeline((0,), (22_000,), (0,)),
        safety_car=BrowserPitLossEstimateUnavailable(),
    )

    # Assert: the unavailable status round-trips unchanged.
    assert isinstance(sidecar.safety_car, BrowserPitLossEstimateUnavailable)
    assert sidecar.as_dict()["safetyCar"] == {"status": "unavailable"}


@pytest.mark.parametrize(
    ("path", "schema_id"),
    [
        ("wrong.json", PIT_LOSS_ESTIMATE_SIDECAR_SCHEMA_ID),
        (PIT_LOSS_ESTIMATE_SIDECAR_FILENAME, "urn:wrong"),
    ],
)
def test_sidecar_reference_enforces_path_and_schema(path: str, schema_id: str) -> None:
    with pytest.raises(ValueError):
        _sidecar_reference(path=path, schema_id=schema_id)


def test_manifest_omits_sidecar_when_unset_and_includes_when_present() -> None:
    without_sidecar = BrowserManifest("race-01", "Race", DRIVERS)
    assert "pitLossEstimateSidecar" not in without_sidecar.as_dict()

    reference = _sidecar_reference()
    with_sidecar = BrowserManifest(
        "race-01", "Race", DRIVERS, pit_loss_estimate_sidecar=reference,
    )
    assert with_sidecar.pit_loss_estimate_sidecar == reference
    assert with_sidecar.as_dict()["pitLossEstimateSidecar"] == reference.as_dict()

    from_mapping = BrowserManifest(
        "race-01", "Race", DRIVERS, pit_loss_estimate_sidecar=reference.as_dict(),
    )
    assert from_mapping.pit_loss_estimate_sidecar == reference
