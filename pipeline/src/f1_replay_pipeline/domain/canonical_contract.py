"""Shared version identities for canonical Parquet contracts.

The version is part of the contract identity, rather than an implementation
detail of a particular writer or reader.  In particular, the v2 dtype tokens
are intentionally distinct from v1 tokens so a manifest cannot accidentally
mix otherwise identical table schemas from the two contracts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal


ContractVersion = Literal["v1", "v2"]
QualifyingPhase = Literal["Q1", "Q2", "Q3"]

QUALIFYING_PHASES: tuple[QualifyingPhase, ...] = ("Q1", "Q2", "Q3")
QUALIFYING_PHASE_COLUMN = "qualifying_phase"

CANONICAL_PARQUET_V1 = "canonical-parquet-v1"
CANONICAL_PARQUET_V2 = "canonical-parquet-v2"

CANONICAL_MANIFEST_SCHEMA_TOKEN_V1 = f"{CANONICAL_PARQUET_V1}:manifest"
CANONICAL_MANIFEST_SCHEMA_TOKEN_V2 = f"{CANONICAL_PARQUET_V2}:manifest"
CANONICAL_TABLE_SCHEMA_TOKEN_V1 = f"{CANONICAL_PARQUET_V1}:table"
CANONICAL_TABLE_SCHEMA_TOKEN_V2 = f"{CANONICAL_PARQUET_V2}:table"
CANONICAL_FORMAT_V1 = CANONICAL_PARQUET_V1
CANONICAL_FORMAT_V2 = CANONICAL_PARQUET_V2
CANONICAL_MANIFEST_SCHEMA_V1 = CANONICAL_MANIFEST_SCHEMA_TOKEN_V1
CANONICAL_MANIFEST_SCHEMA_V2 = CANONICAL_MANIFEST_SCHEMA_TOKEN_V2

_TABLE_NAMES = (
    "session_metadata", "drivers", "car_telemetry", "position_telemetry", "laps",
    "stints", "weather", "track_status_intervals", "race_control_messages", "results",
)


@dataclass(frozen=True)
class CanonicalContract:
    """Immutable identity and token policy for one canonical contract version."""

    version: ContractVersion
    format_version: str
    manifest_version: int
    manifest_schema_token: str
    table_schema_tokens: Mapping[str, str]
    dtype_prefix: str

    def __post_init__(self) -> None:
        if self.format_version != f"canonical-parquet-{self.version}":
            raise ValueError("canonical format_version does not match contract version")
        if self.manifest_version != int(self.version[1:]):
            raise ValueError("canonical manifest_version does not match contract version")
        tokens = dict(self.table_schema_tokens)
        if tuple(tokens) != _TABLE_NAMES or any(not token for token in tokens.values()):
            raise ValueError("canonical table schema tokens must cover the ordered table set")
        object.__setattr__(self, "table_schema_tokens", MappingProxyType(tokens))


CANONICAL_CONTRACT_V1 = CanonicalContract(
    version="v1",
    format_version=CANONICAL_PARQUET_V1,
    manifest_version=1,
    manifest_schema_token=CANONICAL_MANIFEST_SCHEMA_TOKEN_V1,
    table_schema_tokens=MappingProxyType({
        name: f"{CANONICAL_PARQUET_V1}:table:{name}" for name in _TABLE_NAMES
    }),
    dtype_prefix="",
)
CANONICAL_CONTRACT_V2 = CanonicalContract(
    version="v2",
    format_version=CANONICAL_PARQUET_V2,
    manifest_version=2,
    manifest_schema_token=CANONICAL_MANIFEST_SCHEMA_TOKEN_V2,
    table_schema_tokens=MappingProxyType({
        name: f"{CANONICAL_PARQUET_V2}:table:{name}" for name in _TABLE_NAMES
    }),
    dtype_prefix=f"{CANONICAL_PARQUET_V2}:",
)

CONTRACTS: Mapping[ContractVersion, CanonicalContract] = MappingProxyType({
    "v1": CANONICAL_CONTRACT_V1,
    "v2": CANONICAL_CONTRACT_V2,
})
CANONICAL_TABLE_SCHEMA_TOKENS_V1 = CANONICAL_CONTRACT_V1.table_schema_tokens
CANONICAL_TABLE_SCHEMA_TOKENS_V2 = CANONICAL_CONTRACT_V2.table_schema_tokens


def get_canonical_contract(version: ContractVersion | str = "v1") -> CanonicalContract:
    """Return the immutable contract selected by ``v1``/``v2`` identity."""
    normalized = _normalize_version(version)
    return CONTRACTS[normalized]


def schema_dtype_token(dtype_token: str, version: ContractVersion | str = "v1") -> str:
    """Return a versioned manifest dtype token without changing its Polars type."""
    contract = get_canonical_contract(version)
    return f"{contract.dtype_prefix}{dtype_token}"


def _normalize_version(version: ContractVersion | str) -> ContractVersion:
    if version in CONTRACTS:
        return version  # type: ignore[return-value]
    if version == CANONICAL_PARQUET_V1:
        return "v1"
    if version == CANONICAL_PARQUET_V2:
        return "v2"
    raise ValueError(f"unsupported canonical contract version: {version!r}")


__all__ = [
    "CANONICAL_CONTRACT_V1", "CANONICAL_CONTRACT_V2", "CANONICAL_FORMAT_V1", "CANONICAL_FORMAT_V2",
    "CANONICAL_MANIFEST_SCHEMA_TOKEN_V1", "CANONICAL_MANIFEST_SCHEMA_TOKEN_V2",
    "CANONICAL_MANIFEST_SCHEMA_V1", "CANONICAL_MANIFEST_SCHEMA_V2",
    "CANONICAL_PARQUET_V1", "CANONICAL_PARQUET_V2", "CANONICAL_TABLE_SCHEMA_TOKEN_V1",
    "CANONICAL_TABLE_SCHEMA_TOKEN_V2", "CANONICAL_TABLE_SCHEMA_TOKENS_V1",
    "CANONICAL_TABLE_SCHEMA_TOKENS_V2",
    "CONTRACTS", "CanonicalContract", "ContractVersion", "get_canonical_contract",
    "QUALIFYING_PHASES", "QUALIFYING_PHASE_COLUMN", "QualifyingPhase", "schema_dtype_token",
]
