"""Contract tests for the pure catalog v2 models and helpers."""

from __future__ import annotations

import json

import pytest

from f1_replay_pipeline.app.catalog_v2_schema import (
    BROWSER_POINTER_FORMAT,
    CANONICAL_POINTER_FORMAT,
    CATALOG_SCHEMA_VERSION,
    CatalogV2Payload,
    CatalogV2RaceRecord,
    CatalogV2SessionRecord,
    DeprecatedV1Reference,
    HISTORICAL_BROWSER_POINTER_FORMAT,
    HISTORICAL_CANONICAL_POINTER_FORMAT,
    HISTORICAL_V1_ARTIFACT_METADATA,
    V1_DEPRECATION_METADATA,
    session_code_from_generation_id,
    strip_generation_suffix,
    validate_active_catalog,
    validate_v2_cutover_contract,
)
from f1_replay_pipeline.domain.generation_identity import build_v2_generation_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_session(**overrides: object) -> CatalogV2SessionRecord:
    """Build a minimal unvalidated session for race-record tests."""
    defaults: dict[str, object] = {
        "session_code": "r",
        "session_name": "Race",
        "generation_id": None,
        "delivery_version": None,
        "outcome": "failed",
        "validated": False,
        "canonical_pointer": None,
        "browser_pointer": None,
    }
    defaults.update(overrides)
    return CatalogV2SessionRecord(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Existing tests (unchanged)
# ---------------------------------------------------------------------------

def test_catalog_serialization_is_explicit_and_deterministic() -> None:
    session = CatalogV2SessionRecord(
        "r", "Race", "2024-round-05-r", "2024-round-05-r",
        "generated", True, None, "browser/x",
    )
    payload = CatalogV2Payload(2024, (CatalogV2RaceRecord("2024-round-05", 5, "Race", (session,)),))
    encoded = payload.to_json_bytes()

    assert json.loads(encoded) == {
        "schemaVersion": CATALOG_SCHEMA_VERSION,
        "year": 2024,
        "atomicAcrossRaces": False,
        "races": [{
            "race_id": "2024-round-05",
            "round_number": 5,
            "event_name": "Race",
            "sessions": [session.to_dict()],
        }],
    }
    assert encoded.endswith(b"\n")


def test_session_identity_helpers_handle_force_and_browser_successors() -> None:
    assert strip_generation_suffix("2024-round-05-r-force-2") == "2024-round-05-r"
    assert strip_generation_suffix("2024-round-05-r-browser-3") == "2024-round-05-r"
    assert session_code_from_generation_id("2024-round-05-r-force-2", "2024-round-05") == "r"
    assert session_code_from_generation_id(
        "2024-round-08-r", "2024-round-08-monaco-grand-prix",
    ) == "r"
    with pytest.raises(ValueError):
        session_code_from_generation_id("2023-round-08-r", "2024-round-08-monaco-grand-prix")


@pytest.mark.parametrize(
    ("alias", "expected_code"),
    [
        ("Practice 1", "fp1"),
        ("FP1", "fp1"),
        ("Sprint Qualifying", "sq"),
        ("SQ", "sq"),
        ("Sprint Shootout", "ss"),
        ("SS", "ss"),
    ],
)
def test_session_identity_helpers_parse_v2_generation_ids_for_aliases(
    alias: str, expected_code: str,
) -> None:
    generation_id = build_v2_generation_id(2026, 1, alias)

    assert session_code_from_generation_id(generation_id, "2026-round-01") == expected_code
    assert session_code_from_generation_id(f"{generation_id}-force-2", "2026-round-01") == expected_code


def test_catalog_models_reject_malformed_boundaries_and_duplicate_sessions() -> None:
    with pytest.raises(ValueError):
        CatalogV2Payload(2024, (), schema_version=1)
    session = CatalogV2SessionRecord("R", "Race", None, None, "failed", False, None, None)
    with pytest.raises(ValueError):
        CatalogV2RaceRecord("2024-round-08", 8, "Race", (session, session))
    with pytest.raises(ValueError):
        CatalogV2SessionRecord("../r", "Race", None, None, "failed", False, None, None)
    with pytest.raises(ValueError, match="browser artifact reference"):
        CatalogV2SessionRecord(
            "r", "Race", "2024-round-05-r", "2024-round-05-r",
            "generated", True, "canonical/x", None,
        )


def test_v1_contract_metadata_is_explicitly_inactive_and_replaced_by_v2() -> None:
    assert CANONICAL_POINTER_FORMAT == "canonical-parquet-v2"
    assert BROWSER_POINTER_FORMAT == "browser-delivery-v2"
    assert HISTORICAL_CANONICAL_POINTER_FORMAT == "canonical-parquet-v1"
    assert HISTORICAL_BROWSER_POINTER_FORMAT == "browser-delivery-v1"
    assert all(reference["active"] is False and reference["deprecated"] is True for reference in V1_DEPRECATION_METADATA.values())
    assert HISTORICAL_V1_ARTIFACT_METADATA["historical_fixtures"]["count"] == 4
    assert all(reference["active"] is False for reference in HISTORICAL_V1_ARTIFACT_METADATA.values())
    assert DeprecatedV1Reference("canonical manifest", "historical/canonical-manifest.json").to_dict()["active"] is False


def test_active_catalog_rejects_v1_schema_and_deprecated_fields() -> None:
    session = CatalogV2SessionRecord(
        "r", "Race", "2026-round-01-r", "2026-round-01-r", "generated", True,
        None, "browser/2026-round-01/sessions/r/browser-current.json",
    )
    payload = CatalogV2Payload(2026, (CatalogV2RaceRecord("2026-round-01", 1, "Race", (session,)),)).to_dict()

    assert validate_active_catalog(payload).to_dict() == payload
    with pytest.raises(ValueError, match="schemaVersion 2"):
        validate_active_catalog({**payload, "schemaVersion": 1})
    with pytest.raises(ValueError, match="deprecated"):
        validate_active_catalog({**payload, "deprecated": {"active": False}})


def test_v2_cutover_requires_all_four_republished_races() -> None:
    races = tuple(
        CatalogV2RaceRecord(
            f"2026-round-{number:02d}", number, "Race", (
                CatalogV2SessionRecord(
                    "r", "Race", f"2026-round-{number:02d}-r", f"2026-round-{number:02d}-r",
                    "generated", True, None,
                    f"browser/2026-round-{number:02d}/sessions/r/browser-current.json",
                ),
            ),
        )
        for number in range(1, 5)
    )
    payload = CatalogV2Payload(2026, races).to_dict()

    assert validate_v2_cutover_contract(payload, tuple(race.race_id for race in races)).schema_version == 2
    with pytest.raises(ValueError, match="exactly four"):
        validate_v2_cutover_contract(payload, ("2026-round-01",))


# ---------------------------------------------------------------------------
# Visual metadata: happy-path (valid coordinates + circuit_preview)
# ---------------------------------------------------------------------------

class TestVisualMetadataHappyPath:
    """Valid visual metadata is accepted and round-trips through to_dict."""

    def test_race_accepts_valid_coordinates_without_circuit_preview(self) -> None:
        """Coordinates alone (no circuit_preview) form a valid visual block."""
        race = CatalogV2RaceRecord(
            "2024-round-05", 5, "Race", (_minimal_session(),),
            latitude=43.7347, longitude=7.4206,
        )

        assert race.latitude == pytest.approx(43.7347)
        assert race.longitude == pytest.approx(7.4206)
        assert race.circuit_preview is None

    def test_race_accepts_valid_coordinates_with_circuit_preview(self) -> None:
        """All three visual fields present is the full happy path."""
        race = CatalogV2RaceRecord(
            "2024-round-05", 5, "Race", (_minimal_session(),),
            latitude=43.7347, longitude=7.4206,
            circuit_preview="circuits/monaco.svg",
        )

        assert race.circuit_preview == "circuits/monaco.svg"

    @pytest.mark.parametrize("preview", [
        "circuits/monaco.svg",
        "tracks/monaco-gp.svg",
        "a.svg",
        "dir/sub/file.svg",
        "2024/round-05/preview.svg",
    ])
    def test_race_accepts_various_safe_circuit_preview_paths(self, preview: str) -> None:
        """Preview paths matching _SAFE_RELATIVE_POSIX_PATH are accepted."""
        race = CatalogV2RaceRecord(
            "2024-round-05", 5, "Race", (_minimal_session(),),
            latitude=43.7347, longitude=7.4206,
            circuit_preview=preview,
        )
        assert race.circuit_preview == preview

    def test_to_dict_nests_visual_block_with_camel_case(self) -> None:
        """to_dict nests lat/lon under 'visual' and uses camelCase 'circuitPreview'."""
        race = CatalogV2RaceRecord(
            "2024-round-05", 5, "Race", (_minimal_session(),),
            latitude=43.7347, longitude=7.4206,
            circuit_preview="circuits/monaco.svg",
        )
        result = race.to_dict()

        assert "visual" in result
        assert result["visual"] == {
            "latitude": 43.7347,
            "longitude": 7.4206,
            "circuitPreview": "circuits/monaco.svg",
        }
        # Coordinates should NOT appear at top level
        assert "latitude" not in result
        assert "longitude" not in result
        assert "circuitPreview" not in result

    def test_to_dict_nests_visual_without_circuit_preview(self) -> None:
        """Visual block omits circuitPreview key when preview is absent."""
        race = CatalogV2RaceRecord(
            "2024-round-05", 5, "Race", (_minimal_session(),),
            latitude=43.7347, longitude=7.4206,
        )
        result = race.to_dict()
        visual: dict[str, object] = result["visual"]  # type: ignore[assignment]

        assert visual == {"latitude": 43.7347, "longitude": 7.4206}
        assert "circuitPreview" not in visual


# ---------------------------------------------------------------------------
# Visual metadata: backward compatibility (absent fields)
# ---------------------------------------------------------------------------

class TestVisualMetadataAbsent:
    """Races without any visual metadata are accepted for backward compat."""

    def test_race_without_visual_fields_accepted(self) -> None:
        """No latitude/longitude/circuit_preview is the pre-visual baseline."""
        race = CatalogV2RaceRecord(
            "2024-round-05", 5, "Race", (_minimal_session(),),
        )

        assert race.latitude is None
        assert race.longitude is None
        assert race.circuit_preview is None

    def test_to_dict_omits_visual_key_when_no_coordinates(self) -> None:
        """to_dict must NOT include 'visual' when coordinates are absent."""
        race = CatalogV2RaceRecord(
            "2024-round-05", 5, "Race", (_minimal_session(),),
        )
        result = race.to_dict()

        assert "visual" not in result


# ---------------------------------------------------------------------------
# Visual metadata: boundary validation (coordinate ranges)
# ---------------------------------------------------------------------------

class TestVisualBoundaryValidation:
    """Coordinate values at/just-outside valid ranges are validated."""

    @pytest.mark.parametrize("latitude,longitude", [
        (-90, 0),       # south pole
        (90, 0),        # north pole
        (0, -180),      # date-line west
        (0, 180),       # date-line east
        (0, 0),         # null island
        (-90, -180),    # both extremes
        (90, 180),      # both extremes
    ])
    def test_coordinates_at_valid_boundaries_accepted(self, latitude: float, longitude: float) -> None:
        """Exact boundary values are within range and must be accepted."""
        race = CatalogV2RaceRecord(
            "2024-round-05", 5, "Race", (_minimal_session(),),
            latitude=latitude, longitude=longitude,
        )
        assert race.latitude == pytest.approx(latitude)
        assert race.longitude == pytest.approx(longitude)

    @pytest.mark.parametrize("latitude,longitude", [
        (-91, 0),       # south of south pole
        (91, 0),        # north of north pole
        (-90.001, 0),   # fractionally below -90
        (90.001, 0),    # fractionally above 90
    ])
    def test_latitude_out_of_range_rejected(self, latitude: float, longitude: float) -> None:
        """Latitude outside [-90, 90] raises ValueError."""
        with pytest.raises(ValueError, match="latitude"):
            CatalogV2RaceRecord(
                "2024-round-05", 5, "Race", (_minimal_session(),),
                latitude=latitude, longitude=longitude,
            )

    @pytest.mark.parametrize("latitude,longitude", [
        (0, -181),      # west of date-line
        (0, 181),       # east of date-line
        (0, -180.001),  # fractionally below -180
        (0, 180.001),   # fractionally above 180
    ])
    def test_longitude_out_of_range_rejected(self, latitude: float, longitude: float) -> None:
        """Longitude outside [-180, 180] raises ValueError."""
        with pytest.raises(ValueError, match="longitude"):
            CatalogV2RaceRecord(
                "2024-round-05", 5, "Race", (_minimal_session(),),
                latitude=latitude, longitude=longitude,
            )

    @pytest.mark.parametrize("latitude", [float("inf"), float("-inf"), float("nan")])
    def test_non_finite_latitude_rejected(self, latitude: float) -> None:
        """Non-finite latitude values raise ValueError."""
        with pytest.raises(ValueError, match="latitude"):
            CatalogV2RaceRecord(
                "2024-round-05", 5, "Race", (_minimal_session(),),
                latitude=latitude, longitude=0,
            )

    @pytest.mark.parametrize("longitude", [float("inf"), float("-inf"), float("nan")])
    def test_non_finite_longitude_rejected(self, longitude: float) -> None:
        """Non-finite longitude values raise ValueError."""
        with pytest.raises(ValueError, match="longitude"):
            CatalogV2RaceRecord(
                "2024-round-05", 5, "Race", (_minimal_session(),),
                latitude=0, longitude=longitude,
            )

    @pytest.mark.parametrize("latitude", ["43.7", True, False, None, [], {}])
    def test_non_numeric_latitude_rejected(self, latitude: object) -> None:
        """Non-numeric latitude types raise ValueError."""
        with pytest.raises(ValueError, match="latitude"):
            CatalogV2RaceRecord(
                "2024-round-05", 5, "Race", (_minimal_session(),),
                latitude=latitude, longitude=0,  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# Visual metadata: incomplete coordinate pairs
# ---------------------------------------------------------------------------

class TestVisualIncompleteCoordinatePairs:
    """Providing only one coordinate without the other must raise ValueError."""

    def test_latitude_without_longitude_rejected(self) -> None:
        """Latitude present but longitude None triggers pair validation."""
        with pytest.raises(ValueError, match="latitude and longitude must be provided together"):
            CatalogV2RaceRecord(
                "2024-round-05", 5, "Race", (_minimal_session(),),
                latitude=43.7347,
            )

    def test_longitude_without_latitude_rejected(self) -> None:
        """Longitude present but latitude None triggers pair validation."""
        with pytest.raises(ValueError, match="latitude and longitude must be provided together"):
            CatalogV2RaceRecord(
                "2024-round-05", 5, "Race", (_minimal_session(),),
                longitude=7.4206,
            )


# ---------------------------------------------------------------------------
# Visual metadata: unsafe circuit_preview paths
# ---------------------------------------------------------------------------

class TestVisualUnsafeCircuitPreviewPaths:
    """Circuit preview paths that are absolute, traversal, or contain backslashes."""

    def test_absolute_circuit_preview_path_rejected(self) -> None:
        """Absolute POSIX paths must be rejected."""
        with pytest.raises(ValueError, match="circuit_preview"):
            CatalogV2RaceRecord(
                "2024-round-05", 5, "Race", (_minimal_session(),),
                latitude=43.7347, longitude=7.4206,
                circuit_preview="/etc/passwd",
            )

    def test_traversal_circuit_preview_path_rejected(self) -> None:
        """Paths containing '..' segments must be rejected."""
        with pytest.raises(ValueError, match="circuit_preview"):
            CatalogV2RaceRecord(
                "2024-round-05", 5, "Race", (_minimal_session(),),
                latitude=43.7347, longitude=7.4206,
                circuit_preview="../etc/passwd",
            )

    def test_backslash_circuit_preview_path_rejected(self) -> None:
        """Windows-style backslash paths must be rejected."""
        with pytest.raises(ValueError, match="circuit_preview"):
            CatalogV2RaceRecord(
                "2024-round-05", 5, "Race", (_minimal_session(),),
                latitude=43.7347, longitude=7.4206,
                circuit_preview="circuits\\monaco.svg",
            )

    @pytest.mark.parametrize("preview", [
        "",
        "  ",
        "circuits/../escape.svg",
        ".hidden/preview.svg",
        "/absolute/path.svg",
        "C:\\\\Windows\\\\path.svg",
    ])
    def test_various_unsafe_circuit_preview_paths_rejected(self, preview: str) -> None:
        """Blank, traversal, hidden-dot, absolute, and Windows paths are all rejected."""
        with pytest.raises(ValueError):
            CatalogV2RaceRecord(
                "2024-round-05", 5, "Race", (_minimal_session(),),
                latitude=43.7347, longitude=7.4206,
                circuit_preview=preview,
            )


# ---------------------------------------------------------------------------
# Visual metadata: circuit_preview requires coordinates
# ---------------------------------------------------------------------------

class TestVisualCircuitPreviewRequiresCoordinates:
    """circuit_preview is invalid when latitude/longitude are absent."""

    def test_circuit_preview_without_coordinates_rejected(self) -> None:
        """Providing circuit_preview without any coordinates raises ValueError."""
        with pytest.raises(ValueError, match="circuit_preview requires latitude and longitude"):
            CatalogV2RaceRecord(
                "2024-round-05", 5, "Race", (_minimal_session(),),
                circuit_preview="circuits/monaco.svg",
            )

    def test_circuit_preview_with_only_latitude_rejected(self) -> None:
        """circuit_preview with only latitude (incomplete pair) raises ValueError."""
        with pytest.raises(ValueError, match="latitude and longitude must be provided together"):
            CatalogV2RaceRecord(
                "2024-round-05", 5, "Race", (_minimal_session(),),
                latitude=43.7347,
                circuit_preview="circuits/monaco.svg",
            )


# ---------------------------------------------------------------------------
# Visual metadata: serialization round-trip
# ---------------------------------------------------------------------------

class TestVisualSerializationRoundTrip:
    """to_dict → to_json_bytes round-trip preserves visual metadata."""

    def test_visual_metadata_round_trips_through_payload_serialization(self) -> None:
        """Full payload with visual metadata round-trips through JSON."""
        race = CatalogV2RaceRecord(
            "2024-round-05", 5, "Monaco GP", (_minimal_session(),),
            latitude=43.7347, longitude=7.4206,
            circuit_preview="circuits/monaco.svg",
        )
        payload = CatalogV2Payload(2024, (race,))
        encoded = payload.to_json_bytes()
        decoded = json.loads(encoded)

        race_dict = decoded["races"][0]
        assert race_dict["visual"] == {
            "latitude": 43.7347,
            "longitude": 7.4206,
            "circuitPreview": "circuits/monaco.svg",
        }

    def test_absent_visual_metadata_omitted_from_serialization(self) -> None:
        """Race without visual metadata produces no 'visual' key in JSON."""
        race = CatalogV2RaceRecord(
            "2024-round-05", 5, "Monaco GP", (_minimal_session(),),
        )
        payload = CatalogV2Payload(2024, (race,))
        encoded = payload.to_json_bytes()
        decoded = json.loads(encoded)

        race_dict = decoded["races"][0]
        assert "visual" not in race_dict

    def test_coordinates_without_preview_round_trips(self) -> None:
        """Coordinates present but no circuit_preview still produces visual block."""
        race = CatalogV2RaceRecord(
            "2024-round-05", 5, "Monaco GP", (_minimal_session(),),
            latitude=43.7347, longitude=7.4206,
        )
        payload = CatalogV2Payload(2024, (race,))
        encoded = payload.to_json_bytes()
        decoded = json.loads(encoded)

        visual = decoded["races"][0]["visual"]
        assert visual == {"latitude": 43.7347, "longitude": 7.4206}
        assert "circuitPreview" not in visual
