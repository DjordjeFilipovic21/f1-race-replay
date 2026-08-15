# R2 production publishing

This document describes the production-only publication of browser delivery to
Cloudflare R2. The public bucket contains replay-data **v2 browser artifacts**
only. Canonical Parquet is a private pipeline/recovery format and is never
copied to the public bucket.

For the local pipeline and its command selectors, see
[`pipeline/README.md`](../pipeline/README.md). For the v2 delivery contract,
see [the browser delivery interface freeze](browser-delivery-interface-freeze.md).

## Local generation versus production publication

Run these commands from the repository root. They are local and do not access
R2:

```bash
.venv/bin/f1-replay-pipeline generate \
  --year 2024 --round 3 --session R --resume

.venv/bin/f1-replay-pipeline verify --year 2024
```

The generated season root is normally `artifacts/seasons/2024/`. Keep the same
root for incremental runs so the local catalog retains previously validated
records. `verify` performs deep local validation; a directory's existence is
not evidence that a generation or browser delivery is publishable.

Production publication is opt-in. Configure a dedicated AWS profile and the R2
target, then run the local generation with `--publish-r2`:

```bash
aws configure --profile f1-r2
chmod 600 ~/.aws/credentials ~/.aws/config

export R2_ENDPOINT_URL=https://ACCOUNT_ID.r2.cloudflarestorage.com
export R2_BUCKET=f1-race-replay-data
export AWS_PROFILE=f1-r2

.venv/bin/f1-replay-pipeline generate \
  --year 2024 --round 3 --session R --resume --publish-r2
```

Do not place access keys in the repository, local `.env` files, command
arguments, or shell history. Use an R2 token with Object Read & Write access
scoped to the publication bucket.

## Public object layout

The production base URL is:

```text
https://data.f1racereplay.app/seasons/
```

One season uses this layout:

```text
seasons/{year}/catalog.json
seasons/{year}/visuals/{race_id}/circuit-preview.json
seasons/{year}/browser/{race_id}/sessions/{session_code}/browser-current.json
seasons/{year}/browser/{race_id}/generations/{delivery_version}/manifest.json
seasons/{year}/browser/{race_id}/generations/{delivery_version}/chunks/*.json
seasons/{year}/browser/{race_id}/generations/{delivery_version}/*.json
```

`catalog.json` is the only discovery boundary. Deprecated races and sessions
must not be listed there, even when their immutable objects are retained for
rollback. A corrected delivery receives a new `delivery_version`; an existing
generation is never overwritten.

## Validation and commit order

The `--publish-r2` uploader reads the final local season catalog, removes
unvalidated sessions and races without a valid browser session, and deeply
validates every retained browser pointer, manifest, and payload against the
local v2 contract. It then:

1. uploads or reuses immutable generation objects;
2. uploads referenced circuit visuals;
3. uploads session `browser-current.json` pointers;
4. uploads the filtered `catalog.json` last.

An existing immutable key is reused only when both its bytes and HTTP metadata
match. A collision or upload verification failure stops before later discovery
objects are committed. The uploader never uploads canonical Parquet and never
deletes remote objects.

The catalog is the only discovery commit. It is uploaded after the session
pointers, so a failure before catalog upload leaves the previous catalog in
place, although a pointer upload may already have succeeded and require repair.
For rollback, republish the previously validated session pointer and catalog
from an unchanged local season root, then verify the public replay URL. Do not
overwrite or delete the immutable generation being rolled back from.

R2 validation and publication progress is written to stderr. Interactive
terminals receive a live line; redirected logs are throttled to phase changes,
10% increments, and phase completion. The final CLI output includes the catalog
key and cumulative `uploaded` and `reused` counters.

## HTTP metadata and caching

Generation objects:

```text
Content-Type: application/json
Cache-Control: public, max-age=31536000, immutable
```

Catalog and session pointers:

```text
Content-Type: application/json
Cache-Control: public, max-age=0, must-revalidate
```

Circuit previews:

```text
Content-Type: application/json
Cache-Control: public, max-age=86400, must-revalidate
```

Configure an edge Cache Rule for `data.f1racereplay.app` paths containing
`/generations/`. Keep `catalog.json` and `browser-current.json` outside that
immutable rule. R2 CORS must allow `GET` and `HEAD` from
`https://f1racereplay.app`.

Cloudflare compresses supported JSON responses; do not add a decompression
Worker or upload `.br` side files. Optional weather data is an artifact inside
the same immutable v2 delivery and is not uploaded or pointed to independently.

## Manual recovery upload

Use these production-only commands only when the automated publisher cannot
run. They require the endpoint, bucket, and credentials configured above.

Upload a catalog-listed immutable delivery with its production metadata:

```bash
aws s3 sync \
  artifacts/seasons/2024/browser/RACE_ID/generations/DELIVERY_VERSION/ \
  s3://$R2_BUCKET/seasons/2024/browser/RACE_ID/generations/DELIVERY_VERSION/ \
  --endpoint-url "$R2_ENDPOINT_URL" \
  --content-type application/json \
  --cache-control "public, max-age=31536000, immutable"
```

Upload the session pointer and catalog last, with revalidation caching:

```bash
aws s3 cp \
  artifacts/seasons/2024/browser/RACE_ID/sessions/r/browser-current.json \
  s3://$R2_BUCKET/seasons/2024/browser/RACE_ID/sessions/r/browser-current.json \
  --endpoint-url "$R2_ENDPOINT_URL" \
  --content-type application/json \
  --cache-control "public, max-age=0, must-revalidate"

aws s3 cp \
  artifacts/seasons/2024/catalog.json \
  s3://$R2_BUCKET/seasons/2024/catalog.json \
  --endpoint-url "$R2_ENDPOINT_URL" \
  --content-type application/json \
  --cache-control "public, max-age=0, must-revalidate"
```

Verify the catalog and session pointers before considering a recovery complete.
Remove an obsolete root-level `browser-current.json` only after the season
catalog and all replacement session pointers are live:

```bash
aws s3 rm \
  s3://$R2_BUCKET/browser-current.json \
  --endpoint-url "$R2_ENDPOINT_URL"
```
