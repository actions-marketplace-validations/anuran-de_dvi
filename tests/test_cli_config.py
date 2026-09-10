from datetime import datetime

import pytest
from pydantic import ValidationError

from dvi.cli.config import AssetSpec, DviConfig, DviError, load_config, normalized_assets


def _file_source(before="b.parquet", after="a.parquet") -> dict:
    return {"kind": "file", "before": before, "after": after}


def _lineage(tmp_path) -> dict:
    return {"manifest": str(tmp_path / "manifest.json")}


def load_config_from_dict(data: dict):
    from pydantic import ValidationError

    from dvi.cli.config import DviConfig, DviError
    try:
        return DviConfig.model_validate(data)
    except ValidationError as e:
        raise DviError(str(e)) from e

_BASE = {
    "asset": "model.shop.fct_orders",
    "lineage": {"manifest": "target/manifest.json"},
    "changes": [
        {"id": "pr-1", "targets": ["model.shop.stg_orders"],
         "timestamp": "2026-08-30T12:00:00Z"}
    ],
}


def _with_source(source):
    return {**_BASE, "source": source}


def _base_config(**overrides):
    cfg = {
        "asset": "model.shop.fct_orders",
        "source": {"kind": "file", "before": "b.parquet", "after": "a.parquet"},
        "lineage": {"manifest": "manifest.json"},
    }
    cfg.update(overrides)
    return cfg


def test_file_config_parses_with_defaults():
    cfg = DviConfig.model_validate(
        _with_source({"kind": "file", "before": "b.csv", "after": "a.csv"})
    )
    assert cfg.source.kind == "file"
    assert cfg.source.before == "b.csv"
    assert cfg.gate.fail_on == "high"   # default
    assert cfg.gate.model is True       # default
    assert cfg.columns is None


def test_warehouse_config_parses():
    cfg = DviConfig.model_validate(
        _with_source({"kind": "warehouse", "database": "w.duckdb",
                      "before_table": "prod.x", "after_table": "pr.x"})
    )
    assert cfg.source.kind == "warehouse"
    assert cfg.source.before_table == "prod.x"


def test_warehouse_qualified_table_accepted():
    cfg = DviConfig.model_validate(
        _with_source({"kind": "warehouse", "database": "w.duckdb",
                      "before_table": "analytics.public.orders",
                      "after_table": "orders"})
    )
    assert cfg.source.before_table == "analytics.public.orders"


@pytest.mark.parametrize("bad_table", [
    "orders; DROP TABLE users --",
    'orders" ; DROP TABLE users --',
    "orders WHERE 1=1",
    "",
    "analytics..orders",
    "1orders",
])
def test_warehouse_non_identifier_table_rejected(bad_table):
    with pytest.raises(ValidationError):
        DviConfig.model_validate(
            _with_source({"kind": "warehouse", "database": "w.duckdb",
                          "before_table": bad_table, "after_table": "orders"})
        )


def test_store_defaults_to_none():
    cfg = DviConfig.model_validate(
        _with_source({"kind": "file", "before": "b.csv", "after": "a.csv"})
    )
    assert cfg.store is None


def test_store_config_parses():
    cfg = DviConfig.model_validate({
        **_with_source({"kind": "file", "before": "b.csv", "after": "a.csv"}),
        "store": {"path": ".dvi/incidents.db"},
    })
    assert cfg.store is not None
    assert cfg.store.path == ".dvi/incidents.db"


def test_store_config_rejects_unknown_key():
    with pytest.raises(ValidationError):
        DviConfig.model_validate({
            **_with_source({"kind": "file", "before": "b.csv", "after": "a.csv"}),
            "store": {"path": ".dvi/incidents.db", "backend": "postgres"},
        })


def test_mixed_source_keys_rejected():
    # a file source carrying a warehouse-only key must fail (extra=forbid)
    with pytest.raises(ValidationError):
        DviConfig.model_validate(
            _with_source({"kind": "file", "before": "b", "after": "a",
                          "database": "w.duckdb"})
        )


def test_empty_changes_accepted():
    cfg = DviConfig.model_validate({**_BASE, "changes": [],
                                    "source": {"kind": "file", "before": "b", "after": "a"}})
    assert cfg.changes == []


def test_bad_fail_on_rejected():
    with pytest.raises(ValidationError):
        DviConfig.model_validate({
            **_with_source({"kind": "file", "before": "b", "after": "a"}),
            "gate": {"fail_on": "severe"},
        })


def test_load_config_reads_toml_file(tmp_path):
    p = tmp_path / "dvi.toml"
    p.write_text(
        'asset = "model.shop.fct_orders"\n'
        "[source]\n"
        'kind = "file"\n'
        'before = "b.csv"\n'
        'after = "a.csv"\n'
        "[lineage]\n"
        'manifest = "target/manifest.json"\n'
        "[[changes]]\n"
        'id = "pr-1"\n'
        'targets = ["model.shop.stg_orders"]\n'
        "timestamp = 2026-08-30T12:00:00Z\n",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.asset == "model.shop.fct_orders"
    assert cfg.source.kind == "file"


def test_load_config_missing_file_raises_dvi_error():
    with pytest.raises(DviError):
        load_config("does-not-exist.toml")


def test_load_config_bad_toml_raises_dvi_error(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text("this = = broken", encoding="utf-8")
    with pytest.raises(DviError):
        load_config(p)


def test_changes_may_be_omitted():
    cfg = DviConfig.model_validate(_base_config())
    assert cfg.changes == []


def test_git_block_defaults_to_none_base_and_head():
    cfg = DviConfig.model_validate(_base_config())
    assert cfg.git.base is None
    assert cfg.git.head is None


def test_git_block_accepts_base_and_head():
    cfg = DviConfig.model_validate(_base_config(git={"base": "main", "head": "HEAD"}))
    assert cfg.git.base == "main"
    assert cfg.git.head == "HEAD"


def test_change_timestamp_offset_string_normalized_to_naive_utc():
    cfg = DviConfig.model_validate({
        **_base_config(),
        "changes": [{
            "id": "pr-1",
            "targets": ["model.shop.stg_orders"],
            "timestamp": "2026-08-25T11:50:00+02:00",
        }],
    })
    ts = cfg.changes[0].timestamp
    assert ts == datetime(2026, 8, 25, 9, 50, 0)
    assert ts.tzinfo is None


def test_change_timestamp_z_suffix_normalized_to_naive_utc():
    cfg = DviConfig.model_validate({
        **_base_config(),
        "changes": [{
            "id": "pr-1",
            "targets": ["model.shop.stg_orders"],
            "timestamp": "2026-08-30T12:00:00Z",
        }],
    })
    ts = cfg.changes[0].timestamp
    assert ts == datetime(2026, 8, 30, 12, 0, 0)
    assert ts.tzinfo is None


def test_change_timestamp_toml_offset_datetime_normalized_to_naive_utc(tmp_path):
    p = tmp_path / "dvi.toml"
    p.write_text(
        'asset = "model.shop.fct_orders"\n'
        "[source]\n"
        'kind = "file"\n'
        'before = "b.csv"\n'
        'after = "a.csv"\n'
        "[lineage]\n"
        'manifest = "target/manifest.json"\n'
        "[[changes]]\n"
        'id = "pr-1"\n'
        'targets = ["model.shop.stg_orders"]\n'
        "timestamp = 2026-08-25T11:50:00+02:00\n",
        encoding="utf-8",
    )
    cfg = load_config(p)
    ts = cfg.changes[0].timestamp
    assert ts == datetime(2026, 8, 25, 9, 50, 0)
    assert ts.tzinfo is None


def test_legacy_single_asset_normalizes_to_one_spec(tmp_path):
    cfg = DviConfig.model_validate({
        "asset": "model.shop.fct_orders",
        "columns": ["country"],
        "source": _file_source(),
        "lineage": _lineage(tmp_path),
    })
    specs = normalized_assets(cfg)
    assert cfg.is_multi_asset is False
    assert len(specs) == 1
    assert isinstance(specs[0], AssetSpec)
    assert specs[0].name == "model.shop.fct_orders"
    assert specs[0].columns == ["country"]
    assert specs[0].source.kind == "file"


def test_assets_list_normalizes_in_declaration_order(tmp_path):
    cfg = DviConfig.model_validate({
        "lineage": _lineage(tmp_path),
        "assets": [
            {"name": "model.b", "source": _file_source("b_b", "b_a")},
            {"name": "model.a", "source": _file_source("a_b", "a_a"),
             "columns": ["x"]},
        ],
    })
    specs = normalized_assets(cfg)
    assert cfg.is_multi_asset is True
    assert [s.name for s in specs] == ["model.b", "model.a"]   # declaration order here
    assert specs[1].columns == ["x"]


def test_both_modes_declared_is_error(tmp_path):
    with pytest.raises(DviError):
        load_config_from_dict({
            "asset": "model.x",
            "source": _file_source(),
            "lineage": _lineage(tmp_path),
            "assets": [{"name": "model.y", "source": _file_source()}],
        })


def test_neither_mode_declared_is_error(tmp_path):
    with pytest.raises(DviError):
        load_config_from_dict({"lineage": _lineage(tmp_path)})


def test_top_level_change_alongside_assets_is_error(tmp_path):
    with pytest.raises(DviError):
        load_config_from_dict({
            "lineage": _lineage(tmp_path),
            "changes": [{"id": "pr-1", "targets": ["model.x"],
                         "timestamp": "2026-08-25T09:50:00"}],
            "assets": [{"name": "model.y", "source": _file_source()}],
        })


def test_duplicate_asset_name_is_error(tmp_path):
    with pytest.raises(DviError):
        load_config_from_dict({
            "lineage": _lineage(tmp_path),
            "assets": [
                {"name": "dup", "source": _file_source()},
                {"name": "dup", "source": _file_source()},
            ],
        })


def test_change_timestamp_naive_input_unchanged():
    cfg = DviConfig.model_validate({
        **_base_config(),
        "changes": [{
            "id": "pr-1",
            "targets": ["model.shop.stg_orders"],
            "timestamp": "2026-08-25T09:50:00",
        }],
    })
    ts = cfg.changes[0].timestamp
    assert ts == datetime(2026, 8, 25, 9, 50, 0)
    assert ts.tzinfo is None
