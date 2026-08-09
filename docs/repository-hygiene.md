# Repository Hygiene: Generated State, Ownership, and Lifecycle

This document records which files and directories in this repository are
**committed reference material** and which are **disposable generated state**,
who owns each class, how it is created, and when it is safe to delete.

The governing rules are:

- **Never commit generated state or credentials.** Generated artifacts are
  reproducible from committed inputs; committing them bloats the repository,
  creates merge noise, and can leak local paths.
- **Canonical Parquet is generated output and is never committed.** It is a
  private pipeline/recovery format produced from FastF1, not a committed
  contract. Browser deliveries are derived from a validated canonical
  generation and are also generated output.
- **Browser delivery follows the v2-only contract.** v2
  (`contractVersion` `v2`, `formatVersion` `browser-delivery-v2`) is the sole
  supported browser/replay data contract and the only migration target. There
  is no v1 runtime compatibility: no v1 fallbacks, adapters, readers,
  publishers, or mixed-version payloads. Canonical Parquet itself is unchanged;
  version migration lives at the delivery, validation, and browser boundaries.
- **Committed historical v1 fixtures are preserved as reference material.**
  They are frozen, never upgraded in place, excluded from the active catalog,
  and not loaded by the web application. Deleting them or adding a v1 runtime
  path are both out of scope and forbidden by policy.

The `.gitignore` file is the executable expression of the ownership rules below.
If a path is not listed here, the ignore rules, not this document, are
authoritative.

---

## 1. Committed reference material (never delete)

These paths are part of the repository and are **not** disposable generated
state. Do not delete them, do not treat them as caches, and do not regenerate
them as part of normal work.

| Path | What it is | Why it is committed |
| --- | --- | --- |
| `contracts/replay-data/v1/schemas/*.schema.json` | Historical v1 contract schemas | Frozen reference baseline; documents contract history |
| `contracts/replay-data/v1/fixtures/deterministic-race/` | Deterministic v1 replay fixture (manifest, track assets, chunks, golden snapshots) | Committed reference fixture used by historical contract documentation; never upgraded in place |
| `contracts/replay-data/v2/schemas/*.schema.json` | Active v2 contract schemas | The normative browser/replay data contract |
| `contracts/replay-data/v2/fixtures/deterministic-race/` and pit-loss fixtures | Active deterministic v2 fixtures | Shared offline fixtures for Python contract tests and TypeScript replay tests |
| `web/package-lock.json` | Locked web dependency tree | The reproducibility input for `npm ci` in local and CI installs |
| `pipeline/pyproject.toml`, `legacy/requirements*.txt` | Declared Python dependency ranges | Version-boundary inputs for dependency resolution and CI caching |
| `pipeline/constraints.txt`, `pipeline/test-constraints.txt`, `legacy/constraints.txt` | pip-tools constraints artifacts | Intentionally committed generated reproducibility artifacts: pin the exact resolver result for the lowest supported matrix member (Python 3.11); regenerated deliberately with pip-tools, never hand-edited |

The pip-tools constraints artifacts (`pipeline/constraints.txt`,
`pipeline/test-constraints.txt`, and `legacy/constraints.txt`) are generated files that are
**intentionally committed** as reproducibility inputs. They are not disposable
generated state: do not delete them, do not hand-edit pins, and do not
regenerate them as part of normal work. Regenerate them deliberately with
pip-tools only when the declared dependency ranges or the supported Python
matrix change, then commit the refreshed artifact (see `docs/Testing.md`).

### Historical v1 fixtures policy

The committed v1 fixtures and schemas under `contracts/replay-data/v1/` are a
frozen historical baseline. They remain in the repository as reference
material and are covered by existing contract documentation, but:

- They are **never upgraded in place** and are excluded from the active
  catalog.
- The web reader and pipeline readers are **v2-only** and reject v1 pointers,
  manifests, and sidecars.
- Do **not** add v1 runtime fallbacks, adapters, readers, publishers, or
  mixed-version payloads, and do **not** remove the committed fixtures. The
  v1 surface documents history; it is not a compatibility path.

---

## 2. Runtime caches

Caches speed up repeated local runs by storing downloaded or computed data.
They are disposable: deleting a cache never loses committed state, it only
makes the next run slower while the cache is rebuilt.

### `.fastf1-cache/` (repository root and `legacy/.fastf1-cache/`)

- **Created by:** FastF1, automatically on the first session load that
  downloads timing/telemetry data. The legacy desktop application creates
  `legacy/.fastf1-cache/` on first run; the pipeline's FastF1 factory loading
  path may also invoke FastF1 cache behavior. Both roots are ignored.
- **Owned by:** the local FastF1 runtime (any user running the pipeline or the
  legacy application).
- **Safe to delete:** Yes, at any time. The next session load re-downloads and
  re-caches the needed data; the first load after deletion is noticeably
  slower.

### `computed_data/` (repository root and `legacy/computed_data/`)

- **Created by:** the legacy desktop application, automatically on first run,
  and by its insight windows (for example `computed_data/tyre_state.json`).
  The location is configurable through settings; the default is
  `computed_data` (root and `legacy/computed_data/` are both ignored).
- **Owned by:** the local legacy application runtime.
- **Safe to delete:** Yes, at any time. Deleting the `.pkl`/JSON files forces
  recomputation of telemetry data on the next run. The legacy app also exposes
  `--refresh-data` to force recomputation without manual deletion.

---

## 3. Generated outputs

Generated outputs are the product of local pipeline or build commands. They
are reproducible from committed inputs and are never committed.

### `artifacts/` — canonical Parquet generations and browser deliveries

`artifacts/` is the default output root for the pipeline CLI:

- `race`/`testing` commands publish one **canonical Parquet generation** below
  `--output` (default `artifacts`, season layout
  `artifacts/seasons/<year>/canonical`).
- `browser` derives a **browser delivery** from one fully validated, resolved
  canonical generation and publishes it below `--browser-output` (default
  `artifacts/seasons/<year>/browser`).
- `generate` produces both forms for a season and atomically refreshes the
  local season catalog (`artifacts/seasons/<year>/catalog.json`).

**Canonical Parquet is generated output and is never committed.** Each
generation contains ten validated tables plus a deterministic manifest, and is
selected only through the validated `current.json` pointer. Browser deliveries
are derived from canonical data under the **v2-only contract** and are selected
through `browser-current.json`; the browser publication never edits canonical
`current.json` or copies/republishes canonical Parquet. There is **no canonical
Parquet migration**: canonical Parquet is unchanged and remains a private
pipeline/recovery format that is never copied into the public bucket.

- **Created by:** `f1-replay-pipeline race|testing|browser|generate` (see
  `pipeline/README.md` and `docs/r2-production-publishing.md`).
- **Owned by:** the pipeline operator who runs the generation commands.
- **Safe to delete:** Yes. The entire `artifacts/` tree is disposable and can
  be regenerated from source data. Keep the same season root
  (`artifacts/seasons/<year>`) between incremental runs so previously
  generated races remain present in the local catalog; deleting the root means
  those local catalog records must be regenerated too. Publication to R2 is
  opt-in (`--publish-r2`) and never uploads canonical Parquet; it also never
  deletes remote objects, so deleting local `artifacts/` does not remove
  anything already published remotely.

### Generated media (`output_animation.mp4`, `still_frame.png`)

- **Created by:** local media generation tooling; both files are ignored as
  generated media/output.
- **Owned by:** the local tooling run that produced them.
- **Safe to delete:** Yes, at any time.

### `templates/` (untracked generation-time directory)

`templates/` is an untracked directory referenced by browser-delivery
tooling; the delivery tests assert publication never reads, writes, or
modifies it, and it is not part of the committed contract. The root
`.gitignore` ignores `/templates/`, so it stays out of commits. It is not
managed by this document's lifecycle rules beyond this note: keep it out of
commits.

---

## 4. Virtual environments (`.venv/`, `venv/`, `env/`)

- **Created by:** `python3 -m venv .venv` (the project standard; see
  `docs/Testing.md`). All Python commands run through
  `.venv/bin/python`.
- **Owned by:** the developer who created it.
- **Safe to delete:** Yes, at any time. Recreate with
  `python3 -m venv .venv` and reinstall dependencies (test dependencies,
  the editable `./pipeline` package, and `legacy/requirements-dev.txt` when
  working on the desktop application). Never commit the environment.

---

## 5. `node_modules/` (web)

- **Created by:** `npm install` or `npm ci` inside `web/`. The committed
  `web/package-lock.json` is the reproducibility input; CI uses `npm ci`.
- **Owned by:** the npm toolchain.
- **Safe to delete:** Yes, at any time. Run `npm ci` from `web/` to restore
  the exact locked dependency tree. Never commit `node_modules/`.

---

## 6. `.wrangler/` (web)

- **Created by:** local Wrangler tooling (the web dev workflow declares
  `wrangler` in `web/package.json`); the root ignore rule covers any
  `.wrangler/` state directory.
- **Owned by:** the local Wrangler toolchain.
- **Safe to delete:** Yes, at any time. Local Wrangler state is recreated on
  the next command that needs it. Never commit it.

---

## 7. Web build and test output

| Path | Created by | Safe to delete? |
| --- | --- | --- |
| `web/dist/` | `npm run build` (TypeScript check + Vite build) | Yes, at any time; rebuilt on the next build |
| `web/public/` | Local web payload directory, ignored in this repository | Yes for disposable local payload; do not commit it |
| `web/playwright-report/` | `npm run test:e2e` (Playwright report) | Yes; CI uploads it as a 14-day artifact and regenerates it per run |
| `web/test-results/` | `npm run test:e2e` (Playwright test artifacts) | Yes, at any time |

- **Owned by:** the local Node/npm toolchain that produced them.
- These are the only web build/test outputs listed here; `web/dist/`,
  `web/playwright-report/`, and `web/test-results/` are ignored, and
  `web/public/` is ignored in this repository.

---

## 8. Local secrets and environment files

`.env`, `.env.*`, and similar local environment/credential files are ignored
(except the committed `.env.example` and `web/.env.production` templates).
Never commit credentials, R2 access keys, or local paths. R2 credentials belong
in a dedicated AWS profile outside the repository (see
`docs/r2-production-publishing.md`).

---

## 9. Lifecycle quick reference

| Artifact class | Paths | Created by | Owned by | Safe to delete? |
| --- | --- | --- | --- | --- |
| Committed v1 fixtures | `contracts/replay-data/v1/` | Repository history | Repository | **No** — frozen reference material |
| Committed v2 contract | `contracts/replay-data/v2/` | Repository history | Repository | **No** — active contract fixtures/schemas |
| FastF1 cache | `.fastf1-cache/`, `legacy/.fastf1-cache/` | FastF1 on first load | Local FastF1 runtime | Yes — next load re-downloads |
| Legacy computed data | `computed_data/`, `legacy/computed_data/` | Legacy app on first run | Local legacy app | Yes — recomputed on next run |
| Canonical/browser output | `artifacts/` | `f1-replay-pipeline` | Pipeline operator | Yes — regenerable; keep season root for incremental reuse |
| Generated media | `output_animation.mp4`, `still_frame.png` | Local media tooling | Local run | Yes |
| Virtual environments | `.venv/`, `venv/`, `env/` | `python3 -m venv` | Developer | Yes — recreate and reinstall |
| Node dependencies | `node_modules/` | `npm install` / `npm ci` | npm | Yes — restore with `npm ci` |
| Wrangler state | `.wrangler/` | Wrangler tooling | Wrangler | Yes |
| Web build/test output | `web/dist/`, `web/public/`, `web/playwright-report/`, `web/test-results/` | `npm run build` / `npm run test:e2e` | Node toolchain | Yes — rebuilt per run |

---

## 10. Related guidance

- `docs/Testing.md` — local and CI-equivalent test commands.
- `pipeline/README.md` — pipeline commands, output layout, and R2 publication.
- `docs/r2-production-publishing.md` — R2 object layout, caching, and manual
  recovery; canonical Parquet is never uploaded.
- `docs/adr/001-canonical-pipeline-foundation.md` — canonical pipeline
  foundation policies.
- `docs/canonical-parquet-writer-contract.md` — v2 canonical Parquet writer
  contract.
- `docs/replay-data-contract.md` — the active v2 browser/replay contract and
  the v1 frozen baseline policy.
