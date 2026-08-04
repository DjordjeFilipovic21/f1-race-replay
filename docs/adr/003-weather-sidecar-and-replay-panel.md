# ADR-003: Weather sidecar and replay panel contract

- **Status:** Accepted
- **Date:** 2026-08-01
- **Scope:** Phase 0 browser delivery — optional weather sidecar and replay panel
- **Related:** [ADR-001](001-canonical-pipeline-foundation.md), [ADR-002](002-canonical-parquet-writer.md)

## Context

The next browser feature is a replay weather panel: live wind direction and
wind speed plus air/track/weather context. FastF1 exposes `Session.weather_data`
with eight channels (`Time`, `AirTemp`, `Humidity`, `Pressure`, `Rainfall`,
`TrackTemp`, `WindDirection`, `WindSpeed`), sampled approximately once per
minute. The canonical `weather` table already preserves those native rows with
the ordered schema `session_id`, `session_time_ms`, `air_temperature_c`,
`humidity_pct`, `pressure_mbar`, `rainfall`, `track_temperature_c`,
`wind_direction_deg`, `wind_speed_mps`, all nullable.

The browser delivery contract already establishes the pattern this feature must
follow: immutable optional sidecars referenced from the v1 manifest
(`timelineSummary`, `lapSectorSidecar`, `stintSummary`, `pitLossModel`,
`penaltySidecar`) that leave core chunks unchanged and remain absent-safe for
old deliveries. This ADR freezes the v1 weather payload, its semantics, and the
FastF1 zero-sentinel policy.

## Decision

1. **Deliver weather as an optional, immutable sidecar.** The manifest may carry
   a `weatherSidecar` reference to `weather-sidecar.json`, derived from the
   canonical `weather` table by the delivery build alongside chunks. Canonical
   Parquet and core chunks are never rewritten or resampled.

   ```json
   "weatherSidecar": {
     "path": "weather-sidecar.json",
     "schemaId": "urn:f1-cache-replay:schema:replay-data:v1:weather-sidecar",
     "sha256": "<64 lowercase hexadecimal characters>"
   }
   ```

2. **Freeze the v1 payload, field names, units, and ordering.** The sidecar is a
   `v1` object with `contractVersion`, `fixtureId`, and eight equal-length
   columnar arrays in this exact order, one entry per native canonical weather
   row. Field names are camelCase; units match the canonical table exactly.

   ```json
   {
     "contractVersion": "v1",
     "fixtureId": "bahrain-2024",
     "timeMs": [1000, 61000, 121000],
     "airTempC": [24.5, null, 25.8],
     "humidityPct": [62.0, null, 60.0],
     "pressureMbar": [1012.0, null, 1011.0],
     "rainfall": [false, false, false],
     "trackTempC": [31.0, null, 32.0],
     "windDirectionDeg": [270, null, 265],
     "windSpeedMps": [3.2, null, 2.9]
   }
   ```

   | Array | Canonical column | Units | Notes |
   | --- | --- | --- | --- |
   | `timeMs` | `session_time_ms` | absolute integer ms | Strictly ascending; shared replay timeline |
   | `airTempC` | `air_temperature_c` | °C | Nullable |
   | `humidityPct` | `humidity_pct` | % | Nullable |
   | `pressureMbar` | `pressure_mbar` | mbar | Nullable |
    | `rainfall` | `rainfall` | boolean | Nullable; explicit `false` is FastF1's dry/unknown source result, while canonical/adapted `null` remains unavailable |
   | `trackTempC` | `track_temperature_c` | °C | Nullable |
   | `windDirectionDeg` | `wind_direction_deg` | degrees | Meteorological from-direction, 0–359, integer-valued |
   | `windSpeedMps` | `wind_speed_mps` | m/s | Nullable; UI may display km/h (`×3.6`) |

   No unit conversion happens at delivery. `timeMs` values are the exact
   canonical `session_time_ms` values on the same timeline as replay chunks; a
   consumer indexes them against the replay clock. All native weather rows for
   the session are preserved, including rows outside the replay window: weather
   is session-wide context, and clipping would fabricate a boundary.

3. **Preserve native sparse cadence; no interpolation, resampling, or future
   lookup.** The sidecar contains exactly one entry per canonical weather row
   (native approximately once-per-minute samples). No resampling to a fixed
   cadence, no interpolation between samples, no forward fill, no bucketing to
   60-second boundaries, and no synthesized rows. Cadence is descriptive, not
   enforced — the same policy as ADR-001 for canonical tables.

4. **Use causal last-known sampling in the panel.** At replay time `T`, each
   field displays the most recent sample with `sampleTimeMs <= T`; before the
   first sample the field is unavailable. No interpolation, no averaging (wind
   direction in particular must never be linearly averaged), and no future
   sample is ever consulted. Playback and arbitrary seeks use the same pure
   lookup and agree at the same absolute time.

   ```ts
   const i = latestAtOrBefore(weather.timeMs, replayTimeMs)
   if (i < 0 || replayTimeMs - weather.timeMs[i] > STALE_WEATHER_MS) {
     renderUnavailable()
   }
   ```

5. **Define the stale threshold and unavailable states.** A last-known sample
   older than `STALE_WEATHER_MS = 90_000` ms (1.5× the nominal 60-second
   cadence) renders as unavailable, not as live conditions, so a long sensor
   gap is never presented as current weather. Unavailable is a first-class
   state with four triggers, all rendering the same fail-closed "weather
   unavailable" UI — never an error and never a zero:

   - manifest without `weatherSidecar` (old delivery or weather never loaded);
   - replay time before the first sample;
   - a sample row whose measurements sanitized to null; and
   - a last-known sample older than the stale threshold.

6. **Store wind direction as the meteorological from-direction; draw a cautious
   arrow.** `windDirectionDeg` is the direction the wind blows **from**, measured
   clockwise from North (`0` = North, `90` = East), passed through verbatim from
   FastF1 and canonical. FastF1 does not document the from-versus-toward
   convention; the from-direction is the F1 live-timing standard and is
   high-confidence but not FastF1-verified. v1 therefore displays the numeric
   degrees and a compass label derived from the from-direction, with the arrow
   drawn in the from-direction and labeled "wind from". The alternative
   flow-toward arrow (`(deg + 180) % 360`) is not rendered in v1; it ships only
   after one real-world verification against a known GP's reported weather.

7. **Apply the zero-sentinel audit at the sidecar build boundary.** FastF1
   silently replaces missing or malformed channel values with `0` — never
   `NaN` — so the canonical weather table inherits zero-sentinel artifacts.
   The sidecar builder sanitizes per the audit table below so every consumer of
   the artifact gets the same fail-closed result. The canonical table keeps its
   native values; the frontend retains a defensive guard (`null` renders as
   unavailable; `0` is never displayed as a measurement), but sanitization is
   authoritative at build time.

8. **Treat FastF1 weather availability as a first-class absence.** Weather data
   exists only for 2018+ sessions and is not guaranteed per session: FastF1
   soft-fails a weather load with a warning and leaves `weather_data` unset;
   fresh sessions need 30–120 minutes; testing sessions have non-standard
   coverage. A session with an empty canonical weather table emits **no**
   `weatherSidecar` reference — absence is the established optional-artifact
   signal and reuses the old-delivery frontend path. Weather unavailability
   never fails the delivery build.

9. **Preserve backward compatibility and immutability.** `weatherSidecar` is
   optional; strict parsers must explicitly allow its absence. Core chunks are
   unchanged (no chunk fields, no `browser-delivery-v1` version bump). Old
   deliveries and manifests without the sidecar remain valid and replayable.
   The sidecar is published only inside a new complete R2 generation exactly as
   ADR-002 prescribes: immutable generations, deterministic serialization
   (UTF-8 JSON, sorted keys, compact separators, `allow_nan=False`, one trailing
   newline), SHA-256 verified against the manifest reference before the
   `current.json` pointer swap, and schema plus semantic validation (fixture
   agreement, strictly ascending `timeMs`, equal-length arrays). Existing
   generations are never edited; a rollback to an older generation simply lacks
   the sidecar.

## Zero-sentinel audit and decision table

FastF1's parsing loop appends `conv(0)` when a channel key is missing or its
conversion fails, so a `0` can mean "real value" or "missing/malformed". The
table classifies every field, then applies the fail-closed sidecar policy.

| Sidecar field | FastF1 channel | Genuine zero possible? | Sentinel risk | v1 sidecar policy |
| --- | --- | --- | --- | --- |
| `airTempC` | `AirTemp` | No — sub-zero air is absent from the supported corpus | High — missing/malformed becomes `0.0` | `0.0` → `null` |
| `humidityPct` | `Humidity` | Possible in deserts, unverified in corpus | High | `0.0` → `null` unless a sibling measurement in the same row survives sanitization |
| `pressureMbar` | `Pressure` | No — `0` mbar is physically impossible | High | `0.0` → `null` |
| `rainfall` | `Rainfall` | Yes — `false` is genuine dry | FastF1's raw parser maps non-`'1'` values to `false`; canonical/adapted nulls remain null | Preserve explicit `false` as the source's dry/unknown limitation; preserve canonical/adapted `null` as unavailable |
| `trackTempC` | `TrackTemp` | No — sub-zero track is absent from the supported corpus | High | `0.0` → `null` |
| `windDirectionDeg` | `WindDirection` | Yes — `0` is genuine North | Medium — `0` is also the missing substitute | `0` → `null` unless a sibling measurement in the same row survives sanitization |
| `windSpeedMps` | `WindSpeed` | Yes — `0` is genuine calm | Medium — `0.0` is also the missing substitute | `0.0` → `null` unless a sibling measurement in the same row survives sanitization |

Row-level rules:

- A measurement "survives sanitization" when it is non-null after its per-field
  rule. Sentinel-prone zeros never survive; genuine-zero-capable zeros survive
  only when at least one other measurement in the same row does — a fully zero
  row is indistinguishable from a missing sample.
- Rows are never dropped: every canonical weather row maps to one sidecar entry
  at its native `session_time_ms`. A row whose only surviving value is explicit
  `rainfall: false` displays the source's dry/unknown result with all other
  conditions unavailable; a canonical/adapted `rainfall: null` displays
  rainfall as unavailable.
- FastF1's raw API exposes rainfall as a boolean and may turn a missing source
  value into `false`. That source limitation is preserved, not reclassified as
  certainty. The canonical null policy remains authoritative whenever the
  adapter receives a null/unreadable value.

## Consequences

- The panel is a pure read of an immutable, hash-verified artifact; it cannot
  observe partial writes or mutable canonical data.
- Consumers can trust that sentinel-prone zeros never appear in the sidecar;
  only genuine-zero-capable values (with corroboration) can be `0`.
- Weather stays out of core chunks, so chunk identity, `browser-delivery-v1`
  format, and existing sidecars are unaffected; old deliveries replay exactly
  as before and show "weather unavailable".
- The sidecar mirrors canonical sparse rows, so a partially available session
  yields a partially populated sidecar — every field is independently nullable.
- Sanitization at build time means the canonical table (and its logical hash)
  is unchanged by this feature.

## Reconciliation with ADR-001 and ADR-002

This ADR preserves ADR-001's canonical `weather` schema, column order, null
policy, native cadence, and no-interpolation rule; it adds a derived browser
artifact and does not alter any canonical table. It follows ADR-002's immutable
generation publication, deterministic serialization, and `current.json`
visibility boundary. It extends the Phase 0 optional-sidecar pattern already
established by `timelineSummary`, `lapSectorSidecar`, `stintSummary`,
`pitLossModel`, and `penaltySidecar`.

## Assumptions and product follow-ups

- The approximately once-per-minute cadence and the `90_000` ms stale threshold
  are assumptions; the threshold is a display heuristic, not a resampling rule,
  and should be validated in UX review.
- Wind from/toward convention is high-confidence but unverified. Verify against
  a known GP's reported weather before enabling a flow-toward arrow
  (`(deg + 180) % 360`).
- The session loader treats `weather_data` as optional because FastF1 soft
  weather failures leave the lazy property unset or unreadable. The adapter
  turns that known absence into a typed empty weather table; laps, results,
  car/position data, status, and messages remain required.
- Humidity-at-zero and wind-at-zero corroboration assume a desert/calm session
  is possible; a multi-circuit weather corpus should confirm.
- Adapter-level zero-sentinel normalization is deliberately deferred: changing
  the canonical adapter would alter canonical outputs and must be a versioned
  change, not part of this sidecar.
- Product input is needed on wind-speed display unit (m/s vs km/h), compass
  label styling, and whether later versions should add per-sample data-quality
  flags or extra channels.

## References

- [Replay Data Contract](../replay-data-contract.md)
- [Browser delivery interface freeze](../browser-delivery-interface-freeze.md)
- [Canonical pipeline schema and policies](../canonical-pipeline-schema.md)
- FastF1 3.8.3 `Session.weather_data` reference:
  https://docs.fastf1.dev/ and https://docs.fastf1.dev/_modules/fastf1/_api.html
  (analyzed for this ADR in `.tmp/external-context/fastf1/weather-data-api.md`)
