"""Offline behavior coverage for browser-only Cloudflare R2 publication."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from f1_replay_pipeline.app.r2_publication import (
    IMMUTABLE_CACHE_CONTROL,
    MUTABLE_CACHE_CONTROL,
    R2PublicationConfig,
    R2PublicationError,
    R2PublicationSource,
    R2ProgressEvent,
    VISUAL_CACHE_CONTROL,
    build_r2_publication_plan,
    publish_r2_plan,
)


class _MissingObject(Exception):
    response = {"Error": {"Code": "404"}}


class _FakeR2Client:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, object]] = {}
        self.puts: list[str] = []

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        del Bucket
        try:
            value = self.objects[Key]
        except KeyError as error:
            raise _MissingObject from error
        payload = value["Body"]
        assert isinstance(payload, bytes)
        return {
            "ContentLength": len(payload),
            "Metadata": value["Metadata"],
            "ETag": f'"{hashlib.md5(payload, usedforsecurity=False).hexdigest()}"',
            "ContentType": value["ContentType"],
            "CacheControl": value["CacheControl"],
        }

    def put_object(self, **values: object) -> dict[str, object]:
        key = values["Key"]
        assert isinstance(key, str)
        self.puts.append(key)
        self.objects[key] = dict(values)
        return {}


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _publication_source(tmp_path: Path) -> R2PublicationSource:
    race_id = "2024-round-03-australian-grand-prix"
    delivery = "2024-round-03-r"
    generation = tmp_path / "browser" / race_id / "generations" / delivery
    generation.mkdir(parents=True)
    track_payload = _json_bytes({"track": "melbourne"})
    chunk_payload = _json_bytes({"sequence": 1})
    (generation / "track-assets.json").write_bytes(track_payload)
    chunks = generation / "chunks"
    chunks.mkdir()
    (chunks / "chunk-001.json").write_bytes(chunk_payload)
    manifest = {
        "deliveryVersion": delivery,
        "sourceGenerationId": delivery,
        "sourceManifestSha256": "a" * 64,
        "trackAssets": {
            "path": "track-assets.json",
            "sha256": hashlib.sha256(track_payload).hexdigest(),
        },
        "chunks": [{
            "path": "chunks/chunk-001.json",
            "sha256": hashlib.sha256(chunk_payload).hexdigest(),
        }],
    }
    manifest_payload = _json_bytes(manifest)
    (generation / "manifest.json").write_bytes(manifest_payload)
    pointer_directory = tmp_path / "browser" / race_id / "sessions" / "r"
    pointer_directory.mkdir(parents=True)
    (pointer_directory / "browser-current.json").write_bytes(_json_bytes({
        "deliveryVersion": delivery,
        "formatVersion": "browser-delivery-v1",
        "manifestPath": f"generations/{delivery}/manifest.json",
        "manifestSha256": hashlib.sha256(manifest_payload).hexdigest(),
    }))
    visual = tmp_path / "visuals" / race_id
    visual.mkdir(parents=True)
    (visual / "circuit-preview.json").write_bytes(_json_bytes({"points": [[1, 2]]}))
    (tmp_path / "catalog.json").write_bytes(_json_bytes({
        "schemaVersion": 2,
        "year": 2024,
        "atomicAcrossRaces": False,
        "races": [
            {
                "race_id": race_id,
                "round_number": 3,
                "event_name": "Australian Grand Prix",
                "country": "Australia",
                "location": "Melbourne",
                "visual": {
                    "latitude": -37.8497,
                    "longitude": 144.968,
                    "circuitPreview": f"visuals/{race_id}/circuit-preview.json",
                },
                "sessions": [{
                    "session_code": "r",
                    "session_name": "Race",
                    "generation_id": delivery,
                    "delivery_version": delivery,
                    "outcome": "generated",
                    "validated": True,
                    "canonical_pointer": None,
                    "browser_pointer": (
                        f"browser/{race_id}/sessions/r/browser-current.json"
                    ),
                }],
            },
            {
                "race_id": "2024-round-04-japanese-grand-prix",
                "round_number": 4,
                "event_name": "Japanese Grand Prix",
                "sessions": [{
                    "session_code": "r",
                    "session_name": "Race",
                    "generation_id": None,
                    "delivery_version": None,
                    "outcome": "failed",
                    "validated": False,
                    "canonical_pointer": None,
                    "browser_pointer": None,
                }],
            },
        ],
    }))
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    return R2PublicationSource(2024, tmp_path, schemas)


def _skip_deep_validation(*_args: object, **_kwargs: object) -> None:
    pass


def test_plan_contains_only_validated_browser_sessions_and_public_dependencies(
    tmp_path: Path,
) -> None:
    progress: list[R2ProgressEvent] = []
    plan = build_r2_publication_plan(
        _publication_source(tmp_path),
        validate_delivery=_skip_deep_validation,
        progress=progress.append,
    )

    catalog = json.loads(plan.catalog.payload or b"")
    assert [race["race_id"] for race in catalog["races"]] == [
        "2024-round-03-australian-grand-prix",
    ]
    assert catalog["races"][0]["sessions"][0]["canonical_pointer"] is None
    assert len(plan.immutable) == 3
    assert len(plan.visuals) == 1
    assert len(plan.pointers) == 1
    assert all(spec.cache_control == IMMUTABLE_CACHE_CONTROL for spec in plan.immutable)
    assert plan.visuals[0].cache_control == VISUAL_CACHE_CONTROL
    assert plan.pointers[0].cache_control == MUTABLE_CACHE_CONTROL
    assert plan.catalog.cache_control == MUTABLE_CACHE_CONTROL
    assert [(event.completed, event.total) for event in progress] == [(0, 1), (1, 1)]
    assert progress[-1].key == "2024-round-03-australian-grand-prix/r"


def test_publication_orders_immutable_visual_pointer_and_catalog_objects(
    tmp_path: Path,
) -> None:
    plan = build_r2_publication_plan(
        _publication_source(tmp_path),
        validate_delivery=_skip_deep_validation,
    )
    client = _FakeR2Client()
    progress: list[R2ProgressEvent] = []

    result = publish_r2_plan(
        plan, client=client, bucket="replays", progress=progress.append,
    )

    assert client.puts == [spec.key for spec in plan.objects]
    assert result.uploaded == len(plan.objects)
    assert result.reused == 0
    assert client.puts[-1] == "seasons/2024/catalog.json"
    assert [event.phase for event in progress if event.completed == 0] == [
        "r2_uploading_immutable",
        "r2_uploading_visuals",
        "r2_uploading_pointers",
        "r2_committing_catalog",
    ]
    assert progress[-1] == R2ProgressEvent(
        "r2_completed", len(plan.objects), len(plan.objects),
        len(plan.objects), 0, "seasons/2024/catalog.json",
    )


def test_retry_reuses_immutable_objects_and_recommits_mutable_discovery(
    tmp_path: Path,
) -> None:
    plan = build_r2_publication_plan(
        _publication_source(tmp_path),
        validate_delivery=_skip_deep_validation,
    )
    client = _FakeR2Client()
    publish_r2_plan(plan, client=client, bucket="replays")
    client.puts.clear()

    result = publish_r2_plan(plan, client=client, bucket="replays")

    assert result.reused == len(plan.immutable)
    assert result.uploaded == len(plan.visuals) + len(plan.pointers) + 1
    assert client.puts == [
        *(spec.key for spec in plan.visuals),
        *(spec.key for spec in plan.pointers),
        plan.catalog.key,
    ]


def test_immutable_collision_stops_before_pointer_or_catalog_commit(
    tmp_path: Path,
) -> None:
    plan = build_r2_publication_plan(
        _publication_source(tmp_path),
        validate_delivery=_skip_deep_validation,
    )
    client = _FakeR2Client()
    first = plan.immutable[0]
    client.objects[first.key] = {
        "Body": b"x",
        "Metadata": {"sha256": hashlib.sha256(b"x").hexdigest()},
        "ContentType": "application/json",
        "CacheControl": IMMUTABLE_CACHE_CONTROL,
    }

    with pytest.raises(R2PublicationError, match="immutable R2 object collision"):
        publish_r2_plan(plan, client=client, bucket="replays")

    assert plan.catalog.key not in client.puts
    assert not any(spec.key in client.puts for spec in plan.pointers)


def test_environment_config_requires_safe_explicit_r2_target() -> None:
    with pytest.raises(R2PublicationError, match="R2_ENDPOINT_URL, R2_BUCKET"):
        R2PublicationConfig.from_environment({})
    with pytest.raises(R2PublicationError, match="r2.cloudflarestorage.com origin"):
        R2PublicationConfig.from_environment({
            "R2_ENDPOINT_URL": "http://example.com/path",
            "R2_BUCKET": "replays",
        })

    assert R2PublicationConfig.from_environment({
        "R2_ENDPOINT_URL": "https://account.r2.cloudflarestorage.com",
        "R2_BUCKET": "replays",
    }) == R2PublicationConfig(
        "https://account.r2.cloudflarestorage.com",
        "replays",
    )


def test_plan_refuses_to_replace_production_with_an_empty_public_catalog(
    tmp_path: Path,
) -> None:
    source = _publication_source(tmp_path)
    catalog = json.loads((tmp_path / "catalog.json").read_bytes())
    for race in catalog["races"]:
        for session in race["sessions"]:
            session["validated"] = False
            session["browser_pointer"] = None
            session["canonical_pointer"] = None
    (tmp_path / "catalog.json").write_bytes(_json_bytes(catalog))

    with pytest.raises(R2PublicationError, match="no validated browser sessions"):
        build_r2_publication_plan(
            source,
            validate_delivery=_skip_deep_validation,
        )
