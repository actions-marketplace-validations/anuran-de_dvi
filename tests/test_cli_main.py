import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

from dvi.cli.main import main


def _write_manifest(path: Path) -> None:
    manifest = {
        "nodes": {
            "model.shop.stg_orders": {"resource_type": "model",
                                      "depends_on": {"nodes": []}},
            "model.shop.fct_orders": {"resource_type": "model",
                                      "depends_on": {"nodes": ["model.shop.stg_orders"]}},
        },
        "exposures": {},
    }
    path.write_text(json.dumps(manifest), encoding="utf-8")


def _config_text(tmp_path: Path, before: str, after: str, target: str) -> str:
    change_ts = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    return (
        'asset = "model.shop.fct_orders"\n'
        'columns = ["country"]\n'
        "[source]\n"
        'kind = "file"\n'
        f'before = "{before}"\n'
        f'after = "{after}"\n'
        "[lineage]\n"
        f'manifest = "{(tmp_path / "manifest.json").as_posix()}"\n'
        "[[changes]]\n"
        'id = "pr-1"\n'
        'label = "rename country codes"\n'
        f'targets = ["{target}"]\n'
        f"timestamp = {change_ts}\n"
    )


def _setup(tmp_path, *, same=False, target="model.shop.stg_orders"):
    _write_manifest(tmp_path / "manifest.json")
    before = pl.DataFrame({"country": ["UK"] * 40 + ["US"] * 40 + ["DE"] * 20})
    after = before if same else pl.DataFrame(
        {"country": ["GB"] * 40 + ["US"] * 40 + ["DE"] * 20}
    )
    before.write_csv(tmp_path / "before.csv")
    after.write_csv(tmp_path / "after.csv")
    cfg = tmp_path / "dvi.toml"
    cfg.write_text(
        _config_text(tmp_path, (tmp_path / "before.csv").as_posix(),
                     (tmp_path / "after.csv").as_posix(), target),
        encoding="utf-8",
    )
    return cfg


def test_main_incident_fails_gate(tmp_path, capsys):
    cfg = _setup(tmp_path)
    out = tmp_path / "out"

    code = main(["analyze", "--config", str(cfg), "--output-dir", str(out)])

    assert code == 1
    assert (out / "dvi-report.md").exists()
    data = json.loads((out / "dvi-report.json").read_text(encoding="utf-8"))
    assert data["incident"] is not None
    assert data["gate"]["failed"] is True
    assert "semantic change detected" in capsys.readouterr().out


def test_main_clean_run_passes(tmp_path):
    cfg = _setup(tmp_path, same=True)
    out = tmp_path / "out"

    code = main(["analyze", "--config", str(cfg), "--output-dir", str(out)])

    assert code == 0
    data = json.loads((out / "dvi-report.json").read_text(encoding="utf-8"))
    assert data["incident"] is None


def test_main_config_error_returns_2(tmp_path, capsys):
    cfg = _setup(tmp_path, target="model.shop.does_not_exist")
    out = tmp_path / "out"

    code = main(["analyze", "--config", str(cfg), "--output-dir", str(out)])

    assert code == 2
    assert "error" in capsys.readouterr().err.lower()


def test_main_missing_config_returns_2(tmp_path):
    code = main(["analyze", "--config", str(tmp_path / "nope.toml"),
                 "--output-dir", str(tmp_path / "out")])
    assert code == 2


def test_main_bad_column_returns_2(tmp_path, capsys):
    # before/after frames only have 'country'; a bogus column raises a polars
    # error during analysis, which must map to exit 2 (not crash / not 1).
    cfg = _setup(tmp_path)
    text = (tmp_path / "dvi.toml").read_text(encoding="utf-8").replace(
        'columns = ["country"]', 'columns = ["does_not_exist"]'
    )
    (tmp_path / "dvi.toml").write_text(text, encoding="utf-8")
    out = tmp_path / "out"

    code = main(["analyze", "--config", str(cfg), "--output-dir", str(out)])

    assert code == 2
    assert "error" in capsys.readouterr().err.lower()


def test_main_unwritable_output_dir_returns_2(tmp_path):
    cfg = _setup(tmp_path)
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    out = blocked / "sub"

    code = main(["analyze", "--config", str(cfg), "--output-dir", str(out)])

    assert code == 2


def test_main_source_override(tmp_path):
    # Config points at a non-existent 'after'; override supplies the real one.
    cfg = _setup(tmp_path)
    # Rewrite config's after path to a bogus file, then override on the CLI.
    bogus = tmp_path / "bogus.csv"
    text = (tmp_path / "dvi.toml").read_text(encoding="utf-8").replace(
        (tmp_path / "after.csv").as_posix(), bogus.as_posix()
    )
    (tmp_path / "dvi.toml").write_text(text, encoding="utf-8")
    out = tmp_path / "out"

    code = main([
        "analyze", "--config", str(cfg), "--output-dir", str(out),
        "--source-after", (tmp_path / "after.csv").as_posix(),
    ])

    assert code == 1  # override restored the real 'after' → incident fires


def _multi_config_text(tmp_path, a_before, a_after, b_before, b_after) -> str:
    change_ts = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    manifest = (tmp_path / "manifest.json").as_posix()
    return (
        "[lineage]\n"
        f'manifest = "{manifest}"\n'
        "[[assets]]\n"
        'name = "model.shop.fct_orders"\n'
        'columns = ["country"]\n'
        "[assets.source]\n"
        'kind = "file"\n'
        f'before = "{a_before}"\n'
        f'after = "{a_after}"\n'
        "[[assets.changes]]\n"
        'id = "pr-1"\n'
        'targets = ["model.shop.stg_orders"]\n'
        f"timestamp = {change_ts}\n"
        "[[assets]]\n"
        'name = "model.shop.stg_orders"\n'
        'columns = ["country"]\n'
        "[assets.source]\n"
        'kind = "file"\n'
        f'before = "{b_before}"\n'
        f'after = "{b_after}"\n'
        "[[assets.changes]]\n"
        'id = "pr-1"\n'
        'targets = ["model.shop.stg_orders"]\n'
        f"timestamp = {change_ts}\n"
    )


def test_main_multi_asset_aggregated_report(tmp_path):
    _write_manifest(tmp_path / "manifest.json")
    inc_before = pl.DataFrame({"country": ["UK"] * 40 + ["US"] * 40 + ["DE"] * 20})
    inc_after = pl.DataFrame({"country": ["GB"] * 40 + ["US"] * 40 + ["DE"] * 20})
    inc_before.write_csv(tmp_path / "a_b.csv")
    inc_after.write_csv(tmp_path / "a_a.csv")
    inc_before.write_csv(tmp_path / "b_b.csv")   # second asset: clean (same file)
    inc_before.write_csv(tmp_path / "b_a.csv")
    cfg = tmp_path / "dvi.toml"
    cfg.write_text(_multi_config_text(
        tmp_path, (tmp_path / "a_b.csv").as_posix(), (tmp_path / "a_a.csv").as_posix(),
        (tmp_path / "b_b.csv").as_posix(), (tmp_path / "b_a.csv").as_posix()),
        encoding="utf-8")
    out = tmp_path / "out"
    code = main(["analyze", "--config", str(cfg), "--output-dir", str(out)])
    assert code == 1                       # fct_orders incident trips the gate
    data = json.loads((out / "dvi-report.json").read_text(encoding="utf-8"))
    assert [a["asset"] for a in data["assets"]] == \
        ["model.shop.fct_orders", "model.shop.stg_orders"]   # sorted by name
    assert data["gate"]["worst_severity"] == "high"
    md = (out / "dvi-report.md").read_text(encoding="utf-8")
    assert md.splitlines()[0] == "<!-- dvi-report -->"
    assert "| Asset | Result |" in md


def test_main_legacy_single_asset_output_is_byte_identical(tmp_path):
    # Golden: the legacy path must not change shape at all.
    cfg = _setup(tmp_path)                 # existing legacy helper
    out = tmp_path / "out"
    main(["analyze", "--config", str(cfg), "--output-dir", str(out)])
    md = (out / "dvi-report.md").read_text(encoding="utf-8")
    data = json.loads((out / "dvi-report.json").read_text(encoding="utf-8"))
    # Legacy JSON keeps its exact top-level shape (no 'assets' array).
    assert set(data.keys()) == {"asset", "severity", "incident", "gate", "generated_at"}
    assert set(data["gate"].keys()) == {"fail_on", "failed"}
    assert md.rstrip().endswith("<!-- dvi-report -->")
    assert "| Asset | Result |" not in md   # NOT the aggregated shape


def test_main_multi_exit_precedence_clean_plus_errored_is_2(tmp_path, monkeypatch):
    _write_manifest(tmp_path / "manifest.json")
    clean = pl.DataFrame({"country": ["UK"] * 40 + ["US"] * 40 + ["DE"] * 20})
    clean.write_csv(tmp_path / "s.csv")
    change_ts = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    manifest = (tmp_path / "manifest.json").as_posix()
    cfg = tmp_path / "dvi.toml"
    cfg.write_text(
        "[lineage]\n"
        f'manifest = "{manifest}"\n'
        "[[assets]]\n"
        'name = "model.shop.fct_orders"\n'
        'columns = ["country"]\n'
        "[assets.source]\n"
        'kind = "file"\n'
        f'before = "{(tmp_path / "s.csv").as_posix()}"\n'
        f'after = "{(tmp_path / "s.csv").as_posix()}"\n'   # clean
        "[[assets.changes]]\n"
        'id = "pr-1"\n'
        'targets = ["model.shop.stg_orders"]\n'
        f"timestamp = {change_ts}\n"
        "[[assets]]\n"
        'name = "model.shop.stg_orders"\n'
        'columns = ["country"]\n'
        "[assets.source]\n"
        'kind = "file"\n'
        f'before = "{(tmp_path / "missing.csv").as_posix()}"\n'   # errored
        f'after = "{(tmp_path / "missing.csv").as_posix()}"\n'
        "[[assets.changes]]\n"
        'id = "pr-1"\n'
        'targets = ["model.shop.stg_orders"]\n'
        f"timestamp = {change_ts}\n",
        encoding="utf-8")
    out = tmp_path / "out"
    code = main(["analyze", "--config", str(cfg), "--output-dir", str(out)])
    assert code == 2                       # clean + errored → 2
    data = json.loads((out / "dvi-report.json").read_text(encoding="utf-8"))
    assert data["gate"]["failed"] is False
    errored = [a for a in data["assets"] if a["error"] is not None]
    assert errored and errored[0]["asset"] == "model.shop.stg_orders"
