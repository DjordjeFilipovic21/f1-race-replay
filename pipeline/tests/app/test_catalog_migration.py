"""Offline tests for the v2-only catalog cutover boundary."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import f1_replay_pipeline.app.catalog_migration as migration
import f1_replay_pipeline.app.session_pointer_publication as session_pointers
from f1_replay_pipeline.app.catalog_migration import (
    migrate_catalog_v1_to_v2,
    validate_active_catalog_references,
)
from f1_replay_pipeline.app.catalog_v2_schema import CatalogV2Payload, CatalogV2RaceRecord, CatalogV2SessionRecord
from f1_replay_pipeline.app.session_pointer_publication import (
    deterministic_session_browser_pointer_bytes,
    write_session_browser_pointer,
    write_session_canonical_pointer,
)
from f1_replay_pipeline.storage.generation_publication import (
    GenerationPublicationError,
    deterministic_pointer_bytes,
)


def test_v1_catalog_is_rejected_without_mutating_the_historical_payload(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    original = json.dumps({"year": 2024, "races": []}, sort_keys=True).encode()
    catalog.write_bytes(original)

    with pytest.raises(ValueError, match="republish all four races as v2"):
        migrate_catalog_v1_to_v2(tmp_path)

    assert catalog.read_bytes() == original


def test_v2_cutover_requires_the_four_scheduled_races(tmp_path: Path) -> None:
    races = tuple(
        CatalogV2RaceRecord(
            f"2026-round-{number:02d}", number, "Race", (
                CatalogV2SessionRecord(
                    "r", "Race", f"2026-round-{number:02d}-r", f"2026-round-{number:02d}-r",
                    "generated", True, None,
                    f"browser/2026-round-{number:02d}/sessions/r/browser-current.json",
                ),
            ),
        )
        for number in range(1, 4)
    )
    (tmp_path / "catalog.json").write_bytes(CatalogV2Payload(2026, races).to_json_bytes())

    with pytest.raises(ValueError, match="missing republished races"):
        migrate_catalog_v1_to_v2(
            tmp_path,
            schedule=(
                "2026-round-01", "2026-round-02", "2026-round-03", "2026-round-04",
            ),
        )


def test_active_v2_catalog_preflight_validates_both_pointer_formats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    race_id = "2026-round-01"
    generation_id = f"{race_id}-r"
    delivery_version = f"{generation_id}-delivery"
    canonical = tmp_path / "canonical" / race_id
    browser = tmp_path / "browser" / race_id
    (canonical / "generations" / generation_id).mkdir(parents=True)
    (browser / "generations" / delivery_version).mkdir(parents=True)

    canonical_manifest = b"canonical-v2"
    (canonical / "generations" / generation_id / "manifest.json").write_bytes(canonical_manifest)
    canonical_digest = hashlib.sha256(canonical_manifest).hexdigest()
    (canonical / "current.json").write_bytes(json.dumps({
        "format_version": "canonical-parquet-v2",
        "generation_id": generation_id,
        "manifest_path": f"generations/{generation_id}/manifest.json",
        "manifest_sha256": canonical_digest,
    }, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    monkeypatch.setattr(
        migration, "parse_manifest",
        lambda _payload: SimpleNamespace(
            generation_id=generation_id, format_version="canonical-parquet-v2", manifest_version=2,
        ),
    )
    monkeypatch.setattr(
        session_pointers, "parse_manifest",
        lambda _payload: SimpleNamespace(
            generation_id=generation_id, format_version="canonical-parquet-v2", manifest_version=2,
        ),
    )

    browser_manifest = json.dumps({
        "formatVersion": "browser-delivery-v2",
        "contractVersion": "v2",
        "deliveryVersion": delivery_version,
        "sourceGenerationId": generation_id,
        "sourceManifestSha256": canonical_digest,
    }, sort_keys=True, separators=(",", ":")).encode()
    browser_manifest_path = browser / "generations" / delivery_version / "manifest.json"
    browser_manifest_path.write_bytes(browser_manifest)
    (browser / "browser-current.json").write_bytes(json.dumps({
        "formatVersion": "browser-delivery-v2",
        "deliveryVersion": delivery_version,
        "manifestPath": f"generations/{delivery_version}/manifest.json",
        "manifestSha256": hashlib.sha256(browser_manifest).hexdigest(),
    }, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    write_session_canonical_pointer(canonical, "r", generation_id, canonical_digest)
    write_session_browser_pointer(
        browser, "r", delivery_version, hashlib.sha256(browser_manifest).hexdigest(),
    )

    session = CatalogV2SessionRecord(
        "r", "Race", generation_id, delivery_version, "generated", True,
        f"canonical/{race_id}/sessions/r/current.json",
        f"browser/{race_id}/sessions/r/browser-current.json",
    )
    catalog = CatalogV2Payload(
        2026, (CatalogV2RaceRecord(race_id, 1, "Race", (session,)),),
    ).to_json_bytes()
    (tmp_path / "catalog.json").write_bytes(catalog)

    assert validate_active_catalog_references(tmp_path).name == "catalog.json"
    assert migrate_catalog_v1_to_v2(tmp_path).name == "catalog.json"


def test_canonical_v1_pointer_is_not_eligible_for_active_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "canonical"
    generation = root / "generations" / "generation"
    generation.mkdir(parents=True)
    manifest = b"historical-v1"
    (generation / "manifest.json").write_bytes(manifest)
    (root / "current.json").write_bytes(json.dumps({
        "format_version": "canonical-parquet-v1", "generation_id": "generation",
        "manifest_path": "generations/generation/manifest.json",
        "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
    }, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    monkeypatch.setattr(
        migration, "parse_manifest",
        lambda _payload: SimpleNamespace(
            generation_id="generation", format_version="canonical-parquet-v1", manifest_version=1,
        ),
    )

    with pytest.raises(ValueError, match="canonical-parquet-v1.*deprecated"):
        migration._authoritative_canonical(root)


def test_browser_v1_pointer_is_not_eligible_for_active_catalog(tmp_path: Path) -> None:
    root = tmp_path / "browser"
    (root / "generations" / "delivery").mkdir(parents=True)
    manifest = b"browser-v1"
    (root / "generations" / "delivery" / "manifest.json").write_bytes(manifest)
    (root / "browser-current.json").write_bytes(json.dumps({
        "formatVersion": "browser-delivery-v1", "deliveryVersion": "delivery",
        "manifestPath": "generations/delivery/manifest.json",
        "manifestSha256": hashlib.sha256(manifest).hexdigest(),
    }, sort_keys=True, separators=(",", ":")).encode() + b"\n")

    with pytest.raises(ValueError, match="browser-delivery-v1.*deprecated"):
        migration._authoritative_browser(root, "generation", "a" * 64)


def test_guarded_track_asset_reader_rejects_unsafe_paths(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        migration._read_guarded_relative_file(tmp_path, "../outside", "track assets")


def test_recovery_treats_only_an_absent_journal_as_normal(tmp_path: Path) -> None:
    migration.recover_catalog_migration(tmp_path)


def test_recovery_fails_closed_for_symlink_journal(tmp_path: Path) -> None:
    target = tmp_path / "journal-target"
    target.write_text("{}", encoding="utf-8")
    (tmp_path / ".catalog-v2-migration-journal.json").symlink_to(target)

    with pytest.raises(GenerationPublicationError):
        migration.recover_catalog_migration(tmp_path)


def test_recovery_rejects_journal_pointer_that_would_restore_v1(tmp_path: Path) -> None:
    race_id = "2026-round-01"
    (tmp_path / "catalog.json").write_text(json.dumps({
        "year": 2026,
        "races": [{"race_id": race_id, "sessions": [{"session_code": "r"}]}],
    }), encoding="utf-8")
    previous = deterministic_pointer_bytes(
        "2026-round-01-r", "a" * 64, format_version="canonical-parquet-v1",
    )
    journal = {
        "races": [{
            "race_id": race_id,
            "session_code": "r",
            "canonical_previous": base64.b64encode(previous).decode(),
            "browser_previous": None,
            "canonical_parent_existed": False,
            "browser_parent_existed": False,
        }],
    }
    (tmp_path / ".catalog-v2-migration-journal.json").write_text(json.dumps(journal), encoding="utf-8")

    with pytest.raises(ValueError, match="historical canonical pointer"):
        migration.recover_catalog_migration(tmp_path)


def test_recovery_rejects_malformed_journal_before_touching_pointers(tmp_path: Path) -> None:
    (tmp_path / "catalog.json").write_text(json.dumps({"year": 2026, "races": []}), encoding="utf-8")
    journal_path = tmp_path / ".catalog-v2-migration-journal.json"
    journal_path.write_text(json.dumps({"unexpected": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid shape"):
        migration.recover_catalog_migration(tmp_path)

    assert journal_path.is_file()
    assert not (tmp_path / "canonical").exists()
    assert not (tmp_path / "browser").exists()


def test_recovery_restores_only_validated_v2_session_pointers(tmp_path: Path) -> None:
    race_id = "2026-round-01"
    session_code = "r"
    generation_id = f"{race_id}-session-race-mode-race"
    delivery_version = f"{generation_id}-browser-1"
    (tmp_path / "canonical" / race_id).mkdir(parents=True)
    (tmp_path / "browser" / race_id).mkdir(parents=True)
    (tmp_path / "catalog.json").write_text(json.dumps({
        "year": 2026,
        "races": [{"race_id": race_id, "sessions": [{"session_code": session_code}]}],
    }), encoding="utf-8")
    canonical_previous = deterministic_pointer_bytes(
        generation_id, "a" * 64, format_version="canonical-parquet-v2",
    )
    browser_previous = deterministic_session_browser_pointer_bytes(delivery_version, "b" * 64)
    journal = {
        "races": [{
            "race_id": race_id,
            "session_code": session_code,
            "canonical_previous": base64.b64encode(canonical_previous).decode(),
            "browser_previous": base64.b64encode(browser_previous).decode(),
            "canonical_parent_existed": False,
            "browser_parent_existed": False,
        }],
    }
    journal_path = tmp_path / ".catalog-v2-migration-journal.json"
    journal_path.write_text(json.dumps(journal), encoding="utf-8")

    migration.recover_catalog_migration(tmp_path)

    assert not journal_path.exists()
    assert (tmp_path / "canonical" / race_id / "sessions" / session_code / "current.json").read_bytes() == canonical_previous
    assert (tmp_path / "browser" / race_id / "sessions" / session_code / "browser-current.json").read_bytes() == browser_previous
