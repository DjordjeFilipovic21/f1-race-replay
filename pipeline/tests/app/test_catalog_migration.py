"""Offline migration coverage using a synthetic v1 catalog."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import f1_replay_pipeline.app.catalog_migration as migration
from f1_replay_pipeline.app.catalog_migration import migrate_catalog_v1_to_v2
from f1_replay_pipeline.app.session_pointer_publication import write_session_pointer_bytes
from f1_replay_pipeline.storage.generation_publication import GenerationPublicationError


def test_migration_adds_session_snapshots_without_rewriting_race_pointers(tmp_path: Path, monkeypatch) -> None:
    season = tmp_path
    race_id = "2024-round-01"
    canonical = season / "canonical" / race_id
    browser = season / "browser" / race_id
    (canonical / "generations" / f"{race_id}-r").mkdir(parents=True)
    (browser / "generations" / f"{race_id}-r").mkdir(parents=True)
    canonical_manifest = {"generation_id": f"{race_id}-r"}
    canonical_manifest_bytes = json.dumps(canonical_manifest).encode()
    (canonical / "generations" / f"{race_id}-r" / "manifest.json").write_bytes(canonical_manifest_bytes)
    canonical_pointer = {
        "format_version": "canonical-parquet-v1", "generation_id": f"{race_id}-r",
        "manifest_path": f"generations/{race_id}-r/manifest.json", "manifest_sha256": hashlib.sha256(canonical_manifest_bytes).hexdigest(),
    }
    browser_pointer = {
        "formatVersion": "browser-delivery-v1", "deliveryVersion": f"{race_id}-r",
        "manifestPath": f"generations/{race_id}-r/manifest.json", "manifestSha256": "b" * 64,
    }
    (canonical / "current.json").write_bytes(json.dumps(canonical_pointer, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    monkeypatch.setattr(migration, "parse_manifest", lambda _payload: SimpleNamespace(generation_id=f"{race_id}-r"))
    (browser / "browser-current.json").write_text(json.dumps(browser_pointer), encoding="utf-8")
    asset = json.dumps({"trackName": "Synthetic Grand Prix"}).encode()
    (browser / "generations" / f"{race_id}-r" / "track-assets.json").write_bytes(asset)
    manifest = {
        "deliveryVersion": f"{race_id}-r",
        "sourceGenerationId": f"{race_id}-r",
        "sourceManifestSha256": canonical_pointer["manifest_sha256"],
        "trackAssets": {"path": "track-assets.json", "sha256": hashlib.sha256(asset).hexdigest()},
    }
    (browser / "generations" / f"{race_id}-r" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    browser_pointer["manifestSha256"] = hashlib.sha256(
        (browser / "generations" / f"{race_id}-r" / "manifest.json").read_bytes()
    ).hexdigest()
    (browser / "browser-current.json").write_text(json.dumps(browser_pointer), encoding="utf-8")
    catalog = {"year": 2024, "atomicAcrossRaces": False, "races": [{
        "race_id": race_id, "round_number": 1, "generation_id": f"{race_id}-r",
        "delivery_version": f"{race_id}-r", "outcome": "generated", "validated": True,
    }]}
    (season / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")

    migrate_catalog_v1_to_v2(season)
    migrated = json.loads((season / "catalog.json").read_bytes())

    assert migrated["schemaVersion"] == 2
    assert migrated["races"][0]["event_name"] == "Synthetic Grand Prix"
    assert (canonical / "sessions" / "r" / "current.json").is_file()
    assert json.loads((canonical / "current.json").read_bytes()) == canonical_pointer


def test_migration_uses_authoritative_browser_pointer_over_stale_v1_delivery(tmp_path: Path, monkeypatch) -> None:
    race_id = "2024-round-02-readable-event"
    season = tmp_path
    canonical = season / "canonical" / race_id
    browser = season / "browser" / race_id
    generation_id = "2024-round-02-r"
    delivery_version = "2024-round-02-r-origin-hotfix-v2"
    (canonical / "generations" / generation_id).mkdir(parents=True)
    (browser / "generations" / delivery_version).mkdir(parents=True)
    canonical_manifest = {
        "format_version": "canonical-parquet-v1", "manifest_version": 1,
        "generation_id": generation_id, "tables": [], "writer_settings": {},
    }
    canonical_bytes = json.dumps(canonical_manifest, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    monkeypatch.setattr(migration, "parse_manifest", lambda _payload: SimpleNamespace(generation_id=generation_id))
    (canonical / "generations" / generation_id / "manifest.json").write_bytes(canonical_bytes)
    canonical_digest = hashlib.sha256(canonical_bytes).hexdigest()
    canonical_pointer = {
        "format_version": "canonical-parquet-v1", "generation_id": generation_id,
        "manifest_path": f"generations/{generation_id}/manifest.json", "manifest_sha256": canonical_digest,
    }
    (canonical / "current.json").write_bytes(json.dumps(canonical_pointer, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    asset = json.dumps({"trackName": "Hotfix Grand Prix"}).encode()
    (browser / "generations" / delivery_version / "track-assets.json").write_bytes(asset)
    browser_manifest = {
        "deliveryVersion": delivery_version,
        "sourceGenerationId": generation_id,
        "sourceManifestSha256": canonical_digest,
        "trackAssets": {"path": "track-assets.json", "sha256": hashlib.sha256(asset).hexdigest()},
    }
    browser_bytes = json.dumps(browser_manifest, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    (browser / "generations" / delivery_version / "manifest.json").write_bytes(browser_bytes)
    browser_pointer = {
        "formatVersion": "browser-delivery-v1", "deliveryVersion": delivery_version,
        "manifestPath": f"generations/{delivery_version}/manifest.json",
        "manifestSha256": hashlib.sha256(browser_bytes).hexdigest(),
    }
    (browser / "browser-current.json").write_bytes(json.dumps(browser_pointer, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    (season / "catalog.json").write_text(json.dumps({"year": 2024, "races": [{
        "race_id": race_id, "round_number": 2, "generation_id": generation_id,
        "delivery_version": "stale-delivery", "outcome": "generated", "validated": True,
    }]}), encoding="utf-8")

    migrate_catalog_v1_to_v2(season)
    migrated = json.loads((season / "catalog.json").read_bytes())
    session = migrated["races"][0]["sessions"][0]

    assert session["generation_id"] == generation_id
    assert session["delivery_version"] == delivery_version
    assert json.loads((browser / "sessions" / "r" / "browser-current.json").read_bytes())["deliveryVersion"] == delivery_version


def test_guarded_track_asset_reader_accepts_nested_safe_paths(tmp_path: Path) -> None:
    root = tmp_path / "delivery"
    nested = root / "metadata" / "track-assets.json"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b"nested")

    guarded = migration._read_guarded_relative_file(root, "metadata/track-assets.json", "track assets")
    migration._verify_guarded_relative_file(root, guarded, "track assets")
    assert guarded.data == b"nested"


@pytest.mark.parametrize("path", ("../outside", "/outside", "metadata\\track-assets.json", "metadata//track-assets.json", "metadata/./track-assets.json"))
def test_guarded_track_asset_reader_rejects_unsafe_paths(tmp_path: Path, path: str) -> None:
    with pytest.raises(ValueError):
        migration._read_guarded_relative_file(tmp_path, path, "track assets")


def test_guarded_track_asset_reader_rejects_intermediate_and_final_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "delivery"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "asset.json").write_bytes(b"outside")
    root.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)
    (root / "final.json").symlink_to(outside / "asset.json")

    with pytest.raises(OSError):
        migration._read_guarded_relative_file(root, "linked/asset.json", "track assets")
    with pytest.raises(OSError):
        migration._read_guarded_relative_file(root, "final.json", "track assets")


def test_recovery_treats_only_an_absent_journal_as_normal(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)

    migration.recover_catalog_migration(tmp_path)


def test_recovery_fails_closed_for_symlink_journal(tmp_path: Path) -> None:
    target = tmp_path / "journal-target"
    target.write_text("{}", encoding="utf-8")
    (tmp_path / ".catalog-v2-migration-journal.json").symlink_to(target)

    with pytest.raises(GenerationPublicationError):
        migration.recover_catalog_migration(tmp_path)


def test_recovery_fails_closed_for_non_regular_journal(tmp_path: Path) -> None:
    (tmp_path / ".catalog-v2-migration-journal.json").mkdir()

    with pytest.raises(GenerationPublicationError):
        migration.recover_catalog_migration(tmp_path)


def test_recovery_fails_closed_for_malformed_journal(tmp_path: Path) -> None:
    (tmp_path / ".catalog-v2-migration-journal.json").write_text("{", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        migration.recover_catalog_migration(tmp_path)


def _transaction_fixture(tmp_path: Path, monkeypatch) -> tuple[Path, bytes]:
    season = tmp_path
    races = []
    for round_number in (1, 2):
        race_id = f"2024-round-{round_number:02d}"
        races.append({
            "race_id": race_id, "round_number": round_number,
            "generation_id": f"{race_id}-r", "delivery_version": f"{race_id}-r",
            "outcome": "generated", "validated": True,
        })
        (season / "canonical" / race_id).mkdir(parents=True)
        (season / "browser" / race_id).mkdir(parents=True)
    catalog = json.dumps({"year": 2024, "races": races}, sort_keys=True).encode()
    (season / "catalog.json").write_bytes(catalog)
    monkeypatch.setattr(
        migration, "_authoritative_canonical",
        lambda root: (f"{root.name}-r", "a" * 64),
    )
    monkeypatch.setattr(
        migration, "_authoritative_browser",
        lambda root, generation, digest: (f"{generation}-delivery", "b" * 64),
    )
    monkeypatch.setattr(migration, "_track_name", lambda root, version: "Transaction Grand Prix")
    return season, catalog


def test_migration_rolls_back_all_pointer_prefixes_after_later_race_failure(tmp_path: Path, monkeypatch) -> None:
    season, original_catalog = _transaction_fixture(tmp_path, monkeypatch)
    real_writer = migration.write_session_canonical_pointer

    def fail_second_race(root, session_code, generation_id, digest):
        if root.name == "2024-round-02":
            raise OSError("injected second-race failure")
        return real_writer(root, session_code, generation_id, digest)

    monkeypatch.setattr(migration, "write_session_canonical_pointer", fail_second_race)
    with pytest.raises(OSError):
        migrate_catalog_v1_to_v2(season)

    assert (season / "catalog.json").read_bytes() == original_catalog
    assert not (season / "canonical" / "2024-round-01" / "sessions").exists()


def test_migration_rolls_back_canonical_pointer_when_browser_publication_fails(tmp_path: Path, monkeypatch) -> None:
    season, original_catalog = _transaction_fixture(tmp_path, monkeypatch)
    real_writer = migration.write_session_browser_pointer

    def fail_browser(*_args):
        raise OSError("injected browser pointer failure")

    monkeypatch.setattr(migration, "write_session_browser_pointer", fail_browser)
    with pytest.raises(OSError):
        migrate_catalog_v1_to_v2(season)

    assert (season / "catalog.json").read_bytes() == original_catalog
    assert not (season / "canonical" / "2024-round-01" / "sessions").exists()
    assert not (season / "browser" / "2024-round-01" / "sessions").exists()


def test_migration_rolls_back_pointers_when_catalog_publication_fails(tmp_path: Path, monkeypatch) -> None:
    season, original_catalog = _transaction_fixture(tmp_path, monkeypatch)
    old_canonical = b"old canonical pointer"
    old_browser = b"old browser pointer"
    write_session_pointer_bytes(season / "canonical" / "2024-round-01", "r", "current.json", old_canonical)
    write_session_pointer_bytes(season / "browser" / "2024-round-01", "r", "browser-current.json", old_browser)
    real_atomic_write = migration._atomic_write_json

    def fail_catalog(path, value):
        if path.name == "catalog.json":
            raise OSError("injected catalog publication failure")
        return real_atomic_write(path, value)

    monkeypatch.setattr(migration, "_atomic_write_json", fail_catalog)
    with pytest.raises(OSError):
        migrate_catalog_v1_to_v2(season)

    assert (season / "catalog.json").read_bytes() == original_catalog
    assert (season / "canonical" / "2024-round-01" / "sessions" / "r" / "current.json").read_bytes() == old_canonical
    assert (season / "browser" / "2024-round-01" / "sessions" / "r" / "browser-current.json").read_bytes() == old_browser
    for round_number in (1, 2):
        if round_number == 2:
            assert not (season / "canonical" / f"2024-round-{round_number:02d}" / "sessions").exists()
