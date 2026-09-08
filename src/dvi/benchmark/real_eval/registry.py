"""Declarative registry of real datasets for the evaluation harness.

Each dataset is one entry: how to load it, which columns feed the real-vs-real
specificity test, and one injection recipe per detector family for the
injected-recall test. The engine never names a dataset; adding a domain is an
entry plus data.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from dvi.benchmark.real_data import load_diamonds
from dvi.benchmark.synthetic import (
    inject_case_format,
    inject_category_split,
    inject_distribution_shift,
    inject_unit_scale,
    inject_value_substitution,
)

DATA_DIR = Path(__file__).resolve().parents[4] / "data"
ADULT_PATH = DATA_DIR / "adult.parquet"
ONLINE_RETAIL_PATH = DATA_DIR / "online_retail.parquet"


@dataclass(frozen=True)
class InjectionRecipe:
    """One labelled injected-change spec for a single family + column."""

    family: str
    column: str
    inject: Callable[[pl.DataFrame], pl.DataFrame]
    n: int


@dataclass(frozen=True)
class RealDataset:
    """A registry entry: loader + real-vs-real columns + per-family recipes."""

    id: str
    domain: str
    load: Callable[[], pl.DataFrame]
    fp_columns: list[str]
    recipes: list[InjectionRecipe]
    committed: bool


def _diamonds_spec() -> RealDataset:
    return RealDataset(
        id="diamonds",
        domain="retail pricing",
        load=load_diamonds,
        fp_columns=["cut", "color", "clarity", "carat", "depth", "table", "price"],
        recipes=[
            InjectionRecipe(
                "value_substitution", "clarity",
                lambda d: inject_value_substitution(d, "clarity", "SI1", "SI1_RECODED"),
                n=2000,
            ),
            InjectionRecipe(
                "case_format_normalization", "cut",
                lambda d: inject_case_format(d, "cut"), n=2000,
            ),
            InjectionRecipe(
                "category_split_merge", "clarity",
                lambda d: inject_category_split(d, "clarity", "SI1", ["SI1a", "SI1b"]),
                n=2000,
            ),
            InjectionRecipe(
                "numeric_distribution_shift", "price",
                lambda d: inject_distribution_shift(d, "price", pivot=5000.0, factor=2.0),
                n=2000,
            ),
            InjectionRecipe(
                "unit_scale_shift", "price",
                lambda d: inject_unit_scale(d, "price", 100.0), n=2000,
            ),
        ],
        committed=True,
    )


def _adult_spec() -> RealDataset:
    return RealDataset(
        id="adult",
        domain="census income",
        load=lambda: pl.read_parquet(ADULT_PATH),
        fp_columns=["workclass", "occupation", "income", "age", "hours.per.week", "fnlwgt"],
        recipes=[
            InjectionRecipe(
                "value_substitution", "workclass",
                lambda d: inject_value_substitution(d, "workclass", "?", "Unknown"),
                n=2000,
            ),
            InjectionRecipe(
                "case_format_normalization", "sex",
                lambda d: inject_case_format(d, "sex"), n=2000,
            ),
            InjectionRecipe(
                "category_split_merge", "workclass",
                lambda d: inject_category_split(
                    d, "workclass", "Private", ["Private-A", "Private-B"]
                ),
                n=2000,
            ),
            InjectionRecipe(
                "numeric_distribution_shift", "capital.gain",
                lambda d: inject_distribution_shift(
                    d, "capital.gain", pivot=5000.0, factor=2.0
                ),
                n=2000,
            ),
            InjectionRecipe(
                "unit_scale_shift", "hours.per.week",
                lambda d: inject_unit_scale(d, "hours.per.week", 100.0), n=2000,
            ),
        ],
        committed=True,
    )


def _online_retail_spec() -> RealDataset:
    return RealDataset(
        id="online_retail",
        domain="e-commerce transactions",
        load=lambda: pl.read_parquet(ONLINE_RETAIL_PATH),
        fp_columns=["Country", "Quantity", "UnitPrice"],
        recipes=[
            InjectionRecipe(
                "value_substitution", "Country",
                lambda d: inject_value_substitution(
                    d, "Country", "United Kingdom", "UK"
                ),
                n=2000,
            ),
            InjectionRecipe(
                "case_format_normalization", "Country",
                lambda d: inject_case_format(d, "Country"), n=2000,
            ),
            InjectionRecipe(
                "category_split_merge", "Country",
                lambda d: inject_category_split(
                    d, "Country", "United Kingdom", ["UK-North", "UK-South"]
                ),
                n=2000,
            ),
            InjectionRecipe(
                "numeric_distribution_shift", "UnitPrice",
                lambda d: inject_distribution_shift(
                    d, "UnitPrice", pivot=5.0, factor=3.0
                ),
                n=2000,
            ),
            InjectionRecipe(
                "unit_scale_shift", "UnitPrice",
                lambda d: inject_unit_scale(d, "UnitPrice", 100.0), n=2000,
            ),
        ],
        committed=True,
    )


def _committed_specs() -> list[RealDataset]:
    return [_diamonds_spec(), _adult_spec(), _online_retail_spec()]


def dataset_specs() -> list[RealDataset]:
    """Every declared dataset (committed subset). NYC taxi is appended in Task 5."""
    return _committed_specs()


def build_registry() -> list[RealDataset]:
    """Return only datasets whose data actually loads; skip absent ones."""
    live: list[RealDataset] = []
    for spec in dataset_specs():
        try:
            spec.load()
        except (FileNotFoundError, OSError):
            continue
        live.append(spec)
    return live
