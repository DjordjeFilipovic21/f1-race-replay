from f1_replay_pipeline.analysis.live_position.live_position_projection import ProjectionGeometry
from f1_replay_pipeline.delivery.browser.browser_delivery_models import BrowserDriverFields
from f1_replay_pipeline.delivery.browser.browser_position_quality import sanitize_browser_positions


def test_sanitizer_nulls_a_coordinate_outside_the_generated_projection_envelope() -> None:
    fields = _fields(((0, 10.0, 0.0), (100, -832.5, -705.8), (200, 20.0, 0.0)))

    sanitized = sanitize_browser_positions(fields, _geometry())

    assert sanitized.x == (10.0, None, None)
    assert sanitized.y == (0.0, None, None)


def test_sanitizer_preserves_valid_coordinates_and_does_not_mutate_input() -> None:
    fields = _fields(((0, 10.0, 0.0), (2_000, 20.0, 0.0)))

    sanitized = sanitize_browser_positions(fields, _geometry())

    assert sanitized.x == fields.x
    assert sanitized.y == fields.y


def _geometry() -> ProjectionGeometry:
    return ProjectionGeometry(((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)), 400.0)


def _fields(samples: tuple[tuple[int, float, float], ...]) -> BrowserDriverFields:
    times = tuple(sample[0] for sample in samples)
    x = tuple(sample[1] for sample in samples)
    y = tuple(sample[2] for sample in samples)
    null_float = (None,) * len(samples)
    null_int = (None,) * len(samples)
    return BrowserDriverFields(
        "HAM", times, x, y, null_float, null_float, null_int, null_int, null_int,
        (None,) * len(samples), null_int, (None,) * len(samples),
        (None,) * len(samples), null_float, null_float, null_int,
    )
