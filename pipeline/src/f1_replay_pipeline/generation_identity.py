"""Shared safe-component grammar for canonical generation identities."""

from __future__ import annotations

import re


_GENERATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


class GenerationIdentityError(ValueError):
    """Raised when a generation identifier cannot safely name one directory."""


def validate_generation_id(generation_id: object) -> str:
    """Return a generation ID only when it matches the canonical path grammar."""
    if not isinstance(generation_id, str) or not _GENERATION_ID.fullmatch(generation_id):
        raise GenerationIdentityError("generation_id must be a single safe path component")
    return generation_id


__all__ = ["GenerationIdentityError", "validate_generation_id"]
