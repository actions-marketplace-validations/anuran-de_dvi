"""Shared sampling helper for the real-data experiments.

Both the legacy diamonds validator (``real_data``) and the generalised harness
(``real_eval.experiments``) draw disjoint same-distribution splits the same way.
Keeping one definition here prevents the two copies from drifting apart.
"""
from __future__ import annotations

import polars as pl


def two_sample_splits(
    df: pl.DataFrame, n: int, trials: int, *, seed: int = 0
) -> list[tuple[pl.DataFrame, pl.DataFrame]]:
    """Return ``trials`` pairs of disjoint size-``n`` samples of the same frame.

    Each trial reshuffles the whole frame (seeded) and takes the first ``n`` rows
    as the baseline and the next ``n`` as the current — so within a trial the two
    halves never share a row.
    """
    splits: list[tuple[pl.DataFrame, pl.DataFrame]] = []
    for t in range(trials):
        shuffled = df.sample(fraction=1.0, shuffle=True, seed=seed + t)
        splits.append((shuffled.slice(0, n), shuffled.slice(n, n)))
    return splits
