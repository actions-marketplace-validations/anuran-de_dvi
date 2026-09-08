"""The runner aggregates per-dataset + pooled results and a reliability report."""
from __future__ import annotations

from dvi.benchmark.real_eval.runner import RealEvalReport, render_report, run_registry


def test_runner_produces_sane_pooled_metrics_on_committed_data():
    # Small n/trials keep the test fast but exercise the full path.
    report = run_registry(n=800, trials=4, seed=0)
    assert isinstance(report, RealEvalReport)
    ids = {d.dataset_id for d in report.datasets}
    assert {"diamonds", "adult", "online_retail"} <= ids

    # Specificity: real-vs-real must be quiet on real, unchanged data.
    assert report.pooled_fp_rate <= 0.10
    # Sensitivity: planted changes are recovered across families.
    assert report.pooled_recall >= 0.80
    # Calibration pairs exist and carry both classes.
    assert report.reliability.count > 0
    assert report.reliability.positives > 0


def test_render_report_is_deterministic_and_labelled():
    a = render_report(run_registry(n=800, trials=4, seed=0))
    b = render_report(run_registry(n=800, trials=4, seed=0))
    assert a == b
    assert "injected-label" in a.lower()
    assert "Reliability" in a
