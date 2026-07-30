"""Offline migration of the tracked v1 season catalog to catalog v2."""

from __future__ import annotations

import json
import hashlib
import base64
import os
from dataclasses import dataclass
from pathlib import Path
import re
import stat
from typing import Sequence, cast

from f1_replay_pipeline.app.batch_generation import ScheduledRace, _atomic_write_json, _session_name
from f1_replay_pipeline.app.catalog_v2_schema import (
    CatalogV2Payload,
    CatalogV2RaceRecord,
    CatalogV2SessionRecord,
    session_code_from_generation_id,
)
from f1_replay_pipeline.app.catalog_visuals import resolve_venue_coordinates
from f1_replay_pipeline.app.session_pointer_publication import (
    deterministic_session_browser_pointer_bytes,
    deterministic_session_canonical_pointer_bytes,
    read_optional_session_pointer,
    remove_session_pointer,
    session_pointer_directory,
    write_session_pointer_bytes,
    write_session_browser_pointer,
    write_session_canonical_pointer,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_publication import validate_browser_delivery_pointer
from f1_replay_pipeline.domain.dataset_manifest import parse_current_pointer, parse_manifest
from f1_replay_pipeline.domain.generation_identity import validate_generation_id
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
    validate_browser_delivery_pointer(pointer)
    manifest_path = browser_root / "generations" / delivery_version / "manifest.json"
    guarded_manifest = read_regular_file_no_follow(manifest_path, "browser manifest")
    if pointer.get("deliveryVersion") != delivery_version or pointer.get("manifestSha256") != _sha256(guarded_manifest.data):
        return None
    manifest = json.loads(guarded_manifest.data)
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
    manifest_path = canonical_root / "generations" / pointer.generation_id / "manifest.json"
    guarded_manifest = read_regular_file_no_follow(manifest_path, "canonical manifest")
    digest = _sha256(guarded_manifest.data)
    if digest != pointer.manifest_sha256:
        raise ValueError("canonical pointer manifest checksum disagrees")
    manifest = parse_manifest(guarded_manifest.data)
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
    version = validate_browser_delivery_pointer(pointer)
    manifest_path = browser_root / "generations" / version / "manifest.json"
    guarded_manifest = read_regular_file_no_follow(manifest_path, "browser manifest")
    digest = _sha256(guarded_manifest.data)
    if digest != pointer["manifestSha256"]:
        raise ValueError("browser pointer manifest checksum disagrees")
    manifest = json.loads(guarded_manifest.data)
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


@dataclass(frozen=True)
class _PreparedRace:
    race_id: str
    canonical_root: Path
    browser_root: Path
    session_code: str
    generation_id: str
    delivery_version: str
    canonical_manifest_sha256: str
    browser_manifest_sha256: str
    canonical_payload: bytes
    browser_payload: bytes
    prior_canonical: bytes | None
    prior_browser: bytes | None
    canonical_parent_existed: bool
    browser_parent_existed: bool
    record: CatalogV2RaceRecord


def _existing_pointer(root: Path, session_code: str, filename: str) -> bytes | None:
    return read_optional_session_pointer(root, session_code, filename)


def _session_parent_exists(root: Path, session_code: str) -> bool:
    try:
        return stat.S_ISDIR(session_pointer_directory(root, session_code).lstat().st_mode)
    except OSError:
        return False


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


def _journal_value(prepared: Sequence[_PreparedRace], *, status: str) -> dict[str, object]:
    return {
        "version": 1,
        "status": status,
        "races": [
            {
                "race_id": item.race_id,
                "session_code": item.session_code,
                "canonical_previous": base64.b64encode(item.prior_canonical).decode("ascii") if item.prior_canonical is not None else None,
                "browser_previous": base64.b64encode(item.prior_browser).decode("ascii") if item.prior_browser is not None else None,
                "canonical_parent_existed": item.canonical_parent_existed,
                "browser_parent_existed": item.browser_parent_existed,
            }
            for item in prepared
        ],
    }


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


def _rollback_pointers(prepared: Sequence[_PreparedRace]) -> tuple[BaseException, ...]:
    errors: list[BaseException] = []
    for item in reversed(prepared):
        for root, filename, previous, parent_existed in (
            (item.canonical_root, "current.json", item.prior_canonical, item.canonical_parent_existed),
            (item.browser_root, "browser-current.json", item.prior_browser, item.browser_parent_existed),
        ):
            try:
                if previous is None:
                    remove_session_pointer(root, item.session_code, filename, remove_empty_parent=not parent_existed)
                else:
                    write_session_pointer_bytes(root, item.session_code, filename, previous)
            except BaseException as error:
                errors.append(error)
    return tuple(errors)


def recover_catalog_migration(season_root: Path) -> None:
    """Recover a pointer-prefix left by an interrupted migration journal."""
    path = _journal_path(season_root)
    journal_bytes = _read_optional_journal(path)
    if journal_bytes is None:
        return
    journal = json.loads(journal_bytes)
    catalog = json.loads(read_regular_file_no_follow(season_root / "catalog.json", "season catalog").data)
    if catalog.get("schemaVersion") == 2:
        _remove_journal(path)
        return
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


def migrate_catalog_v1_to_v2(
    season_root: Path,
    *,
    schedule: Sequence[ScheduledRace] = (),
) -> Path:
    """Preflight every race, then publish pointers and catalog as one recoverable transaction."""
    catalog_path = season_root / "catalog.json"
    recover_catalog_migration(season_root)
    catalog_file = read_regular_file_no_follow(catalog_path, "season catalog")
    catalog = json.loads(catalog_file.data)
    if catalog.get("schemaVersion") == 2:
        return catalog_path
    if not isinstance(catalog.get("races"), list):
        raise ValueError("v1 season catalog is malformed")
    schedule_by_round = {race.round_number: race for race in schedule}
    prepared: list[_PreparedRace] = []
    for source in catalog["races"]:
        if not isinstance(source, dict):
            raise ValueError("v1 catalog race is malformed")
        race_id_value = source.get("race_id")
        generation_value = source.get("generation_id")
        if not all(isinstance(value, str) for value in (race_id_value, generation_value)):
            raise ValueError("v1 catalog race identity is incomplete")
        race_id = cast(str, race_id_value)
        round_number = source.get("round_number")
        if type(round_number) is not int or round_number < 1:
            raise ValueError("v1 catalog round_number is invalid")
        canonical_root = season_root / "canonical" / race_id
        browser_root = season_root / "browser" / race_id
        generation_id, canonical_manifest_sha256 = _authoritative_canonical(canonical_root)
        delivery_version, browser_manifest_sha256 = _authoritative_browser(
            browser_root, generation_id, canonical_manifest_sha256,
        )
        session_code = session_code_from_generation_id(generation_id, race_id)
        track_name = _track_name(browser_root, delivery_version)
        scheduled = schedule_by_round.get(round_number)
        event_name = (
            scheduled.event_name if scheduled is not None else track_name or str(source.get("event_name") or race_id)
        )
        session = CatalogV2SessionRecord(
            session_code, _session_name(session_code), generation_id, delivery_version,
            str(source.get("outcome") or "generated"), True, None,
            f"browser/{race_id}/sessions/{session_code}/browser-current.json",
        )
        coordinates = resolve_venue_coordinates(
            scheduled.country, scheduled.location,
        ) if scheduled is not None else None
        race_record = CatalogV2RaceRecord(
            race_id, round_number, event_name, (session,),
            scheduled.country if scheduled else None,
            scheduled.location if scheduled else None,
            scheduled.event_date if scheduled else None,
            latitude=coordinates.latitude if coordinates is not None else None,
            longitude=coordinates.longitude if coordinates is not None else None,
        )
        prepared.append(_PreparedRace(
            race_id, canonical_root, browser_root, session_code,
            generation_id, delivery_version, canonical_manifest_sha256, browser_manifest_sha256,
            deterministic_session_canonical_pointer_bytes(generation_id, canonical_manifest_sha256),
            deterministic_session_browser_pointer_bytes(delivery_version, browser_manifest_sha256),
            _existing_pointer(canonical_root, session_code, "current.json"),
            _existing_pointer(browser_root, session_code, "browser-current.json"),
            _session_parent_exists(canonical_root, session_code),
            _session_parent_exists(browser_root, session_code),
            race_record,
        ))
    verify_regular_file_identity(catalog_path, catalog_file, "season catalog")
    payload = CatalogV2Payload(catalog.get("year"), tuple(item.record for item in prepared)).to_dict()
    journal = _journal_path(season_root)
    _atomic_write_json(journal, _journal_value(prepared, status="prepared"))
    try:
        for item in prepared:
            write_session_canonical_pointer(
                item.canonical_root, item.session_code, item.generation_id, item.canonical_manifest_sha256,
            )
            write_session_browser_pointer(
                item.browser_root, item.session_code, item.delivery_version, item.browser_manifest_sha256,
            )
        _atomic_write_json(catalog_path, payload)
    except BaseException as error:
        current_catalog: bytes | None = None
        catalog_state_error: BaseException | None = None
        try:
            current_catalog = read_regular_file_no_follow(catalog_path, "season catalog").data
        except BaseException as state_error:
            catalog_state_error = state_error
        if catalog_state_error is not None:
            raise RuntimeError("catalog migration failed with unknown catalog state; journal recovery is required") from catalog_state_error
        expected_catalog = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        if current_catalog != expected_catalog:
            rollback_errors = _rollback_pointers(prepared)
            if rollback_errors:
                raise RuntimeError("catalog migration failed and recovery is incomplete") from rollback_errors[0]
            _remove_journal(journal)
        else:
            _atomic_write_json(journal, _journal_value(prepared, status="catalog_committed"))
        raise error
    _atomic_write_json(journal, _journal_value(prepared, status="catalog_committed"))
    _remove_journal(journal)
    return catalog_path


def migrate_2024_catalog(season_root: Path) -> Path:
    return migrate_catalog_v1_to_v2(season_root)


__all__ = ["migrate_2024_catalog", "migrate_catalog_v1_to_v2", "recover_catalog_migration"]
