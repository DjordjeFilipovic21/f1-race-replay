"""Deterministic direct tests for symlink and path-traversal defenses.

Existing coverage (verified, not duplicated here):

- ``test_generation_publication.py``: symlinked publication root, generations
  directory, selected generation, tables *directory*, manifest leaf, and
  current-pointer leaf all fail closed without touching external paths
  (``test_symlinked_publication_topology_fails_closed_without_touching_external_paths``);
  an escaping symlink ancestor is rejected before any mutation; a prefix-matching
  staging symlink is never followed during recovery cleanup.
- ``test_canonical_writer.py``: the public API rejects a symlinked publication
  root on both publish and resolve without touching external data.

Gaps covered here (only demonstrable gaps, no duplicated assertions):

- No-follow **table file** reads: a symlinked ``tables/<name>.parquet`` leaf is
  rejected by ``resolve_current_generation`` while the external target stays
  untouched (only the tables *directory* symlink was covered before).
- ``_safe_relative_path`` grammar: absolute paths, ``..``, ``.``, backslashes,
  and NUL bytes all fail closed with ``GenerationPublicationError``.
- ``StagedGenerationWriter.write_bytes`` traversal rejection before any
  filesystem mutation, with explicit no-external-path assertions.
- Manifest/table path validation in ``TableManifestEntry`` and ``parse_manifest``:
  absolute paths, ``..``, ``.``, backslashes, and NUL bytes fail closed.
- Unsupported no-follow capability: when ``os.O_NOFOLLOW`` is unavailable the
  capability guard rejects file and directory no-follow reads before any open,
  leaving the external symlink target untouched.

All identities are canonical-parquet-v2; canonical Parquet files are never
touched (every fixture is written under pytest ``tmp_path``).
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import polars as pl
import pytest

from f1_replay_pipeline.domain.canonical_schema import CANONICAL_TABLE_SCHEMAS_V2
from f1_replay_pipeline.domain.dataset_manifest import (
    DEFAULT_WRITER_SETTINGS,
    DatasetManifest,
    FORMAT_VERSION_V2,
    MANIFEST_VERSION_V2,
    ManifestValidationError,
    TableManifestEntry,
    parse_manifest,
    schema_tokens_for,
    serialize_manifest,
)
from f1_replay_pipeline.domain.logical_hashes import logical_table_sha256
from f1_replay_pipeline.storage.generation_publication import (
    CANONICAL_TABLE_NAMES,
    GenerationPublicationError,
    LocalFilesystem,
    StagedGenerationWriter,
    _safe_relative_path,
    read_regular_file_no_follow,
    resolve_current_generation,
    write_generation,
)
from f1_replay_pipeline.storage.parquet_io import write_canonical_parquet


_DIGEST = "a" * 64


def _materialize(generation_id: str):
    """Materialize a complete valid v2 generation through the staging writer."""

    def materialize(writer: StagedGenerationWriter) -> bytes:
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
            tables.append(TableManifestEntry(
                name=table_name, path=path, row_count=frame.height,
                schema=schema_tokens_for(table_name, "v2"),
                logical_sha256=logical_table_sha256(table_name, frame, version="v2"),
                byte_sha256=hashlib.sha256(payload).hexdigest(),
            ))
        return serialize_manifest(DatasetManifest(
            generation_id=generation_id, tables=tuple(tables),
            writer_settings=DEFAULT_WRITER_SETTINGS,
            format_version=FORMAT_VERSION_V2,
            manifest_version=MANIFEST_VERSION_V2,
        ))

    return materialize


def _publish(parent: Path, generation_id: str):
    """Publish one complete v2 generation; canonical validation is the gate."""
    return write_generation(
        target_parent=parent,
        generation_id=generation_id,
        materialize=_materialize(generation_id),
        validate_manifest=lambda _manifest, _path: None,
    )


def _manifest_payload_with_table_path(path: str) -> bytes:
    """Deterministic v2 manifest bytes with one table path replaced."""
    tables = tuple(
        TableManifestEntry(
            name=name, path=f"tables/{name}.parquet", row_count=0,
            schema=schema_tokens_for(name, "v2"),
            logical_sha256=_DIGEST, byte_sha256="b" * 64,
        )
        for name in CANONICAL_TABLE_NAMES
    )
    manifest = DatasetManifest(
        generation_id="2026-08-09T120000Z-abc", tables=tables,
        writer_settings=DEFAULT_WRITER_SETTINGS,
        format_version=FORMAT_VERSION_V2,
        manifest_version=MANIFEST_VERSION_V2,
    )
    payload = json.loads(serialize_manifest(manifest))
    payload["tables"][0]["path"] = path
    return json.dumps(payload).encode()


def test_no_follow_table_read_rejects_a_symlinked_table_file_without_touching_external_data(
    tmp_path: Path,
) -> None:
    """A symlinked table *file* leaf fails closed on the no-follow read.

    Existing coverage only symlinked the tables directory; this exercises the
    leaf-level no-follow open in ``read_regular_file_no_follow``.
    """
    published = _publish(tmp_path, "one")
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "sentinel"
    sentinel.write_bytes(b"unchanged")
    table_leaf = published.generation_path / "tables" / "session_metadata.parquet"
    table_leaf.unlink()
    table_leaf.symlink_to(sentinel)

    with pytest.raises(GenerationPublicationError, match="canonical generation validation failed"):
        resolve_current_generation(tmp_path)

    assert sentinel.read_bytes() == b"unchanged"
    assert table_leaf.is_symlink()


def test_unsupported_no_follow_capability_rejects_reads_without_touching_external_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing ``os.O_NOFOLLOW`` rejects no-follow reads instead of degrading.

    On a platform without the flag the capability guard must fail closed before
    any open, so a symlinked file or directory is never followed to its external
    target when no-follow opens cannot be enforced.
    """
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "sentinel"
    sentinel.write_bytes(b"unchanged")
    target = tmp_path / "target"
    target.mkdir()
    symlink_file = target / "current.json"
    symlink_file.symlink_to(sentinel)
    symlink_directory = tmp_path / "linked-directory"
    symlink_directory.symlink_to(external, target_is_directory=True)

    monkeypatch.setattr(
        "f1_replay_pipeline.storage.generation_publication._NO_FOLLOW_SUPPORTED", False,
    )

    with pytest.raises(GenerationPublicationError, match="unsupported on this platform"):
        read_regular_file_no_follow(symlink_file, "current pointer")
    with pytest.raises(GenerationPublicationError, match="unsupported on this platform"):
        resolve_current_generation(symlink_directory)

    # fail closed before touching the filesystem: the external target and both
    # symlinks are exactly as arranged.
    assert sentinel.read_bytes() == b"unchanged"
    assert symlink_file.is_symlink()
    assert symlink_directory.is_symlink()


@pytest.mark.parametrize(
    ("value", "match"),
    [
        ("/absolute", "escapes its generation"),
        ("../escape", "escapes its generation"),
        ("a/../b", "escapes its generation"),
        (".", "escapes its generation"),
        ("a\\b", "safe relative POSIX path"),
        ("nul\x00byte", "safe relative POSIX path"),
    ],
)
def test_safe_relative_path_rejects_absolute_traversal_dot_windows_and_nul_paths(
    value: str, match: str,
) -> None:
    """The shared staging-path grammar rejects every escape vector."""
    with pytest.raises(GenerationPublicationError, match=match):
        _safe_relative_path(value)


@pytest.mark.parametrize(
    ("bad_path", "escaped_target"),
    [
        ("../escape", "escape"),
        ("tables/../escape.parquet", "escape.parquet"),
        ("tables\\escape.parquet", "escape.parquet"),
        ("tables/escape\x00.parquet", "escape.parquet"),
    ],
)
def test_staged_writer_rejects_traversal_paths_without_creating_external_files(
    tmp_path: Path, bad_path: str, escaped_target: str,
) -> None:
    """Staged writes reject traversal paths before any filesystem mutation."""
    staging = tmp_path / "staging"
    staging.mkdir()
    writer = StagedGenerationWriter(staging, LocalFilesystem(), lambda _event: None)
    try:
        with pytest.raises(GenerationPublicationError):
            writer.write_bytes(bad_path, b"payload")
    finally:
        writer.close()

    assert not (staging / escaped_target).exists()
    assert not (tmp_path / escaped_target).exists()


def test_staged_writer_rejects_a_dot_relative_path(tmp_path: Path) -> None:
    """A bare ``.`` cannot name a staged file; nothing new is written."""
    staging = tmp_path / "staging"
    staging.mkdir()
    writer = StagedGenerationWriter(staging, LocalFilesystem(), lambda _event: None)
    try:
        with pytest.raises(GenerationPublicationError):
            writer.write_bytes(".", b"payload")
    finally:
        writer.close()

    assert {entry.name for entry in staging.iterdir()} == {"tables"}


@pytest.mark.parametrize("path", [
    "/absolute/escape.parquet",
    "../escape.parquet",
    "tables/../escape.parquet",
    "tables/escape.parquet/..",
    ".",
    "tables\\escape.parquet",
])
def test_table_entry_path_validation_rejects_traversal_dot_and_windows_paths(path: str) -> None:
    """Table paths that escape the generation or use foreign separators fail closed."""
    with pytest.raises(ManifestValidationError, match="table path"):
        TableManifestEntry(
            name="drivers", path=path, row_count=0,
            schema=schema_tokens_for("drivers", "v2"),
            logical_sha256=_DIGEST, byte_sha256="b" * 64,
        )


def test_table_entry_path_validation_rejects_nul_bytes() -> None:
    """A NUL table path cannot construct a manifest entry.

    The public ``TableManifestEntry`` boundary rejects the NUL path with
    ``ManifestValidationError`` (a ``ValueError`` subclass) using the same
    ``table path`` message as the other invalid-path cases.  The defense is
    binary: no manifest model can be built from a NUL path, so no path can be
    escaped.
    """
    with pytest.raises(ManifestValidationError, match="table path"):
        TableManifestEntry(
            name="drivers", path="tables/escape\x00.parquet", row_count=0,
            schema=schema_tokens_for("drivers", "v2"),
            logical_sha256=_DIGEST, byte_sha256="b" * 64,
        )


def test_manifest_parser_rejects_a_table_path_that_escapes_the_generation() -> None:
    """The manifest parse boundary rejects traversal table paths."""
    with pytest.raises(ManifestValidationError, match="table path"):
        parse_manifest(_manifest_payload_with_table_path("../escape.parquet"))
