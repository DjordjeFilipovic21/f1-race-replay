"""Deterministic offline integration tests covering the full batch generation pipeline.

These tests exercise ``run_batch`` end-to-end with real canonical Parquet publication,
real browser delivery through the production composition roots, real catalog
publication, and deep output validation.  Only external boundaries (FastF1 session
resolution, schedule provider) are injected as fakes.  Network access is explicitly
blocked.
"""

from __future__ import annotations

import importlib
import json
import socket
import urllib.request
from pathlib import Path

import pandas as pd
import pytest

from f1_replay_pipeline.app.batch_generation import (
    BatchRequest,
    ScheduledRace,
    run_batch,
    verify_catalog,
)
from f1_replay_pipeline.app.cli import (
    DefaultBrowserService,
    DefaultPipelineService,
)
from f1_replay_pipeline.delivery.browser.browser_delivery_request import (
    BrowserPublishRequest,
    BrowserPublishResult,
)
from f1_replay_pipeline.app.orchestration import PipelineRequest, PipelineResult

from fixtures.fake_fastf1_session import FakeFastF1Session, build_complete_session


# ---------------------------------------------------------------------------
# Shared test infrastructure
# ---------------------------------------------------------------------------


REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_ROOT = REPO_ROOT / "contracts" / "replay-data" / "v1" / "schemas"


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail deterministically if any code path attempts network access."""

    def _fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network access forbidden in batch integration tests")

    monkeypatch.setattr(socket, "create_connection", _fail)
    monkeypatch.setattr(socket.socket, "connect", _fail)
    monkeypatch.setattr(socket.socket, "connect_ex", _fail)
    monkeypatch.setattr(urllib.request, "urlopen", _fail)


def _build_richer_session() -> FakeFastF1Session:
    """Return a complete FakeFastF1Session whose position telemetry and lap
    metadata are sufficient for the track-asset generator to produce valid
    browser delivery artifacts.

    Provides a closed rectangular reference track, a valid reference lap (lap 1),
    and at least two boundary laps (lap >= 2) with position data at their start
    times so that ``_calibrate_start_finish_offset`` can find enough inliers.
    """
    session = build_complete_session()

    # -- Replace laps with multi-lap data (two eligible laps per driver) --
    # HAM: lap 1 (0→92_500 ms), lap 2 (92_500→185_000 ms)
    # VER: lap 1 (skipped, NaT), lap 2 (50_000→142_500 ms) — boundary candidate
    session.laps = pd.DataFrame({
        "DriverNumber": ["44", "1", "44", "1"],
        "LapNumber": [1, 1, 2, 2],
        "LapStartTime": [
            pd.Timedelta(0, unit="s"),
            pd.Timedelta(0, unit="s"),
            pd.Timedelta(92_500, unit="ms"),
            pd.Timedelta(50, unit="s"),
        ],
        "LapTime": [
            pd.Timedelta(92_500, unit="ms"),
            pd.NaT,
            pd.Timedelta(92_500, unit="ms"),
            pd.Timedelta(92_500, unit="ms"),
        ],
        "Time": [
            pd.Timedelta(92_500, unit="ms"),
            pd.NaT,
            pd.Timedelta(185, unit="s"),
            pd.Timedelta(142_500, unit="ms"),
        ],
        "Compound": ["SOFT", pd.NA, "SOFT", "MEDIUM"],
        "Deleted": [False, False, False, False],
        "IsAccurate": [True, pd.NA, True, True],
    })

    # -- Replace position / car telemetry --
    # HAM positions: closed rectangle for reference lap (lap 1 range 0–92_500 ms)
    #   plus an exact boundary point at lap 2 start (92_500 ms).
    # VER position: single boundary point at lap 2 start (50_000 ms).
    def _td(seconds: float) -> pd.Timedelta:
        return pd.Timedelta(seconds, unit="s")

    pos_rows_44 = pd.DataFrame({
        "SessionTime": [_td(s) for s in (10, 20, 30, 40, 50, 92.5)],
        "Time": [_td(s) for s in (10, 20, 30, 40, 50, 92.5)],
        "Date": [pd.Timestamp("2026-03-08T05:00:{:02d}".format(i))
                 for i in range(10, 22, 2)],
        "X": [0.0, 10_000.0, 10_000.0, 0.0, 0.0, 0.0],
        "Y": [0.0, 0.0, 8_000.0, 8_000.0, 0.0, 0.0],
        "Z": [0.0] * 6,
        "Status": ["OnTrack"] * 6,
        "Source": ["pos"] * 6,
    })
    pos_rows_1 = pd.DataFrame({
        "SessionTime": [_td(50)],
        "Time": [_td(50)],
        "Date": [pd.Timestamp("2026-03-08T05:00:50")],
        "X": [10.0], "Y": [0.0], "Z": [0.0],
        "Status": ["OnTrack"], "Source": ["pos"],
    })

    car_rows_44 = pd.DataFrame({
        "SessionTime": [_td(s) for s in (10, 20, 30, 40)],
        "Time": [_td(s) for s in (10, 20, 30, 40)],
        "Date": [pd.Timestamp("2026-03-08T05:00:{:02d}".format(i))
                 for i in range(10, 50, 10)],
        "Speed": [200.0, 220.0, 210.0, 200.0],
        "RPM": [11000, 11500, 11200, 11000],
        "nGear": [7] * 4,
        "Throttle": [100.0] * 4,
        "Brake": [False] * 4,
        "DRS": [12] * 4,
        "Source": ["car"] * 4,
    })
    car_rows_1 = pd.DataFrame({
        "SessionTime": [_td(15)],
        "Time": [_td(15)],
        "Date": [pd.Timestamp("2026-03-08T05:00:15")],
        "Speed": [250.0], "RPM": [12000], "nGear": [7],
        "Throttle": [100.0], "Brake": [False], "DRS": [12],
        "Source": ["car"],
    })

    session.pos_data = {"44": pos_rows_44, "1": pos_rows_1}
    session.car_data = {"44": car_rows_44, "1": car_rows_1}

    return session


def _resolver_for(session: FakeFastF1Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Monkeypatch ``FastF1SessionResolver.__call__`` to return *session* for
    every selection, so the real pipeline service operates entirely offline."""
    resolver_module = importlib.import_module(
        "f1_replay_pipeline.adapters.fastf1.resolver",
    )
    monkeypatch.setattr(
        resolver_module.FastF1SessionResolver,
        "__call__",
        lambda _resolver, _selection: session,
    )


def _schedule(year: int, *, backend: str | None = None) -> tuple[ScheduledRace, ...]:
    """Deterministic in-memory schedule matching the richer fake session."""
    del year, backend
    return (ScheduledRace(3, "Australian Grand Prix", True),)


def _request(root: Path, *, resume: bool = False) -> BatchRequest:
    """Build the shared deterministic request used by both integration flows."""
    return BatchRequest(
        year=2026,
        rounds=(3,),
        all_rounds=False,
        session="R",
        canonical_root=root / "canonical",
        browser_root=root / "browser",
        schema_root=SCHEMA_ROOT,
        resume=resume,
    )


# ---------------------------------------------------------------------------
# Happy-path integration test
# ---------------------------------------------------------------------------


def test_happy_path_publishes_canonical_browser_and_valid_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end batch run exercising real canonical Parquet publication, real
    browser delivery through the production composition roots, real catalog
    generation, and deep ``verify_catalog`` validation — all offline.

    Only the FastF1 resolver and schedule provider are injected; everything
    else follows the real production composition path.
    """
    # Arrange ----------------------------------------------------------------
    session = _build_richer_session()
    _resolver_for(session, monkeypatch)

    request = _request(tmp_path)
    canonical_root = request.canonical_root
    browser_root = request.browser_root

    # Track every pipeline invocation so we can assert exactly one call.
    pipeline_calls: list[PipelineRequest] = []
    def tracking_pipeline(request: PipelineRequest) -> PipelineResult:
        pipeline_calls.append(request)
        return DefaultPipelineService()(request)

    # Act --------------------------------------------------------------------
    progress_events: list[object] = []
    result = run_batch(
        request,
        schedule_provider=_schedule,
        pipeline_service=tracking_pipeline,
        browser_service=DefaultBrowserService(),
        progress=progress_events.append,
    )

    # Assert -----------------------------------------------------------------
    race = result.races[0]

    # -- Single pipeline invocation produced committed canonical artefacts --
    assert len(pipeline_calls) == 1

    # Diagnose browser failure if outcome is not "generated".
    assert race.outcome == "generated", (
        f"expected outcome 'generated', got {race.outcome!r}; "
        f"detail={race.detail!r}; "
        f"last_progress_events={[str(e) for e in progress_events[-6:]]}"
    )
    assert race.generation_id is not None
    race_path = canonical_root / race.race_id
    gens_dir = race_path / "generations"
    generation_dir = gens_dir / race.generation_id
    assert generation_dir.is_dir(), "canonical generation directory must exist"
    assert (race_path / "current.json").is_file(), "canonical current pointer must exist"

    # -- Every canonical Parquet table was written --
    from f1_replay_pipeline.storage.parquet_io import CANONICAL_PARQUET_TABLE_NAMES
    for name in CANONICAL_PARQUET_TABLE_NAMES:
        parquet = generation_dir / "tables" / f"{name}.parquet"
        assert parquet.is_file(), f"canonical Parquet table missing: {name}"

    # -- Manifest carries a real SHA-256 --
    manifest_path = generation_dir / "manifest.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_bytes())
    assert manifest["generation_id"] == race.generation_id
    assert all(isinstance(table["byte_sha256"], str) and len(table["byte_sha256"]) == 64
               for table in manifest["tables"])

    # -- Real browser delivery was published --
    browser_race = browser_root / race.race_id
    assert (browser_race / "browser-current.json").is_file(), "browser pointer must exist"
    browser_gens = browser_race / "generations"
    assert browser_gens.is_dir()

    # -- Catalog was atomically published --
    catalog_path = tmp_path / "catalog.json"
    assert catalog_path.is_file()
    catalog = json.loads(catalog_path.read_bytes())
    assert catalog["year"] == 2026
    assert len(catalog["races"]) == 1
    catalog_race = catalog["races"][0]
    assert catalog_race["race_id"] == race.race_id
    assert catalog_race["validated"] is True
    assert catalog_race["outcome"] == "generated"
    assert catalog_race["generation_id"] == race.generation_id

    # -- verify_catalog deep-validates every catalog reference --
    verify_results = verify_catalog(request)
    assert len(verify_results) == 1
    assert verify_results[0].outcome == "valid"
    assert verify_results[0].race_id == race.race_id
    assert verify_results[0].generation_id == race.generation_id


# ---------------------------------------------------------------------------
# Resume-after-browser-failure integration test
# ---------------------------------------------------------------------------


def test_resume_after_browser_failure_reuses_committed_canonical_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First ``run_batch`` commits canonical generation while browser publication
    fails (non-committed error).  Second ``run_batch`` with ``resume=True``
    detects the committed canonical generation, finishes browser delivery via
    the real browser service, and completes catalog publication.  The pipeline
    service is never invoked during the resume run.
    """
    session = _build_richer_session()
    _resolver_for(session, monkeypatch)

    request = _request(tmp_path)
    canonical_root = request.canonical_root
    browser_root = request.browser_root

    # ========================================================================
    # First run — real pipeline, browser fails before publication
    # ========================================================================

    def _failing_browser(request: BrowserPublishRequest) -> BrowserPublishResult:
        raise RuntimeError("injected non-committed browser failure")

    first_pipeline_calls: list[PipelineRequest] = []
    def _first_pipeline(request: PipelineRequest) -> PipelineResult:
        first_pipeline_calls.append(request)
        return DefaultPipelineService()(request)

    result1 = run_batch(
        request,
        schedule_provider=_schedule,
        pipeline_service=_first_pipeline,
        browser_service=_failing_browser,
    )

    # -- Assert canonical generation was committed --
    race_id = result1.races[0].race_id
    canonical_race = canonical_root / race_id
    assert len(first_pipeline_calls) == 1
    assert (canonical_race / "current.json").is_file(), (
        "canonical current pointer must exist after committed generation"
    )
    canonical_state = json.loads((canonical_race / "current.json").read_bytes())
    generation_id = canonical_state["generation_id"]
    assert generation_id

    # -- Assert race outcome is failed (browser wasn't committed) --
    race1 = result1.races[0]
    assert race1.outcome == "failed"
    assert race1.generation_id == generation_id

    # -- Browser output was NOT produced --
    browser_race = browser_root / race_id
    assert not (browser_race / "browser-current.json").exists(), (
        "browser pointer must not exist after browser failure"
    )

    # ========================================================================
    # Second run — resume, real browser, guarded pipeline
    # ========================================================================

    second_pipeline_calls: list[PipelineRequest] = []
    progress2: list[object] = []
    def _guarded_pipeline(request: PipelineRequest) -> PipelineResult:
        """Track and fail if the pipeline is invoked — resume must reuse the
        already-committed canonical generation."""
        second_pipeline_calls.append(request)
        raise AssertionError(
            "pipeline service must not be called during resume — "
            "canonical generation should be reused"
        )

    result2 = run_batch(
        _request(tmp_path, resume=True),
        schedule_provider=_schedule,
        pipeline_service=_guarded_pipeline,
        browser_service=DefaultBrowserService(),
        progress=progress2.append,
    )

    # -- Assert pipeline was never called during resume --
    assert len(second_pipeline_calls) == 0

    # -- Assert browser publication completed --
    race2 = result2.races[0]
    assert race2.outcome in ("generated", "skipped_valid"), (
        f"resume outcome must be generated or skipped_valid, got {race2.outcome}"
    )
    assert race2.generation_id == generation_id, (
        "canonical generation ID must be reused during resume"
    )
    assert (browser_race / "browser-current.json").is_file(), (
        "browser pointer must exist after successful resume browser publication"
    )

    # Browser delivery version must be a non-empty string.
    assert race2.delivery_version is not None
    assert isinstance(race2.delivery_version, str) and race2.delivery_version.strip()

    # -- Manifest integrity: browser manifest references the canonical source --
    browser_pointer = json.loads(
        (browser_race / "browser-current.json").read_bytes()
    )
    assert race2.delivery_version == browser_pointer["deliveryVersion"]
    manifest_path = browser_race / "generations" / browser_pointer["deliveryVersion"] / "manifest.json"
    browser_manifest = json.loads(manifest_path.read_bytes())
    assert browser_manifest["sourceGenerationId"] == generation_id, (
        "browser manifest must reference the reused canonical generation"
    )

    # -- Catalog was updated with the now-valid browser entry --
    catalog = json.loads((tmp_path / "catalog.json").read_bytes())
    assert catalog["year"] == 2026
    catalog_race = catalog["races"][0]
    assert catalog_race["race_id"] == race_id
    assert catalog_race["validated"] is True
    assert catalog_race["generation_id"] == generation_id

    # -- verify_catalog deep-validates the completed race --
    verify_results = verify_catalog(request)
    assert len(verify_results) == 1
    assert verify_results[0].outcome == "valid"
    assert verify_results[0].race_id == race_id
    assert verify_results[0].generation_id == generation_id
