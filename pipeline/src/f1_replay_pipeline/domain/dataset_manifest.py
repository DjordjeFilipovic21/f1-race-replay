"""Immutable, deterministic metadata models for canonical Parquet generations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import PurePosixPath
import re
from types import MappingProxyType
from typing import cast

from f1_replay_pipeline.domain.canonical_schema import CANONICAL_TABLE_SCHEMAS
from f1_replay_pipeline.domain.generation_identity import GenerationIdentityError, validate_generation_id

FORMAT_VERSION = "canonical-parquet-v1"
MANIFEST_VERSION = 1
CANONICAL_MANIFEST_TABLE_NAMES = (
    "session_metadata", "drivers", "car_telemetry", "position_telemetry", "laps",
    "stints", "weather", "track_status_intervals", "race_control_messages", "results",
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_DEFAULT_WRITER_SETTINGS = {
    "use_pyarrow": False,
    "compression": "zstd",
    "compression_level": 3,
    "statistics": "full",
    "row_group_size": 262144,
    "data_page_size": 1048576,
}
DEFAULT_WRITER_SETTINGS: Mapping[str, object] = MappingProxyType(_DEFAULT_WRITER_SETTINGS)


class ManifestValidationError(ValueError):
    """Raised when metadata violates the canonical Parquet v1 contract."""


@dataclass(frozen=True)
class SchemaToken:
    """One ordered canonical column name and declared dtype token."""

    name: str
    dtype: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ManifestValidationError("schema column names must be non-empty strings")
        if not isinstance(self.dtype, str) or not self.dtype.isascii() or not self.dtype:
            raise ManifestValidationError("schema dtype tokens must be non-empty ASCII strings")

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "dtype": self.dtype}


@dataclass(frozen=True)
class TableManifestEntry:
    """Validated metadata for one canonical table artifact."""

    name: str
    path: str
    row_count: int
    schema: tuple[SchemaToken, ...]
    logical_sha256: str
    byte_sha256: str

    def __post_init__(self) -> None:
        _validate_table_name(self.name)
        _validate_table_path(self.name, self.path)
        if isinstance(self.row_count, bool) or not isinstance(self.row_count, int) or self.row_count < 0:
            raise ManifestValidationError("table row_count must be a non-negative integer")
        _validate_sha256("logical_sha256", self.logical_sha256)
        _validate_sha256("byte_sha256", self.byte_sha256)
        _validate_schema(self.name, self.schema)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "path": self.path,
            "row_count": self.row_count,
            "schema": [column.to_dict() for column in self.schema],
            "logical_sha256": self.logical_sha256,
            "byte_sha256": self.byte_sha256,
        }


@dataclass(frozen=True)
class DatasetManifest:
    """The complete v1 generation manifest, normalized to contract table order."""

    generation_id: str
    tables: tuple[TableManifestEntry, ...] | Mapping[str, TableManifestEntry]
    writer_settings: Mapping[str, object]
    format_version: str = FORMAT_VERSION
    manifest_version: int = MANIFEST_VERSION

    def __post_init__(self) -> None:
        _validate_format_versions(self.format_version, self.manifest_version)
        _validate_generation_id(self.generation_id)
        tables = _normalize_tables(self.tables)
        settings = _freeze_json_value(self.writer_settings, "writer_settings")
        _validate_writer_settings(settings)
        object.__setattr__(self, "tables", tables)
        object.__setattr__(self, "writer_settings", settings)

    def to_dict(self) -> dict[str, object]:
        tables = cast(tuple[TableManifestEntry, ...], self.tables)
        return {
            "format_version": self.format_version,
            "manifest_version": self.manifest_version,
            "generation_id": self.generation_id,
            "tables": [table.to_dict() for table in tables],
            "writer_settings": _thaw_json_value(self.writer_settings),
        }


@dataclass(frozen=True)
class CurrentPointer:
    """The sole v1 visibility boundary for a published generation."""

    generation_id: str
    manifest_sha256: str
    manifest_path: str | None = None
    format_version: str = FORMAT_VERSION
    extensions: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if self.format_version != FORMAT_VERSION:
            raise ManifestValidationError("unsupported current-pointer format_version")
        _validate_generation_id(self.generation_id)
        _validate_sha256("manifest_sha256", self.manifest_sha256)
        expected_path = f"generations/{self.generation_id}/manifest.json"
        path = expected_path if self.manifest_path is None else self.manifest_path
        _validate_relative_posix_path(path, "manifest_path")
        if path != expected_path:
            raise ManifestValidationError("manifest_path must identify this generation's manifest.json")
        object.__setattr__(self, "manifest_path", path)
        extensions = {} if self.extensions is None else self.extensions
        if not isinstance(extensions, Mapping) or {"format_version", "generation_id", "manifest_path", "manifest_sha256"} & set(extensions):
            raise ManifestValidationError("current pointer extensions must not replace required fields")
        object.__setattr__(self, "extensions", _freeze_json_value(extensions, "current pointer extensions"))

    def to_dict(self) -> dict[str, object]:
        manifest_path = self.manifest_path
        assert manifest_path is not None
        value: dict[str, object] = {
            "format_version": self.format_version,
            "generation_id": self.generation_id,
            "manifest_path": manifest_path,
            "manifest_sha256": self.manifest_sha256,
        }
        extensions = _thaw_json_value(self.extensions)
        assert isinstance(extensions, dict)
        value.update(extensions)
        return value


def schema_tokens_for(table_name: str) -> tuple[SchemaToken, ...]:
    """Return exact contract dtype tokens in declared canonical column order."""
    _validate_table_name(table_name)
    return tuple(SchemaToken(name, _dtype_token(dtype)) for name, dtype in CANONICAL_TABLE_SCHEMAS[table_name].items())


def serialize_deterministic_json(value: object) -> bytes:
    """Encode JSON metadata with the exact v1 deterministic byte contract."""
    _validate_json_value(value, "value")
    return json.dumps(_thaw_json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"


def serialize_manifest(manifest: DatasetManifest) -> bytes:
    """Return the exact bytes to write and hash for a validated manifest."""
    return serialize_deterministic_json(manifest.to_dict())


def manifest_sha256(manifest: DatasetManifest) -> str:
    """Return the SHA-256 of the exact deterministic manifest bytes."""
    return hashlib.sha256(serialize_manifest(manifest)).hexdigest()


def serialize_current_pointer(pointer: CurrentPointer) -> bytes:
    """Return deterministic current-pointer JSON bytes."""
    return serialize_deterministic_json(pointer.to_dict())


def parse_manifest(payload: bytes) -> DatasetManifest:
    """Parse and fully validate deterministic manifest JSON without filesystem access."""
    value = _parse_json_object(payload, "manifest")
    _require_keys(value, {"format_version", "manifest_version", "generation_id", "tables", "writer_settings"}, "manifest")
    tables_value = value["tables"]
    if not isinstance(tables_value, list):
        raise ManifestValidationError("manifest tables must be an array")
    tables = tuple(_table_entry_from_dict(table) for table in tables_value)
    settings = value["writer_settings"]
    if not isinstance(settings, Mapping):
        raise ManifestValidationError("manifest writer_settings must be an object")
    manifest = DatasetManifest(
        generation_id=cast(str, value["generation_id"]), tables=tables, writer_settings=settings,
        format_version=cast(str, value["format_version"]),
        manifest_version=cast(int, value["manifest_version"]),
    )
    _require_canonical_payload(payload, serialize_manifest(manifest), "manifest")
    return manifest


def parse_current_pointer(payload: bytes) -> CurrentPointer:
    """Parse and fully validate a v1 current-pointer JSON payload."""
    value = _parse_json_object(payload, "current pointer")
    required = {"format_version", "generation_id", "manifest_path", "manifest_sha256"}
    _require_keys(value, required, "current pointer", exact=False)
    pointer = CurrentPointer(
        generation_id=cast(str, value["generation_id"]),
        manifest_sha256=cast(str, value["manifest_sha256"]),
        manifest_path=cast(str, value["manifest_path"]),
        format_version=cast(str, value["format_version"]),
        extensions={key: item for key, item in value.items() if key not in required},
    )
    _require_canonical_payload(payload, serialize_current_pointer(pointer), "current pointer")
    return pointer


def _normalize_tables(
    tables: tuple[TableManifestEntry, ...] | Mapping[str, TableManifestEntry],
) -> tuple[TableManifestEntry, ...]:
    if isinstance(tables, Mapping):
        if set(tables) != set(CANONICAL_MANIFEST_TABLE_NAMES):
            raise ManifestValidationError("manifest must contain exactly the canonical table set")
        ordered = tuple(tables[name] for name in CANONICAL_MANIFEST_TABLE_NAMES)
    elif isinstance(tables, tuple):
        ordered = tables
    else:
        raise ManifestValidationError("manifest tables must be a mapping or tuple")
    if not all(isinstance(entry, TableManifestEntry) for entry in ordered):
        raise ManifestValidationError("manifest tables must contain table manifest entries")
    if tuple(entry.name for entry in ordered) != CANONICAL_MANIFEST_TABLE_NAMES:
        raise ManifestValidationError("manifest tables must use the canonical table order")
    return ordered


def _table_entry_from_dict(value: object) -> TableManifestEntry:
    if not isinstance(value, Mapping):
        raise ManifestValidationError("manifest table entry must be an object")
    _require_keys(value, {"name", "path", "row_count", "schema", "logical_sha256", "byte_sha256"}, "manifest table entry")
    schema_value = value["schema"]
    if not isinstance(schema_value, list):
        raise ManifestValidationError("manifest table schema must be an array")
    schema = tuple(_schema_token_from_dict(token) for token in schema_value)
    return TableManifestEntry(schema=schema, **{key: value[key] for key in value if key != "schema"})


def _schema_token_from_dict(value: object) -> SchemaToken:
    if not isinstance(value, Mapping):
        raise ManifestValidationError("schema token must be an object")
    _require_keys(value, {"name", "dtype"}, "schema token")
    return SchemaToken(**value)


def _parse_json_object(payload: bytes, label: str) -> Mapping[str, object]:
    try:
        value = json.loads(payload, object_pairs_hook=_reject_duplicate_keys)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ManifestValidationError(f"invalid {label} JSON") from error
    if not isinstance(value, Mapping):
        raise ManifestValidationError(f"{label} must be a JSON object")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ManifestValidationError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _require_keys(value: Mapping[str, object], expected: set[str], label: str, *, exact: bool = True) -> None:
    valid = set(value) == expected if exact else expected <= set(value)
    if not valid:
        raise ManifestValidationError(f"{label} must contain exactly: {', '.join(sorted(expected))}")


def _require_canonical_payload(payload: bytes, expected: bytes, label: str) -> None:
    if payload != expected:
        raise ManifestValidationError(f"{label} JSON bytes do not use the deterministic v1 encoding")


def _validate_schema(table_name: str, schema: tuple[SchemaToken, ...]) -> None:
    if not isinstance(schema, tuple) or schema != schema_tokens_for(table_name):
        raise ManifestValidationError(f"{table_name} schema must match the declared canonical schema")


def _validate_writer_settings(settings: object) -> None:
    actual = _thaw_json_value(settings)
    if not isinstance(actual, dict) or not set(_DEFAULT_WRITER_SETTINGS) <= set(actual):
        raise ManifestValidationError("writer_settings must match the canonical Parquet v1 settings")
    if any(type(actual[key]) is not type(expected) or actual[key] != expected for key, expected in _DEFAULT_WRITER_SETTINGS.items()):
        raise ManifestValidationError("writer_settings must match the canonical Parquet v1 settings")


def _validate_format_versions(format_version: object, manifest_version: object) -> None:
    if format_version != FORMAT_VERSION:
        raise ManifestValidationError("unsupported manifest format_version")
    if type(manifest_version) is not int or manifest_version != MANIFEST_VERSION:
        raise ManifestValidationError("unsupported manifest_version")


def _validate_table_name(name: object) -> None:
    if name not in CANONICAL_MANIFEST_TABLE_NAMES:
        raise ManifestValidationError(f"unknown canonical table: {name!r}")


def _validate_generation_id(generation_id: object) -> None:
    try:
        validate_generation_id(generation_id)
    except GenerationIdentityError as error:
        raise ManifestValidationError(str(error)) from error


def _validate_table_path(name: str, path: object) -> None:
    _validate_relative_posix_path(path, "table path")
    if path != f"tables/{name}.parquet":
        raise ManifestValidationError("table path must identify its canonical Parquet artifact")


def _validate_relative_posix_path(path: object, label: str) -> None:
    if not isinstance(path, str) or not path or "\\" in path:
        raise ManifestValidationError(f"{label} must be a non-empty relative POSIX path")
    pure_path = PurePosixPath(path)
    if pure_path.is_absolute() or any(part in {"", ".", ".."} for part in pure_path.parts):
        raise ManifestValidationError(f"{label} must not escape its generation directory")


def _validate_sha256(label: str, digest: object) -> None:
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ManifestValidationError(f"{label} must be 64 lowercase hexadecimal characters")


def _dtype_token(dtype: object) -> str:
    text = str(dtype)
    replacements = {
        "String": "String", "Int16": "Int16", "Int32": "Int32", "Int64": "Int64",
        "Float64": "Float64", "Boolean": "Boolean",
        "Datetime(time_unit='ms', time_zone='UTC')": "Datetime[ms,UTC]",
    }
    try:
        return replacements[text]
    except KeyError as error:
        raise ManifestValidationError(f"unsupported canonical dtype: {text}") from error


def _freeze_json_value(value: object, label: str) -> object:
    _validate_json_value(value, label)
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json_value(item, label) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze_json_value(item, label) for item in value)
    return value


def _thaw_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_thaw_json_value(item) for item in value]
    return value


def _validate_json_value(value: object, label: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ManifestValidationError(f"{label} must not contain NaN or infinity")
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ManifestValidationError(f"{label} object keys must be strings")
        for item in value.values():
            _validate_json_value(item, label)
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            _validate_json_value(item, label)
        return
    raise ManifestValidationError(f"{label} is not JSON metadata")


__all__ = [
    "CANONICAL_MANIFEST_TABLE_NAMES", "DEFAULT_WRITER_SETTINGS", "DatasetManifest",
    "FORMAT_VERSION", "MANIFEST_VERSION", "CurrentPointer", "ManifestValidationError",
    "SchemaToken", "TableManifestEntry", "manifest_sha256", "schema_tokens_for",
    "parse_current_pointer", "parse_manifest", "serialize_current_pointer",
    "serialize_deterministic_json", "serialize_manifest",
]
