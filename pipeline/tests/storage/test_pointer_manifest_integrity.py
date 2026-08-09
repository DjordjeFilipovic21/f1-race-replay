"""Deterministic integrity tests for current-pointer publication and manifest digests.

The pointer/manifest boundary accepts only canonical-parquet-v2 identities.
These tests pin exact deterministic bytes and digest sensitivity of the public
boundaries and prove fail-closed rejection at the pointer parse and selection
seams.  Every test calls public boundaries directly with in-memory models or
pytest ``tmp_path``; none migrate, rewrite, or regenerate canonical Parquet.
Assertions already present in ``test_dataset_manifest.py``,
``test_generation_publication.py``, or ``test_canonical_writer.py`` are not
repeated here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
from typing import cast

import polars as pl
import pytest

from f1_replay_pipeline.domain.canonical_schema import CANONICAL_TABLE_SCHEMAS_V2
from f1_replay_pipeline.domain.dataset_manifest import (
    CANONICAL_MANIFEST_TABLE_NAMES,
    DEFAULT_WRITER_SETTINGS,
    CurrentPointer,
    DatasetManifest,
    FORMAT_VERSION_V2,
    MANIFEST_VERSION_V2,
    ManifestValidationError,
    TableManifestEntry,
    manifest_sha256,
    parse_current_pointer,
    schema_tokens_for,
    serialize_current_pointer,
    serialize_deterministic_json,
    serialize_manifest,
)
from f1_replay_pipeline.domain.logical_hashes import logical_table_sha256
from f1_replay_pipeline.storage.generation_publication import (
    CANONICAL_TABLE_NAMES,
    GenerationPublicationError,
    deterministic_pointer_bytes,
    resolve_current_generation,
    write_generation,
)
from f1_replay_pipeline.storage.parquet_io import write_canonical_parquet

_DIGEST = "a" * 64


def _entry(name: str) -> TableManifestEntry:
    return TableManifestEntry(
        name=name,
        path=f"tables/{name}.parquet",
        row_count=0,
        schema=schema_tokens_for(name, "v2"),
        logical_sha256=_DIGEST,
        byte_sha256="b" * 64,
    )


def _manifest() -> DatasetManifest:
    return DatasetManifest(
        generation_id="2026-07-15T120000Z-abc",
        tables={name: _entry(name) for name in CANONICAL_MANIFEST_TABLE_NAMES},
        writer_settings=DEFAULT_WRITER_SETTINGS,
        format_version=FORMAT_VERSION_V2,
        manifest_version=MANIFEST_VERSION_V2,
    )


def _noop_validator(manifest: DatasetManifest, generation_path: Path) -> None:
    """Test seam for the injected manifest-validator hook; complete validation still runs."""
    del manifest, generation_path


def _materialize(generation_id: str):
    """Build one fully valid canonical v2 generation through the staging writer."""

    def materialize(writer):
        tables = []
        for table_name in CANONICAL_TABLE_NAMES:
            schema = dict(CANONICAL_TABLE_SCHEMAS_V2[table_name])
            row = {column: "test-session" if column == "session_id" else None for column in schema}
            if table_name == "session_metadata":
                row["session_mode"] = "race"
            frame = (
                pl.DataFrame([row], schema=schema)
                if table_name == "session_metadata"
                else pl.DataFrame(schema=schema)
            )
            with tempfile.TemporaryDirectory() as directory:
                temporary = Path(directory) / f"{table_name}.parquet"
                write_canonical_parquet(table_name, frame, temporary, version="v2")
                payload = temporary.read_bytes()
            path = f"tables/{table_name}.parquet"
            writer.write_bytes(path, payload)
            tables.append(
                TableManifestEntry(
                    name=table_name,
                    path=path,
                    row_count=frame.height,
                    schema=schema_tokens_for(table_name, "v2"),
                    logical_sha256=logical_table_sha256(table_name, frame, version="v2"),
                    byte_sha256=hashlib.sha256(payload).hexdigest(),
                )
            )
        return serialize_manifest(
            DatasetManifest(
                generation_id=generation_id,
                tables=tuple(tables),
                writer_settings=DEFAULT_WRITER_SETTINGS,
                format_version=FORMAT_VERSION_V2,
                manifest_version=MANIFEST_VERSION_V2,
            )
        )

    return materialize


def _publish(parent: Path, generation_id: str):
    return write_generation(
        target_parent=parent,
        generation_id=generation_id,
        materialize=_materialize(generation_id),
        validate_manifest=_noop_validator,
        format_version=FORMAT_VERSION_V2,
    )


def test_deterministic_pointer_bytes_pin_exact_active_v2_bytes() -> None:
    payload = deterministic_pointer_bytes("safe", _DIGEST)

    assert payload == (
        b'{"format_version":"canonical-parquet-v2","generation_id":"safe",'
        b'"manifest_path":"generations/safe/manifest.json",'
        b'"manifest_sha256":"' + _DIGEST.encode() + b'"}\n'
    )


def test_deterministic_pointer_bytes_round_trip_through_domain_boundary() -> None:
    payload = deterministic_pointer_bytes("2026-07-15T120000Z-abc", _DIGEST)

    parsed = parse_current_pointer(payload)

    assert parsed == CurrentPointer(
        "2026-07-15T120000Z-abc", _DIGEST, format_version=FORMAT_VERSION_V2,
    )
    assert serialize_current_pointer(parsed) == payload


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"{", "invalid current pointer JSON"),
        (b"not-json", "invalid current pointer JSON"),
        (b"[]", "current pointer must be a JSON object"),
    ],
)
def test_current_pointer_parser_fails_closed_on_malformed_json(payload: bytes, message: str) -> None:
    with pytest.raises(ManifestValidationError, match=message):
        parse_current_pointer(payload)


def test_resolve_fails_closed_when_no_current_pointer_exists(tmp_path: Path) -> None:
    with pytest.raises(GenerationPublicationError, match="invalid current pointer"):
        resolve_current_generation(tmp_path)


def test_published_current_pointer_is_exact_and_resolvable(tmp_path: Path) -> None:
    result = _publish(tmp_path, "one")

    expected = deterministic_pointer_bytes("one", result.manifest_sha256)
    assert result.pointer_path.read_bytes() == expected
    parsed = parse_current_pointer(expected)
    assert parsed.generation_id == "one"
    assert parsed.manifest_sha256 == result.manifest_sha256

    resolved = resolve_current_generation(tmp_path)
    assert resolved.manifest_sha256 == result.manifest_sha256
    assert resolved.generation_path == result.generation_path
    assert resolved.pointer_path == result.pointer_path


def test_manifest_sha256_changes_when_row_count_changes() -> None:
    base = _manifest()
    entries = cast(tuple[TableManifestEntry, ...], base.tables)
    mutated = DatasetManifest(
        base.generation_id,
        (replace(entries[0], row_count=1), *entries[1:]),
        base.writer_settings,
        format_version=base.format_version,
        manifest_version=base.manifest_version,
    )

    assert manifest_sha256(mutated) != manifest_sha256(base)


def test_manifest_sha256_changes_when_writer_settings_gain_metadata() -> None:
    base = _manifest()
    extended = DatasetManifest(
        base.generation_id,
        base.tables,
        {**dict(DEFAULT_WRITER_SETTINGS), "publisher": {"name": "test"}},
        format_version=base.format_version,
        manifest_version=base.manifest_version,
    )

    assert manifest_sha256(extended) != manifest_sha256(base)


_Mutator = Callable[[dict[str, object]], None]


def _reverse_first_table_schema(payload: dict[str, object]) -> None:
    tables = cast(list[dict[str, object]], payload["tables"])
    schema = cast(list[dict[str, object]], tables[0]["schema"])
    schema.reverse()


def _swap_format_version(payload: dict[str, object]) -> None:
    payload["format_version"] = "canonical-parquet-v3"


def _change_compression_level(payload: dict[str, object]) -> None:
    settings = cast(dict[str, object], payload["writer_settings"])
    settings["compression_level"] = 5


@pytest.mark.parametrize(
    "mutate",
    [_reverse_first_table_schema, _swap_format_version, _change_compression_level],
)
def test_manifest_sha256_is_bound_to_schema_format_and_writer_setting_bytes(mutate: _Mutator) -> None:
    manifest = _manifest()
    payload = json.loads(serialize_manifest(manifest))
    mutate(payload)
    mutated_bytes = serialize_deterministic_json(payload)

    assert mutated_bytes != serialize_manifest(manifest)
    assert hashlib.sha256(mutated_bytes).hexdigest() != manifest_sha256(manifest)
