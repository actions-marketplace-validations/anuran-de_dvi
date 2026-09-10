"""Turn a validated DviConfig into an Incident (or None).

Two adapters converge on the same pipeline call:
- file:      polars reads the two columnar files -> analyze_change (frames)
- warehouse: DuckDB drives the M5a pushdown path -> analyze_change_from_profiles

Everything except *how profiles are produced* (lineage, change list, model) is
shared, so the two producers cannot decide differently — the M5a seam.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

from dvi.changes import collect_commits, derive_change_events, resolve_range
from dvi.incidents import Incident
from dvi.lineage import LineageGraph, load_dbt_manifest
from dvi.pipeline import analyze_change, analyze_change_from_profiles
from dvi.rca import ChangeEvent
from dvi.warehouse import DuckDBDialect, SqlProfileSource

from .config import AssetSpec, DviConfig, DviError, GateConfig, StoreConfig

if TYPE_CHECKING:
    from dvi.calibration.model import LogisticModel

_READERS = {
    ".parquet": pl.read_parquet,
    ".csv": pl.read_csv,
    ".ndjson": pl.read_ndjson,
}


def _read_frame(path: str) -> pl.DataFrame:
    p = Path(path)
    if not p.exists():
        raise DviError(f"source file not found: {p}")
    reader = _READERS.get(p.suffix.lower())
    if reader is None:
        raise DviError(
            f"unsupported source file extension {p.suffix!r} for {p} "
            f"(use one of {', '.join(sorted(_READERS))})"
        )
    try:
        return reader(p)
    except Exception as e:  # noqa: BLE001 - surface any read failure as a clear error
        raise DviError(f"could not read source file {p}: {e}") from e


def _dedup_key(change: ChangeEvent) -> tuple[str, tuple[str, ...], datetime]:
    return (change.id, tuple(sorted(change.targets)), change.timestamp)


def _load_lineage(config: DviConfig) -> LineageGraph:
    manifest_path = Path(config.lineage.manifest)
    if not manifest_path.exists():
        raise DviError(f"lineage manifest not found: {manifest_path}")
    try:
        return load_dbt_manifest(manifest_path)
    except Exception as e:  # noqa: BLE001
        raise DviError(f"could not read lineage manifest {manifest_path}: {e}") from e


def _derive_changes(config: DviConfig, lineage: LineageGraph) -> list[ChangeEvent]:
    base, head = resolve_range(os.environ, config.git.base, config.git.head)
    commits = collect_commits(base, head, cwd=Path.cwd())
    return derive_change_events(commits, lineage.nodes_for_file)


def _declared_changes(
    changes: list, lineage: LineageGraph, manifest: str
) -> list[ChangeEvent]:
    out: list[ChangeEvent] = []
    for change in changes:
        for target in change.targets:
            if target not in lineage.nodes:
                raise DviError(
                    f"change {change.id!r} target {target!r} is not a node in "
                    f"lineage manifest {manifest!r}"
                )
        out.append(ChangeEvent(id=change.id, timestamp=change.timestamp,
                               targets=list(change.targets), label=change.label))
    return out


def _combine(declared: list[ChangeEvent], derived: list[ChangeEvent]) -> list[ChangeEvent]:
    combined: list[ChangeEvent] = []
    seen: set[tuple[str, tuple[str, ...], datetime]] = set()
    for change in [*declared, *derived]:
        key = _dedup_key(change)
        if key in seen:
            continue
        seen.add(key)
        combined.append(change)
    return combined


def _lineage_and_changes(config: DviConfig) -> tuple[LineageGraph, list[ChangeEvent]]:
    # Back-compat shim for the legacy single-asset path and its direct-call test.
    lineage = _load_lineage(config)
    declared = _declared_changes(config.changes, lineage, config.lineage.manifest)
    derived = _derive_changes(config, lineage)
    return lineage, _combine(declared, derived)


def _load_model(config: DviConfig) -> LogisticModel | None:
    if not config.gate.model:
        return None
    # Imported here (not at module top) to avoid any import-order cycle between
    # the pipeline and calibration packages when dvi.cli is first imported.
    from dvi.calibration.loader import load_model

    return load_model()


@dataclass(frozen=True)
class SharedContext:
    lineage: LineageGraph
    derived_changes: list[ChangeEvent]
    model: LogisticModel | None
    gate: GateConfig
    store: StoreConfig | None


def build_shared_context(config: DviConfig) -> SharedContext:
    lineage = _load_lineage(config)
    derived = _derive_changes(config, lineage)
    return SharedContext(
        lineage=lineage,
        derived_changes=derived,
        model=_load_model(config),
        gate=config.gate,
        store=config.store,
    )


@dataclass(frozen=True)
class AssetResult:
    name: str
    incident: Incident | None
    error: str | None


def _run_analysis(
    spec: AssetSpec, shared: SharedContext, changes: list[ChangeEvent]
) -> Incident | None:
    # Anchor the observation to the newest change (declared or derived), not the
    # wall clock, so re-runs are deterministic and the RCA lead window is stable.
    observed_at = max(c.timestamp for c in changes)
    source = spec.source
    if source.kind == "file":
        before = _read_frame(source.before)
        after = _read_frame(source.after)
        try:
            return analyze_change(
                asset=spec.name, before=before, after=after, observed_at=observed_at,
                lineage=shared.lineage, changes=changes, columns=spec.columns,
                model=shared.model,
            )
        except DviError:
            raise
        except Exception as e:  # noqa: BLE001
            raise DviError(f"analysis failed: {e}") from e

    import duckdb

    db = Path(source.database)
    if not db.exists():
        raise DviError(f"warehouse database not found: {db}")
    try:
        con = duckdb.connect(str(db), read_only=True)
    except Exception as e:  # noqa: BLE001
        raise DviError(f"could not open warehouse database {db}: {e}") from e
    try:
        def execute(sql: str):
            return con.execute(sql).fetchall()

        dialect = DuckDBDialect()
        try:
            before = SqlProfileSource(execute, source.before_table,
                                      dialect=dialect).profile(spec.columns)
            after = SqlProfileSource(execute, source.after_table,
                                     dialect=dialect).profile(spec.columns)
        except Exception as e:  # noqa: BLE001
            raise DviError(f"warehouse profiling failed: {e}") from e
    finally:
        con.close()
    try:
        return analyze_change_from_profiles(
            asset=spec.name, before=before, after=after, observed_at=observed_at,
            lineage=shared.lineage, changes=changes, columns=spec.columns,
            model=shared.model,
        )
    except DviError:
        raise
    except Exception as e:  # noqa: BLE001
        raise DviError(f"analysis failed: {e}") from e


def analyze_one(spec: AssetSpec, shared: SharedContext) -> AssetResult:
    """Analyze one asset; deliberate could-not-run failures become .error."""
    try:
        declared = _declared_changes(spec.changes, shared.lineage,
                                     "<asset source>")
        changes = _combine(declared, shared.derived_changes)
        if not changes:
            return AssetResult(spec.name, None, "no change events for asset")
        incident = _run_analysis(spec, shared, changes)
        return AssetResult(spec.name, incident, None)
    except DviError as e:
        return AssetResult(spec.name, None, str(e))


def incident_from_config(config: DviConfig) -> Incident | None:
    """Legacy single-asset entrypoint: analyze the one configured asset."""
    from .config import normalized_assets

    spec = normalized_assets(config)[0]
    shared = build_shared_context(config)
    result = analyze_one(spec, shared)
    if result.error is not None:
        raise DviError(result.error)
    return result.incident
