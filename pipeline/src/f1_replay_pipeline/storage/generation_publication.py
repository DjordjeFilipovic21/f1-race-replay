"""Atomic, filesystem-only publication for canonical dataset generations.

The module deliberately knows neither Polars nor the manifest model.  Callers
materialize validated table bytes through :class:`StagedGenerationWriter` and
may inject their manifest validator for schema, row-count, and logical-hash
validation.  This keeps the pointer/recovery boundary independently testable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import errno
try:
    import fcntl
except ImportError:  # pragma: no cover - unavailable on non-POSIX platforms
    fcntl = None
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from typing import Literal, Protocol, cast
import uuid

from f1_replay_pipeline.domain.dataset_manifest import (
    DatasetManifest,
    ManifestValidationError,
    parse_current_pointer,
)
from f1_replay_pipeline.domain.generation_identity import GenerationIdentityError, validate_generation_id


FORMAT_VERSION = "canonical-parquet-v1"
STAGING_PREFIX = ".canonical-parquet-staging-"
CANONICAL_TABLE_NAMES = (
    "session_metadata", "drivers", "car_telemetry", "position_telemetry", "laps",
    "stints", "weather", "track_status_intervals", "race_control_messages", "results",
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_UNSUPPORTED_DIRECTORY_FSYNC = {errno.EINVAL, errno.ENOTSUP, errno.EBADF}
_NO_FOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_POINTER_TEMP_ATTEMPTS = 16
_STAGING_DIRECTORY_ATTEMPTS = 16
_RECOVERY_LOCK_NAME = ".canonical-parquet-recovery.lock"


class GenerationPublicationError(RuntimeError):
    """Raised when a generation cannot be safely published or selected."""


class PublicationDurabilityUncertainError(GenerationPublicationError):
    """The current-pointer commit happened, but its final durability is unknown."""

    def __init__(self, result: "GenerationPublicationResult", cause: BaseException) -> None:
        super().__init__("current.json was replaced, but post-commit durability is uncertain")
        self.result = result
        self.committed = True
        self.cause = cause


class PublicationCommittedError(GenerationPublicationError):
    """The commit and requested durability completed; a later callback failed."""

    def __init__(self, result: "GenerationPublicationResult", cause: BaseException) -> None:
        super().__init__("current.json was replaced and fsynced, but a post-commit callback failed")
        self.result = result
        self.committed = True
        self.durability_confirmed = True
        self.cause = cause


class PublicationCleanupError(GenerationPublicationError):
    """No publication error occurred, but one or more temporary paths remain."""

    def __init__(
        self,
        cleanup_errors: tuple[BaseException, ...],
        result: "GenerationPublicationResult | None" = None,
    ) -> None:
        super().__init__("publication completed with cleanup failures")
        self.cleanup_errors = cleanup_errors
        self.result = result
        self.committed = result is not None


class RecoveryOwnershipError(GenerationPublicationError):
    """Stale-staging recovery could not prove exclusive ownership."""


@dataclass(frozen=True)
class GuardedFile:
    """A regular-file snapshot and the identity of its guarded directory entry."""

    data: bytes
    device: int
    inode: int


class Filesystem(Protocol):
    """Injectable filesystem operations used by publication and recovery."""

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None: ...
    def mkdtemp(self, *, prefix: str, directory: Path) -> Path: ...
    def replace(self, source: Path, destination: Path) -> None: ...
    def remove_tree(self, path: Path) -> None: ...
    def remove_file(self, path: Path) -> None: ...
    def remove_tree_at(self, directory_descriptor: int, name: str, identity: tuple[int, int]) -> None: ...
    def remove_file_at(self, directory_descriptor: int, name: str, identity: tuple[int, int]) -> None: ...
    def fsync_file(self, descriptor: int) -> None: ...
    def fsync_directory(self, path: Path) -> bool: ...
    def open_exclusive(self, path: Path, flags: int, mode: int) -> int: ...
    def write_file(self, descriptor: int, data: bytes) -> int: ...


class RecoveryLease(Protocol):
    """An acquired recovery ownership token that must be released."""

    def release(self) -> None: ...


class RecoveryLock(Protocol):
    """Injectable exclusive recovery ownership boundary.

    Implementations return a lease only after exclusive ownership is established
    and fail closed for competing, active, malformed, or unverifiable ownership.
    """

    def acquire(self, target_parent: Path) -> RecoveryLease: ...


@dataclass(frozen=True)
class _LocalRecoveryLease:
    descriptor: int

    def release(self) -> None:
        try:
            assert fcntl is not None
            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
        except OSError as error:
            raise RecoveryOwnershipError("unable to release recovery lock") from error
        finally:
            os.close(self.descriptor)


class LocalRecoveryLock:
    """A conservative exclusive lock for one recovery operation.

    The descriptor remains advisory-locked for the entire operation.  A crashed
    process releases the kernel lock; an active process blocks recovery.  The
    lock file is intentionally retained, so release never unlinks another
    owner's replacement pathname.
    """

    def acquire(self, target_parent: Path) -> RecoveryLease:
        path = target_parent / _RECOVERY_LOCK_NAME
        if fcntl is None:
            raise RecoveryOwnershipError("exclusive recovery locking is unsupported")
        flags = os.O_RDWR | os.O_CREAT | _NO_FOLLOW
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as error:
            raise RecoveryOwnershipError("unable to acquire recovery lock") from error
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise RecoveryOwnershipError("recovery lock ownership is malformed")
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            try:
                os.close(descriptor)
            finally:
                pass
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                raise RecoveryOwnershipError("recovery lock is already held") from error
            raise RecoveryOwnershipError("unable to acquire recovery lock") from error
        except BaseException:
            os.close(descriptor)
            raise
        return _LocalRecoveryLease(descriptor)


def _acquire_recovery_lease(lock: RecoveryLock, target_parent: Path) -> RecoveryLease:
    try:
        lease = lock.acquire(target_parent)
    except RecoveryOwnershipError:
        raise
    except BaseException as error:
        raise RecoveryOwnershipError("unable to acquire verifiable recovery ownership") from error
    if not callable(getattr(lease, "release", None)):
        raise RecoveryOwnershipError("recovery lock returned unverifiable ownership")
    return lease


class LocalFilesystem:
    """Production filesystem seam; false means directory fsync is unsupported."""

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        path.mkdir(parents=parents, exist_ok=exist_ok)

    def mkdtemp(self, *, prefix: str, directory: Path) -> Path:
        return Path(tempfile.mkdtemp(prefix=prefix, dir=directory))

    def replace(self, source: Path, destination: Path) -> None:
        os.replace(source, destination)

    def remove_tree(self, path: Path) -> None:
        shutil.rmtree(path)

    def remove_file(self, path: Path) -> None:
        path.unlink()

    def remove_tree_at(self, directory_descriptor: int, name: str, identity: tuple[int, int]) -> None:
        _remove_owned_tree_at(directory_descriptor, name, identity)

    def remove_file_at(self, directory_descriptor: int, name: str, identity: tuple[int, int]) -> None:
        _remove_owned_file_at(directory_descriptor, name, identity)

    def fsync_file(self, descriptor: int) -> None:
        os.fsync(descriptor)

    def fsync_directory(self, path: Path) -> bool:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as error:
            if error.errno in _UNSUPPORTED_DIRECTORY_FSYNC:
                return False
            raise
        try:
            os.fsync(descriptor)
        except OSError as error:
            if error.errno in _UNSUPPORTED_DIRECTORY_FSYNC:
                return False
            raise
        finally:
            os.close(descriptor)
        return True

    def open_exclusive(self, path: Path, flags: int, mode: int) -> int:
        return os.open(path, flags, mode)

    def write_file(self, descriptor: int, data: bytes) -> int:
        return os.write(descriptor, data)


@dataclass(frozen=True)
class GenerationPublicationResult:
    generation_path: Path
    manifest_path: Path
    pointer_path: Path
    manifest_sha256: str
    committed: bool = True
    directory_fsyncs: tuple[DirectoryFsyncStatus, ...] = ()


@dataclass(frozen=True)
class DirectoryFsyncStatus:
    """One attempted directory durability boundary and its explicit outcome."""

    path: Path
    outcome: Literal["succeeded", "unsupported", "failed"]


ManifestValidator = Callable[[DatasetManifest, Path], None]
Checkpoint = Callable[[str], None]


def _noop(_: str) -> None:
    return None


def _safe_generation_id(generation_id: object) -> str:
    try:
        return validate_generation_id(generation_id)
    except GenerationIdentityError as error:
        raise GenerationPublicationError(str(error)) from error


def _require_manifest_validator(validate_manifest: object) -> ManifestValidator:
    if not callable(validate_manifest):
        raise GenerationPublicationError("a complete manifest validator is required")
    return cast(ManifestValidator, validate_manifest)


def _safe_relative_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise GenerationPublicationError("metadata path must be a safe relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise GenerationPublicationError("metadata path escapes its generation")
    return path


def _absolute_path(path: Path) -> Path:
    """Return an absolute lexical path without resolving a possible symlink."""
    return Path(os.path.abspath(path))


def _open_directory_no_follow(path: Path) -> int:
    """Open every directory component without permitting a symlink traversal."""
    absolute = _absolute_path(path)
    descriptor = os.open(absolute.anchor, os.O_RDONLY | _DIRECTORY)
    try:
        for component in absolute.parts[1:]:
            child = os.open(component, os.O_RDONLY | _DIRECTORY | _NO_FOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _verify_directory_entry(directory_descriptor: int, name: str, identity: tuple[int, int]) -> None:
    """Prove a retained directory entry still names the trusted directory."""
    try:
        metadata = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except (OSError, TypeError) as error:
        raise GenerationPublicationError("unable to revalidate trusted directory entry") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino) != identity
    ):
        raise GenerationPublicationError("trusted directory entry changed before commit")


def _verify_directory_path_identity(path: Path, identity: tuple[int, int], label: str) -> None:
    """Ensure a lexical path still names the retained directory before reopening it."""
    try:
        descriptor = _open_directory_no_follow(path)
    except OSError as error:
        raise GenerationPublicationError(f"{label} changed during recovery") from error
    try:
        metadata = os.fstat(descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    os.close(descriptor)
    if not stat.S_ISDIR(metadata.st_mode) or (metadata.st_dev, metadata.st_ino) != identity:
        raise GenerationPublicationError(f"{label} changed during recovery")


def _require_safe_directory(path: Path, label: str) -> Path:
    try:
        descriptor = _open_directory_no_follow(path)
    except OSError as error:
        raise GenerationPublicationError(f"{label} must be a non-symlink directory") from error
    os.close(descriptor)
    return _absolute_path(path)


def _require_safe_existing_ancestors(path: Path, label: str) -> None:
    """Reject a symlink before creating a missing descendant below it."""
    absolute = _absolute_path(path)
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            return
        except OSError as error:
            raise GenerationPublicationError(f"{label} has an inaccessible ancestor") from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise GenerationPublicationError(f"{label} has a symlinked or non-directory ancestor")


def _open_regular_file_no_follow(path: Path, label: str) -> int:
    """Open one regular file through a no-follow parent descriptor.

    Opening the parent and leaf relative to that descriptor closes the usual
    inspect-then-open race for pointer, manifest, and table reads.
    """
    try:
        parent = _open_directory_no_follow(path.parent)
        try:
            descriptor = os.open(path.name, os.O_RDONLY | _NO_FOLLOW, dir_fd=parent)
        finally:
            os.close(parent)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise GenerationPublicationError(f"{label} must be a regular non-symlink file")
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise
    except GenerationPublicationError:
        raise
    except OSError as error:
        raise GenerationPublicationError(f"invalid {label}") from error


def read_regular_file_no_follow(path: Path, label: str) -> GuardedFile:
    """Read one regular file once through no-follow descriptors.

    Consumers must derive all checks and parsing from ``data`` rather than
    reopening ``path``.  ``verify_regular_file_identity`` detects a replacement
    before the caller accepts that snapshot.
    """
    descriptor = _open_regular_file_no_follow(path, label)
    with os.fdopen(descriptor, "rb") as source:
        metadata = os.fstat(source.fileno())
        return GuardedFile(source.read(), metadata.st_dev, metadata.st_ino)


def verify_regular_file_identity(path: Path, guarded: GuardedFile, label: str) -> None:
    """Fail closed when a guarded path no longer names the validated file."""
    descriptor = _open_regular_file_no_follow(path, label)
    try:
        metadata = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (metadata.st_dev, metadata.st_ino) != (guarded.device, guarded.inode):
        raise GenerationPublicationError(f"{label} changed during guarded validation")


def _read_regular_file_at(directory_descriptor: int, name: str, label: str) -> GuardedFile:
    """Read a regular file from a retained directory descriptor exactly once."""
    try:
        descriptor = os.open(name, os.O_RDONLY | _NO_FOLLOW, dir_fd=directory_descriptor)
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise GenerationPublicationError(f"{label} must be a regular non-symlink file")
            return GuardedFile(source.read(), metadata.st_dev, metadata.st_ino)
    except GenerationPublicationError:
        raise
    except OSError as error:
        raise GenerationPublicationError(f"invalid {label}") from error


def _verify_regular_file_identity_at(
    directory_descriptor: int, name: str, guarded: GuardedFile, label: str,
) -> None:
    try:
        metadata = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except (OSError, TypeError) as error:
        raise GenerationPublicationError(f"unable to revalidate {label}") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino) != (guarded.device, guarded.inode)
    ):
        raise GenerationPublicationError(f"{label} changed during guarded validation")


def _entry_metadata_at(directory_descriptor: int, name: str) -> os.stat_result:
    return os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)


def _remove_owned_file_at(directory_descriptor: int, name: str, identity: tuple[int, int]) -> None:
    """Unlink only the exact regular file created by this publisher."""
    metadata = _entry_metadata_at(directory_descriptor, name)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino) != identity
    ):
        raise GenerationPublicationError("owned pointer temporary changed before cleanup")
    os.unlink(name, dir_fd=directory_descriptor)


def _remove_owned_tree_at(directory_descriptor: int, name: str, identity: tuple[int, int]) -> None:
    """Remove one owned tree without resolving names outside its retained parent."""
    metadata = _entry_metadata_at(directory_descriptor, name)
    if not stat.S_ISDIR(metadata.st_mode) or (metadata.st_dev, metadata.st_ino) != identity:
        raise GenerationPublicationError("owned staging directory changed before cleanup")
    try:
        descriptor = os.open(name, os.O_RDONLY | _DIRECTORY | _NO_FOLLOW, dir_fd=directory_descriptor)
    except (OSError, TypeError) as error:
        raise GenerationPublicationError("descriptor-relative staging cleanup is unavailable") from error
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != identity:
            raise GenerationPublicationError("owned staging directory changed before cleanup")
        _remove_tree_contents_at(descriptor)
    finally:
        os.close(descriptor)
    _verify_directory_entry(directory_descriptor, name, identity)
    os.rmdir(name, dir_fd=directory_descriptor)


def _remove_tree_contents_at(directory_descriptor: int) -> None:
    """Recursively remove entries below an already verified owned directory."""
    try:
        names = os.listdir(directory_descriptor)
    except (OSError, TypeError) as error:
        raise GenerationPublicationError("descriptor-relative staging cleanup is unavailable") from error
    for name in names:
        metadata = _entry_metadata_at(directory_descriptor, name)
        if stat.S_ISDIR(metadata.st_mode):
            try:
                child = os.open(name, os.O_RDONLY | _DIRECTORY | _NO_FOLLOW, dir_fd=directory_descriptor)
            except (OSError, TypeError) as error:
                raise GenerationPublicationError("staging cleanup encountered an unsafe directory") from error
            try:
                opened = os.fstat(child)
                if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                    raise GenerationPublicationError("staging cleanup directory changed during removal")
                _remove_tree_contents_at(child)
            finally:
                os.close(child)
            current = _entry_metadata_at(directory_descriptor, name)
            if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise GenerationPublicationError("staging cleanup directory changed during removal")
            os.rmdir(name, dir_fd=directory_descriptor)
        else:
            os.unlink(name, dir_fd=directory_descriptor)


def _read_regular_file_no_follow(path: Path, label: str) -> bytes:
    return read_regular_file_no_follow(path, label).data


def _sha256_file(path: Path, label: str = "generation file") -> str:
    return hashlib.sha256(read_regular_file_no_follow(path, label).data).hexdigest()


def _generation_file(generation_path: Path, relative_path: PurePosixPath) -> Path:
    """Build a metadata path after verifying its directory topology."""
    candidate = generation_path.joinpath(*relative_path.parts)
    _require_safe_directory(generation_path, "selected generation")
    _require_safe_directory(candidate.parent, "generation file parent")
    return candidate


def _write_open_fsynced(
    descriptor: int, data: bytes, filesystem: Filesystem, checkpoint: Checkpoint, label: str,
) -> None:
    checkpoint(f"before_write:{label}")
    with os.fdopen(descriptor, "wb") as destination:
        _write_all(destination.fileno(), data, filesystem)
        destination.flush()
        filesystem.fsync_file(destination.fileno())
    checkpoint(f"after_file_fsync:{label}")


def _write_all(descriptor: int, data: bytes, filesystem: Filesystem) -> None:
    """Write all bytes through the seam so short writes and failures stay testable."""
    written = 0
    while written < len(data):
        count = filesystem.write_file(descriptor, data[written:])
        if not isinstance(count, int) or count <= 0:
            raise OSError("filesystem write returned no progress")
        written += count


def _fsync_directory(
    path: Path,
    label: str,
    filesystem: Filesystem,
    checkpoint: Checkpoint,
    statuses: list[DirectoryFsyncStatus],
    *,
    checkpoint_after: bool = True,
) -> None:
    checkpoint(f"before_directory_fsync:{label}")
    try:
        supported = filesystem.fsync_directory(path)
    except OSError as error:
        statuses.append(DirectoryFsyncStatus(path, "failed"))
        if error.errno in {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}:
            raise GenerationPublicationError(f"directory topology changed before fsync: {label}") from error
        raise
    except BaseException:
        statuses.append(DirectoryFsyncStatus(path, "failed"))
        raise
    statuses.append(DirectoryFsyncStatus(path, "succeeded" if supported else "unsupported"))
    if checkpoint_after:
        checkpoint(f"after_directory_fsync:{label}")


def _pointer_temporary(
    target_parent: Path,
    target_parent_descriptor: int,
    name_factory: Callable[[], str],
) -> tuple[Path, int, tuple[int, int]]:
    """Create a private pointer temp beside ``current.json`` without replacement."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NO_FOLLOW
    for _ in range(_POINTER_TEMP_ATTEMPTS):
        name = name_factory()
        if (
            not isinstance(name, str)
            or not name
            or name in {".", ".."}
            or "\x00" in name
            or Path(name).name != name
        ):
            raise GenerationPublicationError("pointer temporary name must be one safe component")
        path = target_parent / name
        try:
            descriptor = os.open(name, flags, 0o600, dir_fd=target_parent_descriptor)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                os.close(descriptor)
                raise GenerationPublicationError("pointer temporary must be a regular file")
            return path, descriptor, (metadata.st_dev, metadata.st_ino)
        except FileExistsError:
            continue
    raise GenerationPublicationError("unable to create an exclusive pointer temporary")


def _pointer_selects(
    target_parent_descriptor: int, generation_id: str, manifest_sha256: str,
) -> bool:
    """Conservatively reconcile an ambiguous replacement result at the commit point."""
    try:
        pointer_file = _read_regular_file_at(target_parent_descriptor, "current.json", "current pointer")
        pointer = parse_current_pointer(pointer_file.data)
        _verify_regular_file_identity_at(
            target_parent_descriptor, "current.json", pointer_file, "current pointer",
        )
        return (
            pointer.generation_id == generation_id
            and pointer.manifest_sha256 == manifest_sha256
            and pointer.manifest_path == f"generations/{generation_id}/manifest.json"
        )
    except BaseException:
        return False


def _create_staging_directory(target_parent: Path, target_parent_descriptor: int) -> Path:
    """Create a private staging directory through the retained publication-root FD."""
    for _ in range(_STAGING_DIRECTORY_ATTEMPTS):
        name = f"{STAGING_PREFIX}{uuid.uuid4().hex}"
        try:
            os.mkdir(name, mode=0o700, dir_fd=target_parent_descriptor)
            return target_parent / name
        except FileExistsError:
            continue
        except (OSError, TypeError) as error:
            raise GenerationPublicationError("descriptor-relative staging creation is unavailable") from error
    raise GenerationPublicationError("unable to exclusively create staging directory")


class StagedGenerationWriter:
    """Writes only safe, fully-fsynced files below one staging generation."""

    def __init__(
        self,
        staging_path: Path,
        filesystem: Filesystem,
        checkpoint: Checkpoint,
        directory_fsyncs: list[DirectoryFsyncStatus] | None = None,
    ) -> None:
        self._staging_path = staging_path
        self._filesystem = filesystem
        self._checkpoint = checkpoint
        self._directory_fsyncs = directory_fsyncs if directory_fsyncs is not None else []
        try:
            self._staging_descriptor = _open_directory_no_follow(staging_path)
            metadata = os.fstat(self._staging_descriptor)
            self._staging_identity = (metadata.st_dev, metadata.st_ino)
            os.mkdir("tables", mode=0o700, dir_fd=self._staging_descriptor)
            self._tables_descriptor = os.open(
                "tables", os.O_RDONLY | _DIRECTORY | _NO_FOLLOW, dir_fd=self._staging_descriptor,
            )
            tables_metadata = os.fstat(self._tables_descriptor)
            if not stat.S_ISDIR(tables_metadata.st_mode):
                raise GenerationPublicationError("staging tables must be a directory")
            self._tables_identity = (tables_metadata.st_dev, tables_metadata.st_ino)
        except (OSError, GenerationPublicationError) as error:
            self.close()
            raise GenerationPublicationError("unable to retain trusted staging directories") from error

    def close(self) -> None:
        for attribute in ("_tables_descriptor", "_staging_descriptor"):
            descriptor = getattr(self, attribute, None)
            if isinstance(descriptor, int):
                os.close(descriptor)
                setattr(self, attribute, None)

    def _verify_directories(self) -> None:
        staging = os.fstat(self._staging_descriptor)
        tables = os.fstat(self._tables_descriptor)
        if (
            (staging.st_dev, staging.st_ino) != self._staging_identity
            or (tables.st_dev, tables.st_ino) != self._tables_identity
            or not stat.S_ISDIR(staging.st_mode)
            or not stat.S_ISDIR(tables.st_mode)
        ):
            raise GenerationPublicationError("trusted staging directory changed during write")

    def write_bytes(self, relative_path: str, data: bytes) -> Path:
        relative = _safe_relative_path(relative_path)
        if relative.name == "" or not isinstance(data, bytes):
            raise GenerationPublicationError("staged files require a non-empty path and bytes")
        if relative.parts == ("manifest.json",):
            parent_descriptor = self._staging_descriptor
            parent_path = self._staging_path
        elif relative.parts[:1] == ("tables",) and len(relative.parts) == 2:
            parent_descriptor = self._tables_descriptor
            parent_path = self._staging_path / "tables"
        else:
            raise GenerationPublicationError("staged paths must be manifest.json or direct tables/ children")
        self._verify_directories()
        temporary_name = f".{relative.name}.staging-{uuid.uuid4().hex}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NO_FOLLOW
        try:
            descriptor = os.open(temporary_name, flags, 0o600, dir_fd=parent_descriptor)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise GenerationPublicationError("staging temporary must be a regular file")
        except (OSError, GenerationPublicationError) as error:
            raise GenerationPublicationError("unable to exclusively create staging temporary") from error
        _write_open_fsynced(descriptor, data, self._filesystem, self._checkpoint, relative.as_posix())
        self._checkpoint(f"before_rename:{relative.as_posix()}")
        self._verify_directories()
        try:
            os.replace(
                temporary_name, relative.name,
                src_dir_fd=parent_descriptor, dst_dir_fd=parent_descriptor,
            )
        except (OSError, TypeError) as error:
            raise GenerationPublicationError("descriptor-relative staging replacement is unavailable") from error
        self._checkpoint(f"after_rename:{relative.as_posix()}")
        _fsync_directory(
            parent_path,
            f"staging_{relative.parent.as_posix().replace('/', '_')}",
            self._filesystem,
            self._checkpoint,
            self._directory_fsyncs,
        )
        return parent_path / relative.name


def deterministic_pointer_bytes(generation_id: str, manifest_sha256: str) -> bytes:
    """Return the exact bytes written to ``current.json`` for a generation."""
    generation_id = _safe_generation_id(generation_id)
    _require_sha256(manifest_sha256, "manifest_sha256")
    pointer = {
        "format_version": FORMAT_VERSION,
        "generation_id": generation_id,
        "manifest_path": f"generations/{generation_id}/manifest.json",
        "manifest_sha256": manifest_sha256,
    }
    return json.dumps(pointer, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"


def _require_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise GenerationPublicationError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


def validate_generation(
    generation_path: Path,
    *,
    validate_manifest: ManifestValidator,
    expected_generation_id: str | None = None,
    expected_manifest_sha256: str | None = None,
    require_path_name_match: bool = True,
) -> DatasetManifest:
    """Apply the sole complete-generation decision validator once per selection."""
    validate_manifest = _require_manifest_validator(validate_manifest)
    expected_generation_id = _safe_generation_id(
        generation_path.name if expected_generation_id is None else expected_generation_id
    )
    from f1_replay_pipeline.storage.canonical_generation_validation import validate_complete_canonical_generation

    try:
        manifest = validate_complete_canonical_generation(
            generation_path,
            expected_generation_id=expected_generation_id,
            expected_manifest_sha256=expected_manifest_sha256,
            require_path_name_match=require_path_name_match,
        )
    except (GenerationPublicationError, ManifestValidationError, ValueError) as error:
        raise GenerationPublicationError("canonical generation validation failed") from error
    validate_manifest(manifest, generation_path)
    return manifest


def resolve_current_generation(target_parent: Path, *, validate_manifest: ManifestValidator | None = None) -> GenerationPublicationResult:
    """Resolve only a pointer and generation whose complete integrity validates."""
    target_parent = _require_safe_directory(target_parent, "publication root")
    pointer_path = target_parent / "current.json"
    pointer_file = read_regular_file_no_follow(pointer_path, "current pointer")
    try:
        pointer = parse_current_pointer(pointer_file.data)
    except ManifestValidationError as error:
        raise GenerationPublicationError("invalid current pointer") from error
    generation_id = _safe_generation_id(pointer.generation_id)
    manifest_sha256 = _require_sha256(pointer.manifest_sha256, "manifest_sha256")
    generation_path = target_parent / "generations" / generation_id
    _require_safe_directory(target_parent / "generations", "generations directory")
    _require_safe_directory(generation_path, "selected generation")
    manifest_path = _generation_file(generation_path, PurePosixPath("manifest.json"))
    manifest = validate_generation(
        generation_path,
        validate_manifest=lambda _manifest, _path: None,
        expected_generation_id=generation_id,
        expected_manifest_sha256=manifest_sha256,
    )
    if validate_manifest is not None:
        _require_manifest_validator(validate_manifest)(manifest, generation_path)
    verify_regular_file_identity(pointer_path, pointer_file, "current pointer")
    return GenerationPublicationResult(generation_path, manifest_path, pointer_path, manifest_sha256)


def recover_stale_staging(
    target_parent: Path,
    *,
    filesystem: Filesystem | None = None,
    validate_manifest: ManifestValidator | None = None,
    recovery_lock: RecoveryLock | None = None,
) -> GenerationPublicationResult | None:
    """Recover under exclusive ownership, or fail closed without deleting.

    The optional lock seam supports deployments with a verifiable lease.  A
    lock acquisition or release failure is an ownership ambiguity, so no caller
    should treat recovery as successful in that case.
    """
    if validate_manifest is not None:
        validate_manifest = _require_manifest_validator(validate_manifest)
    filesystem = filesystem or LocalFilesystem()
    target_parent = _require_safe_directory(target_parent, "publication root")
    try:
        target_parent_descriptor = _open_directory_no_follow(target_parent)
        metadata = os.fstat(target_parent_descriptor)
    except OSError as error:
        raise GenerationPublicationError("publication root is unavailable for recovery") from error
    target_parent_identity = (metadata.st_dev, metadata.st_ino)
    recovery_lock = recovery_lock or LocalRecoveryLock()
    lease: RecoveryLease | None = None
    try:
        lease = _acquire_recovery_lease(recovery_lock, target_parent)
        _verify_directory_path_identity(target_parent, target_parent_identity, "publication root")
    except BaseException:
        try:
            if lease is not None:
                lease.release()
        finally:
            os.close(target_parent_descriptor)
        raise
    cleanup_errors: list[BaseException] = []
    result: GenerationPublicationResult | None = None
    primary_error: BaseException | None = None
    try:
        try:
            candidate_names = os.listdir(target_parent_descriptor)
        except (OSError, TypeError) as error:
            raise GenerationPublicationError("descriptor-relative recovery cleanup is unavailable") from error
        for candidate_name in candidate_names:
            try:
                candidate_stat = _entry_metadata_at(target_parent_descriptor, candidate_name)
            except Exception as error:
                cleanup_errors.append(error)
                continue
            if stat.S_ISDIR(candidate_stat.st_mode) and candidate_name.startswith(STAGING_PREFIX):
                try:
                    filesystem.remove_tree_at(
                        target_parent_descriptor,
                        candidate_name,
                        (candidate_stat.st_dev, candidate_stat.st_ino),
                    )
                except Exception as error:
                    cleanup_errors.append(error)
        if cleanup_errors:
            cleanup_error = GenerationPublicationError("unable to remove stale staging directories")
            _attach_cleanup_errors(cleanup_error, cleanup_errors)
            raise cleanup_error from cleanup_errors[0]
        try:
            _verify_directory_path_identity(target_parent, target_parent_identity, "publication root")
        except GenerationPublicationError:
            raise
        try:
            result = resolve_current_generation(target_parent, validate_manifest=validate_manifest)
        except GenerationPublicationError:
            result = None
    except BaseException as error:
        primary_error = error
    try:
        lease.release()
    except BaseException as error:
        release_error = error if isinstance(error, RecoveryOwnershipError) else RecoveryOwnershipError(
            "unable to release verifiable recovery ownership"
        )
        if primary_error is not None:
            _attach_cleanup_errors(primary_error, [release_error])
        else:
            raise release_error from error
    finally:
        os.close(target_parent_descriptor)
    if primary_error is not None:
        raise primary_error
    return result


def write_generation(
    *,
    target_parent: Path,
    generation_id: str,
    materialize: Callable[[StagedGenerationWriter], bytes],
    filesystem: Filesystem | None = None,
    validate_manifest: ManifestValidator,
    checkpoint: Checkpoint | None = None,
    pointer_temp_name: Callable[[], str] | None = None,
    recovery_lock: RecoveryLock | None = None,
) -> GenerationPublicationResult:
    """Materialize, validate, rename, and make one generation current last.

    ``materialize`` must use the supplied writer for every table and return the
    exact deterministic manifest bytes.  A failed pre-pointer operation never
    changes ``current.json``; a completed-but-unpointed generation is retained.
    """
    filesystem = filesystem or LocalFilesystem()
    checkpoint = checkpoint or _noop
    pointer_temp_name = pointer_temp_name or (lambda: f"{STAGING_PREFIX}pointer-{uuid.uuid4().hex}")
    validate_manifest = _require_manifest_validator(validate_manifest)
    generation_id = _safe_generation_id(generation_id)
    directory_fsyncs: list[DirectoryFsyncStatus] = []
    target_parent = _absolute_path(target_parent)
    _require_safe_existing_ancestors(target_parent, "publication root")
    target_parent_existed = target_parent.exists()
    if target_parent_existed:
        _require_safe_directory(target_parent, "publication root")
    checkpoint("before_mkdir:target_parent")
    filesystem.mkdir(target_parent, parents=True, exist_ok=True)
    checkpoint("after_mkdir:target_parent")
    target_parent = _require_safe_directory(target_parent, "publication root")
    if not target_parent_existed:
        _fsync_directory(
            target_parent.parent,
            "target_parent_parent_after_creation",
            filesystem,
            checkpoint,
            directory_fsyncs,
        )
    generations_path = target_parent / "generations"
    checkpoint("before_mkdir:generations")
    filesystem.mkdir(generations_path, parents=True, exist_ok=True)
    checkpoint("after_mkdir:generations")
    _require_safe_directory(generations_path, "generations directory")
    _fsync_directory(target_parent, "target_parent_after_generations", filesystem, checkpoint, directory_fsyncs)
    generation_path = generations_path / generation_id
    if os.path.lexists(generation_path):
        raise GenerationPublicationError("refusing to overwrite an existing generation")
    recovery_lock = recovery_lock or LocalRecoveryLock()
    lease = _acquire_recovery_lease(recovery_lock, target_parent)
    if os.path.lexists(generation_path):
        conflict_error = GenerationPublicationError("refusing to overwrite an existing generation")
        try:
            lease.release()
        except BaseException as error:
            release_error = error if isinstance(error, RecoveryOwnershipError) else RecoveryOwnershipError(
                "unable to release verifiable recovery ownership"
            )
            _attach_cleanup_errors(conflict_error, [release_error])
            raise conflict_error from error
        raise conflict_error
    staging_path: Path | None = None
    staging_identity: tuple[int, int] | None = None
    staging_writer: StagedGenerationWriter | None = None
    target_parent_descriptor: int | None = None
    generations_descriptor: int | None = None
    pointer_temporary: Path | None = None
    pointer_temporary_identity: tuple[int, int] | None = None
    result: GenerationPublicationResult | None = None
    primary_error: BaseException | None = None
    cleanup_errors: list[BaseException] = []
    durability_confirmed = False
    try:
        checkpoint("before_mkdtemp:staging")
        target_parent_descriptor = _open_directory_no_follow(target_parent)
        staging_path = _create_staging_directory(target_parent, target_parent_descriptor)
        checkpoint("after_mkdtemp:staging")
        _fsync_directory(target_parent, "target_parent_after_staging", filesystem, checkpoint, directory_fsyncs)
        assert staging_path is not None
        staging_writer = StagedGenerationWriter(staging_path, filesystem, checkpoint, directory_fsyncs)
        staging_identity = staging_writer._staging_identity
        generations_descriptor = os.open(
            "generations", os.O_RDONLY | _DIRECTORY | _NO_FOLLOW, dir_fd=target_parent_descriptor,
        )
        manifest_bytes = materialize(staging_writer)
        if not isinstance(manifest_bytes, bytes):
            raise GenerationPublicationError("materialize must return exact manifest bytes")
        staging_writer.write_bytes("manifest.json", manifest_bytes)
        _fsync_directory(staging_path, "staging_root", filesystem, checkpoint, directory_fsyncs)
        validate_generation(
            staging_path,
            validate_manifest=validate_manifest,
            expected_generation_id=generation_id,
            require_path_name_match=False,
        )
        staging_writer._verify_directories()
        _verify_directory_entry(
            target_parent_descriptor, staging_path.name, staging_writer._staging_identity,
        )
        staging_writer.close()
        staging_writer = None
        checkpoint("before_generation_rename")
        try:
            os.replace(
                staging_path.name,
                generation_id,
                src_dir_fd=target_parent_descriptor,
                dst_dir_fd=generations_descriptor,
            )
        except (OSError, TypeError) as error:
            raise GenerationPublicationError("descriptor-relative generation publication is unavailable") from error
        checkpoint("after_generation_rename")
        _fsync_directory(generation_path, "selected_generation", filesystem, checkpoint, directory_fsyncs)
        _fsync_directory(generations_path, "generations", filesystem, checkpoint, directory_fsyncs)
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        checkpoint("before_exclusive_create:current_pointer")
        assert target_parent_descriptor is not None
        pointer_temporary, pointer_descriptor, pointer_temporary_identity = _pointer_temporary(
            target_parent, target_parent_descriptor, pointer_temp_name,
        )
        with os.fdopen(pointer_descriptor, "wb") as pointer_file:
            checkpoint("after_exclusive_create:current_pointer")
            checkpoint("before_write:current.json")
            _write_all(pointer_file.fileno(), deterministic_pointer_bytes(generation_id, manifest_sha256), filesystem)
            pointer_file.flush()
            filesystem.fsync_file(pointer_file.fileno())
        checkpoint("after_file_fsync:current.json")
        checkpoint("before_pointer_replace")
        pointer_path = target_parent / "current.json"
        try:
            os.replace(
                pointer_temporary.name,
                pointer_path.name,
                src_dir_fd=target_parent_descriptor,
                dst_dir_fd=target_parent_descriptor,
            )
        except BaseException:
            if _pointer_selects(target_parent_descriptor, generation_id, manifest_sha256):
                result = GenerationPublicationResult(
                    generation_path,
                    generation_path / "manifest.json",
                    pointer_path,
                    manifest_sha256,
                    directory_fsyncs=tuple(directory_fsyncs),
                )
            raise
        result = GenerationPublicationResult(
            generation_path,
            generation_path / "manifest.json",
            pointer_path,
            manifest_sha256,
            directory_fsyncs=tuple(directory_fsyncs),
        )
        checkpoint("after_pointer_replace")
        _fsync_directory(
            target_parent,
            "target_parent_after_commit",
            filesystem,
            checkpoint,
            directory_fsyncs,
            checkpoint_after=False,
        )
        durability_confirmed = directory_fsyncs[-1].outcome == "succeeded"
        checkpoint("after_directory_fsync:target_parent_after_commit")
        result = GenerationPublicationResult(
            result.generation_path, result.manifest_path, result.pointer_path, result.manifest_sha256,
            directory_fsyncs=tuple(directory_fsyncs),
        )
    except BaseException as error:
        primary_error = error
    finally:
        if staging_writer is not None:
            staging_writer.close()
        if staging_path is not None:
            try:
                checkpoint("before_cleanup:staging")
                if target_parent_descriptor is None or staging_identity is None:
                    raise GenerationPublicationError("trusted publication root is unavailable for staging cleanup")
                filesystem.remove_tree_at(target_parent_descriptor, staging_path.name, staging_identity)
                checkpoint("after_cleanup:staging")
            except FileNotFoundError:
                pass
            except BaseException as error:
                cleanup_errors.append(error)
        if pointer_temporary is not None:
            try:
                checkpoint("before_cleanup:pointer")
                if target_parent_descriptor is None or pointer_temporary_identity is None:
                    raise GenerationPublicationError("trusted publication root is unavailable for pointer cleanup")
                filesystem.remove_file_at(
                    target_parent_descriptor, pointer_temporary.name, pointer_temporary_identity,
                )
                checkpoint("after_cleanup:pointer")
            except FileNotFoundError:
                pass
            except BaseException as error:
                cleanup_errors.append(error)
        for descriptor in (generations_descriptor, target_parent_descriptor):
            if isinstance(descriptor, int):
                os.close(descriptor)
        try:
            lease.release()
        except BaseException as error:
            cleanup_errors.append(
                error if isinstance(error, RecoveryOwnershipError) else RecoveryOwnershipError(
                    "unable to release verifiable recovery ownership"
                )
            )
    if primary_error is not None:
        _attach_cleanup_errors(primary_error, cleanup_errors)
        if result is not None:
            uncertain_result = GenerationPublicationResult(
                result.generation_path,
                result.manifest_path,
                result.pointer_path,
                result.manifest_sha256,
                directory_fsyncs=tuple(directory_fsyncs),
            )
            if durability_confirmed:
                committed_error = PublicationCommittedError(uncertain_result, primary_error)
                _attach_cleanup_errors(committed_error, cleanup_errors)
                raise committed_error from primary_error
            uncertain = PublicationDurabilityUncertainError(uncertain_result, primary_error)
            _attach_cleanup_errors(uncertain, cleanup_errors)
            raise uncertain from primary_error
        raise primary_error
    if cleanup_errors:
        raise PublicationCleanupError(tuple(cleanup_errors), result) from cleanup_errors[0]
    if result is not None:
        return result
    raise AssertionError("publication did not return or raise")


def _attach_cleanup_errors(primary_error: BaseException, cleanup_errors: list[BaseException]) -> None:
    """Keep every cleanup failure observable without replacing the primary cause."""
    if not cleanup_errors:
        return
    existing = getattr(primary_error, "cleanup_errors", ())
    existing_errors = existing if isinstance(existing, tuple) else ()
    setattr(primary_error, "cleanup_errors", existing_errors + tuple(cleanup_errors))
    for error in cleanup_errors:
        primary_error.add_note(f"cleanup failure: {error!r}")


__all__ = [
    "CANONICAL_TABLE_NAMES", "DirectoryFsyncStatus", "FORMAT_VERSION", "STAGING_PREFIX", "Filesystem", "GuardedFile",
    "GenerationPublicationError", "GenerationPublicationResult", "LocalFilesystem",
    "LocalRecoveryLock", "RecoveryLease", "RecoveryLock", "RecoveryOwnershipError",
    "PublicationCleanupError", "PublicationCommittedError", "PublicationDurabilityUncertainError",
    "StagedGenerationWriter", "deterministic_pointer_bytes", "recover_stale_staging",
    "read_regular_file_no_follow", "resolve_current_generation", "validate_generation",
    "verify_regular_file_identity", "write_generation",
]
