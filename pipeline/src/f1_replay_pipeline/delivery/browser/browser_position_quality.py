"""Quality gates for positions published to the browser runtime."""

from __future__ import annotations

import math
from dataclasses import replace

from f1_replay_pipeline.analysis.live_position.live_position_projection import (
    ProjectionGeometry,
    project_meters,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_models import BrowserDriverFields


# Keep the browser sampler's existing interpolation bound as a hard quarantine
# after an invalid source observation. This prevents a null sentinel sample
# from becoming an apparently valid animated transition.
POSITION_INTERPOLATION_LIMIT_MS = 1_500
MAX_POSITION_SPEED_MPS = 350.0


def sanitize_browser_positions(
    fields: BrowserDriverFields, geometry: ProjectionGeometry,
) -> BrowserDriverFields:
    """Null unreliable coordinates without changing the canonical source."""
    if not isinstance(fields, BrowserDriverFields):
        raise TypeError("fields must be BrowserDriverFields")
    if not isinstance(geometry, ProjectionGeometry):
        raise TypeError("geometry must be ProjectionGeometry")

    x_values = list(fields.x)
    y_values = list(fields.y)
    previous_time: int | None = None
    previous_x: float | None = None
    previous_y: float | None = None
    previous_distance: float | None = None
    quarantine_until = -1

    for index, time_ms in enumerate(fields.time_ms):
        x, y = fields.x[index], fields.y[index]
        # Missing telemetry remains a normal null. Only a supplied finite pair
        # can establish an unreliable-source quarantine.
        supplied_pair = x is not None and y is not None
        projection = (
            None
            if not supplied_pair
            else project_meters(x, y, geometry, previous_track_distance_meters=previous_distance)
        )
        trustworthy = projection is not None and not projection.is_ambiguous
        if trustworthy and previous_time is not None and previous_x is not None and previous_y is not None:
            assert projection is not None and x is not None and y is not None
            elapsed_ms = time_ms - previous_time
            if elapsed_ms > 0 and math.dist((x, y), (previous_x, previous_y)) > MAX_POSITION_SPEED_MPS * elapsed_ms / 1_000:
                trustworthy = False

        if supplied_pair and not trustworthy:
            x_values[index] = None
            y_values[index] = None
            quarantine_until = max(quarantine_until, time_ms + POSITION_INTERPOLATION_LIMIT_MS + 1)
            continue
        if time_ms < quarantine_until:
            x_values[index] = None
            y_values[index] = None
            continue
        if trustworthy:
            assert projection is not None and x is not None and y is not None
            previous_time = time_ms
            previous_x, previous_y = x, y
            previous_distance = projection.track_distance_meters

    return replace(fields, x=tuple(x_values), y=tuple(y_values))


__all__ = ["MAX_POSITION_SPEED_MPS", "POSITION_INTERPOLATION_LIMIT_MS", "sanitize_browser_positions"]
