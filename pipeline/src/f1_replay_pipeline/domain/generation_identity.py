"""Shared safe-component grammar for canonical generation identities."""

from __future__ import annotations

import re

from .session_modes import normalize_session_identity, normalize_session_mode


_GENERATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


class GenerationIdentityError(ValueError):
    """Raised when a generation identifier cannot safely name one directory."""


def validate_generation_id(generation_id: object) -> str:
    """Return a generation ID only when it matches the canonical path grammar."""
    if not isinstance(generation_id, str) or not _GENERATION_ID.fullmatch(generation_id):
        raise GenerationIdentityError("generation_id must be a single safe path component")
    # Keep the filesystem boundary explicit instead of relying only on the
    # regular expression.  This makes the invariants obvious to callers which
    # reuse this validator for session and delivery path components.
    if any(separator in generation_id for separator in ("/", "\\", "\x00")):
        raise GenerationIdentityError("generation_id must be a single safe path component")
    if generation_id in {".", ".."}:
        raise GenerationIdentityError("generation_id must not be a traversal component")
    return generation_id


def build_v2_generation_id(year: object, round_number: object, session: object) -> str:
    """Build an explicit, collision-resistant v2 generation identity.

    The session identity preserves the practice ordinal while the mode remains
    a separate component.  Keeping both components in the path makes the
    v2 boundary explicit and prevents a generic generation label such as
    ``race`` from being reused for a different session mode.
    """
    if type(year) is not int or not 1 <= year <= 9999:
        raise GenerationIdentityError("year must be an integer between 1 and 9999")
    if type(round_number) is not int or not 0 <= round_number <= 9999:
        raise GenerationIdentityError("round_number must be an integer between 0 and 9999")

    identity = normalize_session_identity(session)
    mode = normalize_session_mode(session)
    return validate_generation_id(
        f"{year:04d}-round-{round_number:02d}-session-{identity}-mode-{mode}"
    )


__all__ = ["GenerationIdentityError", "build_v2_generation_id", "validate_generation_id"]
