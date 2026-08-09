"""Deterministic direct tests for recovery ownership and lease boundaries.

These tests exercise the LocalRecoveryLock and LocalFilesystem seams directly,
covering the fail-closed integrity boundaries that the higher-level recovery
tests only reach indirectly: a second concurrent acquire, release hygiene
(descriptor closed, lock file retained), malformed or unavailable ownership,
verified directory identity before staging removal, and recovery that never
alters a corrupt or v1 current pointer.  V2 is the sole supported contract;
the frozen v1 pointer identity is used only to prove the selection boundary
rejects it.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import tempfile
from typing import cast

import polars as pl
import pytest

from f1_replay_pipeline.domain.canonical_schema import CANONICAL_TABLE_SCHEMAS_V2
from f1_replay_pipeline.domain.dataset_manifest import (
    DEFAULT_WRITER_SETTINGS,
    DatasetManifest, MANIFEST_VERSION_V2, TableManifestEntry,
    schema_tokens_for, serialize_manifest,
)
from f1_replay_pipeline.domain.logical_hashes import logical_table_sha256
from f1_replay_pipeline.storage.generation_publication import (
    CANONICAL_TABLE_NAMES, FORMAT_VERSION_V1, FORMAT_VERSION_V2,
    GenerationPublicationError, GenerationPublicationResult, LocalFilesystem,
    LocalRecoveryLock, RecoveryOwnershipError, STAGING_PREFIX,
    deterministic_pointer_bytes, recover_stale_staging, write_generation,
)
from f1_replay_pipeline.storage.parquet_io import write_canonical_parquet


_RECOVERY_LOCK_FILE_NAME = ".canonical-parquet-recovery.lock"


def _materialize(generation_id: str):
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


def _validate_test_manifest(manifest: DatasetManifest, generation_path: Path) -> None:
    """Test seam standing in for a complete domain-specific manifest validator."""
    del generation_path
    assert manifest.format_version == FORMAT_VERSION_V2
    assert manifest.manifest_version == MANIFEST_VERSION_V2
    assert [entry.name for entry in cast(tuple[TableManifestEntry, ...], manifest.tables)] == list(CANONICAL_TABLE_NAMES)


def _publish(parent: Path, generation_id: str) -> GenerationPublicationResult:
    return write_generation(
        target_parent=parent,
        generation_id=generation_id,
        materialize=_materialize(generation_id),
        validate_manifest=_validate_test_manifest,
    )


def test_local_recovery_lock_second_concurrent_acquire_raises_ownership_error(tmp_path: Path) -> None:
    lock = LocalRecoveryLock()
    lease = lock.acquire(tmp_path)
    try:
        with pytest.raises(RecoveryOwnershipError, match="already held"):
            lock.acquire(tmp_path)
    finally:
        lease.release()


def test_local_recovery_lock_release_closes_descriptor_and_retains_lock_file(tmp_path: Path) -> None:
    lock_path = tmp_path / _RECOVERY_LOCK_FILE_NAME
    lease = LocalRecoveryLock().acquire(tmp_path)
    assert lock_path.is_file()

    lease.release()

    # release must close the descriptor; the retained lock file stays in place
    # so release never unlinks a replacement owner's pathname.  The descriptor
    # is read dynamically because the acquire seam is typed as RecoveryLease.
    descriptor = getattr(lease, "descriptor")
    with pytest.raises(OSError):
        os.fstat(descriptor)
    assert lock_path.is_file()

    # the retained file remains immediately re-lockable by the next owner.
    reacquired = LocalRecoveryLock().acquire(tmp_path)
    try:
        assert lock_path.is_file()
    finally:
        reacquired.release()
    assert lock_path.is_file()


def test_recovery_lock_rejects_non_regular_lock_file_as_malformed_ownership(tmp_path: Path) -> None:
    lock_path = tmp_path / _RECOVERY_LOCK_FILE_NAME
    os.mkfifo(lock_path)

    with pytest.raises(RecoveryOwnershipError, match="malformed"):
        LocalRecoveryLock().acquire(tmp_path)

    assert stat.S_ISFIFO(lock_path.stat().st_mode)


def test_recovery_lock_rejects_symlinked_lock_file_without_following(tmp_path: Path) -> None:
    external = tmp_path / "external-lock-target"
    external.write_bytes(b"sentinel")
    lock_path = tmp_path / _RECOVERY_LOCK_FILE_NAME
    lock_path.symlink_to(external)

    with pytest.raises(RecoveryOwnershipError):
        LocalRecoveryLock().acquire(tmp_path)

    assert lock_path.is_symlink()
    assert external.read_bytes() == b"sentinel"


def test_recovery_lock_fails_closed_when_fcntl_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("f1_replay_pipeline.storage.generation_publication.fcntl", None)

    with pytest.raises(RecoveryOwnershipError, match="unsupported"):
        LocalRecoveryLock().acquire(tmp_path)

    # fail closed before touching the filesystem: no lock file is ever created.
    assert not (tmp_path / _RECOVERY_LOCK_FILE_NAME).exists()


def test_recovery_staging_removal_requires_verified_directory_identity(tmp_path: Path) -> None:
    class SwapStagingBeforeRemoval(LocalFilesystem):
        def __init__(self) -> None:
            self._swapped = False

        def remove_tree_at(self, directory_descriptor: int, name: str, identity: tuple[int, int]) -> None:
            if not self._swapped:
                self._swapped = True
                original = tmp_path / name
                original.rename(tmp_path / f"{name}-moved")
                replacement = tmp_path / name
                replacement.mkdir()
                (replacement / "sentinel").write_bytes(b"replacement")
            super().remove_tree_at(directory_descriptor, name, identity)

    stale = tmp_path / f"{STAGING_PREFIX}orphan"
    stale.mkdir()
    (stale / "sentinel").write_bytes(b"original")

    with pytest.raises(GenerationPublicationError, match="unable to remove") as raised:
        recover_stale_staging(tmp_path, filesystem=SwapStagingBeforeRemoval())

    # recover_stale_staging attaches cleanup_errors dynamically through its
    # _attach_cleanup_errors seam, so read it through the same dynamic typed
    # boundary the lock-descriptor seam above uses.
    cleanup_errors = cast(
        tuple[BaseException, ...],
        getattr(raised.value, "cleanup_errors"),
    )
    assert "owned staging directory changed before cleanup" in {
        str(error) for error in cleanup_errors
    }
    swapped_in = tmp_path / f"{STAGING_PREFIX}orphan"
    assert (swapped_in / "sentinel").read_bytes() == b"replacement"


def test_recovery_with_corrupt_current_pointer_fails_closed_without_altering_pointer(tmp_path: Path) -> None:
    corrupt_bytes = b"{not-valid-json"
    (tmp_path / "current.json").write_bytes(corrupt_bytes)

    assert recover_stale_staging(tmp_path) is None
    assert (tmp_path / "current.json").read_bytes() == corrupt_bytes


def test_recovery_with_v1_current_pointer_fails_closed_without_altering_pointer(tmp_path: Path) -> None:
    published = _publish(tmp_path, "previous")
    v1_pointer = deterministic_pointer_bytes(
        "previous", published.manifest_sha256, format_version=FORMAT_VERSION_V1,
    )
    (tmp_path / "current.json").write_bytes(v1_pointer)

    # a frozen v1 pointer is structurally parseable but the selection boundary
    # rejects the mixed version: recovery fails closed and leaves it untouched.
    assert recover_stale_staging(tmp_path) is None
    assert (tmp_path / "current.json").read_bytes() == v1_pointer
    assert (tmp_path / "generations" / "previous").exists()
