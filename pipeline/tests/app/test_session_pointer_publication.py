"""Filesystem behavior for replaceable session pointer snapshots."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from f1_replay_pipeline.app.session_pointer_publication import (
    read_session_canonical_pointer,
    read_session_browser_pointer,
    read_optional_session_pointer,
    write_session_canonical_pointer,
    write_session_browser_pointer,
)
import f1_replay_pipeline.app.session_pointer_publication as session_pointers


def test_session_canonical_pointer_replacement_resolves_from_race_root(tmp_path: Path, monkeypatch) -> None:
    canonical = tmp_path / "canonical" / "race"
    generation = canonical / "generations" / "race-r"
    generation.mkdir(parents=True)
    manifest = {
        "format_version": "canonical-parquet-v1", "manifest_version": 1,
        "generation_id": "race-r", "tables": [], "writer_settings": {},
    }
    (generation / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    digest = hashlib.sha256((generation / "manifest.json").read_bytes()).hexdigest()
    successor = canonical / "generations" / "race-r-force-1"
    successor.mkdir()
    successor_payload = b'{"generation_id":"race-r-force-1"}'
    (successor / "manifest.json").write_bytes(successor_payload)
    monkeypatch.setattr(
        session_pointers,
        "parse_manifest",
        lambda payload: SimpleNamespace(generation_id=json.loads(payload)["generation_id"]),
    )
    original_payload = (generation / "manifest.json").read_bytes()
    write_session_canonical_pointer(canonical, "r", "race-r", digest)
    assert (canonical / "sessions" / "r" / "current.json").is_file()
    write_session_canonical_pointer(
        canonical, "r", "race-r-force-1", hashlib.sha256(successor_payload).hexdigest(),
    )
    assert read_session_canonical_pointer(canonical, "r").generation_path == successor
    assert (generation / "manifest.json").read_bytes() == original_payload


def test_browser_pointer_uses_existing_browser_pointer_shape(tmp_path: Path) -> None:
    browser = tmp_path / "browser"
    (browser / "generations" / "delivery").mkdir(parents=True)
    (browser / "generations" / "delivery" / "manifest.json").write_text(json.dumps({"deliveryVersion": "delivery"}), encoding="utf-8")
    successor = browser / "generations" / "delivery-browser-1"
    successor.mkdir()
    (successor / "manifest.json").write_text(json.dumps({"deliveryVersion": "delivery-browser-1"}), encoding="utf-8")
    digest = hashlib.sha256((browser / "generations" / "delivery" / "manifest.json").read_bytes()).hexdigest()
    successor_digest = hashlib.sha256((successor / "manifest.json").read_bytes()).hexdigest()
    path = write_session_browser_pointer(browser, "r", "delivery", digest)
    pointer = json.loads(path.read_bytes())
    assert pointer["formatVersion"] == "browser-delivery-v2"
    assert "manifestPath" in pointer
    write_session_browser_pointer(browser, "r", "delivery-browser-1", successor_digest)
    assert json.loads((browser / "sessions" / "r" / "browser-current.json").read_bytes())["deliveryVersion"] == "delivery-browser-1"


def test_session_pointer_readers_reject_manifest_checksum_changes(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical"
    generation = canonical / "generations" / "race-r"
    generation.mkdir(parents=True)
    payload = b'{"not":"a canonical manifest"}'
    (generation / "manifest.json").write_bytes(payload)
    write_session_canonical_pointer(canonical, "r", "race-r", hashlib.sha256(payload).hexdigest())
    (generation / "manifest.json").write_bytes(payload + b"changed")
    with pytest.raises(Exception):
        read_session_canonical_pointer(canonical, "r")


def test_browser_session_pointer_reader_rejects_bad_checksum(tmp_path: Path) -> None:
    browser = tmp_path / "browser"
    generation = browser / "generations" / "delivery"
    generation.mkdir(parents=True)
    (generation / "manifest.json").write_text(json.dumps({"deliveryVersion": "delivery"}), encoding="utf-8")
    write_session_browser_pointer(browser, "r", "delivery", "a" * 64)
    with pytest.raises(Exception):
        read_session_browser_pointer(browser, "r")


def test_session_pointer_creation_rejects_symlinked_sessions_directory(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical"
    external = tmp_path / "external"
    canonical.mkdir()
    external.mkdir()
    (canonical / "sessions").symlink_to(external, target_is_directory=True)

    with pytest.raises(OSError):
        write_session_canonical_pointer(canonical, "r", "race-r", "a" * 64)
    assert not (external / "r" / "current.json").exists()


def test_session_pointer_creation_rejects_symlinked_session_directory(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical"
    external = tmp_path / "external"
    canonical.mkdir()
    external.mkdir()
    (canonical / "sessions").mkdir()
    (canonical / "sessions" / "r").symlink_to(external, target_is_directory=True)

    with pytest.raises(OSError):
        write_session_canonical_pointer(canonical, "r", "race-r", "a" * 64)
    assert not (external / "current.json").exists()


def test_descriptor_relative_replacement_survives_session_directory_substitution(tmp_path: Path, monkeypatch) -> None:
    canonical = tmp_path / "canonical"
    external = tmp_path / "external"
    canonical.mkdir()
    external.mkdir()
    (canonical / "sessions" / "r").mkdir(parents=True)
    real_replace = session_pointers.os.replace
    substituted = {"done": False}

    def substitute(source, destination, *, src_dir_fd=None, dst_dir_fd=None):
        if not substituted["done"]:
            original = canonical / "sessions" / "r"
            original.rename(canonical / "sessions" / "r-original")
            original.symlink_to(external, target_is_directory=True)
            substituted["done"] = True
        return real_replace(source, destination, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    monkeypatch.setattr(session_pointers.os, "replace", substitute)
    write_session_canonical_pointer(canonical, "r", "race-r", "a" * 64)

    assert not (external / "current.json").exists()
    assert (canonical / "sessions" / "r-original" / "current.json").is_file()


def test_optional_session_pointer_returns_none_for_absent_sessions_directory(tmp_path: Path) -> None:
    root = tmp_path / "canonical"
    root.mkdir()

    assert read_optional_session_pointer(root, "r", "current.json") is None


def test_optional_session_pointer_returns_none_for_absent_session_directory(tmp_path: Path) -> None:
    root = tmp_path / "canonical"
    (root / "sessions").mkdir(parents=True)

    assert read_optional_session_pointer(root, "r", "current.json") is None


def test_optional_session_pointer_returns_none_for_absent_pointer_leaf(tmp_path: Path) -> None:
    root = tmp_path / "canonical"
    (root / "sessions" / "r").mkdir(parents=True)

    assert read_optional_session_pointer(root, "r", "current.json") is None


@pytest.mark.parametrize("level", ("sessions", "session", "pointer"))
def test_optional_session_pointer_rejects_symlinks(tmp_path: Path, level: str) -> None:
    root = tmp_path / "canonical"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()
    if level == "sessions":
        (root / "sessions").symlink_to(external, target_is_directory=True)
    else:
        (root / "sessions" / "r").mkdir(parents=True)
        if level == "session":
            (root / "sessions" / "r").rename(root / "sessions" / "r-real")
            (root / "sessions" / "r").symlink_to(external, target_is_directory=True)
        else:
            (root / "sessions" / "r" / "current.json").symlink_to(external / "pointer")

    with pytest.raises(OSError):
        read_optional_session_pointer(root, "r", "current.json")


def test_optional_session_pointer_rejects_non_regular_leaf(tmp_path: Path) -> None:
    root = tmp_path / "canonical"
    (root / "sessions" / "r" / "current.json").mkdir(parents=True)

    with pytest.raises(ValueError):
        read_optional_session_pointer(root, "r", "current.json")


def test_optional_session_pointer_returns_immutable_existing_bytes(tmp_path: Path) -> None:
    root = tmp_path / "canonical"
    payload = b"existing pointer bytes"
    pointer = root / "sessions" / "r" / "current.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_bytes(payload)

    assert read_optional_session_pointer(root, "r", "current.json") == payload


@pytest.mark.parametrize("session_code", ["../escape", "/absolute", "nested/code", "nested\\code", "nul\x00code"])
def test_session_pointer_writers_reject_malicious_session_components(
    tmp_path: Path, session_code: str,
) -> None:
    # Arrange
    root = tmp_path / "canonical"

    # Act / Assert
    with pytest.raises(ValueError, match="safe path component"):
        write_session_canonical_pointer(root, session_code, "generation", "a" * 64)
    assert not root.exists()


def test_session_browser_reader_rejects_historical_v1_manifest(tmp_path: Path) -> None:
    # Arrange
    browser = tmp_path / "browser"
    generation = browser / "generations" / "delivery"
    generation.mkdir(parents=True)
    manifest = json.dumps({
        "formatVersion": "browser-delivery-v1",
        "contractVersion": "v1",
        "deliveryVersion": "delivery",
    }, separators=(",", ":")).encode() + b"\n"
    (generation / "manifest.json").write_bytes(manifest)
    digest = hashlib.sha256(manifest).hexdigest()
    write_session_browser_pointer(browser, "r", "delivery", digest)

    # Act / Assert
    with pytest.raises(ValueError, match="contract identity"):
        read_session_browser_pointer(browser, "r")
