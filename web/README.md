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
