"""Focused domain tests for the V2-only dataset manifest boundary.

The active manifest, schema-reference, and pointer boundary accepts only
canonical-parquet-v2 identities.  Frozen canonical-parquet-v1 material stays
historical-only: the committed v1 golden fixture is never modified, and every
active constructor/parser rejects v1 identities.
"""

import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import cast

import pytest

from f1_replay_pipeline.domain.dataset_manifest import (
    CANONICAL_MANIFEST_TABLE_NAMES,
    DEFAULT_WRITER_SETTINGS,
    CurrentPointer,
    DatasetManifest,
    ManifestValidationError,
    TableManifestEntry,
    manifest_sha256,
    parse_current_pointer,
    parse_manifest,
    schema_tokens_for,
    serialize_current_pointer,
    serialize_deterministic_json,
    serialize_manifest,
    FORMAT_VERSION_V1,
    FORMAT_VERSION_V2,
    MANIFEST_VERSION_V1,
    MANIFEST_VERSION_V2,
    schema_tokens_for_version,
)
from f1_replay_pipeline.domain.generation_identity import GenerationIdentityError, validate_generation_id

_DIGEST = "a" * 64
_GOLDEN_MANIFEST_V2 = Path(__file__).parents[2] / "golden" / "canonical_manifest_v2.json"
_GOLDEN_MANIFEST_V1 = Path(__file__).parents[2] / "golden" / "canonical_manifest_v1.json"


def _entry(name: str) -> TableManifestEntry:
    return TableManifestEntry(
        name=name,
        path=f"tables/{name}.parquet",
        row_count=0,
        schema=schema_tokens_for(name, "v2"),
        logical_sha256=_DIGEST,
        byte_sha256="b" * 64,
    )


def _manifest(tables=None) -> DatasetManifest:
    return DatasetManifest(
        generation_id="2026-07-15T120000Z-abc",
        tables={name: _entry(name) for name in CANONICAL_MANIFEST_TABLE_NAMES} if tables is None else tables,
        writer_settings=DEFAULT_WRITER_SETTINGS,
        format_version=FORMAT_VERSION_V2,
        manifest_version=MANIFEST_VERSION_V2,
    )


def test_manifest_golden_bytes_and_digest_are_exact():
    manifest = _manifest()

    golden = _GOLDEN_MANIFEST_V2.read_bytes()
    parsed = parse_manifest(golden)

    assert parsed == manifest
    assert serialize_manifest(parsed) == golden
    assert manifest_sha256(manifest) == hashlib.sha256(serialize_manifest(manifest)).hexdigest()


def test_manifest_normalizes_reordered_mapping_to_contract_table_order():
    reverse_mapping = {name: _entry(name) for name in reversed(CANONICAL_MANIFEST_TABLE_NAMES)}

    manifest = _manifest(reverse_mapping)

    entries = cast(tuple[TableManifestEntry, ...], manifest.tables)
    assert tuple(entry.name for entry in entries) == CANONICAL_MANIFEST_TABLE_NAMES


def test_deterministic_json_golden_handles_unicode_and_nested_unordered_mappings():
    value = {"z": {"β": [2, 1], "a": "café"}, "a": True}

    payload = serialize_deterministic_json(value)

    assert payload == b'{"a":true,"z":{"a":"caf\xc3\xa9","\xce\xb2":[2,1]}}\n'
    assert hashlib.sha256(payload).hexdigest() == "1884350b8dceab8d425d0af94005786c425e097f3aef8d6c8b206224c7dab16d"


def test_deterministic_json_thaws_immutable_mappings_nested_inside_lists():
    value = {"events": [MappingProxyType({"payload": MappingProxyType({"flag": "GREEN"})})]}

    payload = serialize_deterministic_json(value)

    assert payload == b'{"events":[{"payload":{"flag":"GREEN"}}]}\n'


def test_manifest_writer_settings_preserve_nested_metadata_deterministically():
    settings = {"writer": {"page": {"size": 1048576}}, **dict(DEFAULT_WRITER_SETTINGS)}

    manifest = DatasetManifest(
        "safe", {name: _entry(name) for name in CANONICAL_MANIFEST_TABLE_NAMES}, settings,
        format_version=FORMAT_VERSION_V2, manifest_version=MANIFEST_VERSION_V2,
    )

    assert b'"writer":{"page":{"size":1048576}}' in serialize_manifest(manifest)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: DatasetManifest("id", tuple(_entry(name) for name in CANONICAL_MANIFEST_TABLE_NAMES), DEFAULT_WRITER_SETTINGS, format_version="unknown"),
        lambda: DatasetManifest("id", tuple(_entry(name) for name in CANONICAL_MANIFEST_TABLE_NAMES), DEFAULT_WRITER_SETTINGS, manifest_version=MANIFEST_VERSION_V1),
        lambda: _manifest(tuple(_entry(name) for name in reversed(CANONICAL_MANIFEST_TABLE_NAMES))),
        lambda: _manifest({name: _entry(name) for name in CANONICAL_MANIFEST_TABLE_NAMES[:-1]}),
        lambda: serialize_deterministic_json({"nested": {"bad": math.nan}}),
        lambda: CurrentPointer("../unsafe", _DIGEST),
        lambda: CurrentPointer("safe", _DIGEST, "generations/other/manifest.json"),
        lambda: CurrentPointer("safe", "A" * 64),
        lambda: CurrentPointer("safe", _DIGEST, format_version=FORMAT_VERSION_V1),
        lambda: DatasetManifest(
            "2026-07-15T120000Z-abc",
            {name: _entry(name) for name in CANONICAL_MANIFEST_TABLE_NAMES},
            DEFAULT_WRITER_SETTINGS,
            format_version=FORMAT_VERSION_V1,
            manifest_version=MANIFEST_VERSION_V1,
        ),
    ],
)
def test_models_reject_invalid_versions_paths_table_sets_and_hashes(factory):
    with pytest.raises(ManifestValidationError):
        factory()


def test_manifest_rejects_table_schema_that_does_not_preserve_canonical_tokens():
    with pytest.raises(ManifestValidationError):
        TableManifestEntry(
            "drivers", "tables/drivers.parquet", 0,
            tuple(reversed(schema_tokens_for("drivers", "v2"))), _DIGEST, "b" * 64,
        )


def test_v2_contract_exposes_versioned_manifest_and_table_tokens():
    manifest = DatasetManifest(
        "2026-07-15T120000Z-abc",
        {name: TableManifestEntry(
            name=name,
            path=f"tables/{name}.parquet",
            row_count=0,
            schema=schema_tokens_for_version(name, "v2"),
            logical_sha256=_DIGEST,
            byte_sha256="b" * 64,
        ) for name in CANONICAL_MANIFEST_TABLE_NAMES},
        DEFAULT_WRITER_SETTINGS,
        format_version=FORMAT_VERSION_V2,
        manifest_version=MANIFEST_VERSION_V2,
    )

    assert manifest.schema_token == "canonical-parquet-v2:manifest"
    assert cast(tuple[TableManifestEntry, ...], manifest.tables)[0].schema_token == (
        "canonical-parquet-v2:table:session_metadata"
    )
    assert manifest.to_dict()["format_version"] == FORMAT_VERSION_V2


def test_schema_reference_rejects_v1_identities():
    with pytest.raises(ManifestValidationError, match="only available for canonical-parquet-v2"):
        schema_tokens_for("drivers", "v1")
    with pytest.raises(ManifestValidationError, match="only available for canonical-parquet-v2"):
        schema_tokens_for_version("session_metadata", "v1")


def test_table_entry_rejects_explicit_v1_schema_version():
    with pytest.raises(ManifestValidationError, match="schema_version must be exactly v2"):
        TableManifestEntry(
            name="drivers", path="tables/drivers.parquet", row_count=0,
            schema=schema_tokens_for("drivers", "v2"), logical_sha256=_DIGEST,
            byte_sha256="b" * 64, schema_version="v1",
        )


def test_manifest_rejects_v1_format_and_manifest_pair():
    with pytest.raises(ManifestValidationError, match="must identify canonical-parquet-v2"):
        DatasetManifest(
            "2026-07-15T120000Z-abc",
            {name: _entry(name) for name in CANONICAL_MANIFEST_TABLE_NAMES},
            DEFAULT_WRITER_SETTINGS,
            format_version=FORMAT_VERSION_V1,
            manifest_version=MANIFEST_VERSION_V1,
        )


def test_manifest_rejects_mixed_format_and_manifest_versions():
    with pytest.raises(ManifestValidationError, match="must identify canonical-parquet-v2"):
        DatasetManifest(
            "2026-07-15T120000Z-abc",
            {name: _entry(name) for name in CANONICAL_MANIFEST_TABLE_NAMES},
            DEFAULT_WRITER_SETTINGS,
            format_version=FORMAT_VERSION_V2,
            manifest_version=MANIFEST_VERSION_V1,
        )


def test_manifest_rejects_an_explicit_unknown_table_schema_version():
    with pytest.raises(ManifestValidationError, match="schema_version must be exactly v2"):
        TableManifestEntry(
            name="drivers", path="tables/drivers.parquet", row_count=0,
            schema=schema_tokens_for("drivers", "v2"), logical_sha256=_DIGEST,
            byte_sha256="b" * 64, schema_version="",  # type: ignore[arg-type]
        )


def test_current_pointer_bytes_are_exact_and_manifest_digest_is_not_self_referential():
    manifest = _manifest()
    pointer = CurrentPointer(manifest.generation_id, manifest_sha256(manifest), format_version=FORMAT_VERSION_V2)

    payload = serialize_current_pointer(pointer)

    assert b"manifest_sha256" not in serialize_manifest(manifest)
    assert payload == (
        b'{"format_version":"canonical-parquet-v2","generation_id":"2026-07-15T120000Z-abc",'
        b'"manifest_path":"generations/2026-07-15T120000Z-abc/manifest.json",'
        b'"manifest_sha256":"' + manifest_sha256(manifest).encode() + b'"}\n'
    )


def test_current_pointer_rejects_v1_format_version():
    with pytest.raises(ManifestValidationError, match="canonical-parquet-v2"):
        CurrentPointer("2026-07-15T120000Z-abc", _DIGEST, format_version=FORMAT_VERSION_V1)


def test_parsers_reject_missing_versions_and_preserve_valid_models():
    manifest = _manifest()
    pointer = CurrentPointer(manifest.generation_id, manifest_sha256(manifest), format_version=FORMAT_VERSION_V2)

    assert parse_manifest(serialize_manifest(manifest)) == manifest
    assert parse_current_pointer(serialize_current_pointer(pointer)) == pointer
    with pytest.raises(ManifestValidationError):
        parse_manifest(b'{"generation_id":"safe"}')
    with pytest.raises(ManifestValidationError):
        parse_current_pointer(b'{"generation_id":"safe","manifest_path":"generations/safe/manifest.json","manifest_sha256":"' + _DIGEST.encode() + b'"}')
    with pytest.raises(ManifestValidationError):
        parse_current_pointer(b'{"format_version":"canonical-parquet-v1","generation_id":"safe","manifest_path":"generations/safe/manifest.json","manifest_sha256":"' + _DIGEST.encode() + b'"}\n')


def test_manifest_parser_rejects_the_frozen_v1_golden():
    with pytest.raises(ManifestValidationError, match="must identify canonical-parquet-v2"):
        parse_manifest(_GOLDEN_MANIFEST_V1.read_bytes())


@pytest.mark.parametrize(
    "payload",
    [
        b'{"format_version":"canonical-parquet-v2","manifest_version":true,"generation_id":"safe","tables":[],"writer_settings":{}}\n',
        b'{"format_version":"canonical-parquet-v2","manifest_version":2.0,"generation_id":"safe","tables":[],"writer_settings":{}}\n',
        b'{"generation_id":"safe","format_version":"canonical-parquet-v2","manifest_version":2,"tables":[],"writer_settings":{}}\n',
    ],
)
def test_manifest_parser_rejects_noncanonical_or_noninteger_versions(payload):
    with pytest.raises(ManifestValidationError):
        parse_manifest(payload)


def test_pointer_parser_accepts_deterministic_extension_fields():
    pointer = CurrentPointer("safe", _DIGEST, extensions={"publisher": "test"})

    parsed = parse_current_pointer(serialize_current_pointer(pointer))

    assert parsed == pointer


@pytest.mark.parametrize(
    "payload",
    [
        b'{"format_version":"canonical-parquet-v2","generation_id":"safe","generation_id":"other","manifest_path":"generations/safe/manifest.json","manifest_sha256":"' + _DIGEST.encode() + b'"}\n',
        b'{ "format_version":"canonical-parquet-v2","generation_id":"safe","manifest_path":"generations/safe/manifest.json","manifest_sha256":"' + _DIGEST.encode() + b'"}\n',
    ],
)
def test_pointer_parser_rejects_duplicate_keys_and_noncanonical_bytes(payload):
    with pytest.raises(ManifestValidationError):
        parse_current_pointer(payload)


@pytest.mark.parametrize("generation_id", ["", ".", "..", "../escape", "/absolute", "nested/id", "nested\\id", "nul\x00byte", "has space", "🚗"])
def test_shared_generation_id_grammar_rejects_malformed_ids_at_manifest_and_pointer_boundaries(generation_id: str):
    with pytest.raises(GenerationIdentityError):
        validate_generation_id(generation_id)
    with pytest.raises(ManifestValidationError):
        DatasetManifest(generation_id, {name: _entry(name) for name in CANONICAL_MANIFEST_TABLE_NAMES}, DEFAULT_WRITER_SETTINGS)
    with pytest.raises(ManifestValidationError):
        CurrentPointer(generation_id, _DIGEST)
    pointer_payload = json.dumps({
        "format_version": "canonical-parquet-v2",
        "generation_id": generation_id,
        "manifest_path": f"generations/{generation_id}/manifest.json",
        "manifest_sha256": _DIGEST,
    }).encode()
    with pytest.raises(ManifestValidationError):
        parse_current_pointer(pointer_payload)
