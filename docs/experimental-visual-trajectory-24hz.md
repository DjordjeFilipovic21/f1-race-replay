# Experimental 24 Hz visual trajectories

**Status: experimental only.** This module is not imported by browser delivery,
publication, the browser runtime, or canonical processing. Its values are
presentation evidence, never canonical observations or a browser contract.

## Hypothesis

For native position telemetry, a caller-anchored 24 Hz grid can provide a useful
comparison between bounded linear interpolation and a bounded, time-aware PCHIP
candidate. The PCHIP candidate may reduce abrupt velocity changes without
inventing values outside local source-coordinate bounds.

## Boundaries and algorithms

- Input is ordered, paired finite native `x`/`y` observations at absolute integer
  session milliseconds. Equal timestamps retain the lexicographically smallest
  `(x, y)` pair, independent of duplicate ordering.
- The grid uses `start_ms + floor((N * 1000 + 12) / 24)` while it is at most
  `end_ms`; an off-grid terminal point is never added.
- Both strategies use that exact grid, preserve exact source timestamps, do not
  extrapolate, and emit paired nulls across source intervals over 1,500 ms.
- Linear is the baseline. PCHIP precomputes per-axis Fritsch--Carlson harmonic
  tangents, falls back to linear without four contiguous bounded observations,
  and clamps each cubic result to its interval endpoint bounds.

## Comparison metrics

The experiment reports coverage, contiguous rendered path length, p95 magnitude
of frame-to-frame velocity change (acceleration), and maximum Euclidean PCHIP
deviation from linear where both are valid. These are descriptive metrics, not
ground-truth accuracy claims.

## Decision limits

Metrics must be reviewed across representative circuits, weather, pit layouts,
and telemetry gaps before considering integration. Axis-wise clamping prevents
coordinate overshoot but does not prove a point lies on a physically valid track
path; PCHIP can also introduce visually undesirable curvature on sparse or noisy
telemetry.

## Bahrain 2024 experiment evidence

The validated local canonical generation at `artifacts/demo-bahrain-2024` was
evaluated without modifying or republishing it. Across 20 drivers, each with
22,142 finite native position observations, the caller-anchored grid contained
137,617 timestamps per driver. The complete comparison took 20.478 seconds on
the development machine.

Both candidates had mean coverage **0.999978**. Median per-driver p95 visual
acceleration fell from **433.502 m/s²** for bounded linear interpolation to
**347.799 m/s²** for PCHIP, a reduction of about **19.8%**. Mean rendered path
length changed by **+0.0423%**. Maximum candidate deviation from linear was
**13.162 m**, observed for SAI; most drivers remained below 7.3 m.

These acceleration magnitudes reflect noisy source-position derivatives and
are comparison metrics, not claims about physical vehicle acceleration. The
result supports further visual evaluation but does not yet justify production
schema or publication changes. Reproduce the artifact-backed evidence with:

```bash
.venv/bin/python -m pytest -s pipeline/tests/test_visual_trajectory_bahrain.py
```

## Browser viability MVP

Visual testing found no meaningful benefit from the bounded PCHIP candidate, so
the browser MVP now exposes a stronger zero-phase low-pass candidate without
changing delivery artifacts. This is intentionally an evaluation shortcut, not
the proposed production format. The existing 24 FPS playback clock provides the
visual evaluation cadence.

Run the web app and compare these URLs after a full page reload:

```text
http://localhost:5173/                     # default experimental smooth filter
http://localhost:5173/?trajectory=linear  # linear baseline
```

The replay header identifies the active trajectory mode. All non-coordinate
fields retain their production behavior. Smooth is the temporary default on
this experimental branch; `?trajectory=linear` remains the explicit baseline.

The `smooth` mode applies a centered 750 ms triangular low-pass window to native
coordinates before bounded linear sampling. It preserves the first and last
source points, never bridges intervals over 1,500 ms, and limits displacement
from each native coordinate to 10 m per axis. Filtering is disabled while the
driver is in the pit lane or has exact `OffTrack` status. This zero-phase
candidate is intended only to answer whether visibly stronger source jitter
suppression is worthwhile; it is not a physical acceleration model.
