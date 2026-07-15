"""Public orchestration for publishing validated canonical Parquet generations.

``publish_canonical_generation`` deliberately accepts only in-memory canonical
Polars frames.  It contains no loader, network, GUI, or import-time I/O path.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import tempfile

import polars as pl

from f1_replay_pipeline.canonical_generation_validation import validate_complete_canonical_generation
from f1_replay_pipeline.dataset_manifest import (
    DEFAULT_WRITER_SETTINGS,
    DatasetManifest,
    TableManifestEntry,
    schema_tokens_for,
    serialize_manifest,
)
from f1_replay_pipeline.generation_identity import validate_generation_id
from f1_replay_pipeline.generation_publication import (
    DirectoryFsyncStatus,
    Filesystem,
    GenerationPublicationResult,
    StagedGenerationWriter,
    resolve_current_generation,
    write_generation,
)
from f1_replay_pipeline.logical_hashes import logical_table_sha256
from f1_replay_pipeline.parquet_io import (
    CANONICAL_PARQUET_TABLE_NAMES,
    ensure_native_parquet_compatibility,
    validate_canonical_frames,
    write_canonical_parquet,
)


Publisher = Callable[..., GenerationPublicationResult]
Checkpoint = Callable[[str], None]


@dataclass(frozen=True)
class PublishedCanonicalGeneration:
    """Immutable locations and digest for a generation visible through ``current.json``."""

    generation_id: str
    generation_path: Path
    manifest_path: Path
    pointer_path: Path
    manifest_sha256: str
    committed: bool = True
    directory_fsyncs: tuple[DirectoryFsyncStatus, ...] = field(default=(), compare=False)

    @property
    def durability_confirmed(self) -> bool:
        """Whether the final current-pointer directory fsync was confirmed.

        An empty status list (for example, a resolved pre-existing generation),
        an unsupported fsync, or a failed fsync is deliberately not reported as
        durable.  Callers can inspect ``directory_fsyncs`` for the exact
        platform outcome rather than treating a committed generation as an
        unconditionally durable one.
        """
        return (
            self.committed
            and bool(self.directory_fsyncs)
            and self.directory_fsyncs[-1].outcome == "succeeded"
        )


@dataclass(frozen=True)
class _TablePlan:
    name: str
    frame: pl.DataFrame
    logical_sha256: str


def publish_canonical_generation(
    *,
    frames: Mapping[str, pl.DataFrame],
    target_parent: Path,
    generation_id: str,
    filesystem: Filesystem | None = None,
    checkpoint: Checkpoint | None = None,
    publisher: Publisher = write_generation,
) -> PublishedCanonicalGeneration:
    """Validate and atomically publish all ten canonical frames.

    Validation, native-writer compatibility checks, and logical hashes complete
    before the publisher is called, so an invalid input cannot create staging
    artifacts.  ``filesystem``, ``checkpoint``, and ``publisher`` are explicit
    seams for deterministic publication-failure tests.
    """
    _validate_target_parent(target_parent)
    validate_generation_id(generation_id)
    plans = _prepare_table_plans(frames)
    result = publisher(
        target_parent=target_parent,
        generation_id=generation_id,
        materialize=lambda writer: _materialize_generation(generation_id, plans, writer),
        filesystem=filesystem,
        validate_manifest=_validate_complete_manifest,
        checkpoint=checkpoint,
    )
    return _published_metadata(generation_id, result)


def resolve_published_canonical_generation(target_parent: Path) -> PublishedCanonicalGeneration:
    """Resolve and fully verify the canonical generation selected by ``current.json``."""
    _validate_target_parent(target_parent)
    result = resolve_current_generation(target_parent)
    return _published_metadata(result.generation_path.name, result)


def _validate_target_parent(target_parent: Path) -> None:
    if not isinstance(target_parent, Path):
        raise TypeError("target_parent must be a pathlib.Path")


def _prepare_table_plans(frames: Mapping[str, pl.DataFrame]) -> tuple[_TablePlan, ...]:
    validate_canonical_frames(frames)
    ensure_native_parquet_compatibility()
    return tuple(
        _TablePlan(name, frames[name], logical_table_sha256(name, frames[name]))
        for name in CANONICAL_PARQUET_TABLE_NAMES
    )


def _materialize_generation(
    generation_id: str, plans: tuple[_TablePlan, ...], writer: StagedGenerationWriter,
) -> bytes:
    entries = tuple(_materialize_table(plan, writer) for plan in plans)
    manifest = DatasetManifest(
        generation_id=generation_id,
        tables=entries,
        writer_settings=DEFAULT_WRITER_SETTINGS,
    )
    return serialize_manifest(manifest)


def _materialize_table(plan: _TablePlan, writer: StagedGenerationWriter) -> TableManifestEntry:
    """Round-trip native Parquet before copying its exact closed bytes into staging."""
    with tempfile.TemporaryDirectory(prefix="canonical-parquet-") as directory:
        temporary_path = Path(directory) / f"{plan.name}.parquet"
        write_canonical_parquet(plan.name, plan.frame, temporary_path)
        payload = temporary_path.read_bytes()
        writer.write_bytes(f"tables/{plan.name}.parquet", payload)
    return TableManifestEntry(
        name=plan.name,
        path=f"tables/{plan.name}.parquet",
        row_count=plan.frame.height,
        schema=schema_tokens_for(plan.name),
        logical_sha256=plan.logical_sha256,
        byte_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _validate_complete_manifest(manifest: DatasetManifest, generation_path: Path) -> None:
    """Accept the already-complete canonical snapshot at the policy seam."""
    del manifest, generation_path


def _published_metadata(
    generation_id: str, result: GenerationPublicationResult,
) -> PublishedCanonicalGeneration:
    return PublishedCanonicalGeneration(
        generation_id=generation_id,
        generation_path=result.generation_path,
        manifest_path=result.manifest_path,
        pointer_path=result.pointer_path,
        manifest_sha256=result.manifest_sha256,
        committed=result.committed,
        directory_fsyncs=result.directory_fsyncs,
    )


__all__ = [
    "Checkpoint", "PublishedCanonicalGeneration", "Publisher", "publish_canonical_generation",
    "resolve_published_canonical_generation",
]
