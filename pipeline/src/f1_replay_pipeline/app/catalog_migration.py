"""Offline preflight for the v2 catalog cutover.

Historical v1 artifacts are intentionally not upgraded in place.  The later
republish step must create v2 artifacts first, after which this module can
validate the complete active catalog and its session pointers.
"""

from __future__ import annotations

import json
import hashlib
import base64
import os
from dataclasses import dataclass
from pathlib import Path
import re
import stat
from typing import Sequence

from f1_replay_pipeline.app.catalog_v2_schema import (
    BROWSER_POINTER_FORMAT,
    CANONICAL_POINTER_FORMAT,
    validate_v2_cutover_contract,
    validate_active_catalog,
)
from f1_replay_pipeline.app.session_pointer_publication import (
    read_session_browser_pointer,
    read_session_canonical_pointer,
    remove_session_pointer,
    write_session_pointer_bytes,
)
from f1_replay_pipeline.domain.dataset_manifest import parse_current_pointer, parse_manifest
from f1_replay_pipeline.domain.generation_identity import validate_generation_id
from f1_replay_pipeline.delivery.browser.browser_delivery_publication import validate_browser_delivery_pointer
from f1_replay_pipeline.storage.generation_publication import read_regular_file_no_follow, verify_regular_file_identity


@dataclass(frozen=True)
class _GuardedRelativeFile:
    data: bytes
    device: int
    inode: int
    parts: tuple[str, ...]


def _safe_relative_parts(value: object) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("track asset path must be a safe relative POSIX path")
    if value.startswith("/") or re.match(r"^[A-Za-z]:[/\\]", value):
        raise ValueError("track asset path must be relative")
    parts = tuple(value.split("/"))
    if any(not part or part in {".", ".."} for part in parts):
        raise ValueError("track asset path must not escape its delivery")
    return parts


def _open_directory_no_follow(path: Path) -> int:
    absolute = Path(os.path.abspath(path))
    descriptor = os.open(absolute.anchor, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        for component in absolute.parts[1:]:
            child = os.open(
                component,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_relative_file(root: Path, parts: tuple[str, ...], label: str) -> tuple[int, tuple[int, int]]:
    parent = _open_directory_no_follow(root)
    try:
        for component in parts[:-1]:
            child = os.open(
                component,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent,
            )
            os.close(parent)
            parent = child
        descriptor = os.open(parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent)
        os.close(parent)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            os.close(descriptor)
            raise ValueError(f"{label} must be a regular file")
        return descriptor, (metadata.st_dev, metadata.st_ino)
    except BaseException:
        try:
            os.close(parent)
        except OSError:
            pass
        raise


def _read_guarded_relative_file(root: Path, value: object, label: str) -> _GuardedRelativeFile:
    parts = _safe_relative_parts(value)
    descriptor, identity = _open_relative_file(root, parts, label)
    with os.fdopen(descriptor, "rb") as source:
        return _GuardedRelativeFile(source.read(), identity[0], identity[1], parts)


def _verify_guarded_relative_file(root: Path, guarded: _GuardedRelativeFile, label: str) -> None:
    descriptor, identity = _open_relative_file(root, guarded.parts, label)
    os.close(descriptor)
    if identity != (guarded.device, guarded.inode):
        raise ValueError(f"{label} changed during guarded validation")


def _track_name(browser_root: Path, delivery_version: str) -> str | None:
    pointer_path = browser_root / "browser-current.json"
    guarded_pointer = read_regular_file_no_follow(pointer_path, "browser current pointer")
    pointer = json.loads(guarded_pointer.data)
    _require_v2_browser_pointer(pointer)
    validate_browser_delivery_pointer(pointer)
    manifest_path = browser_root / "generations" / delivery_version / "manifest.json"
    guarded_manifest = read_regular_file_no_follow(manifest_path, "browser manifest")
    if pointer.get("deliveryVersion") != delivery_version or pointer.get("manifestSha256") != _sha256(guarded_manifest.data):
        return None
    manifest = json.loads(guarded_manifest.data)
    _require_v2_browser_manifest(manifest)
    reference = manifest.get("trackAssets")
    if not isinstance(reference, dict) or not isinstance(reference.get("path"), str):
        return None
    guarded_asset = _read_guarded_relative_file(manifest_path.parent, reference["path"], "track assets")
    if _sha256(guarded_asset.data) != reference.get("sha256"):
        return None
    assets = json.loads(guarded_asset.data)
    value = assets.get("trackName") if isinstance(assets, dict) else None
    verify_regular_file_identity(manifest_path, guarded_manifest, "browser manifest")
    verify_regular_file_identity(pointer_path, guarded_pointer, "browser current pointer")
    _verify_guarded_relative_file(manifest_path.parent, guarded_asset, "track assets")
    return value if isinstance(value, str) and value.strip() else None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _authoritative_canonical(canonical_root: Path) -> tuple[str, str]:
    pointer_path = canonical_root / "current.json"
    guarded_pointer = read_regular_file_no_follow(pointer_path, "canonical current pointer")
    pointer = parse_current_pointer(guarded_pointer.data)
    if pointer.format_version != CANONICAL_POINTER_FORMAT:
        raise ValueError(
            "active catalog requires canonical-parquet-v2 pointers; "
            "canonical-parquet-v1 is deprecated"
        )
    manifest_path = canonical_root / "generations" / pointer.generation_id / "manifest.json"
    guarded_manifest = read_regular_file_no_follow(manifest_path, "canonical manifest")
    digest = _sha256(guarded_manifest.data)
    if digest != pointer.manifest_sha256:
        raise ValueError("canonical pointer manifest checksum disagrees")
    manifest = parse_manifest(guarded_manifest.data)
    if manifest.format_version != CANONICAL_POINTER_FORMAT or manifest.manifest_version != 2:
        raise ValueError("active catalog rejects a v1 or mixed-version canonical manifest")
    if manifest.generation_id != pointer.generation_id:
        raise ValueError("canonical manifest generation disagrees")
    verify_regular_file_identity(manifest_path, guarded_manifest, "canonical manifest")
    verify_regular_file_identity(pointer_path, guarded_pointer, "canonical current pointer")
    return pointer.generation_id, digest


def _authoritative_browser(
    browser_root: Path, expected_generation_id: str, expected_manifest_sha256: str,
) -> tuple[str, str]:
    pointer_path = browser_root / "browser-current.json"
    guarded_pointer = read_regular_file_no_follow(pointer_path, "browser current pointer")
    pointer = json.loads(guarded_pointer.data)
    version = _require_v2_browser_pointer(pointer)
    # Reuse the browser contract's exact pointer shape check.  Checking only
    # deliveryVersion would allow a forged manifestPath or checksum field to
    # become the active discovery boundary.
    validate_browser_delivery_pointer(pointer)
    manifest_path = browser_root / "generations" / version / "manifest.json"
    guarded_manifest = read_regular_file_no_follow(manifest_path, "browser manifest")
    digest = _sha256(guarded_manifest.data)
    if digest != pointer["manifestSha256"]:
        raise ValueError("browser pointer manifest checksum disagrees")
    manifest = json.loads(guarded_manifest.data)
    _require_v2_browser_manifest(manifest)
    if (
        manifest.get("deliveryVersion") != version
        or manifest.get("sourceGenerationId") != expected_generation_id
        or manifest.get("sourceManifestSha256") != expected_manifest_sha256
    ):
        raise ValueError("browser delivery provenance disagrees with canonical generation")
    if not isinstance(manifest.get("sourceManifestSha256"), str):
        raise ValueError("browser delivery source manifest checksum is missing")
    verify_regular_file_identity(manifest_path, guarded_manifest, "browser manifest")
    verify_regular_file_identity(pointer_path, guarded_pointer, "browser current pointer")
    return version, digest


def _require_v2_browser_pointer(pointer: object) -> str:
    if not isinstance(pointer, dict):
        raise ValueError("active catalog browser pointer must be an object")
    if pointer.get("formatVersion") != BROWSER_POINTER_FORMAT:
        raise ValueError(
            "active catalog requires browser-delivery-v2 pointers; "
            "browser-delivery-v1 is deprecated"
        )
    version = pointer.get("deliveryVersion")
    if not isinstance(version, str) or not version:
        raise ValueError("active catalog browser pointer deliveryVersion is missing")
    return version


def _require_v2_browser_manifest(manifest: object) -> None:
    if not isinstance(manifest, dict):
        raise ValueError("active catalog browser manifest must be an object")
    if (
        manifest.get("formatVersion") != BROWSER_POINTER_FORMAT
        or manifest.get("contractVersion") != "v2"
    ):
        raise ValueError("active catalog rejects a v1 or mixed-version browser manifest")


def _journal_path(season_root: Path) -> Path:
    return season_root / ".catalog-v2-migration-journal.json"


def _read_optional_journal(path: Path) -> bytes | None:
    """Return None only when the guarded journal leaf is genuinely absent."""
    descriptor = _open_directory_no_follow(path.parent)
    try:
        try:
            os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return None
    finally:
        os.close(descriptor)
    return read_regular_file_no_follow(path, "catalog migration journal").data


def _remove_journal(path: Path) -> None:
    try:
        descriptor = _open_directory_no_follow(path.parent)
    except FileNotFoundError:
        return
    try:
        try:
            os.unlink(path.name, dir_fd=descriptor)
        except FileNotFoundError:
            pass
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def validate_active_catalog_references(season_root: Path) -> Path:
    """Validate a v2 catalog without changing any pointer or catalog bytes."""
    catalog_path = season_root / "catalog.json"
    catalog_file = read_regular_file_no_follow(catalog_path, "season catalog")
    catalog = json.loads(catalog_file.data)
    parsed = validate_active_catalog(catalog)
    for race in parsed.races:
        for session in race.sessions:
            if not session.validated:
                continue
            if session.generation_id is None or session.delivery_version is None:
                raise ValueError(f"active catalog session {race.race_id}/{session.session_code} is incomplete")
            canonical_root = season_root / "canonical" / race.race_id
            browser_root = season_root / "browser" / race.race_id
            expected_browser_pointer = (
                f"browser/{race.race_id}/sessions/{session.session_code}/browser-current.json"
            )
            if session.browser_pointer != expected_browser_pointer:
                raise ValueError(
                    f"active catalog session {race.race_id}/{session.session_code} has an unexpected browser pointer"
                )
            if session.canonical_pointer is not None:
                expected_canonical_pointer = (
                    f"canonical/{race.race_id}/sessions/{session.session_code}/current.json"
                )
                if session.canonical_pointer != expected_canonical_pointer:
                    raise ValueError(
                        f"active catalog session {race.race_id}/{session.session_code} has an unexpected canonical pointer"
                    )
            generation_id, canonical_digest = _authoritative_canonical(canonical_root)
            delivery_version, _ = _authoritative_browser(browser_root, generation_id, canonical_digest)
            try:
                session_canonical = read_session_canonical_pointer(canonical_root, session.session_code)
            except FileNotFoundError:
                session_canonical = None
            if session_canonical is not None:
                if (
                    session_canonical.generation_path.name != generation_id
                    or session_canonical.manifest_sha256 != canonical_digest
                ):
                    raise ValueError(
                        f"active catalog session {race.race_id}/{session.session_code} disagrees with canonical session pointer"
                    )
            try:
                session_browser = read_session_browser_pointer(browser_root, session.session_code)
            except FileNotFoundError:
                session_browser = None
            if session_browser is not None and session_browser.delivery_version != delivery_version:
                raise ValueError(
                    f"active catalog session {race.race_id}/{session.session_code} disagrees with browser session pointer"
                )
            if generation_id != session.generation_id or delivery_version != session.delivery_version:
                raise ValueError(
                    f"active catalog session {race.race_id}/{session.session_code} disagrees with its v2 pointers"
                )
    verify_regular_file_identity(catalog_path, catalog_file, "season catalog")
    return catalog_path


def recover_catalog_migration(season_root: Path) -> None:
    """Recover a pointer-prefix left by an interrupted migration journal."""
    path = _journal_path(season_root)
    journal_bytes = _read_optional_journal(path)
    if journal_bytes is None:
        return
    journal = json.loads(journal_bytes)
    catalog = json.loads(read_regular_file_no_follow(season_root / "catalog.json", "season catalog").data)
    if catalog.get("schemaVersion") == 2:
        # A recovery marker must not be discarded merely because an attacker
        # changed the catalog version field.  Validate the complete shape
        # before treating the migration as already committed.
        validate_active_catalog(catalog)
        validate_active_catalog_references(season_root)
        _remove_journal(path)
        return
    _validate_recovery_journal(journal, catalog)
    errors: list[BaseException] = []
    for entry in reversed(journal.get("races", ())):
        race_id = validate_generation_id(entry["race_id"])
        code = validate_generation_id(entry["session_code"])
        for root_name, filename, key, parent_key in (
            ("canonical", "current.json", "canonical_previous", "canonical_parent_existed"),
            ("browser", "browser-current.json", "browser_previous", "browser_parent_existed"),
        ):
            try:
                encoded = entry.get(key)
                previous = base64.b64decode(encoded, validate=True) if encoded is not None else None
                if previous is not None:
                    _validate_recovery_pointer(previous, filename)
                root = season_root / root_name / race_id
                if previous is None:
                    remove_session_pointer(root, code, filename, remove_empty_parent=not entry.get(parent_key, False))
                else:
                    write_session_pointer_bytes(root, code, filename, previous)
            except BaseException as error:
                errors.append(error)
    if errors:
        raise RuntimeError("catalog migration recovery is incomplete") from errors[0]
    _remove_journal(path)


def _validate_recovery_journal(journal: object, catalog: object) -> None:
    """Validate rollback metadata before allowing it to select any path.

    The journal is local input, not a trusted capability.  In particular, a
    syntactically valid base64 value must not be enough to restore a v1 or
    attacker-crafted pointer into a session directory.
    """
    if not isinstance(journal, dict) or set(journal) != {"races"}:
        raise ValueError("catalog migration journal has an invalid shape")
    entries = journal["races"]
    if not isinstance(entries, list):
        raise ValueError("catalog migration journal races must be an array")
    catalog_races = catalog.get("races") if isinstance(catalog, dict) else None
    allowed: dict[str, set[str]] = {}
    if isinstance(catalog_races, list):
        for race in catalog_races:
            if not isinstance(race, dict):
                continue
            race_id = race.get("race_id")
            if not isinstance(race_id, str):
                continue
            validate_generation_id(race_id)
            sessions = race.get("sessions", ())
            codes = {
                session.get("session_code")
                for session in sessions
                if isinstance(session, dict) and isinstance(session.get("session_code"), str)
            }
            allowed[race_id] = {validate_generation_id(code) for code in codes}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("catalog migration journal entry must be an object")
        required = {
            "race_id", "session_code", "canonical_previous", "browser_previous",
            "canonical_parent_existed", "browser_parent_existed",
        }
        if set(entry) != required:
            raise ValueError("catalog migration journal entry has an invalid shape")
        race_id = validate_generation_id(entry["race_id"])
        code = validate_generation_id(entry["session_code"])
        if race_id not in allowed or code not in allowed[race_id]:
            raise ValueError("catalog migration journal selects a non-catalog session")
        if type(entry["canonical_parent_existed"]) is not bool or type(entry["browser_parent_existed"]) is not bool:
            raise ValueError("catalog migration journal parent flags must be booleans")
        for key in ("canonical_previous", "browser_previous"):
            encoded = entry[key]
            if encoded is not None and not isinstance(encoded, str):
                raise ValueError("catalog migration journal pointer bytes must be base64 text")
            if encoded is not None:
                try:
                    payload = base64.b64decode(encoded, validate=True)
                except (ValueError, TypeError) as error:
                    raise ValueError("catalog migration journal pointer bytes are invalid base64") from error
                filename = "current.json" if key == "canonical_previous" else "browser-current.json"
                _validate_recovery_pointer(payload, filename)


def _validate_recovery_pointer(payload: bytes, filename: str) -> None:
    if filename == "current.json":
        pointer = parse_current_pointer(payload)
        if pointer.format_version != CANONICAL_POINTER_FORMAT:
            raise ValueError("recovery refuses a historical canonical pointer")
        return
    if filename == "browser-current.json":
        pointer = json.loads(payload)
        if not isinstance(pointer, dict) or pointer.get("formatVersion") != BROWSER_POINTER_FORMAT:
            raise ValueError("recovery refuses a historical browser pointer")
        validate_browser_delivery_pointer(pointer)
        return
    raise ValueError("catalog migration journal names an invalid pointer")


def migrate_catalog_v1_to_v2(
    season_root: Path,
    *,
    schedule: Sequence[object] = (),
) -> Path:
    """Reject v1 migration and validate an already-republished v2 catalog.

    The function name remains for callers of the old operational command, but
    it no longer creates active records from historical v1 artifacts.
    """
    catalog_path = season_root / "catalog.json"
    recover_catalog_migration(season_root)
    catalog_file = read_regular_file_no_follow(catalog_path, "season catalog")
    catalog = json.loads(catalog_file.data)
    if catalog.get("schemaVersion") == 2:
        if schedule:
            required_races = _schedule_race_ids(schedule)
            validate_v2_cutover_contract(catalog, required_races)
        return validate_active_catalog_references(season_root)
    if not isinstance(catalog.get("races"), list):
        raise ValueError("season catalog is malformed; expected active schemaVersion 2")
    raise ValueError(
        "refusing to activate a v1 catalog: canonical and browser v1 artifacts are "
        "deprecated; republish all four races as v2 before switching the active catalog"
    )


def _schedule_race_ids(schedule: Sequence[object]) -> tuple[str, ...]:
    """Extract the four immutable race identities required for cutover."""
    race_ids: list[str] = []
    for item in schedule:
        if isinstance(item, str):
            race_id = item
        elif isinstance(item, dict) and isinstance(item.get("race_id"), str):
            race_id = item["race_id"]
        else:
            race_id = getattr(item, "race_id", None)
        if not isinstance(race_id, str):
            raise ValueError("cutover schedule entries must provide a race_id")
        validate_generation_id(race_id)
        race_ids.append(race_id)
    return tuple(race_ids)

def migrate_2024_catalog(season_root: Path) -> Path:
    return migrate_catalog_v1_to_v2(season_root)


__all__ = [
    "migrate_2024_catalog", "migrate_catalog_v1_to_v2", "recover_catalog_migration",
    "validate_active_catalog_references",
]
