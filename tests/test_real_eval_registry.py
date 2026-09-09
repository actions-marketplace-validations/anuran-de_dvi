"""The dataset registry declares datasets and skips absent ones."""
from __future__ import annotations

import polars as pl

from dvi.benchmark.real_eval.registry import (
    InjectionRecipe,
    RealDataset,
    build_registry,
    dataset_specs,
)

_FAMILIES = {
    "value_substitution",
    "case_format_normalization",
    "category_split_merge",
    "numeric_distribution_shift",
    "unit_scale_shift",
}


def test_specs_cover_all_five_families_per_committed_dataset():
    for spec in dataset_specs():
        if not spec.committed:
            continue
        families = {r.family for r in spec.recipes}
        assert families == _FAMILIES, f"{spec.id} missing {_FAMILIES - families}"


def test_build_registry_only_returns_loadable_datasets():
    reg = build_registry()
    ids = {d.id for d in reg}
    # diamonds + adult + online_retail are committed, so always present.
    assert {"diamonds", "adult", "online_retail"} <= ids
    for d in reg:
        frame = d.load()
        assert isinstance(frame, pl.DataFrame)
        assert frame.height > 2 * max(r.n for r in d.recipes)


def test_nyc_taxi_spec_declared_but_skipped_when_file_absent():
    from dvi.benchmark.real_eval.registry import NYC_TAXI_PATH, build_registry, dataset_specs

    assert "nyc_taxi" in {s.id for s in dataset_specs()}
    if not NYC_TAXI_PATH.exists():
        assert "nyc_taxi" not in {d.id for d in build_registry()}


def test_absent_dataset_is_skipped(monkeypatch):
    import dvi.benchmark.real_eval.registry as reg_mod

    missing = RealDataset(
        id="ghost",
        domain="nowhere",
        load=lambda: (_ for _ in ()).throw(FileNotFoundError("gone")),
        fp_columns=["x"],
        recipes=[
            InjectionRecipe("value_substitution", "x", lambda d: d, n=10)
        ],
        committed=False,
    )
    monkeypatch.setattr(
        reg_mod, "dataset_specs", lambda: [*reg_mod._committed_specs(), missing]
    )
    ids = {d.id for d in reg_mod.build_registry()}
    assert "ghost" not in ids
