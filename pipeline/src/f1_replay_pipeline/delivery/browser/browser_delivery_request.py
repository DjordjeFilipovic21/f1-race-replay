"""Lightweight request/result values for browser publication orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class BrowserDeliveryServiceError(RuntimeError):
    """An expected browser publication application failure."""


@dataclass(frozen=True)
class BrowserPublishRequest:
    canonical_parent: Path
    browser_parent: Path
    delivery_version: str
    schema_root: Path


@dataclass(frozen=True)
class BrowserPublishResult:
    request: BrowserPublishRequest
    delivery_version: str
    publication: object


__all__ = ["BrowserDeliveryServiceError", "BrowserPublishRequest", "BrowserPublishResult"]
