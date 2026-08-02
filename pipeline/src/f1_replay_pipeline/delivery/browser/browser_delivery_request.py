"""Lightweight request/result values for browser publication orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


BrowserDeliveryContractVersion = Literal["v1", "v2"]


class BrowserDeliveryServiceError(RuntimeError):
    """An expected browser publication application failure."""


@dataclass(frozen=True)
class BrowserPublishRequest:
    canonical_parent: Path
    browser_parent: Path
    delivery_version: str
    schema_root: Path
    # v1 remains constructible only for explicit historical tooling; the
    # application request boundary is v2 by default.
    contract_version: BrowserDeliveryContractVersion = "v2"

    def __post_init__(self) -> None:
        if self.contract_version not in {"v1", "v2"}:
            raise ValueError("contract_version must be v1 or v2")


@dataclass(frozen=True)
class BrowserPublishResult:
    request: BrowserPublishRequest
    delivery_version: str
    publication: object


__all__ = [
    "BrowserDeliveryContractVersion", "BrowserDeliveryServiceError",
    "BrowserPublishRequest", "BrowserPublishResult",
]
