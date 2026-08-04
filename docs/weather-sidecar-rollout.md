# Weather sidecar rollout

This guide is for pipeline implementers, release operators, and frontend
owners shipping the optional replay weather panel. Weather is an independent
browser artifact: it is derived from an existing canonical generation, stored
inside a new immutable browser generation, and referenced only when usable
weather rows exist. The canonical Parquet generation and core replay chunks
must not be rewritten for this feature.

## Release invariants

- `weatherSidecar` is optional. A manifest without it is a valid old delivery,
  not a failed release.
- A sidecar contains native sparse weather rows. It does not add rows,
  interpolate, forward-fill, or change the `browser-delivery-v1` contract.
- The sidecar is published as part of a complete generation. Never upload a
  loose sidecar beside `browser-current.json` or modify an existing generation.
- A session whose FastF1 weather load failed, was unavailable, or produced no
  rows still receives a valid replay delivery; the panel shows unavailable.
- The canonical weather table remains the source of truth. FastF1 zero-sentinel
  sanitization belongs at sidecar build time and must not alter canonical data.

These rules let a frontend rollout proceed without making every historical
race wait for a weather backfill.

The reusable pipeline fixtures expose the same cases by name: `normal_sparse`,
`no_weather`, `partial_null`, `zero_sentinel`, and
`old_delivery_no_sidecar`. The old-delivery manifest deliberately omits
`weatherSidecar`; keeping that case in the fixture set prevents a release test
from accidentally treating optional absence as an error.

## Validation checklist

Run the focused Python checks from the repository root. Use the project virtual
environment; do not use system Python.

```bash
.venv/bin/python -m pytest \
  pipeline/tests/fixtures/test_synthetic_fixtures.py \
  pipeline/tests/adapters/fastf1/test_weather_status.py \
  pipeline/tests/delivery/browser/test_browser_weather_sidecar.py

.venv/bin/python -m pytest \
  pipeline/tests/delivery/browser/test_browser_delivery_publication.py \
  pipeline/tests/delivery/browser/test_browser_delivery_integration.py \
  tests/contracts/test_replay_contract.py

.venv/bin/python -m compileall -q pipeline/tests/fixtures
```

The first command checks reusable deterministic fixtures, FastF1-shaped input,
normalization, nullable observations, missing weather, native cadence, and the
zero-sentinel audit. The second checks the publication boundary and contract
registry. `compileall` catches syntax errors in fixture changes without running
the full suite.

The web checks are separate because the web project uses Node tooling:

```bash
npm --prefix web run typecheck
npm --prefix web test -- \
  --run tests/data/replay/weather-loading.test.ts \
  tests/features/replay/selectors/weather-selectors.test.ts \
  tests/features/replay/panels/weather-panel.test.tsx \
  tests/features/replay/workspace/replay-panel-layout.test.ts \
  tests/features/replay/workspace/replay-workspace.test.tsx
```

Before approving a release, record these checks explicitly:

1. **Contract:** the weather payload passes the weather-sidecar schema and
   semantic checks: exact field names, aligned arrays, finite values, and
   strictly ascending non-negative `timeMs`.
2. **Publication:** the manifest reference is optional, points to exactly
   `weather-sidecar.json`, and the sidecar is included in the same generation.
3. **Loader:** present sidecars are fetched, fixture-checked, and digest-
   checked; manifests without the reference cause no weather request.
4. **Selector:** lookup is the latest row at or before the replay cursor;
   before-first, no-measurement, and stale rows return unavailable.
5. **UI:** normal, null, stale, unavailable, wind direction, and zero-valued
   display states are accessible and do not render fabricated zeroes. Rainfall
   remains in the sidecar and drives the replay weather state; it is intentionally
   omitted from the compact Weather metric grid.
6. **Digest:** the manifest sidecar SHA-256 equals the uploaded bytes, and the
   pointer `manifestSha256` equals the uploaded manifest bytes.
7. **Pointer:** the pointer resolves to the intended immutable generation and
   its manifest provenance matches the canonical generation.
8. **Old delivery:** an existing manifest with no `weatherSidecar` still loads,
   makes no sidecar request, and renders weather unavailable.

For a production-like publication validation, use the existing opt-in command
with a selected session. It validates the complete local season payload before
uploading anything:

```bash
f1-replay-pipeline generate \
  --year 2024 \
  --round 3 \
  --session R \
  --resume \
  --publish-r2
```

Keep R2 credentials in the AWS profile described in
[R2 production publishing](r2-production-publishing.md); never place keys in
the repository, an `.env` file, command arguments, or shell history.

## Publication order in R2

The order is a visibility guarantee. A pointer or catalog can be cached or
read immediately, so every object it names must already be complete.

1. Build a new browser `delivery_version` from the validated canonical
   generation. Include the sidecar only if the weather table has rows.
2. Validate the full generation locally: schemas, semantic rules, fixture
   identity, deterministic serialization, and every SHA-256 reference.
3. Upload every immutable object under
   `browser/{race_id}/generations/{delivery_version}/`, including
   `manifest.json`, chunks, track assets, and `weather-sidecar.json` when
   present. Use one immutable cache policy for the whole generation.
4. Verify the uploaded generation by reading it back and checking the manifest,
   artifact digests, and canonical source-generation binding.
5. Upload the session's mutable `browser-current.json` pointer. It must name
   the new generation and its manifest digest.
6. Publish the season catalog last, with the new generation and delivery
   version. Only then is the generation discoverable to the public application.
7. Purge cached `404` responses below the affected season path and verify the
   production replay URL.

Do not publish the sidecar first, swap the pointer before generation upload, or
publish a catalog entry that names a not-yet-uploaded generation. A weather
sidecar does not get its own pointer and does not change the catalog schema.

## Frontend-first rollout

Deploy the frontend that understands the optional field before changing any
session pointer or catalog entry to a weather-enabled generation. The frontend
must:

- accept both manifests with and without `weatherSidecar`;
- skip the weather request when the reference is absent;
- treat a missing artifact, unavailable session, null row, or stale row as the
  same non-error unavailable state; and
- keep core replay loading independent of weather loading.

After the frontend is live, canary one weather-enabled session. Check the
browser network panel for the generation-relative sidecar URL, verify the
manifest and sidecar digests, seek backward across a weather sample, and check
that no future sample appears. Expand the catalog only after the canary is
healthy. This order prevents an older bundle from making a required fetch or
turning an optional feature into a replay outage.

## Existing deliveries and optional absence

Old browser deliveries remain authoritative until their pointer is changed.
They have no `weatherSidecar` reference and need no migration. The loader must
not probe a guessed sidecar path for them: absence is intentional and avoids a
new request for every historical race.

When FastF1 has no weather for a session, publish the normal delivery without a
sidecar. This includes soft weather-load failures, sessions outside the
available source coverage, and sessions whose canonical weather table is empty.
Do not manufacture a row from `weatherState`, rainfall defaults, or another
table. The replay remains useful and weather simply reports unavailable.

## Selective backfill from existing canonical generations

Backfill is optional. Use it only for sessions where the product value justifies
another browser generation.

1. Resolve the existing canonical generation ID and manifest digest. Read that
   immutable generation; do not regenerate or rewrite its Parquet files.
2. Run the browser delivery builder against that canonical input and assign a
   new delivery version. The new manifest must retain the original
   `sourceGenerationId` and `sourceManifestSha256`.
3. If the canonical weather table is empty or absent because FastF1 was
   unavailable, publish no sidecar and stop. Do not fail the race or invent
   weather values.
4. Validate and publish the new complete browser generation using the order
   above. Retain the old browser generation and its pointer information until
   the new one is verified in production.
5. Update the session pointer, then the catalog, only for the selected session.
   Leave unrelated races and sessions untouched.

Selective backfill is deliberately browser-only. It gives a session a new
`delivery_version` while preserving canonical identity, which makes the
change auditable and allows an immediate pointer rollback.

## Retention and rollback

Keep the prior immutable browser generation for at least the release's agreed
rollback window. R2 generations are cheap compared with the cost of losing a
known-good replay, and immutable cache entries cannot be safely repaired in
place.

To roll back, restore the previous session pointer (including its matching
manifest digest), verify that the referenced old generation is still present,
and publish the catalog last if its generation or delivery fields changed.
Do not delete the failed generation before investigating it; it may be needed
for digest comparison or incident review. A rollback to an older generation
without a sidecar is valid and intentionally renders weather unavailable.

## Cache behavior

- Generation objects, including the sidecar, use
  `public, max-age=31536000, immutable`.
- `browser-current.json` and `catalog.json` use
  `public, max-age=0, must-revalidate` and remain outside the immutable cache
  rule.
- Never overwrite a generation key. A correction always gets a new delivery
  version, so browsers and the CDN cannot retain a mixture of old and new
  bytes under one immutable URL.
- Purge cached season-path `404` responses after publishing newly discoverable
  objects. Do not add compressed sidecar files; Cloudflare handles supported
  response compression.

## Known limitations

- FastF1 weather is approximately minute cadence, not a continuous sensor
  stream. The panel uses a last-known row and a 90,000 ms stale threshold.
- MVP does not interpolate or forward-fill weather. In particular, wind
  direction is never linearly interpolated or averaged.
- FastF1 does not explicitly document whether wind degrees are from- or
  toward-direction. v1 treats them as the high-confidence meteorological
  **from** direction and labels the arrow cautiously; verify against a known GP
  before changing to a flow-toward arrow.
- FastF1 replaces missing or malformed numeric weather values with zero. The
  sidecar converts sentinel-prone air/track temperature and pressure zeroes to
  null. Humidity, wind direction, and wind speed zeroes survive only with a
  corroborating measurement in the same row. Explicit FastF1 rainfall `false`
  remains the source's dry/unknown limitation; canonical or adapted rainfall
  `null` remains null and renders unavailable rather than being fabricated as
  dry.
- Weather availability is not guaranteed per session. FastF1 coverage, soft
  load failures, recently finished sessions, and special testing sessions can
  all produce an absent sidecar. Absence is supported behavior, not a release
  failure.

See [ADR-003](adr/003-weather-sidecar-and-replay-panel.md) and the
[Replay Data Contract](replay-data-contract.md#optional-weather-sidecar) for
the frozen payload and semantic rules.
