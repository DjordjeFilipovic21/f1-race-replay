# F1 Replay Pipeline

`f1-replay-pipeline` is an isolated Python foundation for transforming FastF1
data into deterministic, canonical replay tables. It is separate from the
legacy desktop application and its `src/` modules.

## Installation

From this directory:

```bash
python -m pip install .
```

The package requires Python 3.11+ and installs FastF1 3.8.x and Polars 1.x.

## Foundation scope

This initial boundary establishes independent packaging and a deliberately
small top-level import surface. Importing `f1_replay_pipeline` does not load
FastF1 or Polars and performs no network, GUI, or OpenGL work. Import the
specific `canonical_schema`, `normalizers`, or `validators` module when that
capability is needed.

The foundation now provides explicit canonical schemas, pure time/identifier/
null normalizers, deterministic sort-and-deduplication, in-memory Polars
validation, and synthetic offline fixtures. It keeps car and position streams
at their separate native cadences; it neither resamples nor interpolates them.

## Deferred work

FastF1 extraction/adaptation and all Parquet writer responsibilities—including
logical-hash encoding and implementation, atomic builds, checksums, manifests,
and byte-level output controls—are intentionally deferred. The package
currently does not fetch data or write files.
