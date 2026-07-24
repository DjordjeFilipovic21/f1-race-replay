"""Offline failure-injection coverage for the generation pointer protocol."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import cast

import polars as pl
import pytest

from f1_replay_pipeline.domain.canonical_schema import CANONICAL_TABLE_SCHEMAS
from f1_replay_pipeline.domain.dataset_manifest import (
    DEFAULT_WRITER_SETTINGS,
    DatasetManifest,
    TableManifestEntry,
    schema_tokens_for,
    serialize_manifest,
)
from f1_replay_pipeline.storage.generation_publication import (
    CANONICAL_TABLE_NAMES, FORMAT_VERSION, GenerationPublicationError,
    LocalFilesystem, LocalRecoveryLock, STAGING_PREFIX, recover_stale_staging,
    PublicationCleanupError, PublicationDurabilityUncertainError,
    RecoveryLock, RecoveryOwnershipError, resolve_current_generation, write_generation,
)
from f1_replay_pipeline.domain.logical_hashes import logical_table_sha256
from f1_replay_pipeline.storage.parquet_io import write_canonical_parquet


def _materialize(generation_id: str):
    def materialize(writer):
        tables = []
        for table_name in CANONICAL_TABLE_NAMES:
            schema = dict(CANONICAL_TABLE_SCHEMAS[table_name])
            frame = (
                pl.DataFrame(
                    [{column: "test-session" if column == "session_id" else None for column in schema}],
                    schema=schema,
                )
                if table_name == "session_metadata"
                else pl.DataFrame(schema=schema)
            )
            with tempfile.TemporaryDirectory() as directory:
                temporary = Path(directory) / f"{table_name}.parquet"
                write_canonical_parquet(table_name, frame, temporary)
                payload = temporary.read_bytes()
            path = f"tables/{table_name}.parquet"
            writer.write_bytes(path, payload)
            tables.append(TableManifestEntry(
                name=table_name, path=path, row_count=frame.height,
                schema=schema_tokens_for(table_name),
                logical_sha256=logical_table_sha256(table_name, frame),
                byte_sha256=hashlib.sha256(payload).hexdigest(),
            ))
        return serialize_manifest(DatasetManifest(
            generation_id=generation_id, tables=tuple(tables),
            writer_settings=DEFAULT_WRITER_SETTINGS,
        ))
    return materialize


def _validate_test_manifest(manifest: DatasetManifest, generation_path: Path) -> None:
    """Test seam standing in for a complete domain-specific manifest validator."""
    del generation_path
    assert [entry.name for entry in cast(tuple[TableManifestEntry, ...], manifest.tables)] == list(CANONICAL_TABLE_NAMES)


def _publish(parent: Path, generation_id: str):
    return write_generation(target_parent=parent, generation_id=generation_id, materialize=_materialize(generation_id), validate_manifest=_validate_test_manifest)


def test_publish_uses_same_parent_staging_and_replaces_current_last(tmp_path: Path) -> None:
    result = _publish(tmp_path, "one")

    assert result.pointer_path.is_file()
    assert not list(tmp_path.glob(f"{STAGING_PREFIX}*"))


@pytest.mark.parametrize("failure", ["before_write:tables/session_metadata.parquet", "after_file_fsync:tables/session_metadata.parquet", "after_generation_rename", "before_pointer_replace"])
def test_injected_failures_never_make_an_incomplete_generation_current(tmp_path: Path, failure: str) -> None:
    previous = _publish(tmp_path, "previous")
    previous_pointer = previous.pointer_path.read_bytes()

    def inject(event: str) -> None:
        if event == failure:
            raise OSError(f"injected {event}")

    with pytest.raises(OSError, match="injected"):
        write_generation(target_parent=tmp_path, generation_id="failed", materialize=_materialize("failed"), checkpoint=inject, validate_manifest=_validate_test_manifest)

    assert previous.pointer_path.read_bytes() == previous_pointer


class DirectoryFsyncFailure(LocalFilesystem):
    def fsync_directory(self, path: Path) -> bool:
        raise OSError("injected directory fsync")


def test_directory_fsync_failure_preserves_the_prior_pointer(tmp_path: Path) -> None:
    previous = _publish(tmp_path, "previous")
    previous_pointer = previous.pointer_path.read_bytes()

    with pytest.raises(OSError, match="directory fsync"):
        write_generation(target_parent=tmp_path, generation_id="failed", materialize=_materialize("failed"), filesystem=DirectoryFsyncFailure(), validate_manifest=_validate_test_manifest)

    assert previous.pointer_path.read_bytes() == previous_pointer


def test_cleanup_failure_does_not_hide_the_publication_failure(tmp_path: Path) -> None:
    def inject(event: str) -> None:
        if event in {"before_write:tables/session_metadata.parquet", "before_cleanup:staging"}:
            raise OSError(event)

    with pytest.raises(OSError, match="before_write") as raised:
        write_generation(target_parent=tmp_path, generation_id="failed", materialize=_materialize("failed"), checkpoint=inject, validate_manifest=_validate_test_manifest)

    assert len(raised.value.cleanup_errors) == 1


class FailingFilesystem(LocalFilesystem):
    def __init__(self, operation: str) -> None:
        self.operation = operation

    def _fail(self, operation: str) -> None:
        if self.operation == operation:
            raise OSError(f"injected {operation}")

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        self._fail("mkdir")
        super().mkdir(path, parents=parents, exist_ok=exist_ok)

    def open_exclusive(self, path: Path, flags: int, mode: int) -> int:
        self._fail("exclusive create")
        return super().open_exclusive(path, flags, mode)

    def write_file(self, descriptor: int, data: bytes) -> int:
        self._fail("write")
        return super().write_file(descriptor, data)

    def fsync_file(self, descriptor: int) -> None:
        self._fail("file fsync")
        super().fsync_file(descriptor)

    def replace(self, source: Path, destination: Path) -> None:
        if destination.name == "current.json":
            self._fail("pointer replace")
        self._fail("rename")
        super().replace(source, destination)

    def fsync_directory(self, path: Path) -> bool:
        self._fail("directory fsync")
        return super().fsync_directory(path)

    def remove_tree_at(self, directory_descriptor: int, name: str, identity: tuple[int, int]) -> None:
        self._fail("cleanup tree")
        super().remove_tree_at(directory_descriptor, name, identity)

    def remove_file_at(self, directory_descriptor: int, name: str, identity: tuple[int, int]) -> None:
        self._fail("cleanup file")
        super().remove_file_at(directory_descriptor, name, identity)


@pytest.mark.parametrize("operation", ["mkdir", "write", "file fsync", "directory fsync"])
def test_filesystem_operation_failures_before_commit_preserve_the_prior_pointer(
    tmp_path: Path, operation: str,
) -> None:
    previous = _publish(tmp_path, "previous")
    pointer_before = previous.pointer_path.read_bytes()

    with pytest.raises(OSError, match=f"injected {operation}"):
        write_generation(
            target_parent=tmp_path,
            generation_id="failed",
            materialize=_materialize("failed"),
            filesystem=FailingFilesystem(operation),
            validate_manifest=_validate_test_manifest,
        )

    assert previous.pointer_path.read_bytes() == pointer_before


def test_cleanup_only_failure_is_observable(tmp_path: Path) -> None:
    class CleanupOnlyFailure(LocalFilesystem):
        def remove_tree_at(self, directory_descriptor: int, name: str, identity: tuple[int, int]) -> None:
            raise OSError("injected cleanup tree")

        def remove_file_at(self, directory_descriptor: int, name: str, identity: tuple[int, int]) -> None:
            raise OSError("injected cleanup file")

    def fail_before_pointer(event: str) -> None:
        if event == "before_pointer_replace":
            raise OSError("injected pre-commit failure")

    with pytest.raises(OSError, match="pre-commit") as raised:
        write_generation(
            target_parent=tmp_path,
            generation_id="failed",
            materialize=_materialize("failed"),
            filesystem=CleanupOnlyFailure(),
            checkpoint=fail_before_pointer,
            validate_manifest=_validate_test_manifest,
        )

    assert {str(error) for error in raised.value.cleanup_errors} == {
        "injected cleanup tree",
        "injected cleanup file",
    }


def test_post_commit_directory_fsync_failure_reports_committed_durability_uncertainty(tmp_path: Path) -> None:
    class PostCommitFsyncFailure(LocalFilesystem):
        def __init__(self) -> None:
            self.target_parent_calls = 0

        def fsync_directory(self, path: Path) -> bool:
            if path == tmp_path:
                self.target_parent_calls += 1
                if self.target_parent_calls == 3:
                    raise OSError("injected post-commit fsync")
            return super().fsync_directory(path)

    _publish(tmp_path, "previous")

    with pytest.raises(PublicationDurabilityUncertainError, match="durability is uncertain") as raised:
        write_generation(
            target_parent=tmp_path,
            generation_id="next",
            materialize=_materialize("next"),
            filesystem=PostCommitFsyncFailure(),
            validate_manifest=_validate_test_manifest,
        )

    assert raised.value.result.committed
    assert raised.value.result.directory_fsyncs[-1].outcome == "failed"
    assert json.loads((tmp_path / "current.json").read_bytes())["generation_id"] == "next"


def test_ambiguous_pointer_replace_reports_the_observed_commit_as_uncertain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_replace = os.replace

    def replace_then_fail(source: str, destination: str, *args: object, **kwargs: object) -> None:
        original_replace(source, destination, *args, **kwargs)
        if destination == "current.json":
            raise OSError("injected ambiguous pointer replace")

    monkeypatch.setattr(os, "replace", replace_then_fail)

    with pytest.raises(PublicationDurabilityUncertainError) as raised:
        write_generation(
            target_parent=tmp_path,
            generation_id="next",
            materialize=_materialize("next"),
            validate_manifest=_validate_test_manifest,
        )

    assert raised.value.result.committed
    assert json.loads((tmp_path / "current.json").read_bytes())["generation_id"] == "next"


def test_successful_commit_reports_cleanup_failure(tmp_path: Path) -> None:
    def fail_cleanup(event: str) -> None:
        if event == "before_cleanup:staging":
            raise OSError("injected successful-publication cleanup")

    with pytest.raises(PublicationCleanupError, match="cleanup failures") as raised:
        write_generation(
            target_parent=tmp_path,
            generation_id="next",
            materialize=_materialize("next"),
            checkpoint=fail_cleanup,
            validate_manifest=_validate_test_manifest,
        )

    assert raised.value.committed
    assert json.loads((tmp_path / "current.json").read_bytes())["generation_id"] == "next"


def test_recovery_removes_only_known_staging_and_rejects_corrupt_pointer(tmp_path: Path) -> None:
    previous = _publish(tmp_path, "previous")
    stale = tmp_path / f"{STAGING_PREFIX}orphan"
    stale.mkdir()
    protected = tmp_path / ".not-our-staging"
    protected.mkdir()
    (tmp_path / "current.json").write_text("{}")

    assert recover_stale_staging(tmp_path, validate_manifest=_validate_test_manifest) is None
    assert not stale.exists()
    assert protected.exists()
    assert previous.generation_path.exists()


class _Lease:
    def __init__(self, release_error: BaseException | None = None) -> None:
        self.release_error = release_error
        self.released = False

    def release(self) -> None:
        self.released = True
        if self.release_error is not None:
            raise self.release_error


class _RecoveryLock:
    def __init__(self, lease: _Lease | None = None, acquire_error: BaseException | None = None) -> None:
        self.lease = lease or _Lease()
        self.acquire_error = acquire_error
        self.calls = 0

    def acquire(self, target_parent: Path) -> _Lease:
        self.calls += 1
        if self.acquire_error is not None:
            raise self.acquire_error
        return self.lease


@pytest.mark.parametrize("ownership_error", [
    RecoveryOwnershipError("another publisher has an active lease"),
    RecoveryOwnershipError("ownership metadata is malformed"),
    OSError("ownership acquisition failed"),
])
def test_recovery_never_deletes_staging_when_ownership_is_active_malformed_or_unavailable(
    tmp_path: Path, ownership_error: BaseException,
) -> None:
    stale = tmp_path / f"{STAGING_PREFIX}another-publisher"
    stale.mkdir()

    with pytest.raises(RecoveryOwnershipError):
        recover_stale_staging(tmp_path, recovery_lock=_RecoveryLock(acquire_error=ownership_error))

    assert stale.is_dir()


def test_recovery_contention_allows_only_the_owner_to_remove_staging(tmp_path: Path) -> None:
    stale = tmp_path / f"{STAGING_PREFIX}orphan"
    stale.mkdir()
    owner = _RecoveryLock()

    assert recover_stale_staging(tmp_path, recovery_lock=owner) is None
    assert owner.lease.released
    assert not stale.exists()

    competing_stale = tmp_path / f"{STAGING_PREFIX}active-publisher"
    competing_stale.mkdir()
    with pytest.raises(RecoveryOwnershipError):
        recover_stale_staging(
            tmp_path,
            recovery_lock=_RecoveryLock(acquire_error=RecoveryOwnershipError("lock held")),
        )
    assert competing_stale.is_dir()


def test_active_writer_ownership_blocks_recovery_without_deleting_its_staging(tmp_path: Path) -> None:
    active_staging = tmp_path / f"{STAGING_PREFIX}active-writer"
    active_staging.mkdir()
    active_writer = LocalRecoveryLock()

    active_lease = active_writer.acquire(tmp_path)
    try:
        with pytest.raises(RecoveryOwnershipError):
            recover_stale_staging(tmp_path)
    finally:
        active_lease.release()

    assert active_staging.is_dir()


def test_recovery_rejects_an_unverifiable_acquired_lease_without_deleting_staging(tmp_path: Path) -> None:
    class MalformedLock:
        def acquire(self, target_parent: Path) -> None:
            return None

    stale = tmp_path / f"{STAGING_PREFIX}orphan"
    stale.mkdir()

    with pytest.raises(RecoveryOwnershipError, match="unverifiable"):
        recover_stale_staging(tmp_path, recovery_lock=cast(RecoveryLock, MalformedLock()))

    assert stale.is_dir()


def test_recovery_never_follows_a_prefix_matching_staging_symlink(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "sentinel"
    sentinel.write_bytes(b"unchanged")
    staging_link = tmp_path / f"{STAGING_PREFIX}link"
    staging_link.symlink_to(external, target_is_directory=True)

    assert recover_stale_staging(tmp_path) is None
    assert staging_link.is_symlink()
    assert sentinel.read_bytes() == b"unchanged"


def test_recovery_releases_ownership_when_cleanup_fails(tmp_path: Path) -> None:
    class CleanupFailure(LocalFilesystem):
        def remove_tree_at(self, directory_descriptor: int, name: str, identity: tuple[int, int]) -> None:
            raise OSError("cleanup failed")

    stale = tmp_path / f"{STAGING_PREFIX}orphan"
    stale.mkdir()
    lock = _RecoveryLock()

    with pytest.raises(GenerationPublicationError, match="unable to remove"):
        recover_stale_staging(tmp_path, filesystem=CleanupFailure(), recovery_lock=lock)

    assert lock.lease.released


def test_recovery_exposes_every_staging_cleanup_failure(tmp_path: Path) -> None:
    class CleanupFailure(LocalFilesystem):
        def remove_tree_at(self, directory_descriptor: int, name: str, identity: tuple[int, int]) -> None:
            raise OSError(f"cleanup failed: {name}")

    first = tmp_path / f"{STAGING_PREFIX}first"
    second = tmp_path / f"{STAGING_PREFIX}second"
    first.mkdir()
    second.mkdir()

    with pytest.raises(GenerationPublicationError, match="unable to remove") as raised:
        recover_stale_staging(tmp_path, filesystem=CleanupFailure())

    assert {str(error) for error in raised.value.cleanup_errors} == {
        f"cleanup failed: {first.name}", f"cleanup failed: {second.name}",
    }


def test_recovery_preserves_staging_cleanup_errors_when_lease_release_also_fails(tmp_path: Path) -> None:
    class CleanupFailure(LocalFilesystem):
        def remove_tree_at(self, directory_descriptor: int, name: str, identity: tuple[int, int]) -> None:
            raise OSError("staging cleanup failed")

    stale = tmp_path / f"{STAGING_PREFIX}orphan"
    stale.mkdir()
    lock = _RecoveryLock(_Lease(OSError("lease release failed")))

    with pytest.raises(GenerationPublicationError, match="unable to remove") as raised:
        recover_stale_staging(tmp_path, filesystem=CleanupFailure(), recovery_lock=lock)

    assert {str(error) for error in raised.value.cleanup_errors} == {
        "staging cleanup failed", "unable to release verifiable recovery ownership",
    }


def test_recovery_aggregates_arbitrary_cleanup_and_release_exceptions(tmp_path: Path) -> None:
    class CleanupFailure(LocalFilesystem):
        def remove_tree_at(self, directory_descriptor: int, name: str, identity: tuple[int, int]) -> None:
            raise RuntimeError(f"cleanup failed: {name}")

    for name in ("first", "second"):
        (tmp_path / f"{STAGING_PREFIX}{name}").mkdir()
    lock = _RecoveryLock(_Lease(TypeError("lease release failed")))

    with pytest.raises(GenerationPublicationError, match="unable to remove") as raised:
        recover_stale_staging(tmp_path, filesystem=CleanupFailure(), recovery_lock=lock)

    assert {type(error) for error in raised.value.cleanup_errors} == {
        RuntimeError, RecoveryOwnershipError,
    }


def test_recovery_release_failure_is_reported_after_no_active_staging_is_deleted(tmp_path: Path) -> None:
    stale = tmp_path / f"{STAGING_PREFIX}orphan"
    stale.mkdir()
    lock = _RecoveryLock(_Lease(OSError("release failed")))

    with pytest.raises(RecoveryOwnershipError, match="release"):
        recover_stale_staging(tmp_path, recovery_lock=lock)

    assert lock.lease.released
    assert not stale.exists()


@pytest.mark.parametrize("generation_id", ["", ".", "..", "../escape", "/absolute", "nested/id", "nested\\id", "nul\x00byte", "has space", "🚗"])
def test_rejects_malformed_generation_ids_before_creating_filesystem_paths(tmp_path: Path, generation_id: str) -> None:
    target = tmp_path / "not-created"

    with pytest.raises(GenerationPublicationError, match="safe path component"):
        _publish(target, generation_id)

    assert not target.exists()


def test_rejects_existing_generation(tmp_path: Path) -> None:
    _publish(tmp_path, "one")
    with pytest.raises(GenerationPublicationError, match="overwrite"):
        _publish(tmp_path, "one")


class UnsupportedDirectoryFsync(LocalFilesystem):
    def fsync_directory(self, path: Path) -> bool:
        return False


def test_unsupported_directory_fsync_is_explicitly_degraded(tmp_path: Path) -> None:
    result = write_generation(target_parent=tmp_path, generation_id="one", materialize=_materialize("one"), filesystem=UnsupportedDirectoryFsync(), validate_manifest=_validate_test_manifest)

    assert result.pointer_path.is_file()
    assert result.directory_fsyncs
    assert {status.outcome for status in result.directory_fsyncs} == {"unsupported"}


def test_generic_resolution_and_recovery_use_complete_canonical_validation(tmp_path: Path) -> None:
    published = _publish(tmp_path, "one")
    (published.generation_path / "tables" / "drivers.parquet").write_bytes(b"tampered")

    with pytest.raises(GenerationPublicationError, match="canonical generation validation failed"):
        resolve_current_generation(tmp_path, validate_manifest=lambda *_: None)
    assert recover_stale_staging(tmp_path, validate_manifest=lambda *_: None) is None


@pytest.mark.parametrize("location", ["root", "generations", "selected_generation", "tables", "manifest", "current"])
def test_symlinked_publication_topology_fails_closed_without_touching_external_paths(
    tmp_path: Path, location: str,
) -> None:
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "sentinel"
    sentinel.write_bytes(b"unchanged")
    root = tmp_path / "dataset"
    previous = _publish(root, "previous")
    pointer_before = previous.pointer_path.read_bytes()

    if location == "root":
        root.rename(tmp_path / "real-dataset")
        root.symlink_to(tmp_path / "real-dataset", target_is_directory=True)
        operation = lambda: _publish(root, "next")
    elif location == "generations":
        (root / "generations").rename(external / "generations")
        (root / "generations").symlink_to(external / "generations", target_is_directory=True)
        operation = lambda: _publish(root, "next")
    elif location == "selected_generation":
        (root / "generations" / "previous").rename(external / "previous")
        (root / "generations" / "previous").symlink_to(external / "previous", target_is_directory=True)
        operation = lambda: resolve_current_generation(root, validate_manifest=_validate_test_manifest)
    elif location == "tables":
        tables = root / "generations" / "previous" / "tables"
        tables.rename(external / "tables")
        tables.symlink_to(external / "tables", target_is_directory=True)
        operation = lambda: resolve_current_generation(root, validate_manifest=_validate_test_manifest)
    elif location == "manifest":
        manifest = root / "generations" / "previous" / "manifest.json"
        manifest.unlink()
        manifest.symlink_to(sentinel)
        operation = lambda: resolve_current_generation(root, validate_manifest=_validate_test_manifest)
    else:
        (root / "current.json").unlink()
        (root / "current.json").symlink_to(sentinel)
        operation = lambda: resolve_current_generation(root, validate_manifest=_validate_test_manifest)

    with pytest.raises((GenerationPublicationError, OSError)):
        operation()

    assert sentinel.read_bytes() == b"unchanged"
    if location in {"generations", "selected_generation", "tables", "manifest", "current"}:
        assert previous.pointer_path.read_bytes() == pointer_before or previous.pointer_path.is_symlink()


def test_publication_rejects_an_escaping_symlink_ancestor_without_writing_external_path(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "sentinel"
    sentinel.write_bytes(b"unchanged")
    escape = tmp_path / "escape"
    escape.symlink_to(external, target_is_directory=True)

    with pytest.raises(GenerationPublicationError, match="publication root"):
        _publish(escape / "dataset", "one")

    assert sentinel.read_bytes() == b"unchanged"
    assert not (external / "dataset").exists()


class PointerTempCollisionFilesystem(LocalFilesystem):
    def __init__(self) -> None:
        self.calls = 0

    def open_exclusive(self, path: Path, flags: int, mode: int) -> int:
        self.calls += 1
        assert flags & os.O_CREAT
        assert flags & os.O_EXCL
        if self.calls == 1:
            path.write_bytes(b"collision")
            raise FileExistsError(path)
        return super().open_exclusive(path, flags, mode)


def test_pointer_temporary_retries_exclusive_creation_collision_and_preserves_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = _publish(tmp_path, "previous")
    names = iter((f"{STAGING_PREFIX}pointer-collision", f"{STAGING_PREFIX}pointer-retry"))
    (tmp_path / f"{STAGING_PREFIX}pointer-collision").write_bytes(b"collision")

    result = write_generation(
        target_parent=tmp_path,
        generation_id="next",
        materialize=_materialize("next"),
        validate_manifest=_validate_test_manifest,
        pointer_temp_name=lambda: next(names),
    )

    assert result.generation_path.name == "next"
    assert (tmp_path / f"{STAGING_PREFIX}pointer-collision").read_bytes() == b"collision"
    assert previous.generation_path.exists()


def test_staging_file_writes_remain_in_the_retained_directory_after_parent_swap(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    external = tmp_path / "external"
    external.mkdir()
    moved = tmp_path / "moved-dataset"

    def swap_parent(event: str) -> None:
        if event == "before_write:tables/session_metadata.parquet":
            root.rename(moved)
            root.symlink_to(external, target_is_directory=True)

    with pytest.raises(GenerationPublicationError):
        write_generation(
            target_parent=root,
            generation_id="swapped",
            materialize=_materialize("swapped"),
            validate_manifest=_validate_test_manifest,
            checkpoint=swap_parent,
        )

    assert not list(external.iterdir())


def test_publication_cleanup_and_ambiguous_commit_reconciliation_stay_on_retained_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "dataset"
    external = tmp_path / "external"
    external.mkdir()
    staging_name = f"{STAGING_PREFIX}matching"
    pointer_name = f"{STAGING_PREFIX}pointer-matching"
    (external / staging_name).mkdir()
    (external / staging_name / "sentinel").write_bytes(b"staging unchanged")
    (external / "current.json").write_bytes(b"{}")
    moved = tmp_path / "moved-dataset"
    original_replace = os.replace

    def replace_then_swap(source: str, destination: str, *args: object, **kwargs: object) -> None:
        original_replace(source, destination, *args, **kwargs)
        if destination == "current.json":
            (external / pointer_name).write_bytes(b"pointer temp unchanged")
            root.rename(moved)
            root.symlink_to(external, target_is_directory=True)
            raise OSError("injected ambiguous pointer replace")

    monkeypatch.setattr(os, "replace", replace_then_swap)

    with pytest.raises(PublicationDurabilityUncertainError) as raised:
        write_generation(
            target_parent=root,
            generation_id="next",
            materialize=_materialize("next"),
            pointer_temp_name=lambda: pointer_name,
            validate_manifest=_validate_test_manifest,
        )

    assert raised.value.result.committed
    assert json.loads((moved / "current.json").read_bytes())["generation_id"] == "next"
    assert (external / staging_name / "sentinel").read_bytes() == b"staging unchanged"
    assert (external / pointer_name).read_bytes() == b"pointer temp unchanged"
    assert (external / "current.json").read_bytes() == b"{}"


def test_publication_cleanup_does_not_delete_matching_external_staging_or_pointer_temp(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    external = tmp_path / "external"
    external.mkdir()
    pointer_name = f"{STAGING_PREFIX}pointer-temp"
    moved = tmp_path / "moved-dataset"
    staging_name: str | None = None

    def inject(event: str) -> None:
        nonlocal staging_name
        if event == "after_mkdtemp:staging":
            staging_name = next(root.glob(f"{STAGING_PREFIX}*")).name
            (external / staging_name).mkdir()
            (external / staging_name / "sentinel").write_bytes(b"staging unchanged")
            (external / "current.json").write_bytes(b"external current")
        elif event == "before_pointer_replace":
            raise OSError("injected pre-commit failure")
        elif event == "before_cleanup:staging":
            root.rename(moved)
            root.symlink_to(external, target_is_directory=True)
            (external / pointer_name).write_bytes(b"pointer unchanged")

    with pytest.raises(OSError, match="pre-commit"):
        write_generation(
            target_parent=root,
            generation_id="next",
            materialize=_materialize("next"),
            checkpoint=inject,
            pointer_temp_name=lambda: pointer_name,
            validate_manifest=_validate_test_manifest,
        )

    assert staging_name is not None
    assert not (moved / staging_name).exists()
    assert not (moved / pointer_name).exists()
    assert (external / staging_name / "sentinel").read_bytes() == b"staging unchanged"
    assert (external / pointer_name).read_bytes() == b"pointer unchanged"
    assert (external / "current.json").read_bytes() == b"external current"


def test_recovery_cleanup_stays_on_retained_parent_when_root_is_swapped(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    root.mkdir()
    stale_name = f"{STAGING_PREFIX}orphan"
    (root / stale_name).mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (external / stale_name).mkdir()
    (external / stale_name / "sentinel").write_bytes(b"unchanged")
    (external / f"{STAGING_PREFIX}pointer-temp").write_bytes(b"unchanged")
    (external / "current.json").write_bytes(b'{"external":true}')
    moved = tmp_path / "moved-dataset"

    class SwapDuringCleanup(LocalFilesystem):
        def remove_tree_at(self, directory_descriptor: int, name: str, identity: tuple[int, int]) -> None:
            root.rename(moved)
            root.symlink_to(external, target_is_directory=True)
            super().remove_tree_at(directory_descriptor, name, identity)

    with pytest.raises(GenerationPublicationError, match="changed during recovery"):
        recover_stale_staging(root, filesystem=SwapDuringCleanup())

    assert not (moved / stale_name).exists()
    assert (external / stale_name / "sentinel").read_bytes() == b"unchanged"
    assert (external / f"{STAGING_PREFIX}pointer-temp").read_bytes() == b"unchanged"
    assert (external / "current.json").read_bytes() == b'{"external":true}'
