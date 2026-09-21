"""The shared two_sample_splits helper is defined once and reused everywhere."""
from __future__ import annotations

import polars as pl


def test_two_sample_splits_is_single_shared_definition():
    # #34: the helper must live in one module; the real_data and real_eval
    # copies were byte-identical and could drift. Both must now be the SAME
    # object imported from dvi.benchmark._sampling.
    from dvi.benchmark import _sampling, real_data
    from dvi.benchmark.real_eval import experiments

    assert real_data.two_sample_splits is _sampling.two_sample_splits
    assert experiments.two_sample_splits is _sampling.two_sample_splits


def test_two_sample_splits_disjoint_sized_and_seeded():
    from dvi.benchmark._sampling import two_sample_splits

    df = pl.DataFrame({"x": list(range(1000))})
    splits = two_sample_splits(df, n=100, trials=3, seed=0)

    assert len(splits) == 3
    for baseline, current in splits:
        assert baseline.height == 100
        assert current.height == 100
        # The two halves of a trial never share a row.
        assert set(baseline["x"].to_list()).isdisjoint(current["x"].to_list())

    # Deterministic under a fixed seed.
    again = two_sample_splits(df, n=100, trials=3, seed=0)
    assert [s[0]["x"].to_list() for s in splits] == [s[0]["x"].to_list() for s in again]
