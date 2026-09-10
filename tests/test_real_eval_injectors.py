"""Injection functions plant one labelled change per detector family."""
from __future__ import annotations

import polars as pl

from dvi.benchmark.synthetic import (
    inject_case_format,
    inject_category_split,
    inject_distribution_shift,
    inject_unit_scale,
)


def test_case_format_upper_cases_values_and_preserves_shape():
    df = pl.DataFrame({"c": ["Male", "Female", None, "Male"]})
    out = inject_case_format(df, "c")
    assert out["c"].to_list() == ["MALE", "FEMALE", None, "MALE"]
    assert out.height == df.height


def test_case_format_single_category_only_recases_that_category():
    # The spec describes normalising "an existing category" (singular): with a
    # category argument, only that value's rows change surface form; every other
    # category and the null keep their spelling. Models a realistic re-casing
    # incident where one category was normalised, not the whole column.
    df = pl.DataFrame({"c": ["Ideal", "Premium", None, "Ideal", "Good"]})
    out = inject_case_format(df, "c", category="Ideal")
    assert out["c"].to_list() == ["IDEAL", "Premium", None, "IDEAL", "Good"]
    assert out.height == df.height


def test_category_split_partitions_only_the_target_value():
    df = pl.DataFrame({"c": ["A", "A", "A", "A", "B"]})
    out = inject_category_split(df, "c", "A", ["A1", "A2"])
    # Round-robin over the four "A" rows in position order; "B" untouched.
    assert out["c"].to_list() == ["A1", "A2", "A1", "A2", "B"]
    assert out.height == df.height


def test_unit_scale_multiplies_numeric_column():
    df = pl.DataFrame({"c": [1.0, 2.5, 3.0]})
    out = inject_unit_scale(df, "c", 100.0)
    assert out["c"].to_list() == [100.0, 250.0, 300.0]


def test_distribution_shift_only_stretches_above_pivot():
    df = pl.DataFrame({"c": [10.0, 60.0, 100.0]})
    out = inject_distribution_shift(df, "c", pivot=55.0, factor=2.0)
    assert out["c"].to_list() == [10.0, 120.0, 200.0]
