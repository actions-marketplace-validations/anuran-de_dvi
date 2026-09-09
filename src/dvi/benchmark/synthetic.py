"""Synthetic data + failure injection for the DVI benchmark.

M1 ships the minimum needed to prove the hypothesis: an orders table with a
controllable ``country`` distribution, and a value-substitution injector. The
full multi-table benchmark with negatives, decoys and concurrent distractors
arrives in M2.
"""

from __future__ import annotations

import random

import polars as pl

_OTHER_COUNTRIES = ["US", "DE", "FR", "IN", "BR"]


def make_orders(n: int, uk_share: float = 0.2, seed: int = 0) -> pl.DataFrame:
    """Build a synthetic ``orders`` table with an exact ``UK`` share.

    ``UK`` gets exactly ``round(n * uk_share)`` rows; the remainder is spread
    deterministically across other countries. Row order is shuffled (seeded) for
    realism, but the category counts are exact so tests are deterministic.
    """
    uk_count = round(n * uk_share)
    countries = ["UK"] * uk_count
    for i in range(n - uk_count):
        countries.append(_OTHER_COUNTRIES[i % len(_OTHER_COUNTRIES)])

    rng = random.Random(seed)
    rng.shuffle(countries)

    return pl.DataFrame(
        {
            "order_id": list(range(1, n + 1)),
            "country": countries,
            "amount": [round(10 + (i % 90) + 0.99, 2) for i in range(n)],
        }
    )


def inject_value_substitution(
    df: pl.DataFrame, column: str, old: str, new: str
) -> pl.DataFrame:
    """Return a copy of ``df`` with every ``old`` in ``column`` relabelled ``new``.

    This is the silent rename: schema, row count and null rate are untouched.
    """
    return df.with_columns(
        pl.when(pl.col(column) == old)
        .then(pl.lit(new))
        .otherwise(pl.col(column))
        .alias(column)
    )


def inject_case_format(df: pl.DataFrame, column: str) -> pl.DataFrame:
    """Return a copy with every non-null string in ``column`` upper-cased.

    The case/format normalisation incident: the vocabulary is preserved, only
    the surface form changes. Nulls and row count are untouched.
    """
    return df.with_columns(pl.col(column).str.to_uppercase().alias(column))


def inject_category_split(
    df: pl.DataFrame, column: str, value: str, into: list[str]
) -> pl.DataFrame:
    """Return a copy where rows carrying ``value`` are split across ``into``.

    The rows whose ``column == value`` are relabelled round-robin (by their
    position among the matching rows) across ``into``; every other row is
    untouched. Deterministic — no randomness. Schema and row count preserved.
    """
    # Position among the matching rows: a running count of prior matches.
    match = pl.col(column) == value
    rank = match.cum_sum() - 1  # 0-based index among matches; ignored for non-matches
    expr = pl.col(column)
    for i, label in enumerate(into):
        expr = (
            pl.when(match & (rank % len(into) == i))
            .then(pl.lit(label))
            .otherwise(expr)
        )
    return df.with_columns(expr.alias(column))


def inject_unit_scale(df: pl.DataFrame, column: str, factor: float) -> pl.DataFrame:
    """Return a copy with the numeric ``column`` multiplied by ``factor``.

    The unit/scale incident: dollars silently re-encoded as cents (``factor=100``).
    """
    return df.with_columns((pl.col(column) * factor).alias(column))


def inject_distribution_shift(
    df: pl.DataFrame, column: str, pivot: float, factor: float
) -> pl.DataFrame:
    """Return a copy with values above ``pivot`` in ``column`` scaled by ``factor``.

    A non-affine tail thickening (the ``_stretch_above`` shape from the synthetic
    scenarios): the body of the distribution is unchanged, only the upper tail
    moves, so it cannot be undone by a simple re-scale.
    """
    return df.with_columns(
        pl.when(pl.col(column) > pivot)
        .then(pl.col(column) * factor)
        .otherwise(pl.col(column))
        .alias(column)
    )


def categorical(column: str, counts: dict[str, int], seed: int = 0) -> pl.DataFrame:
    """Build a single-column categorical frame with exact per-value counts.

    Counts are exact (deterministic tests); row order is seeded-shuffled for realism.
    """
    values: list[str] = []
    for value, count in counts.items():
        values.extend([value] * count)
    random.Random(seed).shuffle(values)
    return pl.DataFrame({column: values})


def numeric(column: str, values: list[float]) -> pl.DataFrame:
    """Build a single-column numeric frame from an explicit list of values."""
    return pl.DataFrame({column: [float(v) for v in values]})


def ramp(n: int, low: float = 10.0, high: float = 100.0) -> list[float]:
    """A deterministic spread of ``n`` values cycling across ``[low, high]``."""
    span = high - low
    return [low + (i % (int(span) + 1)) for i in range(n)]
