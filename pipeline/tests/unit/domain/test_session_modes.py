import pytest

from f1_replay_pipeline.domain.normalizers import NormalizationError
from f1_replay_pipeline.domain.generation_identity import GenerationIdentityError, build_v2_generation_id
from f1_replay_pipeline.domain.session_modes import normalize_session_identity, normalize_session_mode


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("FP1", "practice"),
        ("fp2", "practice"),
        ("Practice 3", "practice"),
        ("Q", "qualifying"),
        ("Qualifying", "qualifying"),
        ("R", "race"),
        ("Race", "race"),
        ("S", "sprint"),
        ("Sprint Qualifying", "sprint-qualifying"),
        ("SQ", "sprint-qualifying"),
        ("Sprint Shootout", "sprint-shootout"),
        ("SS", "sprint-shootout"),
    ],
)
def test_normalize_session_mode_maps_supported_aliases(token, expected):
    assert normalize_session_mode(token) == expected


def test_normalize_session_identity_keeps_practice_sessions_distinct():
    assert normalize_session_identity("FP1") == "practice-1"
    assert normalize_session_identity("Practice 2") == "practice-2"


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("FP1", "2026-round-03-session-practice-1-mode-practice"),
        ("Practice 2", "2026-round-03-session-practice-2-mode-practice"),
        ("Qualifying", "2026-round-03-session-qualifying-mode-qualifying"),
        ("Race", "2026-round-03-session-race-mode-race"),
        ("SQ", "2026-round-03-session-sprint-qualifying-mode-sprint-qualifying"),
    ],
)
def test_build_v2_generation_id_is_deterministic_and_mode_explicit(alias, expected):
    # Arrange: provide one supported alias for a fixed event identity.
    year, round_number = 2026, 3

    # Act: build the versioned generation identity twice.
    first = build_v2_generation_id(year, round_number, alias)
    second = build_v2_generation_id(year, round_number, alias)

    # Assert: aliases map to one stable, safe path component.
    assert first == expected
    assert second == first


def test_build_v2_generation_id_cannot_collide_across_practice_sessions():
    # Arrange: select two practice runs from the same event.
    event = (2026, 3)

    # Act: derive their v2 identities.
    fp1 = build_v2_generation_id(*event, "FP1")
    fp2 = build_v2_generation_id(*event, "FP2")
    fp3 = build_v2_generation_id(*event, "FP3")

    # Assert: each practice run remains independently addressable.
    assert len({fp1, fp2, fp3}) == 3


@pytest.mark.parametrize("value", [None, "", "  ", "FP1/../../", "FP1\\other", "FP1\x00"])
def test_session_identity_rejects_blank_or_unsafe_components(value):
    with pytest.raises(NormalizationError, match="session mode"):
        normalize_session_mode(value)


@pytest.mark.parametrize(
    ("year", "round_number", "session"),
    [(0, 3, "Race"), (2026, -1, "Race"), (2026, 3, "Race/../other")],
)
def test_build_v2_generation_id_rejects_unsafe_identity_inputs(year, round_number, session):
    with pytest.raises((GenerationIdentityError, NormalizationError)):
        build_v2_generation_id(year, round_number, session)


@pytest.mark.parametrize("token", [None, "", "  ", "unknown"])
def test_normalize_session_mode_rejects_blank_or_unsupported_tokens(token):
    with pytest.raises(NormalizationError, match="session mode"):
        normalize_session_mode(token)


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("Q", "qualifying"),
        ("Qualifying", "qualifying"),
        ("R", "race"),
        ("S", "sprint"),
        ("SQ", "sprint-qualifying"),
        ("SS", "sprint-shootout"),
        ("Testing", "testing"),
    ],
)
def test_normalize_session_identity_maps_non_practice_modes_to_their_mode(alias, expected):
    assert normalize_session_identity(alias) == expected


@pytest.mark.parametrize("alias", [" FP1 ", " fp1 ", "  Sprint  ", "qualifying ", "Practice 2 "])
def test_normalize_session_mode_accepts_whitespace_padded_aliases(alias):
    assert normalize_session_mode(alias) == normalize_session_mode(alias.strip())


def test_build_v2_generation_id_supports_round_zero_testing_identity():
    # Arrange: testing events use round zero in FastF1 schedules.
    generation_id = build_v2_generation_id(2026, 0, "Testing")

    # Assert: the round-zero testing identity is explicit and deterministic.
    assert generation_id == "2026-round-00-session-testing-mode-testing"
    assert build_v2_generation_id(2026, 0, "Testing") == generation_id


@pytest.mark.parametrize(
    ("year", "round_number", "session"),
    [("2026", 3, "Race"), (2026, "3", "Race")],
)
def test_build_v2_generation_id_rejects_non_integer_year_and_round(year, round_number, session):
    with pytest.raises(GenerationIdentityError):
        build_v2_generation_id(year, round_number, session)
