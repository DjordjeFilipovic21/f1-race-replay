"""Reproducibility tests proving the committed constraints artifacts stay valid.

The pipeline and legacy dependency manifests declare top-level version ranges,
while pipeline/constraints.txt and legacy/constraints.txt pin the exact resolver
result. pipeline/test-constraints.txt mirrors the contract-unit test-extras pins
from legacy/constraints.txt exactly. These tests parse the committed artifacts
only (never the network, pip, or a subprocess) and assert:

- every pinned version satisfies the range declared for the same package,
- every top-level declared package appears in its constraints artifact, and
- every pipeline test-extras pin matches the exact legacy/constraints.txt pin.

Refreshing a constraints file with pip-tools must therefore keep every pin
inside the declared range, must not drop a declared top-level package, and must
keep pipeline/test-constraints.txt aligned with the legacy pins.

Scope boundary: these offline checks prove declared-range pinning and
top-level coverage only. Python-version resolver/installability is owned by
CI/pip dry-run validation across the supported Python matrix; this suite does
not claim transitive dependency-matrix resolution.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Iterable

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PIPELINE_PYPROJECT = PROJECT_ROOT / "pipeline" / "pyproject.toml"
PIPELINE_CONSTRAINTS = PROJECT_ROOT / "pipeline" / "constraints.txt"
PIPELINE_TEST_CONSTRAINTS = PROJECT_ROOT / "pipeline" / "test-constraints.txt"
LEGACY_REQUIREMENTS = (
    PROJECT_ROOT / "legacy" / "requirements.txt",
    PROJECT_ROOT / "legacy" / "requirements-dev.txt",
)
LEGACY_CONSTRAINTS = PROJECT_ROOT / "legacy" / "constraints.txt"


def _normalize_name(name: str) -> str:
    """Return the PEP 503 normalized package name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_constraints(content: str) -> dict[str, Version]:
    """Parse a pip-tools constraints artifact into {name: pinned version}.

    Any non-comment line that is not an exact ``name==version`` pin raises,
    anchoring the artifact to the documented pip-tools regeneration contract.
    """
    pins: dict[str, Version] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        requirement = stripped.split(";", 1)[0].split("#", 1)[0].strip()
        name, separator, version = requirement.partition("==")
        if not separator or not name or not version:
            raise ValueError(f"Unexpected constraints entry: {line!r}")
        pins[_normalize_name(name)] = Version(version)
    return pins


def _parse_pep508_declarations(entries: Iterable[str]) -> dict[str, SpecifierSet]:
    """Parse PEP 508 dependency entries into {name: declared range}."""
    declared: dict[str, SpecifierSet] = {}
    for entry in entries:
        requirement = Requirement(entry)
        declared[_normalize_name(requirement.name)] = requirement.specifier
    return declared


def _parse_requirements_files(paths: Iterable[Path]) -> dict[str, SpecifierSet]:
    """Parse requirements files (expanding -r includes) into {name: range}."""
    declared: dict[str, SpecifierSet] = {}
    seen: set[Path] = set()
    for path in paths:
        _collect_requirements_file(path, declared, seen)
    return declared


def _collect_requirements_file(
    path: Path, declared: dict[str, SpecifierSet], seen: set[Path]
) -> None:
    resolved = path.resolve()
    if resolved in seen:
        return
    seen.add(resolved)
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r "):
            _collect_requirements_file(path.parent / line[3:].strip(), declared, seen)
            continue
        requirement = Requirement(line)
        name = _normalize_name(requirement.name)
        declared[name] = declared.get(name, SpecifierSet()) & requirement.specifier


def _range_violations(
    pins: dict[str, Version], declared: dict[str, SpecifierSet]
) -> list[str]:
    """Return pins that fall outside the declared range for the same package."""
    violations: list[str] = []
    for name, pinned in sorted(pins.items()):
        specifier = declared.get(name)
        if specifier is not None and pinned not in specifier:
            violations.append(f"{name}=={pinned} violates declared range {specifier}")
    return violations


def _coverage_gaps(
    declared: dict[str, SpecifierSet], pins: dict[str, Version]
) -> list[str]:
    """Return declared top-level packages missing from the constraints artifact."""
    return [name for name in sorted(declared) if name not in pins]


def _pin_mismatches(
    pipeline_pins: dict[str, Version], legacy_pins: dict[str, Version]
) -> list[str]:
    """Return pipeline test-extras pins that differ from the legacy pins."""
    mismatches: list[str] = []
    for name, pinned in sorted(pipeline_pins.items()):
        legacy_pin = legacy_pins.get(name)
        if legacy_pin is None:
            mismatches.append(f"{name}=={pinned} missing from legacy/constraints.txt")
        elif pinned != legacy_pin:
            mismatches.append(f"{name}=={pinned} differs from legacy pin {legacy_pin}")
    return mismatches


def _pipeline_declared() -> dict[str, SpecifierSet]:
    with PIPELINE_PYPROJECT.open("rb") as pyproject_file:
        pyproject = tomllib.load(pyproject_file)
    return _parse_pep508_declarations(pyproject["project"]["dependencies"])


def test_pipeline_constraints_pins_satisfy_declared_ranges() -> None:
    # Arrange
    pins = _parse_constraints(PIPELINE_CONSTRAINTS.read_text(encoding="utf-8"))
    declared = _pipeline_declared()

    # Act
    violations = _range_violations(pins, declared)

    # Assert
    assert violations == []


def test_pipeline_declared_dependencies_are_pinned_in_constraints() -> None:
    # Arrange
    pins = _parse_constraints(PIPELINE_CONSTRAINTS.read_text(encoding="utf-8"))
    declared = _pipeline_declared()

    # Act
    gaps = _coverage_gaps(declared, pins)

    # Assert
    assert gaps == []


def test_legacy_constraints_pins_satisfy_declared_ranges() -> None:
    # Arrange
    pins = _parse_constraints(LEGACY_CONSTRAINTS.read_text(encoding="utf-8"))
    declared = _parse_requirements_files(LEGACY_REQUIREMENTS)

    # Act
    violations = _range_violations(pins, declared)

    # Assert
    assert violations == []


def test_legacy_declared_dependencies_are_pinned_in_constraints() -> None:
    # Arrange
    pins = _parse_constraints(LEGACY_CONSTRAINTS.read_text(encoding="utf-8"))
    declared = _parse_requirements_files(LEGACY_REQUIREMENTS)

    # Act
    gaps = _coverage_gaps(declared, pins)

    # Assert
    assert gaps == []


def test_pipeline_test_constraints_match_legacy_constraints() -> None:
    # Arrange
    pipeline_pins = _parse_constraints(
        PIPELINE_TEST_CONSTRAINTS.read_text(encoding="utf-8")
    )
    legacy_pins = _parse_constraints(LEGACY_CONSTRAINTS.read_text(encoding="utf-8"))

    # Act
    mismatches = _pin_mismatches(pipeline_pins, legacy_pins)

    # Assert
    assert mismatches == []
