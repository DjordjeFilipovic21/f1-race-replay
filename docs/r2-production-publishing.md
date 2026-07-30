# R2 production publishing

`data.f1racereplay.app` serves browser delivery only. Canonical Parquet is a
private pipeline/recovery format and must not be copied into the public bucket.

## Object layout

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

The catalog is the only discovery boundary. Deprecated races and sessions must
not appear in it, even if their immutable objects are retained temporarily for
rollback.

For the initial 2024 release, the only public race IDs are:

```text
2024-round-01-bahrain-grand-prix
2024-round-02-saudi-arabian-grand-prix
```

## Publication order

Publish immutable objects before mutable discovery objects:

1. Upload each referenced `generations/{delivery_version}/` directory.
2. Upload referenced `visuals/`.
3. Upload each `sessions/{session_code}/browser-current.json`.
4. Upload `catalog.json` last.
5. Purge any cached `404` responses below `/seasons/{year}/`.
6. Verify both production replay URLs through the web application.

Never overwrite a generation. A corrected delivery gets a new
`delivery_version`, then the session pointer and catalog are committed last.

## HTTP metadata

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

Cloudflare already compresses these JSON responses for supported browsers. Do
not add a decompression Worker and do not upload `.br` side files.

Configure an edge Cache Rule for:

```text
Hostname equals data.f1racereplay.app
URI path contains /generations/
```

Keep `catalog.json` and `browser-current.json` outside that immutable cache
rule. The R2 CORS policy must allow `GET` and `HEAD` from
`https://f1racereplay.app`.

## Automated pipeline publication

The recommended production path is the opt-in `generate --publish-r2` flag.
Normal generation performs no R2 access.

Configure the R2 target and its standard S3 credentials:

```bash
export R2_ENDPOINT_URL=https://ACCOUNT_ID.r2.cloudflarestorage.com
export R2_BUCKET=f1-race-replay-data
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
```

Generate or resume selected rounds and publish all valid catalog references:

```bash
f1-replay-pipeline generate \
  --year 2024 \
  --round 3 \
  --session R \
  --resume \
  --publish-r2
```

The uploader:

1. Reads the final local `catalog.json` after batch generation.
2. Removes unvalidated sessions and races with no valid browser session from
   the public payload.
3. Deep-validates every retained browser pointer, manifest, and payload.
4. Uploads or reuses immutable generation objects.
5. Uploads visuals and session pointers.
6. Uploads the filtered public catalog last.

The CLI reports validation and per-phase object counters on stderr, including
the cumulative `uploaded` and `reused` counts. Interactive output refreshes in
place; redirected logs are emitted at phase changes and 10% increments.

An existing immutable key is reused only when its bytes and HTTP metadata
agree. A collision or upload verification failure stops publication before
later discovery objects are committed. The uploader never uploads canonical
Parquet and never deletes remote objects.

The local season catalog is authoritative. Incremental runs should reuse the
same `artifacts/seasons/{year}` root so previously published valid races remain
present. Use manual recovery only when the automated publication cannot run.

## Manual S3-compatible upload

The manual uploader needs the account endpoint and bucket name in addition to
the existing R2 access key:

```bash
export R2_ENDPOINT_URL=https://ACCOUNT_ID.r2.cloudflarestorage.com
export R2_BUCKET=BUCKET_NAME
```

For each catalog-listed race, upload its generation with immutable metadata:

```bash
aws s3 sync \
  artifacts/seasons/2024/browser/RACE_ID/generations/DELIVERY_VERSION/ \
  s3://$R2_BUCKET/seasons/2024/browser/RACE_ID/generations/DELIVERY_VERSION/ \
  --endpoint-url "$R2_ENDPOINT_URL" \
  --content-type application/json \
  --cache-control "public, max-age=31536000, immutable"
```

Upload session pointers and the catalog with revalidation:

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

Delete the obsolete single-race entrypoint only after the season catalog and
both session pointers are live:

```bash
aws s3 rm \
  s3://$R2_BUCKET/browser-current.json \
  --endpoint-url "$R2_ENDPOINT_URL"
```
