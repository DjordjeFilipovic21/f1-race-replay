"""Isolated import smoke coverage for the separately packaged pipeline."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from textwrap import dedent


PUBLIC_MODULES = (
    "f1_replay_pipeline",
    "f1_replay_pipeline.domain.canonical_schema",
    "f1_replay_pipeline.domain.normalizers",
    "f1_replay_pipeline.domain.validators",
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
LAZY_CLI_MODULES = (
    "fastf1",
    "f1_replay_pipeline.adapters.fastf1.resolver",
    "f1_replay_pipeline.delivery.browser.browser_delivery_service",
    "f1_replay_pipeline.storage.canonical_writer",
)


def _expected_pipeline_modules() -> list[str]:
    package_root = Path(__file__).parents[1] / "src" / "f1_replay_pipeline"
    names = {"f1_replay_pipeline"}
    for source in package_root.rglob("*.py"):
        relative = source.relative_to(package_root)
        parts = relative.parts[:-1] if relative.name == "__init__.py" else relative.with_suffix("").parts
        names.add(".".join(("f1_replay_pipeline", *parts)))
    return sorted(names)


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


def test_public_pipeline_modules_import_from_installed_package_without_side_effects(tmp_path):
    # Arrange
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
        project_root = str(Path({str(project_root.resolve())!r}))

        assert project_root not in sys.path, sys.path
        imported = {{name: importlib.import_module(name) for name in modules}}
        package_root = Path(imported["f1_replay_pipeline"].__file__).resolve().parent
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


def test_every_shipped_pipeline_module_imports_from_the_installed_package(tmp_path):
    expected_modules = _expected_pipeline_modules()
    snippet = dedent(
        f"""
        import importlib
        from pathlib import Path
        import pkgutil
        import socket

        def deny_network(*args, **kwargs):
            raise AssertionError("network access is forbidden during imports")

        socket.create_connection = deny_network
        socket.socket.connect = deny_network
        socket.socket.connect_ex = deny_network

        package = importlib.import_module("f1_replay_pipeline")
        package_root = Path(package.__file__).resolve().parent
        module_names = ["f1_replay_pipeline", *sorted(
            module.name
            for module in pkgutil.walk_packages(package.__path__, package.__name__ + ".")
        )]
        assert module_names == {expected_modules!r}

        imported = [importlib.import_module(name) for name in module_names]
        module_files = [
            Path(module.__file__).resolve()
            for module in imported
            if getattr(module, "__file__", None)
        ]
        assert all(path.is_relative_to(package_root) for path in module_files)
        """
    )

    result = _run_isolated_python(snippet, tmp_path)

    assert result.returncode == 0, result.stderr


def test_top_level_pipeline_import_does_not_load_polars_or_fastf1(tmp_path):
    snippet = dedent(
        """
        import importlib
        import sys

        module = importlib.import_module("f1_replay_pipeline")
        assert module.__file__
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


def test_cli_import_keeps_runtime_composition_modules_lazy(tmp_path):
    snippet = dedent(
        f"""
        import importlib
        import sys

        importlib.import_module("f1_replay_pipeline.app.cli")
        lazy_modules = {LAZY_CLI_MODULES!r}
        loaded = sorted(
            name for name in sys.modules
            if any(name == module or name.startswith(module + ".") for module in lazy_modules)
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
