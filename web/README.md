# F1 Race Replay Web

The web package is a Vite + React replay application. It provides a
framework-independent replay-data v1 loader, a replay engine for clocking,
sampling, caching, and event delivery, and React feature modules for the
workspace, playback controls, telemetry, leaderboard, and track map.

The Vite bootstrap at `src/main.tsx` renders the application shell from
`src/app/App.tsx`. Replay data, engine, and feature modules live in their
respective `src/data/replay/`, `src/engine/replay/`, and `src/features/replay/`
packages.

## Setup

```bash
cd web
npm ci
npm run ci
```

## Replay-data boundary

`src/data/replay/` accepts an injected asynchronous byte source. Production
code can use `createFetchSource(import.meta.env.VITE_REPLAY_DATA_BASE_URL ?? '/replay-data/')`;
tests use the committed fixture under `../contracts/` directly. The loader
validates v1 identities, safe relative paths, column alignment, chunk ownership
and overlap, and SHA-256 digests whenever a pointer or artifact reference
supplies one. Returned public values are read-only frozen values.

Generated replay artifacts must remain outside `web/src` and `web/public`.

## Architecture and data flow

`src/main.tsx` mounts `src/app/App.tsx`, which composes replay data loading,
the replay engine, playback controls, and the workspace panels. The injected
replay-data source loads the pointer, generation manifest, track assets, and
owned chunks; validated snapshots then flow through the engine into the React
feature modules. Keep the data boundary and engine independent of React.

## Adding a workspace panel

Register the panel in the replay workspace registry and layout types, provide a
focused feature component, and wrap its rendered element in the existing panel
frame/error-boundary conventions. Reuse the established dark panel surfaces,
headers, spacing, and muted labels. Add behavior-focused unit coverage for
new public behavior and keep generated data outside `src` and `public`.

## Performance-sensitive subscriptions

Subscribe only to the replay state needed by a panel and select stable,
minimal slices. Avoid subscribing to the full snapshot when a derived value or
driver-specific slice is sufficient; keep formatting and other transformations
outside render-time hot paths where practical.

## Stylesheet organization

`src/styles.css` is the sole stylesheet entry point. It imports the ordered
layers in `src/styles/`: tokens, base rules, workspace shell, playback,
leaderboard, track map, panels, and responsive rules. Preserve this import
order and keep selectors/declarations scoped to their existing visual role.

## Validation commands

Run focused unit tests with `npm test -- <path>` and the full web unit suite
with `npm test`. Run production-like browser coverage with
`npm run test:e2e`; Playwright builds the app and serves it through Vite
preview on port 4173 using the deterministic `/replay-data/` override.
