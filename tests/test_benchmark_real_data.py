"""Validation on a real public dataset (diamonds, 53,940 rows).

Two experiments the synthetic suite cannot run:

- **real-vs-real false positives** — two disjoint samples of the *same* real
  distribution must produce no symptoms. This is the honest robustness test; a
  detector that fabricates change here is a noise cannon in production.
- **injected recall on real data** — a known semantic change (a category renamed)
  planted into a real sample must be recovered despite real-world sampling noise.
"""

from dvi.benchmark._sampling import two_sample_splits
from dvi.benchmark.real_data import (
    diamonds_injected_recall_report,
    diamonds_real_vs_real_report,
    load_diamonds,
)


def test_load_diamonds_has_expected_shape():
    df = load_diamonds()
    assert df.height == 53_940
    assert {"cut", "color", "clarity", "carat", "price"} <= set(df.columns)


def test_two_sample_splits_are_disjoint_and_sized():
    df = load_diamonds().with_row_index("rid")
    splits = two_sample_splits(df, n=500, trials=4)

    assert len(splits) == 4
    for baseline, current in splits:
        assert baseline.height == 500
        assert current.height == 500
        # The two halves of a trial must not share a single row.
        assert set(baseline["rid"].to_list()).isdisjoint(current["rid"].to_list())


def test_real_vs_real_produces_no_false_positives_at_scale():
    df = load_diamonds()
    report = diamonds_real_vs_real_report(
        df,
        columns=["cut", "color", "clarity", "carat", "depth", "table", "price"],
        n=1000,
        trials=30,
    )
    # The sample-size-aware guards must hold the line: same distribution in,
    # nothing out.
    assert report.checks == 30 * 7
    assert report.false_positive_rate == 0.0, report.examples


def test_injected_substitution_is_recovered_on_real_data():
    df = load_diamonds()
    report = diamonds_injected_recall_report(
        df,
        column="clarity",
        from_value="SI1",
        to_value="SI1_RECODED",
        n=2000,
        trials=15,
    )
    # A real semantic change survives real-world noise.
    assert report.recall >= 0.95
