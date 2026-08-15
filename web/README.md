# F1 Race Replay Web

The web package is a Vite + React browser application for browsing published
Formula 1 replay sessions and viewing their telemetry, leaderboard, track, and
playback panels. The browser data boundary and replay engine remain independent
of React.

## Requirements and local start

- Node.js `>=22.12.0` (CI currently uses Node 24)
- npm

From the repository root:

```bash
cd web
npm ci
npm run dev
```

Open the local Vite URL printed by the development server. When
`VITE_REPLAY_SEASONS_BASE_URL` is unset during `npm run dev`, Vite serves replay
data from its `/@fs/.../artifacts/seasons/` URL rooted at the repository's
`artifacts/seasons/` directory. Set the optional base URL when data is served
from another location:

```bash
VITE_REPLAY_SEASONS_BASE_URL=https://data.example.test/seasons/ npm run dev
```

`VITE_REPLAY_SEASON_YEARS` optionally adds a comma-separated list of years to
the season selector, for example `2025,2026`.

## Available commands

Run these commands from `web/`:

| Command | Purpose |
| --- | --- |
| `npm run dev` | Start the Vite development server |
| `npm run build` | Type-check application code and create a production build |
| `npm run preview` | Serve the production build locally |
| `npm run typecheck` | Type-check application and test code |
| `npm test` | Run the Vitest unit suite |
| `npm test -- path/to/test` | Run a focused Vitest test path |
| `npm run test:watch` | Run Vitest in watch mode |
| `npm run test:e2e` | Run Playwright browser tests |
| `npm run ci` | Run typecheck, unit tests, and the production build |

The web CI workflow runs `npm ci`, `npm run ci`, installs Playwright Chromium,
and then runs `npm run test:e2e`.

## Replay-data flow

`src/main.tsx` mounts `src/app/App.tsx`. The application then:

1. Loads the season catalog from the configured seasons base URL.
2. Lets the user choose a listed race and a session marked ready to replay.
3. Resolves that session's `browser_pointer` to a browser generation and pointer.
4. Loads the pointer, V2 manifest, track assets, optional sidecars, and owned
   replay chunks through the injected source in `src/data/replay/`.
5. Validates identities, safe relative paths, digests, column alignment, chunk
   ownership, and overlap before passing snapshots to the replay engine and
   React feature modules.

V2 is the sole supported browser/replay data contract. The loader rejects V1 or
mixed-version replay payloads; a method name such as `track-status-median-v1`
is an identifier within a V2 artifact, not a V1 replay contract.

## Local data and production delivery

Local development uses Vite's `/@fs/.../artifacts/seasons/` URL rooted at the
repository's `artifacts/seasons/` directory when
`VITE_REPLAY_SEASONS_BASE_URL` is unset. The environment override remains
available for another location. Production builds and previews fall back to
`/replay-data/seasons/`, while production deployments can point the variable at
separately published browser-delivery artifacts and cache them independently
from the web bundle. Generated replay artifacts do not belong in `web/src` or
`web/public`.

See the canonical boundary and runtime documentation:

- [`Replay data contract`](../docs/replay-data-contract.md)
- [`Browser replay engine runtime semantics`](../docs/browser-replay-engine-runtime-semantics.md)
- [`Browser delivery interface freeze`](../docs/browser-delivery-interface-freeze.md)
- [`R2 production publishing`](../docs/r2-production-publishing.md)

## Web contributor conventions

- Keep `src/data/replay/` and `src/engine/replay/` framework-independent.
- New workspace panels must use the established dark panel surfaces, borders,
  headers, spacing, and muted labels described in [`AGENTS.md`](AGENTS.md).
- `src/styles.css` is the stylesheet entry point; preserve its ordered imports
  when adding styles.
- Subscribe panels only to the replay state they need and keep expensive
  formatting out of render-time hot paths where practical.
- Add behavior-focused unit coverage for new public behavior. Keep generated
  data outside `src` and `public`.
