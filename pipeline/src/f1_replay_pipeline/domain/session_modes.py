"""Pure normalization of FastF1 session labels into replay modes."""

from __future__ import annotations

from collections.abc import Mapping
import re
from types import MappingProxyType
from typing import Literal

from .normalizers import NormalizationError


SessionMode = Literal[
    "practice",
    "qualifying",
    "race",
    "sprint",
    "sprint-qualifying",
    "sprint-shootout",
    "testing",
]

_SESSION_TOKEN = re.compile(r"[^a-z0-9]+")
_MODE_ALIASES: Mapping[str, SessionMode] = MappingProxyType({
    "fp1": "practice",
    "fp-1": "practice",
    "practice": "practice",
    "practice-1": "practice",
    "fp2": "practice",
    "fp-2": "practice",
    "practice-2": "practice",
    "fp3": "practice",
    "fp-3": "practice",
    "practice-3": "practice",
    "q": "qualifying",
    "qualifying": "qualifying",
    "r": "race",
    "race": "race",
    "s": "sprint",
    "sprint": "sprint",
    "sq": "sprint-qualifying",
    "sprint-qualifying": "sprint-qualifying",
    "ss": "sprint-shootout",
    "sprint-shootout": "sprint-shootout",
    "testing": "testing",
})


def normalize_session_mode(value: object) -> SessionMode:
    """Return the canonical replay mode for a FastF1 session label.

    The normalizer deliberately accepts aliases at the adapter boundary while
    returning one stable value for consumers.  It does not infer a mode from
    an empty or unknown label.
    """
    token = _session_token(value)
    try:
        return _MODE_ALIASES[token]
    except KeyError as error:
        supported = ", ".join(sorted(_MODE_ALIASES))
        raise NormalizationError(
            f"unsupported session mode {value!r}; expected one of: {supported}"
        ) from error


def normalize_session_identity(value: object) -> str:
    """Return a stable session identity token without collapsing practice runs."""
    token = _session_token(value)
    mode = normalize_session_mode(value)
    if token in {"fp1", "fp-1", "practice-1"}:
        return "practice-1"
    if token in {"fp2", "fp-2", "practice-2"}:
        return "practice-2"
    if token in {"fp3", "fp-3", "practice-3"}:
        return "practice-3"
    return mode


def _session_token(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NormalizationError(
            "session mode is required and must be a supported non-blank string"
        )
    if any(ord(character) < 0x20 for character in value):
        raise NormalizationError("session mode must be a safe identity component")
    candidate = value.strip()
    if (
        "/" in candidate
        or "\\" in candidate
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]*", candidate)
    ):
        raise NormalizationError("session mode must be a safe identity component")
    return _SESSION_TOKEN.sub("-", candidate.casefold()).strip("-")


__all__ = ["SessionMode", "normalize_session_identity", "normalize_session_mode"]
