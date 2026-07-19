"""Offline behavioral coverage for sequential batch generation."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from f1_replay_pipeline.batch_generation import BatchRequest, BatchResult, ScheduledRace, _browser_output_valid, publish_catalog, run_batch, verify_catalog
from f1_replay_pipeline.browser_delivery_publication import BrowserValidationProgress
from f1_replay_pipeline.browser_delivery_request import BrowserPublishResult
from f1_replay_pipeline.cli import main
from f1_replay_pipeline.generation_publication import (
    GenerationPublicationResult,
    PublicationDurabilityUncertainError,
)
from f1_replay_pipeline.orchestration import PipelineResult, PublicationError


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
        ("2024-round-01-one", "2024-round-01-r"), ("2024-round-02-two", "2024-round-02-r"),
    ]
    assert calls == [1, 2]


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
    canonical = SimpleNamespace(generation_path=tmp_path / "generation", manifest_sha256="a" * 64)
    monkeypatch.setattr("f1_replay_pipeline.batch_generation._canonical_state", lambda *_paths: canonical)
    monkeypatch.setattr("f1_replay_pipeline.batch_generation._browser_output_valid", lambda *_paths: True)
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
    assert captured.out == "race_id=2024-round-01-one outcome=generated generation_id=2024-round-01-r delivery_version=2024-round-01-r\n"
    assert "canonical_generating" in captured.err


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
    monkeypatch.setattr("f1_replay_pipeline.cli._terminal_progress_renderer", Renderer)
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
    canonical = SimpleNamespace(generation_path=tmp_path / "existing", manifest_sha256="a" * 64)
    monkeypatch.setattr("f1_replay_pipeline.batch_generation._canonical_state", lambda *_paths: canonical)
    monkeypatch.setattr("f1_replay_pipeline.batch_generation._browser_output_valid", lambda *_paths: False)

    result = run_batch(
        _request(tmp_path, resume=True),
        schedule_provider=lambda *_args, **_kwargs: (ScheduledRace(1, "One", True),),
        pipeline_service=pipeline,
        browser_service=browser,
    )

    assert calls == []
    assert result.races[0].generation_id == "existing"


def test_canonical_durability_warning_continues_browser_and_preserves_outcome(
    tmp_path: Path, monkeypatch,
) -> None:
    generation_id = "2024-round-01-r"
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

    monkeypatch.setattr("f1_replay_pipeline.batch_generation._canonical_state", lambda *_paths: committed)

    result = run_batch(
        _request(tmp_path),
        schedule_provider=lambda *_args, **_kwargs: (ScheduledRace(1, "One", True),),
        pipeline_service=pipeline,
        browser_service=browser,
    )

    assert result.races[0].outcome == "committed_with_durability_warning"
    assert result.races[0].generation_id == generation_id
    assert len(browser_calls) == 1


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
    deep_calls = []
    monkeypatch.setattr("f1_replay_pipeline.batch_generation._outputs_valid", lambda *paths: deep_calls.append(paths) or True)
    monkeypatch.setattr("f1_replay_pipeline.batch_generation._shallow_canonical_state", lambda path: SimpleNamespace(generation_path=path / "generations" / "g", manifest_sha256="a" * 64))
    monkeypatch.setattr("f1_replay_pipeline.batch_generation._shallow_browser_output_valid", lambda *_paths: True)
    calls, pipeline, browser = _services()
    schedule = lambda *_args, **_kwargs: (ScheduledRace(1, "One", True), ScheduledRace(2, "Two", True))
    run_batch(_request(tmp_path, rounds=(1,)), schedule_provider=schedule, pipeline_service=pipeline, browser_service=browser)
    run_batch(_request(tmp_path, rounds=(2,)), schedule_provider=schedule, pipeline_service=pipeline, browser_service=browser)

    catalog = json.loads((tmp_path / "catalog.json").read_text(encoding="utf-8"))
    assert [record["race_id"] for record in catalog["races"]] == ["2024-round-01-one", "2024-round-02-two"]
    assert len(deep_calls) == 2


def test_catalog_drops_malformed_prior_references_without_deep_validation(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "canonical").mkdir()
    (tmp_path / "catalog.json").write_text(json.dumps({
        "year": 2024,
        "races": [{"race_id": "../outside", "validated": True, "canonical": "canonical/../outside/current.json", "browser": "browser/../outside/browser-current.json"}],
    }), encoding="utf-8")
    monkeypatch.setattr("f1_replay_pipeline.batch_generation._outputs_valid", lambda *_paths: pytest.fail("retained output was deeply validated"))

    publish_catalog(BatchResult(_request(tmp_path), ()))

    assert json.loads((tmp_path / "catalog.json").read_text(encoding="utf-8"))["races"] == []


def test_verify_catalog_deeply_validates_each_catalog_reference(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "canonical").mkdir()
    (tmp_path / "catalog.json").write_text(json.dumps({
        "year": 2024,
        "races": [{"race_id": "2024-round-01-one", "round_number": 1, "validated": True, "canonical": "canonical/2024-round-01-one/current.json", "browser": "browser/2024-round-01-one/browser-current.json"}],
    }), encoding="utf-8")
    canonical = GenerationPublicationResult(tmp_path / "canonical" / "2024-round-01-one" / "generations" / "g", tmp_path / "manifest.json", tmp_path / "current.json", "a" * 64)
    calls = []
    monkeypatch.setattr("f1_replay_pipeline.batch_generation._retained_record_valid", lambda *_args: True)
    monkeypatch.setattr("f1_replay_pipeline.batch_generation._canonical_state", lambda *_args: canonical)
    monkeypatch.setattr("f1_replay_pipeline.browser_delivery_publication.validate_complete_browser_delivery", lambda *args, **kwargs: calls.append((args, kwargs)))

    results = verify_catalog(_request(tmp_path))

    assert [result.outcome for result in results] == ["valid"]
    assert len(calls) == 1


def test_verify_cli_reports_stable_results_and_nonzero_invalid_status(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr("f1_replay_pipeline.cli.verify_catalog", lambda *_args, **_kwargs: (
        SimpleNamespace(race_id="2024-round-01-one", outcome="valid"),
        SimpleNamespace(race_id="2024-round-02-two", outcome="invalid"),
    ))

    status = main(["verify", "--year", "2024", "--output", str(tmp_path / "canonical"), "--browser-output", str(tmp_path / "browser")])
    captured = capsys.readouterr()

    assert status == 1
    assert captured.out.splitlines() == ["race_id=2024-round-01-one outcome=valid", "race_id=2024-round-02-two outcome=invalid"]
