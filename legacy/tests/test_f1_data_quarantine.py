"""Focused quarantine-boundary tests for the legacy src.f1_data pickle cache.

The legacy computed-data cache is stored as pickle, and unpickling untrusted
files can execute arbitrary code. Reading and writing that cache is therefore
quarantined behind an explicit opt-in flag (``F1_LEGACY_ALLOW_PICKLE_CACHE=1``).

These tests are white-box checks of the quarantine guards themselves: they
prove the default runtime path never reaches ``pickle.load``/``pickle.dump``,
and that the historical cache is only usable when the operator explicitly opts
in. When the boundary is open, the cache path is additionally constrained to a
trusted cache root: traversal, symlinked components, and non-regular targets
are rejected before any load or write, so the opt-in cannot be redirected to
attacker-controlled pickle code or used to write arbitrary files. This boundary
is not a V1 replay compatibility path; it only re-enables the legacy
application's own computed-data cache.
"""

import os
import pickle
import sys
from pathlib import Path

import pytest

OPTIONAL_DEPENDENCIES = {
    "arcade",
    "fastf1",
    "matplotlib",
    "numpy",
    "pandas",
    "pyglet",
    "PySide6",
    "questionary",
    "rich",
}

try:
    from src.f1_data import (
        LEGACY_PICKLE_CACHE_ENV,
        _legacy_cache_root,
        _legacy_pickle_cache_allowed,
        _load_legacy_pickle_cache,
        _write_legacy_pickle_cache,
    )
except ModuleNotFoundError as exc:
    missing_dependency = (exc.name or "").split(".")[0]

    if missing_dependency in OPTIONAL_DEPENDENCIES:
        pytest.skip(f"optional dependency not installed: {missing_dependency}")

    raise


def _make_symlink(source, link_name):
    """Create a symlink, skipping the test when the platform forbids it."""
    try:
        os.symlink(str(source), str(link_name))
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create symlink on this platform: {exc}")


@pytest.fixture
def cache_file(tmp_path):
    """A valid legacy pickle cache payload beneath a trusted temp cache root."""
    payload = {"frames": [{"t": 0.0}], "total_laps": 57}
    root = tmp_path
    path = root / "nested" / "event_telemetry.pkl"
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)

    return root, path, payload


class _FakeSettings:
    """Minimal settings stand-in returning a fixed computed-data location."""

    def __init__(self, value):
        self._value = value

    def get(self, key):
        return self._value


def test_legacy_pickle_cache_disabled_by_default(monkeypatch):
    """Without any environment override the boundary is closed."""
    monkeypatch.delenv(LEGACY_PICKLE_CACHE_ENV, raising=False)

    assert _legacy_pickle_cache_allowed() is False


def test_legacy_pickle_cache_requires_explicit_opt_in_value(monkeypatch):
    """Only the exact "1" value opens the boundary; anything else stays closed."""
    monkeypatch.setenv(LEGACY_PICKLE_CACHE_ENV, "1")
    assert _legacy_pickle_cache_allowed() is True

    for value in ("0", "true", "yes", ""):
        monkeypatch.setenv(LEGACY_PICKLE_CACHE_ENV, value)
        assert _legacy_pickle_cache_allowed() is False


def test_default_path_never_reaches_pickle_load(monkeypatch, cache_file):
    """The default path ignores an existing cache without calling pickle.load."""
    root, cache_path, _ = cache_file

    def _fail_load(*args, **kwargs):
        raise AssertionError("pickle.load reached on the default runtime path")

    monkeypatch.delenv(LEGACY_PICKLE_CACHE_ENV, raising=False)
    monkeypatch.setattr(pickle, "load", _fail_load)

    assert _load_legacy_pickle_cache(
        str(cache_path), cache_root=str(root)
    ) is None


def test_opt_in_loads_existing_pickle_cache(monkeypatch, cache_file):
    """With the opt-in flag set, an existing cache file is loaded."""
    root, cache_path, payload = cache_file

    monkeypatch.setenv(LEGACY_PICKLE_CACHE_ENV, "1")

    # The boundary rejects absolute cache paths; only the relative path beneath
    # the trusted cache root is a legitimate opt-in load (as in the write test).
    relative_cache_path = cache_path.relative_to(root)

    assert (
        _load_legacy_pickle_cache(
            str(relative_cache_path), cache_root=str(root)
        )
        == payload
    )


def test_missing_cache_returns_none(monkeypatch, tmp_path):
    """A missing cache returns None even when the opt-in flag is set."""
    monkeypatch.setenv(LEGACY_PICKLE_CACHE_ENV, "1")

    assert (
        _load_legacy_pickle_cache("missing.pkl", cache_root=str(tmp_path)) is None
    )


def test_refresh_data_skips_cache_even_with_opt_in(monkeypatch, cache_file):
    """--refresh-data forces recomputation even with the opt-in flag set."""
    root, cache_path, _ = cache_file

    monkeypatch.setenv(LEGACY_PICKLE_CACHE_ENV, "1")
    monkeypatch.setattr(sys, "argv", ["main.py", "--refresh-data"])

    assert (
        _load_legacy_pickle_cache(str(cache_path), cache_root=str(root)) is None
    )


def test_default_path_never_reaches_pickle_dump(monkeypatch, tmp_path):
    """The default path never creates or writes pickle cache files."""
    target = tmp_path / "event_telemetry.pkl"

    def _fail_dump(*args, **kwargs):
        raise AssertionError("pickle.dump reached on the default runtime path")

    monkeypatch.delenv(LEGACY_PICKLE_CACHE_ENV, raising=False)
    monkeypatch.setattr(pickle, "dump", _fail_dump)

    _write_legacy_pickle_cache(
        "event_telemetry.pkl", {"frames": []}, cache_root=str(tmp_path)
    )

    assert target.exists() is False


def test_opt_in_writes_pickle_cache(monkeypatch, tmp_path):
    """With the opt-in flag set, the cache is written and reads back intact."""
    target = tmp_path / "nested" / "event_telemetry.pkl"
    payload = {"frames": [{"t": 0.0}]}

    monkeypatch.setenv(LEGACY_PICKLE_CACHE_ENV, "1")
    _write_legacy_pickle_cache(
        "nested/event_telemetry.pkl", payload, cache_root=str(tmp_path)
    )

    assert target.exists() is True

    with open(target, "rb") as f:
        assert pickle.load(f) == payload


def test_opt_in_write_creates_missing_root(monkeypatch, tmp_path):
    """The writer creates a missing cache root, like the legacy first run."""
    root = tmp_path / "computed_data"
    payload = {"frames": []}

    monkeypatch.setenv(LEGACY_PICKLE_CACHE_ENV, "1")
    _write_legacy_pickle_cache(
        "event_telemetry.pkl", payload, cache_root=str(root)
    )

    assert (root / "event_telemetry.pkl").exists() is True


def test_traversal_load_rejected_before_pickle(monkeypatch, tmp_path):
    """Traversal cache paths are rejected without reading outside the root."""
    root = tmp_path
    outside = tmp_path.parent / "evil_outside.pkl"
    with open(outside, "wb") as f:
        pickle.dump({"pwned": True}, f, protocol=pickle.HIGHEST_PROTOCOL)

    def _fail_load(*args, **kwargs):
        raise AssertionError("pickle.load reached for a traversal cache path")

    monkeypatch.setenv(LEGACY_PICKLE_CACHE_ENV, "1")
    monkeypatch.setattr(pickle, "load", _fail_load)

    assert _load_legacy_pickle_cache(
        "../evil_outside.pkl", cache_root=str(root)
    ) is None
    assert _load_legacy_pickle_cache(
        str(outside), cache_root=str(root)
    ) is None
    assert _load_legacy_pickle_cache(
        "a/../../evil_outside.pkl", cache_root=str(root)
    ) is None


def test_traversal_write_rejected_no_arbitrary_file(monkeypatch, tmp_path):
    """Traversal cache writes are rejected and create no file outside root."""
    root = tmp_path
    outside = tmp_path.parent / "evil_outside_write.pkl"

    def _fail_dump(*args, **kwargs):
        raise AssertionError("pickle.dump reached for a traversal cache path")

    monkeypatch.setenv(LEGACY_PICKLE_CACHE_ENV, "1")
    monkeypatch.setattr(pickle, "dump", _fail_dump)

    _write_legacy_pickle_cache(
        "../evil_outside_write.pkl", {"frames": []}, cache_root=str(root)
    )

    assert outside.exists() is False


def test_symlinked_cache_file_rejected(monkeypatch, tmp_path):
    """A cache file that is a symlink is rejected instead of followed."""
    root = tmp_path
    outside = tmp_path.parent / "real_payload.pkl"
    with open(outside, "wb") as f:
        pickle.dump({"pwned": True}, f, protocol=pickle.HIGHEST_PROTOCOL)
    _make_symlink(outside, root / "link.pkl")

    def _fail_load(*args, **kwargs):
        raise AssertionError("pickle.load reached for a symlinked cache file")

    monkeypatch.setenv(LEGACY_PICKLE_CACHE_ENV, "1")
    monkeypatch.setattr(pickle, "load", _fail_load)

    assert _load_legacy_pickle_cache(
        "link.pkl", cache_root=str(root)
    ) is None


def test_symlinked_parent_directory_rejected(monkeypatch, tmp_path):
    """A symlinked parent directory is rejected instead of followed."""
    root = tmp_path
    outside = tmp_path.parent / "real_parent"
    outside.mkdir(exist_ok=True)
    with open(outside / "victim.pkl", "wb") as f:
        pickle.dump({"pwned": True}, f, protocol=pickle.HIGHEST_PROTOCOL)
    _make_symlink(outside, root / "linked")

    def _fail_load(*args, **kwargs):
        raise AssertionError("pickle.load reached through a symlinked parent")

    monkeypatch.setenv(LEGACY_PICKLE_CACHE_ENV, "1")
    monkeypatch.setattr(pickle, "load", _fail_load)

    assert _load_legacy_pickle_cache(
        "linked/victim.pkl", cache_root=str(root)
    ) is None


def test_write_rejects_symlinked_target_without_touching_target(
    monkeypatch, tmp_path
):
    """Writing through a symlinked final component leaves the target intact."""
    root = tmp_path
    outside = tmp_path.parent / "victim_write.pkl"
    original = b"original-payload"
    outside.write_bytes(original)
    _make_symlink(outside, root / "link.pkl")

    def _fail_dump(*args, **kwargs):
        raise AssertionError("pickle.dump reached for a symlinked cache target")

    monkeypatch.setenv(LEGACY_PICKLE_CACHE_ENV, "1")
    monkeypatch.setattr(pickle, "dump", _fail_dump)

    _write_legacy_pickle_cache(
        "link.pkl", {"frames": []}, cache_root=str(root)
    )

    assert outside.read_bytes() == original
    assert (root / "link.pkl").is_symlink()


def test_write_rejects_symlinked_parent_without_arbitrary_write(
    monkeypatch, tmp_path
):
    """Writing through a symlinked parent creates nothing outside the root."""
    root = tmp_path
    outside = tmp_path.parent / "write_parent"
    outside.mkdir(exist_ok=True)
    _make_symlink(outside, root / "linked")

    def _fail_dump(*args, **kwargs):
        raise AssertionError("pickle.dump reached through a symlinked parent")

    monkeypatch.setenv(LEGACY_PICKLE_CACHE_ENV, "1")
    monkeypatch.setattr(pickle, "dump", _fail_dump)

    _write_legacy_pickle_cache(
        "linked/new.pkl", {"frames": []}, cache_root=str(root)
    )

    assert (outside / "new.pkl").exists() is False


def test_directory_target_rejected(monkeypatch, tmp_path):
    """A directory at the cache path is rejected as a non-regular target."""
    root = tmp_path
    (root / "dir.pkl").mkdir()

    def _fail_load(*args, **kwargs):
        raise AssertionError("pickle.load reached for a directory cache target")

    monkeypatch.setenv(LEGACY_PICKLE_CACHE_ENV, "1")
    monkeypatch.setattr(pickle, "load", _fail_load)

    assert _load_legacy_pickle_cache(
        "dir.pkl", cache_root=str(root)
    ) is None


def test_fifo_target_rejected(monkeypatch, tmp_path):
    """A FIFO at the cache path is rejected without blocking on open."""
    if not hasattr(os, "mkfifo"):
        pytest.skip("os.mkfifo is not available on this platform")

    root = tmp_path
    os.mkfifo(root / "fifo.pkl")

    def _fail_load(*args, **kwargs):
        raise AssertionError("pickle.load reached for a FIFO cache target")

    monkeypatch.setenv(LEGACY_PICKLE_CACHE_ENV, "1")
    monkeypatch.setattr(pickle, "load", _fail_load)

    assert _load_legacy_pickle_cache(
        "fifo.pkl", cache_root=str(root)
    ) is None


def test_legacy_cache_root_derived_from_settings(monkeypatch, tmp_path):
    """The trusted root comes from the configured computed-data location."""
    monkeypatch.setattr(
        "src.f1_data.get_settings", lambda: _FakeSettings(str(tmp_path))
    )

    assert _legacy_cache_root() == tmp_path.resolve()


def test_legacy_cache_root_defaults_to_computed_data(monkeypatch):
    """Without a configured location the root defaults to computed_data/."""
    monkeypatch.setattr(
        "src.f1_data.get_settings", lambda: _FakeSettings(None)
    )

    assert _legacy_cache_root() == Path("computed_data").resolve()
