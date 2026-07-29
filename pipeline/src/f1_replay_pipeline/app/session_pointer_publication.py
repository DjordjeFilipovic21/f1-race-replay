"""Session-indexed snapshots of the existing race-level pointer contracts.

Pointers contain no session-specific format.  Their location supplies the
index while their manifest paths continue to resolve from the race root.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat

from f1_replay_pipeline.delivery.browser.browser_delivery_publication import validate_browser_delivery_pointer
from f1_replay_pipeline.domain.dataset_manifest import parse_current_pointer, parse_manifest, serialize_deterministic_json
from f1_replay_pipeline.domain.generation_identity import validate_generation_id
from f1_replay_pipeline.storage.generation_publication import (
    GenerationPublicationResult,
    _open_directory_no_follow,
    read_regular_file_no_follow,
    verify_regular_file_identity,
    deterministic_pointer_bytes,
)


CANONICAL_POINTER_FORMAT = "canonical-parquet-v1"
BROWSER_POINTER_FORMAT = "browser-delivery-v1"


def _safe(value: str, label: str) -> str:
    try:
        return validate_generation_id(value)
    except ValueError as error:
        raise ValueError(f"{label} must be a safe path component") from error


def session_pointer_directory(root: Path, session_code: str) -> Path:
    return root / "sessions" / _safe(session_code, "session_code")


def canonical_session_pointer_path(canonical_root: Path, session_code: str) -> Path:
    return session_pointer_directory(canonical_root, session_code) / "current.json"


def browser_session_pointer_path(browser_root: Path, session_code: str) -> Path:
    return session_pointer_directory(browser_root, session_code) / "browser-current.json"


_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_NO_FOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _require_directory(path: Path) -> None:
    descriptor = _open_directory_no_follow(path)
    os.close(descriptor)


def _open_child_directory(parent: int, name: str, *, create: bool) -> tuple[int, bool]:
    created = False
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent)
            created = True
        except FileExistsError:
            pass
    descriptor = os.open(name, os.O_RDONLY | _DIRECTORY | _NO_FOLLOW, dir_fd=parent)
    if created:
        try:
            os.fsync(parent)
        except BaseException:
            os.close(descriptor)
            raise
    return descriptor, created


def _open_session_directories(root: Path, session_code: str, *, create: bool) -> tuple[int, int, int, bool]:
    code = _safe(session_code, "session_code")
    root_descriptor = _open_directory_no_follow(root)
    sessions_descriptor: int | None = None
    code_descriptor: int | None = None
    try:
        sessions_descriptor, _ = _open_child_directory(root_descriptor, "sessions", create=create)
        code_descriptor, code_created = _open_child_directory(sessions_descriptor, code, create=create)
        return root_descriptor, sessions_descriptor, code_descriptor, code_created
    except BaseException:
        for descriptor in (code_descriptor, sessions_descriptor, root_descriptor):
            if descriptor is not None:
                os.close(descriptor)
        raise


def _atomic_replace(parent_descriptor: int, name: str, payload: bytes) -> None:
    temporary_name = f".{name}.session-{os.urandom(12).hex()}.tmp"
    descriptor = os.open(
        temporary_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NO_FOLLOW,
        0o600,
        dir_fd=parent_descriptor,
    )
    try:
        with os.fdopen(descriptor, "wb") as target:
            view = memoryview(payload)
            while view:
                written = os.write(target.fileno(), view)
                if written <= 0:
                    raise OSError("short pointer write")
                view = view[written:]
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary_name, name, src_dir_fd=parent_descriptor, dst_dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
    except BaseException:
        try:
            os.unlink(temporary_name, dir_fd=parent_descriptor)
        except FileNotFoundError:
            pass
        raise


def write_session_pointer_bytes(root: Path, session_code: str, filename: str, payload: bytes) -> Path:
    if filename not in {"current.json", "browser-current.json"} or not isinstance(payload, bytes):
        raise ValueError("invalid session pointer write")
    root_descriptor, sessions_descriptor, code_descriptor, _ = _open_session_directories(
        root, session_code, create=True,
    )
    try:
        _atomic_replace(code_descriptor, filename, payload)
    finally:
        os.close(code_descriptor)
        os.close(sessions_descriptor)
        os.close(root_descriptor)
    return session_pointer_directory(root, session_code) / filename


def remove_session_pointer(root: Path, session_code: str, filename: str, *, remove_empty_parent: bool = False) -> None:
    if filename not in {"current.json", "browser-current.json"}:
        raise ValueError("invalid session pointer removal")
    try:
        root_descriptor, sessions_descriptor, code_descriptor, _ = _open_session_directories(
            root, session_code, create=False,
        )
    except FileNotFoundError:
        return
    try:
        try:
            os.unlink(filename, dir_fd=code_descriptor)
            os.fsync(code_descriptor)
        except FileNotFoundError:
            return
        if remove_empty_parent:
            try:
                os.rmdir(_safe(session_code, "session_code"), dir_fd=sessions_descriptor)
                os.fsync(sessions_descriptor)
                try:
                    os.rmdir("sessions", dir_fd=root_descriptor)
                    os.fsync(root_descriptor)
                except OSError:
                    pass
            except OSError:
                pass
    finally:
        os.close(code_descriptor)
        os.close(sessions_descriptor)
        os.close(root_descriptor)


def read_optional_session_pointer(root: Path, session_code: str, filename: str) -> bytes | None:
    """Read an existing session pointer, treating only missing leaves as absent."""
    if filename not in {"current.json", "browser-current.json"}:
        raise ValueError("invalid session pointer read")
    code = _safe(session_code, "session_code")
    root_descriptor = _open_directory_no_follow(root)
    sessions_descriptor: int | None = None
    code_descriptor: int | None = None
    pointer_descriptor: int | None = None
    try:
        try:
            sessions_descriptor, _ = _open_child_directory(root_descriptor, "sessions", create=False)
        except FileNotFoundError:
            return None
        try:
            code_descriptor, _ = _open_child_directory(sessions_descriptor, code, create=False)
        except FileNotFoundError:
            return None
        try:
            pointer_descriptor = os.open(filename, os.O_RDONLY | _NO_FOLLOW, dir_fd=code_descriptor)
        except FileNotFoundError:
            return None
        metadata = os.fstat(pointer_descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("session pointer must be a regular file")
        with os.fdopen(pointer_descriptor, "rb") as source:
            pointer_descriptor = None
            return source.read()
    finally:
        if pointer_descriptor is not None:
            os.close(pointer_descriptor)
        if code_descriptor is not None:
            os.close(code_descriptor)
        if sessions_descriptor is not None:
            os.close(sessions_descriptor)
        os.close(root_descriptor)


def write_session_canonical_pointer(
    canonical_root: Path,
    session_code: str,
    generation_id: str,
    manifest_sha256: str,
) -> Path:
    """Atomically replace one session snapshot without touching its generation."""
    return write_session_pointer_bytes(
        canonical_root, session_code, "current.json", deterministic_pointer_bytes(generation_id, manifest_sha256),
    )


def deterministic_session_canonical_pointer_bytes(generation_id: str, manifest_sha256: str) -> bytes:
    """Return the canonical race-pointer bytes used by a session snapshot."""
    return deterministic_pointer_bytes(generation_id, manifest_sha256)


def write_session_browser_pointer(
    browser_root: Path,
    session_code: str,
    delivery_version: str,
    manifest_sha256: str,
) -> Path:
    """Atomically replace one session browser snapshot."""
    version = _safe(delivery_version, "delivery_version")
    _require_digest(manifest_sha256)
    payload = serialize_deterministic_json({
        "formatVersion": BROWSER_POINTER_FORMAT,
        "deliveryVersion": version,
        "manifestPath": f"generations/{version}/manifest.json",
        "manifestSha256": manifest_sha256,
    })
    return write_session_pointer_bytes(browser_root, session_code, "browser-current.json", payload)


def deterministic_session_browser_pointer_bytes(delivery_version: str, manifest_sha256: str) -> bytes:
    version = _safe(delivery_version, "delivery_version")
    _require_digest(manifest_sha256)
    return serialize_deterministic_json({
        "formatVersion": BROWSER_POINTER_FORMAT,
        "deliveryVersion": version,
        "manifestPath": f"generations/{version}/manifest.json",
        "manifestSha256": manifest_sha256,
    })


def _require_digest(value: object) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("manifest_sha256 must be a lowercase SHA-256 digest")
    return value


def read_session_canonical_pointer(canonical_root: Path, session_code: str) -> GenerationPublicationResult:
    """Read a session pointer and resolve its manifest from the race root."""
    pointer_path = canonical_session_pointer_path(canonical_root, session_code)
    _require_directory(canonical_root)
    guarded_pointer = read_regular_file_no_follow(pointer_path, "session canonical pointer")
    pointer = parse_current_pointer(guarded_pointer.data)
    generation_id = _safe(pointer.generation_id, "generation_id")
    expected_path = f"generations/{generation_id}/manifest.json"
    if pointer.manifest_path != expected_path:
        raise ValueError("session canonical pointer manifest path disagrees")
    generation = canonical_root / "generations" / generation_id
    _require_directory(canonical_root / "generations")
    _require_directory(generation)
    manifest_path = generation / "manifest.json"
    guarded_manifest = read_regular_file_no_follow(manifest_path, "session canonical manifest")
    if hashlib.sha256(guarded_manifest.data).hexdigest() != pointer.manifest_sha256:
        raise ValueError("session canonical pointer manifest checksum disagrees")
    manifest = parse_manifest(guarded_manifest.data)
    if manifest.generation_id != generation_id:
        raise ValueError("session canonical manifest generation disagrees")
    verify_regular_file_identity(manifest_path, guarded_manifest, "session canonical manifest")
    verify_regular_file_identity(pointer_path, guarded_pointer, "session canonical pointer")
    return GenerationPublicationResult(generation, manifest_path, pointer_path, pointer.manifest_sha256)


@dataclass(frozen=True)
class SessionBrowserPointer:
    delivery_version: str
    manifest_sha256: str
    pointer_path: Path
    manifest_path: Path


def read_session_browser_pointer(browser_root: Path, session_code: str) -> SessionBrowserPointer:
    """Read a session browser pointer and resolve its manifest from the race root."""
    pointer_path = browser_session_pointer_path(browser_root, session_code)
    _require_directory(browser_root)
    guarded_pointer = read_regular_file_no_follow(pointer_path, "session browser pointer")
    pointer = json.loads(guarded_pointer.data)
    version = validate_browser_delivery_pointer(pointer)
    manifest_path = browser_root / "generations" / version / "manifest.json"
    _require_directory(browser_root / "generations")
    _require_directory(manifest_path.parent)
    guarded_manifest = read_regular_file_no_follow(manifest_path, "session browser manifest")
    digest = hashlib.sha256(guarded_manifest.data).hexdigest()
    if pointer.get("manifestSha256") != digest:
        raise ValueError("session browser pointer manifest checksum disagrees")
    manifest = json.loads(guarded_manifest.data)
    if manifest.get("deliveryVersion") != version:
        raise ValueError("session browser manifest delivery version disagrees")
    verify_regular_file_identity(manifest_path, guarded_manifest, "session browser manifest")
    verify_regular_file_identity(pointer_path, guarded_pointer, "session browser pointer")
    return SessionBrowserPointer(version, digest, pointer_path, manifest_path)


def promote_session_canonical_pointer(canonical_root: Path, session_code: str) -> Path:
    """Restore a validated session snapshot to the race-level compatibility alias."""
    session_path = canonical_session_pointer_path(canonical_root, session_code)
    read_session_canonical_pointer(canonical_root, session_code)
    guarded = read_regular_file_no_follow(session_path, "session canonical pointer")
    parse_current_pointer(guarded.data)
    verify_regular_file_identity(session_path, guarded, "session canonical pointer")
    root_descriptor = _open_directory_no_follow(canonical_root)
    try:
        _atomic_replace(root_descriptor, "current.json", guarded.data)
    finally:
        os.close(root_descriptor)
    return canonical_root / "current.json"


__all__ = [
    "SessionBrowserPointer", "browser_session_pointer_path", "canonical_session_pointer_path",
    "BROWSER_POINTER_FORMAT", "CANONICAL_POINTER_FORMAT",
    "deterministic_session_browser_pointer_bytes", "deterministic_session_canonical_pointer_bytes",
    "promote_session_canonical_pointer", "read_session_browser_pointer",
    "read_session_canonical_pointer", "session_pointer_directory",
    "write_session_browser_pointer", "write_session_canonical_pointer", "write_session_pointer_bytes",
    "remove_session_pointer", "read_optional_session_pointer",
]
