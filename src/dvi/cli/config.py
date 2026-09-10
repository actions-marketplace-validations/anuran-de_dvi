"""Declarative config for the DVI CLI: parse dvi.toml, validate with pydantic.

The config is the single source of truth for a run — what asset to analyze,
where its before/after data lives, the lineage manifest, the change list RCA
attributes to, and the gate. Every expected failure surfaces as a DviError so
the CLI can map it to exit code 2 instead of a raw traceback.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


class DviError(Exception):
    """A clear, user-facing error (bad config, missing input, unresolved target)."""


# A dot-separated SQL identifier: each part must be a plain, unquoted identifier
# (letter/underscore start, then letters/digits/underscores/dollar). This rejects
# whitespace, semicolons, quotes, comment markers and empty parts, so a table name
# can never carry a SQL-injection payload into the generated warehouse queries.
_IDENT_PART = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")


def _validate_table_identifier(value: str) -> str:
    parts = value.split(".")
    if not all(_IDENT_PART.fullmatch(part) for part in parts):
        raise ValueError(
            f"invalid table identifier {value!r}: each dot-separated part must be a "
            "plain SQL identifier (letters, digits, underscore, dollar; not starting "
            "with a digit)"
        )
    return value


class FileSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["file"]
    before: str
    after: str


class WarehouseSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["warehouse"]
    database: str
    before_table: str
    after_table: str

    @field_validator("before_table", "after_table")
    @classmethod
    def _check_table_identifier(cls, value: str) -> str:
        return _validate_table_identifier(value)


class LineageConfig(BaseModel):
    manifest: str


class ChangeConfig(BaseModel):
    id: str
    targets: list[str] = Field(min_length=1)
    timestamp: datetime
    label: str = ""

    @field_validator("timestamp", mode="after")
    @classmethod
    def _normalize_naive_utc(cls, value: datetime) -> datetime:
        # Mirror dvi.changes.gitlog._to_naive_utc so declared and derived
        # change timestamps are always naive UTC and comparable/max()-able
        # together (the DVI spec's global "naive UTC" constraint).
        if value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value


class GitConfig(BaseModel):
    """Optional commit range for auto-deriving change events."""

    model_config = ConfigDict(extra="forbid")
    base: str | None = None
    head: str | None = None


class GateConfig(BaseModel):
    fail_on: Literal["low", "medium", "high", "critical"] = "high"
    model: bool = True


class StoreConfig(BaseModel):
    """Optional incident persistence. Present ⇒ each run records its incident."""

    model_config = ConfigDict(extra="forbid")
    path: str


class AssetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    source: FileSource | WarehouseSource = Field(discriminator="kind")
    changes: list[ChangeConfig] = Field(default_factory=list)
    columns: list[str] | None = None


class DviConfig(BaseModel):
    # legacy single-asset (optional now)
    asset: str | None = None
    source: FileSource | WarehouseSource | None = None
    changes: list[ChangeConfig] = Field(default_factory=list)
    columns: list[str] | None = None
    # multi-asset
    assets: list[AssetConfig] = Field(default_factory=list)
    # shared across all assets
    lineage: LineageConfig
    git: GitConfig = Field(default_factory=GitConfig)
    gate: GateConfig = Field(default_factory=GateConfig)
    store: StoreConfig | None = None

    @property
    def is_multi_asset(self) -> bool:
        return len(self.assets) > 0

    @model_validator(mode="after")
    def _validate_mode(self) -> DviConfig:
        legacy_active = self.asset is not None and self.source is not None
        multi_active = len(self.assets) > 0
        if legacy_active and multi_active:
            raise ValueError(
                "declare either a single top-level asset+source or an [[assets]] "
                "list, not both"
            )
        if not legacy_active and not multi_active:
            raise ValueError(
                "no asset declared: set top-level asset+source, or an [[assets]] list"
            )
        if multi_active and (
            self.asset is not None
            or self.source is not None
            or self.changes
            or self.columns is not None
        ):
            raise ValueError(
                "with [[assets]], declare asset/source/changes/columns per asset "
                "inside each [[assets]] entry, not at the top level"
            )
        names = [a.name for a in self.assets]
        if len(names) != len(set(names)):
            dupes = sorted({n for n in names if names.count(n) > 1})
            raise ValueError(f"duplicate asset name(s): {', '.join(dupes)}")
        return self


@dataclass(frozen=True)
class AssetSpec:
    name: str
    source: FileSource | WarehouseSource
    changes: list[ChangeConfig]
    columns: list[str] | None


def normalized_assets(config: DviConfig) -> list[AssetSpec]:
    """Collapse legacy and multi-asset config into one canonical spec list."""
    if config.is_multi_asset:
        return [
            AssetSpec(name=a.name, source=a.source, changes=list(a.changes),
                      columns=a.columns)
            for a in config.assets
        ]
    # legacy: validator guarantees asset and source are set here
    assert config.asset is not None and config.source is not None
    return [AssetSpec(name=config.asset, source=config.source,
                      changes=list(config.changes), columns=config.columns)]


def load_config(path: str | Path) -> DviConfig:
    """Load and validate a dvi.toml, wrapping any failure in DviError."""
    p = Path(path)
    try:
        with p.open("rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError as e:
        raise DviError(f"config file not found: {p}") from e
    except tomllib.TOMLDecodeError as e:
        raise DviError(f"invalid TOML in {p}: {e}") from e
    try:
        return DviConfig.model_validate(raw)
    except ValidationError as e:
        raise DviError(f"invalid config {p}:\n{e}") from e
