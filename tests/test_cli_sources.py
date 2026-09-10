import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import polars as pl
import pytest

import dvi.cli.sources as sources_mod
from dvi.changes import CommitRecord
from dvi.cli.config import AssetSpec, DviConfig, DviError
from dvi.cli.sources import (
    AssetResult,
    analyze_one,
    build_shared_context,
    incident_from_config,
)


def _write_manifest(path: Path) -> None:
    manifest = {
        "nodes": {
            "model.shop.stg_orders": {
                "resource_type": "model",
                "depends_on": {"nodes": []},
            },
            "model.shop.fct_orders": {
                "resource_type": "model",
                "depends_on": {"nodes": ["model.shop.stg_orders"]},
            },
        },
        "exposures": {},
    }
    path.write_text(json.dumps(manifest), encoding="utf-8")


def _frames():
    # A clean value-substitution: the dominant 'UK' category is renamed to 'GB'.
    before = pl.DataFrame({"country": ["UK"] * 40 + ["US"] * 40 + ["DE"] * 20})
    after = pl.DataFrame({"country": ["GB"] * 40 + ["US"] * 40 + ["DE"] * 20})
    return before, after


def _config(tmp_path: Path, source: dict) -> DviConfig:
    return DviConfig.model_validate({
        "asset": "model.shop.fct_orders",
        "columns": ["country"],
        "source": source,
        "lineage": {"manifest": str(tmp_path / "manifest.json")},
        "changes": [{
            "id": "pr-1",
            "label": "rename country codes",
            "targets": ["model.shop.stg_orders"],
            "timestamp": datetime.now(UTC) - timedelta(hours=1),
        }],
    })


def test_file_source_produces_incident(tmp_path):
    _write_manifest(tmp_path / "manifest.json")
    before, after = _frames()
    before.write_csv(tmp_path / "before.csv")
    after.write_csv(tmp_path / "after.csv")
    cfg = _config(tmp_path, {
        "kind": "file",
        "before": str(tmp_path / "before.csv"),
        "after": str(tmp_path / "after.csv"),
    })

    inc = incident_from_config(cfg)

    assert inc is not None
    assert inc.severity == "high"
    assert inc.primary_cause.explained[0].symptom.signature == "value_substitution"


def test_warehouse_source_matches_file_decision(tmp_path):
    _write_manifest(tmp_path / "manifest.json")
    before, after = _frames()
    dbfile = tmp_path / "w.duckdb"
    con = duckdb.connect(str(dbfile))
    for name, df in [("before_orders", before), ("after_orders", after)]:
        con.execute(f"CREATE TABLE {name}(country VARCHAR)")
        con.executemany(f"INSERT INTO {name} VALUES (?)", list(df.iter_rows()))
    con.close()
    cfg = _config(tmp_path, {
        "kind": "warehouse",
        "database": str(dbfile),
        "before_table": "before_orders",
        "after_table": "after_orders",
    })

    inc = incident_from_config(cfg)

    assert inc is not None
    assert inc.severity == "high"
    assert inc.primary_cause.explained[0].symptom.signature == "value_substitution"


def test_clean_data_yields_no_incident(tmp_path):
    _write_manifest(tmp_path / "manifest.json")
    before, _ = _frames()
    before.write_csv(tmp_path / "same.csv")
    cfg = _config(tmp_path, {
        "kind": "file",
        "before": str(tmp_path / "same.csv"),
        "after": str(tmp_path / "same.csv"),
    })

    assert incident_from_config(cfg) is None


def test_unresolved_target_raises_dvi_error(tmp_path):
    _write_manifest(tmp_path / "manifest.json")
    before, after = _frames()
    before.write_csv(tmp_path / "before.csv")
    after.write_csv(tmp_path / "after.csv")
    cfg = _config(tmp_path, {
        "kind": "file",
        "before": str(tmp_path / "before.csv"),
        "after": str(tmp_path / "after.csv"),
    })
    cfg.changes[0].targets = ["model.shop.does_not_exist"]

    with pytest.raises(DviError):
        incident_from_config(cfg)


def test_missing_source_file_raises_dvi_error(tmp_path):
    _write_manifest(tmp_path / "manifest.json")
    cfg = _config(tmp_path, {
        "kind": "file",
        "before": str(tmp_path / "nope.csv"),
        "after": str(tmp_path / "nope.csv"),
    })

    with pytest.raises(DviError):
        incident_from_config(cfg)


def test_unsupported_file_extension_raises_dvi_error(tmp_path):
    _write_manifest(tmp_path / "manifest.json")
    unsupported = tmp_path / "before.txt"
    unsupported.write_text("country\nUK\n", encoding="utf-8")
    cfg = _config(tmp_path, {
        "kind": "file",
        "before": str(unsupported),
        "after": str(unsupported),
    })

    with pytest.raises(DviError):
        incident_from_config(cfg)


def test_missing_warehouse_database_raises_dvi_error(tmp_path):
    _write_manifest(tmp_path / "manifest.json")
    cfg = _config(tmp_path, {
        "kind": "warehouse",
        "database": str(tmp_path / "does_not_exist.duckdb"),
        "before_table": "before_orders",
        "after_table": "after_orders",
    })

    with pytest.raises(DviError):
        incident_from_config(cfg)


def test_stale_change_timestamp_still_fires_incident(tmp_path):
    # observed_at is anchored to the change timestamp, not wall-clock, so a
    # PR open for 30 days must still fire — the old datetime.now(UTC)
    # anchor would push this change's lead time past RCA's 24h window and
    # silently return None.
    _write_manifest(tmp_path / "manifest.json")
    before, after = _frames()
    before.write_csv(tmp_path / "before.csv")
    after.write_csv(tmp_path / "after.csv")
    cfg = _config(tmp_path, {
        "kind": "file",
        "before": str(tmp_path / "before.csv"),
        "after": str(tmp_path / "after.csv"),
    })
    cfg.changes[0].timestamp = datetime.now(UTC) - timedelta(days=30)

    inc = incident_from_config(cfg)

    assert inc is not None
    assert isinstance(inc.severity, str) and inc.severity != ""


def test_warehouse_profiling_failure_raises_dvi_error(tmp_path):
    _write_manifest(tmp_path / "manifest.json")
    dbfile = tmp_path / "empty.duckdb"
    con = duckdb.connect(str(dbfile))
    con.close()
    cfg = _config(tmp_path, {
        "kind": "warehouse",
        "database": str(dbfile),
        "before_table": "before_orders",
        "after_table": "after_orders",
    })

    with pytest.raises(DviError):
        incident_from_config(cfg)


def _manifest_with_paths(path):
    manifest = {
        "nodes": {
            "model.shop.stg_orders": {
                "resource_type": "model",
                "depends_on": {"nodes": []},
                "original_file_path": "models/stg_orders.sql",
            },
            "model.shop.fct_orders": {
                "resource_type": "model",
                "depends_on": {"nodes": ["model.shop.stg_orders"]},
                "original_file_path": "models/fct_orders.sql",
            },
        },
        "exposures": {},
    }
    path.write_text(json.dumps(manifest), encoding="utf-8")


def test_derived_changes_are_unioned_with_none_declared(tmp_path, monkeypatch):
    _manifest_with_paths(tmp_path / "manifest.json")
    before = tmp_path / "b.parquet"
    after = tmp_path / "a.parquet"
    b, a = _frames()
    b.write_parquet(before)
    a.write_parquet(after)

    config = DviConfig.model_validate({
        "asset": "model.shop.fct_orders",
        "columns": ["country"],
        "source": {"kind": "file", "before": str(before), "after": str(after)},
        "lineage": {"manifest": str(tmp_path / "manifest.json")},
        # no [[changes]] declared
    })

    monkeypatch.setattr(sources_mod, "collect_commits", lambda *a, **k: [
        CommitRecord("abcdef1234", datetime(2026, 8, 25, 9, 50), "deploy stg",
                     ("models/stg_orders.sql",)),
    ])

    incident = sources_mod.incident_from_config(config)
    assert incident is not None  # derived change drove the analysis


def test_no_declared_and_no_derived_changes_is_an_error(tmp_path, monkeypatch):
    _manifest_with_paths(tmp_path / "manifest.json")
    before = tmp_path / "b.parquet"
    after = tmp_path / "a.parquet"
    b, a = _frames()
    b.write_parquet(before)
    a.write_parquet(after)

    config = DviConfig.model_validate({
        "asset": "model.shop.fct_orders",
        "columns": ["country"],
        "source": {"kind": "file", "before": str(before), "after": str(after)},
        "lineage": {"manifest": str(tmp_path / "manifest.json")},
    })
    monkeypatch.setattr(sources_mod, "collect_commits", lambda *a, **k: [])

    with pytest.raises(DviError, match="no change events"):
        sources_mod.incident_from_config(config)


def test_explicit_and_derived_duplicate_is_collapsed(tmp_path, monkeypatch):
    _manifest_with_paths(tmp_path / "manifest.json")
    before = tmp_path / "b.parquet"
    after = tmp_path / "a.parquet"
    b, a = _frames()
    b.write_parquet(before)
    a.write_parquet(after)

    config = DviConfig.model_validate({
        "asset": "model.shop.fct_orders",
        "columns": ["country"],
        "source": {"kind": "file", "before": str(before), "after": str(after)},
        "lineage": {"manifest": str(tmp_path / "manifest.json")},
        "changes": [{
            "id": "abcdef1",
            "targets": ["model.shop.stg_orders"],
            "timestamp": "2026-08-25T09:50:00",
        }],
    })
    # Derived event identical to the explicit one (same id/targets/timestamp).
    monkeypatch.setattr(sources_mod, "collect_commits", lambda *a, **k: [
        CommitRecord("abcdef1000", datetime(2026, 8, 25, 9, 50), "deploy stg",
                     ("models/stg_orders.sql",)),
    ])

    lineage, changes = sources_mod._lineage_and_changes(config)
    ids_targets = [(c.id, tuple(c.targets), c.timestamp) for c in changes]
    assert ids_targets.count(("abcdef1", ("model.shop.stg_orders",),
                              datetime(2026, 8, 25, 9, 50))) == 1


def test_offset_aware_explicit_timestamp_unions_with_derived_without_typeerror(
    tmp_path, monkeypatch
):
    # config.changes[].timestamp is normalized to naive UTC by ChangeConfig, so
    # max(c.timestamp for c in changes) must not raise "can't compare
    # offset-naive and offset-aware datetimes" against naive derived timestamps.
    _manifest_with_paths(tmp_path / "manifest.json")
    before = tmp_path / "b.parquet"
    after = tmp_path / "a.parquet"
    b, a = _frames()
    b.write_parquet(before)
    a.write_parquet(after)

    config = DviConfig.model_validate({
        "asset": "model.shop.fct_orders",
        "columns": ["country"],
        "source": {"kind": "file", "before": str(before), "after": str(after)},
        "lineage": {"manifest": str(tmp_path / "manifest.json")},
        "changes": [{
            "id": "pr-1",
            "targets": ["model.shop.stg_orders"],
            "timestamp": "2026-08-25T11:50:00+02:00",
        }],
    })
    monkeypatch.setattr(sources_mod, "collect_commits", lambda *a, **k: [
        CommitRecord("abcdef1234", datetime(2026, 8, 25, 9, 50), "deploy stg",
                     ("models/stg_orders.sql",)),
    ])

    incident = sources_mod.incident_from_config(config)
    assert incident is not None


def _spec_from(config: DviConfig) -> AssetSpec:
    from dvi.cli.config import normalized_assets
    return normalized_assets(config)[0]


def test_analyze_one_returns_incident_result(tmp_path, monkeypatch):
    _write_manifest(tmp_path / "manifest.json")
    before, after = _frames()
    before.write_csv(tmp_path / "before.csv")
    after.write_csv(tmp_path / "after.csv")
    cfg = _config(tmp_path, {
        "kind": "file",
        "before": str(tmp_path / "before.csv"),
        "after": str(tmp_path / "after.csv"),
    })
    monkeypatch.setattr(sources_mod, "collect_commits", lambda *a, **k: [])
    shared = build_shared_context(cfg)
    result = analyze_one(_spec_from(cfg), shared)
    assert isinstance(result, AssetResult)
    assert result.error is None
    assert result.incident is not None
    assert result.incident.severity == "high"


def test_analyze_one_clean_pair_has_no_incident_no_error(tmp_path, monkeypatch):
    _write_manifest(tmp_path / "manifest.json")
    before, _ = _frames()
    before.write_csv(tmp_path / "same.csv")
    cfg = _config(tmp_path, {
        "kind": "file",
        "before": str(tmp_path / "same.csv"),
        "after": str(tmp_path / "same.csv"),
    })
    monkeypatch.setattr(sources_mod, "collect_commits", lambda *a, **k: [])
    shared = build_shared_context(cfg)
    result = analyze_one(_spec_from(cfg), shared)
    assert result.incident is None
    assert result.error is None


def test_analyze_one_no_changes_is_errored_not_raised(tmp_path, monkeypatch):
    _manifest_with_paths(tmp_path / "manifest.json")
    before = tmp_path / "b.parquet"
    after = tmp_path / "a.parquet"
    b, a = _frames()
    b.write_parquet(before)
    a.write_parquet(after)
    cfg = DviConfig.model_validate({
        "asset": "model.shop.fct_orders",
        "columns": ["country"],
        "source": {"kind": "file", "before": str(before), "after": str(after)},
        "lineage": {"manifest": str(tmp_path / "manifest.json")},
    })
    monkeypatch.setattr(sources_mod, "collect_commits", lambda *a, **k: [])
    shared = build_shared_context(cfg)
    result = analyze_one(_spec_from(cfg), shared)
    assert result.incident is None
    assert result.error is not None
    assert "no change events" in result.error


def test_analyze_one_bad_source_is_errored_not_raised(tmp_path, monkeypatch):
    _write_manifest(tmp_path / "manifest.json")
    cfg = _config(tmp_path, {
        "kind": "file",
        "before": str(tmp_path / "nope.csv"),
        "after": str(tmp_path / "nope.csv"),
    })
    monkeypatch.setattr(sources_mod, "collect_commits", lambda *a, **k: [])
    shared = build_shared_context(cfg)
    result = analyze_one(_spec_from(cfg), shared)
    assert result.incident is None
    assert result.error is not None


def test_shared_context_loads_manifest_once_across_assets(tmp_path, monkeypatch):
    _write_manifest(tmp_path / "manifest.json")
    calls = {"n": 0}
    real = sources_mod.load_dbt_manifest

    def counting(path):
        calls["n"] += 1
        return real(path)

    monkeypatch.setattr(sources_mod, "load_dbt_manifest", counting)
    monkeypatch.setattr(sources_mod, "collect_commits", lambda *a, **k: [])
    before, after = _frames()
    before.write_csv(tmp_path / "b.csv")
    after.write_csv(tmp_path / "a.csv")
    cfg = DviConfig.model_validate({
        "lineage": {"manifest": str(tmp_path / "manifest.json")},
        "assets": [
            {"name": "model.shop.fct_orders",
             "source": {"kind": "file", "before": str(tmp_path / "b.csv"),
                        "after": str(tmp_path / "a.csv")},
             "columns": ["country"],
             "changes": [{"id": "pr-1", "targets": ["model.shop.stg_orders"],
                          "timestamp": "2026-08-25T09:50:00"}]},
            {"name": "model.shop.stg_orders",
             "source": {"kind": "file", "before": str(tmp_path / "b.csv"),
                        "after": str(tmp_path / "a.csv")},
             "columns": ["country"],
             "changes": [{"id": "pr-1", "targets": ["model.shop.stg_orders"],
                          "timestamp": "2026-08-25T09:50:00"}]},
        ],
    })
    build_shared_context(cfg)
    assert calls["n"] == 1
