"""Build the optional issued-penalty sidecar from canonical race control data."""

from __future__ import annotations

from typing import cast

from f1_replay_pipeline.delivery.browser.browser_delivery_models import (
    BrowserPenaltySidecar,
    CanonicalGenerationSnapshot,
)
from f1_replay_pipeline.delivery.browser.browser_penalty_issuance import (
    parse_race_control_penalties,
)


def build_penalty_sidecar(snapshot: CanonicalGenerationSnapshot) -> BrowserPenaltySidecar:
    """Return deterministic issued-penalty data for one canonical snapshot.

    An empty sidecar is valid and lets orchestration decide whether to publish the
    optional artifact.  The parser receives only canonical race-control messages
    and driver metadata, so pit data cannot introduce an inferred served state.
    """
    session = snapshot.frames["session_metadata"].row(0, named=True)
    fixture_id = cast(str, session["session_id"])
    issuances = parse_race_control_penalties(
        snapshot.frames["race_control_messages"],
        snapshot.frames["drivers"],
    )
    return BrowserPenaltySidecar(fixture_id, issuances)


__all__ = ["build_penalty_sidecar"]
