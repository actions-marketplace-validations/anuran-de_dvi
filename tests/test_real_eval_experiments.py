"""The generalised real-vs-real and injected-recall experiments."""
from __future__ import annotations

import polars as pl

from dvi.benchmark.real_eval.experiments import (
    injected_recall_report,
    real_vs_real_report,
)
from dvi.benchmark.synthetic import inject_value_substitution


def _stable_frame(n: int = 4000) -> pl.DataFrame:
    # A benign categorical + numeric frame: same distribution across any split.
    cats = ["US", "UK", "DE", "FR"]
    return pl.DataFrame(
        {
            "country": [cats[i % len(cats)] for i in range(n)],
            "amount": [10.0 + (i % 90) for i in range(n)],
        }
    )


def test_real_vs_real_stays_silent_on_same_distribution():
    df = _stable_frame()
    report = real_vs_real_report(
        df, columns=["country", "amount"], n=1000, trials=10
    )
    assert report.checks == 20  # 2 columns x 10 trials
    assert report.fires == 0
    assert report.false_positive_rate == 0.0
    # Silent checks still contribute a (0.0, 0) calibration pair.
    assert len(report.pairs) == 20
    assert all(p == (0.0, 0) for p in report.pairs)


def test_injected_recall_recovers_a_planted_rename():
    df = _stable_frame()
    report = injected_recall_report(
        df,
        family="value_substitution",
        column="country",
        inject=lambda d: inject_value_substitution(d, "country", "UK", "United Kingdom"),
        n=2000,
        trials=10,
    )
    assert report.family == "value_substitution"
    assert report.recall >= 0.9
    # Every hit contributes a (confidence, 1) pair with a real confidence.
    assert len(report.pairs) == report.hits
    assert all(label == 1 and 0.0 <= conf <= 1.0 for conf, label in report.pairs)


def test_injected_recall_ignores_a_subthreshold_decoy():
    df = _stable_frame()
    # A no-op "injection" changes nothing, so nothing should fire.
    report = injected_recall_report(
        df,
        family="value_substitution",
        column="country",
        inject=lambda d: d,
        n=2000,
        trials=10,
    )
    assert report.hits == 0
