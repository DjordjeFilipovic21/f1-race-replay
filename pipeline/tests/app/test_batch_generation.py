"""Offline behavioral coverage for sequential batch generation."""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from f1_replay_pipeline.app.batch_generation import (
    BatchRaceResult,
    BatchRequest,
    BatchResult,
    ScheduledRace,
    _atomic_write_json,
    _browser_output_valid,
    _generation_id,
    _retained_catalog_records,
    _run_race,
    _session_code,
    _shallow_browser_output_valid,
    deterministic_generation_id,
    publish_catalog,
    run_batch,
    verify_catalog,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_publication import (
    BrowserDeliveryCleanupError,
    BrowserDeliveryCommittedError,
    BrowserDeliveryDurabilityUncertainError,
    PublishedBrowserDelivery,
    BrowserValidationProgress,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_request import (
    BrowserDeliveryServiceError,
    BrowserPublishResult,
)
from f1_replay_pipeline.app.cli import _R2ProgressRenderer, main
from f1_replay_pipeline.app.r2_publication import (
    R2PublicationError,
    R2PublicationResult,
    R2ProgressEvent,
)
from f1_replay_pipeline.storage.generation_publication import (
    GenerationPublicationResult,
    PublicationCommittedError,
    PublicationDurabilityUncertainError,
    validate_generation_id,
)
from f1_replay_pipeline.app.orchestration import PipelineResult, PublicationError


def _request(tmp_path: Path, *, rounds: tuple[int, ...] | None = (1,), all_rounds: bool = False, **changes: object) -> BatchRequest:
    values = {
        "year": 2024, "rounds": rounds, "all_rounds": all_rounds, "session": "R",
        "canonical_root": tmp_path / "canonical", "browser_root": tmp_path / "browser",
        "schema_root": tmp_path / "schemas",
    }
    values.update(changes)
    return BatchRequest(**values)  # type: ignore[arg-type]


def _services(fail_round: int | None = None):
    calls: list[int] = []

    def pipeline(request):
        calls.append(request.selection.round_number)
        if request.selection.round_number == fail_round:
            raise RuntimeError("offline failure")
        return PipelineResult(request, request.generation_id or "missing", object())

    def browser(request):
        return BrowserPublishResult(request, request.delivery_version, object())

    return calls, pipeline, browser


def test_single_and_multiple_selected_races_use_isolated_deterministic_ids(tmp_path: Path) -> None:
    calls, pipeline, browser = _services()
    result = run_batch(
        _request(tmp_path, rounds=(2, 1)), schedule_provider=lambda *_args, **_kwargs: (
            ScheduledRace(1, "One", True), ScheduledRace(2, "Two", True),
        ), pipeline_service=pipeline, browser_service=browser,
    )

    assert [(race.race_id, race.generation_id) for race in result.races] == [
        (
            "2024-round-01-one",
            "2024-round-01-session-race-mode-race",
        ),
        (
            "2024-round-02-two",
            "2024-round-02-session-race-mode-race",
        ),
    ]
    assert calls == [1, 2]


@pytest.mark.parametrize(
    ("alias", "expected_identity", "expected_code"),
    [
        ("FP1", "practice-1", "fp1"),
        ("Practice 1", "practice-1", "fp1"),
        ("FP2", "practice-2", "fp2"),
        ("Practice 2", "practice-2", "fp2"),
        ("FP3", "practice-3", "fp3"),
        ("Practice 3", "practice-3", "fp3"),
        ("Q", "qualifying", "q"),
        ("Qualifying", "qualifying", "q"),
        ("R", "race", "r"),
        ("Race", "race", "r"),
        ("S", "sprint", "s"),
        ("Sprint", "sprint", "s"),
        ("SQ", "sprint-qualifying", "sq"),
        ("Sprint Qualifying", "sprint-qualifying", "sq"),
        ("SS", "sprint-shootout", "ss"),
        ("Sprint Shootout", "sprint-shootout", "ss"),
    ],
)
def test_batch_aliases_share_safe_v2_identity_and_session_code(
    tmp_path: Path, alias: str, expected_identity: str, expected_code: str,
) -> None:
    # Arrange
    request = _request(tmp_path, session=alias)

    # Act
    generation_id = deterministic_generation_id(2026, 1, alias)
    session_code = _session_code(request)

    # Assert
    assert generation_id == (
        f"2026-round-01-session-{expected_identity}-mode-"
        f"{expected_identity if expected_identity != 'practice-1' and expected_identity != 'practice-2' and expected_identity != 'practice-3' else 'practice'}"
    )
    assert validate_generation_id(generation_id) == generation_id
    assert session_code == expected_code


def test_forced_batch_generation_id_validates_the_v2_suffix(tmp_path: Path) -> None:
    # Arrange
    request = _request(tmp_path, session="Sprint Shootout", force=True)
    canonical = tmp_path / "canonical" / "race"
    browser = tmp_path / "browser" / "race"
    base = deterministic_generation_id(2024, 1, request.session)
    (canonical / "generations" / f"{base}-force-1").mkdir(parents=True)

    # Act
    generation_id = _generation_id(request, canonical, browser, 1)

    # Assert
    assert generation_id == f"{base}-force-2"
    assert validate_generation_id(generation_id) == generation_id


def test_all_and_zero_race_selection_complete_without_network(tmp_path: Path) -> None:
    calls, pipeline, browser = _services()
    all_result = run_batch(
        _request(tmp_path, rounds=None, all_rounds=True),
        schedule_provider=lambda *_args, **_kwargs: (ScheduledRace(1, "One", True), ScheduledRace(2, "Two", True)),
        pipeline_service=pipeline, browser_service=browser,
    )
    result = run_batch(_request(tmp_path / "empty", rounds=None, all_rounds=True), schedule_provider=lambda *_args, **_kwargs: (), pipeline_service=pipeline, browser_service=browser)

    assert result.races == ()
    assert [race.round_number for race in all_result.races] == [1, 2]


def test_fail_fast_and_continue_on_error_have_truthful_final_outcomes(tmp_path: Path) -> None:
    schedule = lambda *_args, **_kwargs: (ScheduledRace(1, "One", True), ScheduledRace(2, "Two", True))
    calls, pipeline, browser = _services(fail_round=1)
    failed_fast = run_batch(_request(tmp_path, rounds=(1, 2)), schedule_provider=schedule, pipeline_service=pipeline, browser_service=browser)
    calls_continue, pipeline_continue, browser_continue = _services(fail_round=1)
    continued = run_batch(_request(tmp_path, rounds=(1, 2), continue_on_error=True), schedule_provider=schedule, pipeline_service=pipeline_continue, browser_service=browser_continue)

    assert [race.outcome for race in failed_fast.races] == ["failed"]
    assert [race.outcome for race in continued.races] == ["failed", "generated"]
    assert calls == [1]
    assert calls_continue == [1, 2]


def test_resume_skips_only_validated_outputs_and_force_regenerates(tmp_path: Path, monkeypatch) -> None:
    calls, pipeline, browser = _services()
    canonical = SimpleNamespace(
        generation_path=tmp_path / "2024-round-01-session-race-mode-race",
        manifest_sha256="a" * 64,
    )
    monkeypatch.setattr("f1_replay_pipeline.app.batch_generation._session_canonical_state", lambda *_paths: canonical)
    monkeypatch.setattr("f1_replay_pipeline.app.batch_generation._session_outputs_valid", lambda *_paths: True)
    monkeypatch.setattr(
        "f1_replay_pipeline.app.batch_generation.read_session_browser_pointer",
        lambda *_paths: SimpleNamespace(delivery_version="existing-delivery"),
    )
    resumed = run_batch(_request(tmp_path, resume=True), schedule_provider=lambda *_args, **_kwargs: (ScheduledRace(1, "One", True),), pipeline_service=pipeline, browser_service=browser)
    forced = run_batch(_request(tmp_path, resume=True, force=True), schedule_provider=lambda *_args, **_kwargs: (ScheduledRace(1, "One", True),), pipeline_service=pipeline, browser_service=browser)

    assert resumed.races[0].outcome == "skipped_valid"
    assert forced.races[0].outcome == "generated"
    assert calls == [1]


def test_future_schedule_event_is_explicitly_skipped(tmp_path: Path) -> None:
    calls, pipeline, browser = _services()
    result = run_batch(_request(tmp_path), schedule_provider=lambda *_args, **_kwargs: (ScheduledRace(1, "Future", False),), pipeline_service=pipeline, browser_service=browser)

    assert result.races[0].outcome == "skipped_unavailable"
    assert calls == []


def test_generate_keeps_results_on_stdout_and_progress_on_stderr(tmp_path: Path, capsys) -> None:
    _calls, pipeline, browser = _services()
    status = main(
        ["generate", "--year", "2024", "--round", "1", "--output", str(tmp_path / "canonical"), "--browser-output", str(tmp_path / "browser")],
        service=pipeline, browser_service=browser, schedule_provider=lambda *_args, **_kwargs: (ScheduledRace(1, "One", True),),
    )
    captured = capsys.readouterr()

    assert status == 0
    assert captured.out == (
        "race_id=2024-round-01-one outcome=generated "
        "generation_id=2024-round-01-session-race-mode-race "
        "delivery_version=2024-round-01-session-race-mode-race\n"
    )
    assert "canonical_generating" in captured.err


def test_generate_publish_r2_flag_runs_after_local_catalog_publication(
    tmp_path: Path, capsys,
) -> None:
    _calls, pipeline, browser = _services()
    received = []

    def r2_publisher(source):
        received.append(source)
        assert (source.season_root / "catalog.json").is_file()
        return R2PublicationResult(4, 2, "seasons/2024/catalog.json")

    status = main(
        [
            "generate", "--year", "2024", "--round", "1",
            "--output", str(tmp_path / "canonical"),
            "--browser-output", str(tmp_path / "browser"),
            "--publish-r2",
        ],
        service=pipeline,
        browser_service=browser,
        schedule_provider=lambda *_args, **_kwargs: (ScheduledRace(1, "One", True),),
        r2_publisher=r2_publisher,
    )

    assert status == 0
    assert len(received) == 1
    assert received[0].year == 2024
    assert capsys.readouterr().out.endswith(
        "r2_catalog=seasons/2024/catalog.json uploaded=4 reused=2\n"
    )


def test_generate_publish_r2_failure_is_visible_and_returns_nonzero(
    tmp_path: Path, capsys,
) -> None:
    _calls, pipeline, browser = _services()

    def r2_publisher(_source):
        raise R2PublicationError("R2 credentials are unavailable")

    status = main(
        [
            "generate", "--year", "2024", "--round", "1",
            "--output", str(tmp_path / "canonical"),
            "--browser-output", str(tmp_path / "browser"),
            "--publish-r2",
        ],
        service=pipeline,
        browser_service=browser,
        schedule_provider=lambda *_args, **_kwargs: (ScheduledRace(1, "One", True),),
        r2_publisher=r2_publisher,
    )

    assert status == 1
    assert "error: R2 credentials are unavailable" in capsys.readouterr().err


def test_generate_renders_granular_r2_progress_to_stderr(
    tmp_path: Path, capsys,
) -> None:
    _calls, pipeline, browser = _services()

    class GranularR2Publisher:
        def __call__(self, _source):
            raise AssertionError("granular R2 publication must be preferred")

        def publish_with_progress(self, _source, progress):
            progress(R2ProgressEvent("r2_validating", 0, 1))
            progress(R2ProgressEvent(
                "r2_validating", 1, 1, key="2024-round-01-one/r",
            ))
            progress(R2ProgressEvent(
                "r2_uploading_immutable", 1, 2, uploaded=1,
                key="seasons/2024/browser/chunk-001.json",
            ))
            progress(R2ProgressEvent(
                "r2_uploading_immutable", 2, 2, uploaded=1, reused=1,
                key="seasons/2024/browser/chunk-002.json",
            ))
            progress(R2ProgressEvent(
                "r2_completed", 4, 4, uploaded=3, reused=1,
                key="seasons/2024/catalog.json",
            ))
            return R2PublicationResult(3, 1, "seasons/2024/catalog.json")

    status = main(
        [
            "generate", "--year", "2024", "--round", "1",
            "--output", str(tmp_path / "canonical"),
            "--browser-output", str(tmp_path / "browser"),
            "--publish-r2",
        ],
        service=pipeline,
        browser_service=browser,
        schedule_provider=lambda *_args, **_kwargs: (ScheduledRace(1, "One", True),),
        r2_publisher=GranularR2Publisher(),
    )

    captured = capsys.readouterr()
    assert status == 0
    assert "r2 000% | validating 0/1" in captured.err
    assert "r2 050% | immutable 1/2" in captured.err
    assert "r2 100% | completed 4/4" in captured.err
    assert "seasons/2024/browser/chunk-001.json" not in captured.err


def test_interactive_r2_progress_overwrites_one_compact_line(
    monkeypatch,
) -> None:
    class InteractiveStderr(io.StringIO):
        def isatty(self) -> bool:
            return True

    stderr = InteractiveStderr()
    monkeypatch.setattr(sys, "stderr", stderr)
    renderer = _R2ProgressRenderer()
    long_key = (
        "seasons/2024/browser/2024-round-04-japanese-grand-prix/"
        "generations/2024-round-04-r/chunks/cr2-position.json"
    )

    renderer(R2ProgressEvent(
        "r2_uploading_immutable", 2348, 2350, uploaded=717, reused=1631, key=long_key,
    ))
    renderer(R2ProgressEvent(
        "r2_uploading_immutable", 2350, 2350, uploaded=719, reused=1631, key=long_key,
    ))

    output = stderr.getvalue()
    assert output.count("\r") == 2
    assert output.endswith("\n")
    assert "\033" not in output
    assert long_key not in output
    assert all(len(refresh) <= 80 for refresh in output.split("\r")[1:])


def test_generate_prints_failed_race_detail_after_renderer_closes(
    tmp_path: Path, capsys, monkeypatch,
) -> None:
    closed: list[bool] = []

    class Renderer:
        def __call__(self, _event) -> None:
            pass

        def close(self) -> None:
            closed.append(True)
            print("renderer closed", file=sys.stderr)

    _calls, pipeline, browser = _services(fail_round=1)
    monkeypatch.setattr("f1_replay_pipeline.app.cli._terminal_progress_renderer", Renderer)
    status = main(
        ["generate", "--year", "2024", "--round", "1", "--output", str(tmp_path / "canonical"), "--browser-output", str(tmp_path / "browser")],
        service=pipeline,
        browser_service=browser,
        schedule_provider=lambda *_args, **_kwargs: (ScheduledRace(1, "One", True),),
    )
    captured = capsys.readouterr()

    assert status == 1
    assert closed == [True]
    assert captured.out == "race_id=2024-round-01-one outcome=failed\n"
    assert captured.err.splitlines() == [
        "renderer closed", "failure: race_id=2024-round-01-one detail=RuntimeError: offline failure",
    ]


def test_granular_browser_progress_uses_monotonic_operation_stages(tmp_path: Path) -> None:
    events = []

    class Browser:
        def __call__(self, request):
            return BrowserPublishResult(request, request.delivery_version, object())

        def publish_with_progress(self, request, progress):
            for phase in (
                "canonical_snapshot_reading", "track_assets_generating", "browser_building",
                "browser_payload_preparing", "browser_contract_schema_loading",
                BrowserValidationProgress("browser_schema_artifact_validating", 3, 6, "chunk 1/2"),
                "browser_artifacts_staging",
                "browser_pointer_committing_durability",
            ):
                progress(phase)
            return self(request)

    _calls, pipeline, _browser = _services()
    run_batch(
        _request(tmp_path),
        schedule_provider=lambda *_args, **_kwargs: (ScheduledRace(1, "One", True),),
        pipeline_service=pipeline,
        browser_service=Browser(),
        progress=events.append,
    )

    assert [(event.phase, event.stage_index) for event in events if event.race_id] == [
        ("race_queued", 0), ("canonical_generating", 1),
        ("canonical_snapshot_reading", 2), ("track_assets_generating", 3),
        ("browser_building", 4), ("browser_payload_preparing", 5),
        ("browser_contract_schema_loading", 6), ("browser_schema_artifact_validating", 7),
        ("browser_artifacts_staging", 8), ("browser_pointer_committing_durability", 9),
        ("race_succeeded", 9),
    ]
    assert {event.stage_total for event in events if event.race_id} == {9}
    validation = next(event for event in events if event.detail == "chunk 1/2")
    assert (validation.phase_completed, validation.phase_total) == (3, 6)


def test_catalog_completion_follows_catalog_validation_and_publication(tmp_path: Path) -> None:
    events = []
    _calls, pipeline, browser = _services()

    run_batch(
        _request(tmp_path),
        schedule_provider=lambda *_args, **_kwargs: (ScheduledRace(1, "One", True),),
        pipeline_service=pipeline,
        browser_service=browser,
        progress=events.append,
    )

    assert [event.phase for event in events[-3:]] == [
        "catalog_revalidating_references", "catalog_publishing", "batch_completed",
    ]


def test_generate_uses_season_output_defaults_and_formats_a_real_progress_bar(
    tmp_path: Path, capsys, monkeypatch,
) -> None:
    requests = []

    def pipeline(request):
        requests.append(request)
        return PipelineResult(request, request.generation_id, object())

    def browser(request):
        return BrowserPublishResult(request, request.delivery_version, object())

    monkeypatch.chdir(tmp_path)
    status = main(
        ["generate", "--year", "2024", "--round", "5"],
        service=pipeline,
        browser_service=browser,
        schedule_provider=lambda *_args, **_kwargs: (ScheduledRace(5, "Five", True),),
    )

    assert status == 0
    assert requests[0].output_directory == Path("artifacts/seasons/2024/canonical/2024-round-05-five")
    captured = capsys.readouterr()
    assert "progress 100% | race 1/1" in captured.err
    assert "canonical_generating" in captured.err


def test_atomic_json_write_keeps_replacement_in_open_directory_during_directory_swap(
    tmp_path: Path, monkeypatch,
) -> None:
    root = tmp_path / "catalog"
    outside = tmp_path / "outside"
    original_root = tmp_path / "catalog-original"
    root.mkdir()
    outside.mkdir()
    destination = root / "catalog.json"
    destination.write_text("old", encoding="utf-8")
    outside_destination = outside / "catalog.json"
    outside_destination.write_text("sentinel", encoding="utf-8")
    real_replace = os.replace

    def swap_before_replace(source, target, *, src_dir_fd=None, dst_dir_fd=None):
        root.rename(original_root)
        root.symlink_to(outside, target_is_directory=True)
        return real_replace(source, target, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    monkeypatch.setattr(os, "replace", swap_before_replace)

    _atomic_write_json(destination, {"safe": True})

    assert json.loads((original_root / "catalog.json").read_text(encoding="utf-8")) == {"safe": True}
    assert outside_destination.read_text(encoding="utf-8") == "sentinel"


def test_atomic_json_write_cleans_temporary_file_by_open_directory_after_swap(
    tmp_path: Path, monkeypatch,
) -> None:
    root = tmp_path / "catalog"
    outside = tmp_path / "outside"
    original_root = tmp_path / "catalog-original"
    root.mkdir()
    outside.mkdir()
    destination = root / "catalog.json"
    destination.write_text("old", encoding="utf-8")

    def fail_after_swap(_source, _target, *, src_dir_fd=None, dst_dir_fd=None):
        root.rename(original_root)
        root.symlink_to(outside, target_is_directory=True)
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(os, "replace", fail_after_swap)

    with pytest.raises(OSError, match="simulated replacement failure"):
        _atomic_write_json(destination, {"safe": True})

    assert list(original_root.glob(".catalog-*.tmp")) == []
    assert list(outside.glob(".catalog-*.tmp")) == []


def test_atomic_json_write_rejects_symlinked_directory_without_touching_target(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "catalog"
    root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError):
        _atomic_write_json(root / "catalog.json", {"safe": True})

    assert list(outside.iterdir()) == []


def _valid_browser_artifacts(tmp_path: Path, *, source_generation_id: str = "generation", source_manifest_sha256: str = "a" * 64, reference_path: str = "track-assets.json") -> tuple[GenerationPublicationResult, Path]:
    browser = tmp_path / "browser"
    generation = browser / "generations" / "delivery"
    generation.mkdir(parents=True)
    artifact = generation / reference_path
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"artifact")
    manifest = {
        "deliveryVersion": "delivery",
        "sourceGenerationId": source_generation_id,
        "sourceManifestSha256": source_manifest_sha256,
        "trackAssets": {"path": reference_path, "sha256": hashlib.sha256(b"artifact").hexdigest()},
        "chunks": [],
    }
    manifest_bytes = json.dumps(manifest).encode("utf-8")
    (generation / "manifest.json").write_bytes(manifest_bytes)
    (browser / "browser-current.json").write_text(json.dumps({
        "formatVersion": "browser-delivery-v1",
        "deliveryVersion": "delivery",
        "manifestPath": "generations/delivery/manifest.json",
        "manifestSha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }), encoding="utf-8")
    return GenerationPublicationResult(
        tmp_path / source_generation_id,
        tmp_path / "manifest.json",
        tmp_path / "current.json",
        source_manifest_sha256,
    ), browser


def test_browser_resume_validation_rejects_untrusted_reference_paths_and_symlinks(tmp_path: Path) -> None:
    canonical, browser = _valid_browser_artifacts(tmp_path, reference_path="../outside")
    assert not _browser_output_valid(canonical, browser)

    canonical, browser = _valid_browser_artifacts(tmp_path / "symlink")
    target = tmp_path / "target"
    target.write_bytes(b"artifact")
    artifact = browser / "generations" / "delivery" / "track-assets.json"
    artifact.unlink()
    artifact.symlink_to(target)
    assert not _browser_output_valid(canonical, browser)

    canonical, browser = _valid_browser_artifacts(tmp_path / "version")
    (browser / "browser-current.json").write_text(json.dumps({
        "formatVersion": "browser-delivery-v1",
        "deliveryVersion": "../outside",
        "manifestPath": "generations/../outside/manifest.json",
        "manifestSha256": "a" * 64,
    }), encoding="utf-8")
    assert not _browser_output_valid(canonical, browser)

    canonical, browser = _valid_browser_artifacts(tmp_path / "pointer-symlink")
    pointer = browser / "browser-current.json"
    pointer_target = tmp_path / "pointer-target.json"
    pointer_target.write_bytes(pointer.read_bytes())
    pointer.unlink()
    pointer.symlink_to(pointer_target)
    assert not _browser_output_valid(canonical, browser)


@pytest.mark.parametrize("pointer_change", [
    {"formatVersion": "browser-delivery-v0"},
    {"manifestSha256": None},
    {"unexpected": True},
])
def test_browser_resume_and_retained_catalog_validation_require_exact_pointer_shape(
    tmp_path: Path, pointer_change: dict[str, object],
) -> None:
    canonical, browser = _valid_browser_artifacts(tmp_path)
    pointer_path = browser / "browser-current.json"
    pointer = json.loads(pointer_path.read_bytes())
    pointer.update(pointer_change)
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

    assert not _browser_output_valid(canonical, browser)
    assert not _shallow_browser_output_valid(canonical, browser)


def test_browser_resume_validation_requires_current_canonical_source_identity(tmp_path: Path) -> None:
    _canonical, browser = _valid_browser_artifacts(tmp_path, source_generation_id="other")
    different_canonical = GenerationPublicationResult(
        tmp_path / "generation",
        tmp_path / "manifest.json",
        tmp_path / "current.json",
        "a" * 64,
    )
    assert not _browser_output_valid(different_canonical, browser)

    canonical, browser = _valid_browser_artifacts(tmp_path / "checksum", source_manifest_sha256="b" * 64)
    different_canonical = GenerationPublicationResult(
        tmp_path / "checksum" / "generation",
        tmp_path / "checksum" / "manifest.json",
        tmp_path / "checksum" / "current.json",
        "a" * 64,
    )
    assert not _browser_output_valid(different_canonical, browser)


def test_resume_reuses_valid_canonical_generation_when_browser_needs_retry(tmp_path: Path, monkeypatch) -> None:
    calls, pipeline, browser = _services()
    generation_id = "2024-round-01-session-race-mode-race"
    canonical = SimpleNamespace(generation_path=tmp_path / generation_id, manifest_sha256="a" * 64)
    monkeypatch.setattr("f1_replay_pipeline.app.batch_generation._session_canonical_state", lambda *_paths: canonical)
    monkeypatch.setattr("f1_replay_pipeline.app.batch_generation._session_outputs_valid", lambda *_paths: False)
    monkeypatch.setattr("f1_replay_pipeline.app.batch_generation.promote_session_canonical_pointer", lambda *_paths: None)

    result = run_batch(
        _request(tmp_path, resume=True),
        schedule_provider=lambda *_args, **_kwargs: (ScheduledRace(1, "One", True),),
        pipeline_service=pipeline,
        browser_service=browser,
    )

    assert calls == []
    assert result.races[0].generation_id == generation_id
    assert result.races[0].session_code == "r"


def test_resume_preserves_an_invalid_occupied_delivery_and_uses_a_browser_successor(
    tmp_path: Path, monkeypatch,
) -> None:
    generation_id = "2024-round-01-session-race-mode-race"
    canonical = SimpleNamespace(
        generation_path=tmp_path / "canonical" / generation_id,
        manifest_sha256="a" * 64,
    )
    browser_root = tmp_path / "browser" / "2024-round-01-one"
    occupied = browser_root / "generations" / generation_id
    occupied.mkdir(parents=True)
    (occupied / "partial.json").write_text("invalid", encoding="utf-8")
    browser_calls = []

    def browser(request):
        browser_calls.append(request)
        return BrowserPublishResult(request, request.delivery_version, object())

    monkeypatch.setattr("f1_replay_pipeline.app.batch_generation._session_canonical_state", lambda *_paths: canonical)
    monkeypatch.setattr("f1_replay_pipeline.app.batch_generation._session_outputs_valid", lambda *_paths: False)
    monkeypatch.setattr("f1_replay_pipeline.app.batch_generation.promote_session_canonical_pointer", lambda *_paths: None)
    _calls, pipeline, _browser = _services()

    result = run_batch(
        _request(tmp_path, resume=True),
        schedule_provider=lambda *_args, **_kwargs: (ScheduledRace(1, "One", True),),
        pipeline_service=pipeline, browser_service=browser,
    )

    assert (browser_calls[0].canonical_parent, browser_calls[0].delivery_version) == (
        tmp_path / "canonical" / "2024-round-01-one", f"{generation_id}-browser-1",
    )
    assert (occupied / "partial.json").read_text(encoding="utf-8") == "invalid"
    assert result.races[0].delivery_version == f"{generation_id}-browser-1"


def test_canonical_durability_warning_continues_browser_and_preserves_outcome(
    tmp_path: Path, monkeypatch,
) -> None:
    generation_id = "2024-round-01-session-race-mode-race"
    committed = GenerationPublicationResult(
        tmp_path / "canonical" / generation_id,
        tmp_path / "canonical" / generation_id / "manifest.json",
        tmp_path / "canonical" / "current.json",
        "a" * 64,
    )
    warning = PublicationDurabilityUncertainError(committed, OSError("fsync failed"))

    def pipeline(request):
        del request
        raise PublicationError("wrapped publication warning") from warning

    browser_calls = []

    def browser(request):
        browser_calls.append(request)
        return BrowserPublishResult(request, request.delivery_version, object())

    monkeypatch.setattr("f1_replay_pipeline.app.batch_generation._canonical_state", lambda *_paths: committed)

    result = run_batch(
        _request(tmp_path),
        schedule_provider=lambda *_args, **_kwargs: (ScheduledRace(1, "One", True),),
        pipeline_service=pipeline,
        browser_service=browser,
    )

    assert result.races[0].outcome == "committed_with_durability_warning"
    assert result.races[0].generation_id == generation_id
    assert len(browser_calls) == 1


def test_canonical_committed_error_continues_browser_as_generated(tmp_path: Path, monkeypatch) -> None:
    generation_id = "2024-round-01-session-race-mode-race"
    committed = GenerationPublicationResult(
        tmp_path / "canonical" / generation_id,
        tmp_path / "canonical" / generation_id / "manifest.json",
        tmp_path / "canonical" / "current.json",
        "a" * 64,
    )
    committed_error = PublicationCommittedError(committed, OSError("post-commit callback failed"))

    def pipeline(request):
        del request
        raise PublicationError("wrapped committed publication") from committed_error

    browser_calls = []

    def browser(request):
        browser_calls.append(request)
        return BrowserPublishResult(request, request.delivery_version, object())

    monkeypatch.setattr("f1_replay_pipeline.app.batch_generation._canonical_state", lambda *_paths: committed)

    result = run_batch(
        _request(tmp_path),
        schedule_provider=lambda *_args, **_kwargs: (ScheduledRace(1, "One", True),),
        pipeline_service=pipeline,
        browser_service=browser,
    )

    assert result.races[0].outcome == "generated"
    assert result.races[0].generation_id == generation_id
    assert result.races[0].delivery_version == generation_id
    assert len(browser_calls) == 1


@pytest.mark.parametrize(
    ("publication_error", "expected_outcome"),
    [
        (BrowserDeliveryDurabilityUncertainError, "committed_with_durability_warning"),
        (BrowserDeliveryCommittedError, "generated"),
    ],
)
def test_browser_committed_errors_in_wrapped_chains_preserve_generated_result(
    tmp_path: Path, publication_error, expected_outcome: str,
) -> None:
    publication = cast(PublishedBrowserDelivery, SimpleNamespace(delivery_version="delivery-committed"))
    committed_error = publication_error(publication, OSError("injected browser publication failure"))

    def browser(request: object):
        del request
        raise BrowserDeliveryServiceError("wrapped browser publication failure") from committed_error

    _calls, pipeline, _browser = _services()
    result = run_batch(
        _request(tmp_path),
        schedule_provider=lambda *_args, **_kwargs: (ScheduledRace(1, "One", True),),
        pipeline_service=pipeline,
        browser_service=browser,
    )

    race = result.races[0]
    assert race.outcome == expected_outcome
    assert race.generation_id == "2024-round-01-session-race-mode-race"
    assert race.delivery_version == "delivery-committed"
    assert race.detail is not None


def test_browser_cleanup_error_with_committed_result_is_not_reported_as_failure(tmp_path: Path) -> None:
    publication = cast(PublishedBrowserDelivery, SimpleNamespace(delivery_version="delivery-cleanup"))
    committed_error = BrowserDeliveryCleanupError((OSError("lease release failed"),), publication)

    def browser(request: object):
        del request
        raise BrowserDeliveryServiceError("wrapped browser cleanup failure") from committed_error

    _calls, pipeline, _browser = _services()
    result = run_batch(
        _request(tmp_path),
        schedule_provider=lambda *_args, **_kwargs: (ScheduledRace(1, "One", True),),
        pipeline_service=pipeline,
        browser_service=browser,
    )

    assert result.races[0].outcome == "generated"
    assert result.races[0].delivery_version == "delivery-cleanup"


def test_missing_explicit_schedule_round_fails_actionably_and_cli_returns_nonzero(tmp_path: Path, capsys) -> None:
    schedule = lambda *_args, **_kwargs: (ScheduledRace(1, "One", True),)
    calls, pipeline, browser = _services()
    status = main(
        ["generate", "--year", "2024", "--round", "2", "--output", str(tmp_path / "canonical"), "--browser-output", str(tmp_path / "browser")],
        service=pipeline, browser_service=browser, schedule_provider=schedule,
    )
    captured = capsys.readouterr()

    assert status == 1
    assert "requested round(s) missing from the 2024 schedule: 2" in captured.err


def test_catalog_merges_prior_validated_races_atomically(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("f1_replay_pipeline.app.batch_generation._session_outputs_valid", lambda *_paths: True)
    monkeypatch.setattr("f1_replay_pipeline.app.batch_generation._retained_session_valid", lambda *_args: True)
    calls, pipeline, browser = _services()
    schedule = lambda *_args, **_kwargs: (ScheduledRace(1, "One", True), ScheduledRace(2, "Two", True))
    run_batch(_request(tmp_path, rounds=(1,)), schedule_provider=schedule, pipeline_service=pipeline, browser_service=browser)
    run_batch(_request(tmp_path, rounds=(2,)), schedule_provider=schedule, pipeline_service=pipeline, browser_service=browser)

    catalog = json.loads((tmp_path / "catalog.json").read_text(encoding="utf-8"))
    assert catalog["schemaVersion"] == 2
    assert [record["race_id"] for record in catalog["races"]] == ["2024-round-01-one", "2024-round-02-two"]
    for record in catalog["races"]:
        assert len(record["sessions"]) == 1
        assert not any(field in record for field in ("generation_id", "delivery_version", "canonical", "browser"))


def test_catalog_drops_malformed_prior_references_without_deep_validation(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "canonical").mkdir()
    (tmp_path / "catalog.json").write_text(json.dumps({
        "schemaVersion": 2,
        "year": 2024,
        "atomicAcrossRaces": False,
        "races": [{
            "race_id": "../outside",
            "round_number": 1,
            "event_name": "Unsafe",
            "sessions": [{
                "session_code": "r",
                "session_name": "Race",
                "generation_id": "2024-round-01-r",
                "delivery_version": "2024-round-01-r",
                "outcome": "generated",
                "validated": True,
                "canonical_pointer": "canonical/../outside/current.json",
                "browser_pointer": "browser/../outside/browser-current.json",
            }],
        }],
    }), encoding="utf-8")
    monkeypatch.setattr("f1_replay_pipeline.app.batch_generation._outputs_valid", lambda *_paths: pytest.fail("retained output was deeply validated"))

    publish_catalog(BatchResult(_request(tmp_path), ()))

    assert json.loads((tmp_path / "catalog.json").read_text(encoding="utf-8"))["races"] == []


def test_verify_catalog_deeply_validates_each_catalog_reference(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "canonical").mkdir()
    (tmp_path / "catalog.json").write_text(json.dumps({
        "schemaVersion": 2,
        "year": 2024,
        "atomicAcrossRaces": False,
        "races": [{
            "race_id": "2024-round-01-one", "round_number": 1, "event_name": "One",
            "sessions": [{
                "session_code": "r", "session_name": "Race",
                "generation_id": "2024-round-01-r", "delivery_version": "2024-round-01-r",
                "outcome": "generated", "validated": True,
                "canonical_pointer": "canonical/2024-round-01-one/sessions/r/current.json",
                "browser_pointer": "browser/2024-round-01-one/sessions/r/browser-current.json",
            }],
        }],
    }), encoding="utf-8")
    canonical = GenerationPublicationResult(tmp_path / "canonical" / "2024-round-01-one" / "generations" / "g", tmp_path / "manifest.json", tmp_path / "current.json", "a" * 64)
    calls = []
    monkeypatch.setattr("f1_replay_pipeline.app.batch_generation._retained_session_valid", lambda record, session, request: session["session_code"] == "r")
    monkeypatch.setattr("f1_replay_pipeline.app.batch_generation.read_session_canonical_pointer", lambda *_args: canonical)
    monkeypatch.setattr(
        "f1_replay_pipeline.storage.canonical_generation_validation.validate_complete_canonical_generation",
        lambda *args, **kwargs: calls.append(("canonical", args, kwargs)),
    )
    monkeypatch.setattr("f1_replay_pipeline.delivery.browser.browser_delivery_publication.validate_complete_browser_delivery", lambda *args, **kwargs: calls.append((args, kwargs)))

    results = verify_catalog(_request(tmp_path))

    assert [result.outcome for result in results] == ["valid"]
    assert len(calls) == 2
    assert calls[0][0] == "canonical"
    assert calls[1][1]["pointer_path"].name == "browser-current.json"


def test_verify_catalog_rejects_v1_with_migration_message(tmp_path: Path) -> None:
    (tmp_path / "canonical").mkdir()
    (tmp_path / "catalog.json").write_text(json.dumps({"year": 2024, "races": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="schemaVersion 2.*migrate"):
        verify_catalog(_request(tmp_path))


def test_verify_cli_reports_stable_results_and_nonzero_invalid_status(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr("f1_replay_pipeline.app.cli.verify_catalog", lambda *_args, **_kwargs: (
        SimpleNamespace(race_id="2024-round-01-one", outcome="valid"),
        SimpleNamespace(race_id="2024-round-02-two", outcome="invalid"),
    ))

    status = main(["verify", "--year", "2024", "--output", str(tmp_path / "canonical"), "--browser-output", str(tmp_path / "browser")])
    captured = capsys.readouterr()

    assert status == 1
    assert captured.out.splitlines() == ["race_id=2024-round-01-one outcome=valid", "race_id=2024-round-02-two outcome=invalid"]


def test_publish_catalog_emits_only_v2_and_merges_sessions_for_one_readable_race(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr("f1_replay_pipeline.app.batch_generation._session_outputs_valid", lambda *args: True)
    request = _request(tmp_path)
    race_id = "2024-round-08-monaco-grand-prix"
    publish_catalog(BatchResult(request, (
        BatchRaceResult(race_id, 8, "generated", "2024-round-08-r", "2024-round-08-r", session_code="r", session_name="Race", event_name="Monaco Grand Prix"),
    )))
    monkeypatch.setattr("f1_replay_pipeline.app.batch_generation._retained_session_valid", lambda *_args: True)
    publish_catalog(BatchResult(request, (
        BatchRaceResult(race_id, 8, "generated", "2024-round-08-q", "2024-round-08-q", session_code="q", session_name="Qualifying", event_name="Monaco Grand Prix"),
    )))

    catalog = json.loads((tmp_path / "catalog.json").read_bytes())
    race = catalog["races"][0]
    assert catalog["schemaVersion"] == 2
    assert {session["session_code"] for session in race["sessions"]} == {"q", "r"}
    assert not any(field in race for field in ("generation_id", "delivery_version", "canonical", "browser"))


def test_failed_catalog_session_has_no_unproven_pointer_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("f1_replay_pipeline.app.batch_generation._session_outputs_valid", lambda *args: False)
    request = _request(tmp_path)
    publish_catalog(BatchResult(request, (
        BatchRaceResult("2024-round-01", 1, "failed", "2024-round-01-r", detail="failure", session_code="r", session_name="Race"),
    )))

    session = json.loads((tmp_path / "catalog.json").read_bytes())["races"][0]["sessions"][0]
    assert session["validated"] is False
    assert session["canonical_pointer"] is None
    assert session["browser_pointer"] is None


def test_failed_rerun_keeps_last_known_good_catalog_session(tmp_path: Path, monkeypatch) -> None:
    request = _request(tmp_path)
    race_id = "2024-round-01-bahrain-grand-prix"
    monkeypatch.setattr("f1_replay_pipeline.app.batch_generation._session_outputs_valid", lambda *_args: True)
    publish_catalog(BatchResult(request, (
        BatchRaceResult(
            race_id, 1, "generated", "2024-round-01-session-race-mode-race", "delivery-good",
            session_code="r", session_name="Race", event_name="Bahrain Grand Prix",
        ),
    )))
    monkeypatch.setattr("f1_replay_pipeline.app.batch_generation._retained_session_valid", lambda *_args: True)
    monkeypatch.setattr("f1_replay_pipeline.app.batch_generation._session_outputs_valid", lambda *_args: False)

    publish_catalog(BatchResult(request, (
        BatchRaceResult(
            race_id, 1, "failed", "2024-round-01-session-race-mode-race-force-1", detail="offline failure",
            session_code="r", session_name="Race", event_name="Bahrain Grand Prix",
        ),
    )))

    session = json.loads((tmp_path / "catalog.json").read_bytes())["races"][0]["sessions"][0]
    assert session["generation_id"] == "2024-round-01-session-race-mode-race"
    assert session["delivery_version"] == "delivery-good"
    assert session["outcome"] == "generated"
    assert session["validated"] is True
    assert session["canonical_pointer"] is None
    assert session["browser_pointer"] == f"browser/{race_id}/sessions/r/browser-current.json"


def test_catalog_read_failure_preserves_existing_catalog(tmp_path: Path, monkeypatch) -> None:
    request = _request(tmp_path)
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps({
        "schemaVersion": 2, "year": 2024, "atomicAcrossRaces": False, "races": [],
    }), encoding="utf-8")
    original_catalog = catalog_path.read_bytes()

    def fail_catalog_read(*_args) -> None:
        raise PermissionError("catalog temporarily unreadable")

    monkeypatch.setattr(
        "f1_replay_pipeline.app.batch_generation.read_regular_file_no_follow",
        fail_catalog_read,
    )

    with pytest.raises(PermissionError, match="temporarily unreadable"):
        publish_catalog(BatchResult(request, ()))

    assert catalog_path.read_bytes() == original_catalog


def test_malformed_existing_catalog_is_not_replaced_by_partial_publication(tmp_path: Path) -> None:
    request = _request(tmp_path)
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        publish_catalog(BatchResult(request, (
            BatchRaceResult(
                "2024-round-01", 1, "failed", detail="offline failure",
                session_code="r", session_name="Race",
            ),
        )))

    assert catalog_path.read_text(encoding="utf-8") == "{not-json"


def test_resume_selects_requested_session_pointer_for_readable_race_folder(tmp_path: Path, monkeypatch) -> None:
    request = _request(tmp_path, session="Q", resume=True)
    canonical = SimpleNamespace(generation_path=tmp_path / "canonical-generation", manifest_sha256="a" * 64)
    monkeypatch.setattr("f1_replay_pipeline.app.batch_generation._session_canonical_state", lambda *_args: canonical)
    monkeypatch.setattr("f1_replay_pipeline.app.batch_generation._session_outputs_valid", lambda *_args: True)
    monkeypatch.setattr(
        "f1_replay_pipeline.app.batch_generation.read_session_browser_pointer",
        lambda *_args: SimpleNamespace(delivery_version="q-delivery"),
    )

    result = _run_race(
        request,
        ScheduledRace(8, "Monaco Grand Prix", True),
        1,
        1,
        lambda request: pytest.fail("resume must not invoke canonical generation"),
        lambda request: pytest.fail("validated session must not republish browser output"),
        lambda _event: None,
    )

    assert result.outcome == "skipped_valid"
    assert result.session_code == "q"
    assert result.race_id == "2024-round-08-monaco-grand-prix"


def test_retention_filters_sessions_independently(tmp_path: Path, monkeypatch) -> None:
    request = _request(tmp_path)
    (tmp_path / "catalog.json").write_text(json.dumps({
        "schemaVersion": 2,
        "year": 2024,
        "atomicAcrossRaces": False,
        "races": [{
            "race_id": "2024-round-08-monaco-grand-prix",
            "round_number": 8,
            "event_name": "Monaco Grand Prix",
            "sessions": [
                {"session_code": "r", "session_name": "Race", "generation_id": "2024-round-08-r", "delivery_version": "r", "outcome": "generated", "validated": True, "canonical_pointer": "canonical/x", "browser_pointer": "browser/x"},
                {"session_code": "q", "session_name": "Qualifying", "generation_id": "2024-round-08-q", "delivery_version": "q", "outcome": "generated", "validated": True, "canonical_pointer": "canonical/x", "browser_pointer": "browser/x"},
            ],
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(
        "f1_replay_pipeline.app.batch_generation._retained_session_valid",
        lambda _record, session, _request: session["session_code"] == "q",
    )

    retained = _retained_catalog_records(tmp_path, request)

    record = cast(dict[str, object], retained["2024-round-08-monaco-grand-prix"])
    sessions = cast(list[dict[str, object]], record["sessions"])
    assert [session["session_code"] for session in sessions] == ["q"]
