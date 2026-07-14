"""Isolated import smoke coverage for the separately packaged pipeline."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from textwrap import dedent


PUBLIC_MODULES = (
    "f1_replay_pipeline",
    "f1_replay_pipeline.canonical_schema",
    "f1_replay_pipeline.normalizers",
    "f1_replay_pipeline.validators",
)
FORBIDDEN_MODULE_PREFIXES = (
    "src",
    "fastf1",
    "arcade",
    "glfw",
    "matplotlib",
    "OpenGL",
    "pygame",
    "PySide6",
    "pyglet",
    "tkinter",
)


def _run_isolated_python(snippet: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Run a snippet without project-path or environment-variable leakage."""
    return subprocess.run(
        [sys.executable, "-I", "-c", snippet],
        capture_output=True,
        check=False,
        cwd=tmp_path,
        env={},
        text=True,
    )


def test_public_pipeline_modules_import_from_editable_install_without_side_effects(tmp_path):
    # Arrange
    package_root = Path(__file__).parents[1] / "src" / "f1_replay_pipeline"
    project_root = Path(__file__).parents[2]
    snippet = dedent(
        f"""
        import importlib
        from pathlib import Path
        import socket
        import sys

        class NetworkAccessError(AssertionError):
            pass

        def deny_network(*args, **kwargs):
            raise NetworkAccessError("network access is forbidden during imports")

        socket.create_connection = deny_network
        socket.socket.connect = deny_network
        socket.socket.connect_ex = deny_network

        modules = {list(PUBLIC_MODULES)!r}
        forbidden_prefixes = {FORBIDDEN_MODULE_PREFIXES!r}
        package_root = Path({str(package_root.resolve())!r})
        project_root = str(Path({str(project_root.resolve())!r}))

        assert project_root not in sys.path, sys.path
        imported = {{name: importlib.import_module(name) for name in modules}}
        module_files = {{name: Path(module.__file__).resolve() for name, module in imported.items()}}
        assert all(path.is_relative_to(package_root) for path in module_files.values())

        loaded_forbidden = sorted(
            name for name in sys.modules
            if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden_prefixes)
        )
        assert not loaded_forbidden, loaded_forbidden

        """
    )

    # Act
    result = _run_isolated_python(snippet, tmp_path)

    # Assert
    assert result.returncode == 0, result.stderr


def test_top_level_pipeline_import_does_not_load_polars_or_fastf1(tmp_path):
    package_root = Path(__file__).parents[1] / "src" / "f1_replay_pipeline"
    snippet = dedent(
        f"""
        import importlib
        import sys
        from pathlib import Path

        module = importlib.import_module("f1_replay_pipeline")
        assert Path(module.__file__).resolve().is_relative_to(Path({str(package_root.resolve())!r}))
        forbidden = ("polars", "fastf1")
        loaded = sorted(
            name for name in sys.modules
            if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
        )
        assert not loaded, loaded
        """
    )

    result = _run_isolated_python(snippet, tmp_path)

    assert result.returncode == 0, result.stderr


def test_import_smoke_network_guard_rejects_connection_apis(tmp_path):
    # Arrange
    snippet = dedent(
        """
        import socket

        class NetworkAccessError(AssertionError):
            pass

        def deny_network(*args, **kwargs):
            raise NetworkAccessError("network access is forbidden during imports")

        socket.create_connection = deny_network
        socket.socket.connect = deny_network
        socket.socket.connect_ex = deny_network

        probes = (
            lambda: socket.create_connection(("example.invalid", 443)),
            lambda: socket.socket().connect(("127.0.0.1", 1)),
            lambda: socket.socket().connect_ex(("127.0.0.1", 1)),
        )
        blocked = 0
        for probe in probes:
            try:
                probe()
            except NetworkAccessError:
                blocked += 1
            else:
                raise AssertionError("network guard allowed a connection")

        assert blocked == len(probes)
        """
    )

    # Act
    result = _run_isolated_python(snippet, tmp_path)

    # Assert
    assert result.returncode == 0, result.stderr
