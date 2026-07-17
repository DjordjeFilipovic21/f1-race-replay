# F1 Race Replay Web

The web package is a Vite + React shell and a framework-independent TypeScript
loader for replay-data v1. It intentionally contains no replay clock,
interpolation, canvas rendering, caching, or replay UI.

## Setup

```bash
cd web
npm ci
npm run ci
```

## Replay-data boundary

`src/replay-data/` accepts an injected asynchronous byte source. Production
code can use `createFetchSource(import.meta.env.VITE_REPLAY_DATA_BASE_URL ?? '/replay-data/')`;
tests use the committed fixture under `../contracts/` directly. The loader
validates v1 identities, safe relative paths, column alignment, chunk ownership
and overlap, and SHA-256 digests whenever a pointer or artifact reference
supplies one. Returned public values are read-only frozen values.

Generated replay artifacts must remain outside `web/src` and `web/public`.
