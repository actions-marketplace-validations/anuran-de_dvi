"""The runner aggregates per-dataset + pooled results and a reliability report."""
from __future__ import annotations

from dvi.benchmark.real_eval.runner import RealEvalReport, render_report, run_registry


def test_runner_produces_sane_pooled_metrics_on_committed_data():
    # Small n/trials keep the test fast but exercise the full path.
    # committed_only=True keeps CI/tests deterministic and fast regardless of
    # whether a developer has prepped the git-ignored local taxi dataset.
    report = run_registry(n=800, trials=4, seed=0, committed_only=True)
    assert isinstance(report, RealEvalReport)
    ids = {d.dataset_id for d in report.datasets}
    assert {"diamonds", "adult", "online_retail"} == ids

    # Specificity: real-vs-real must be quiet on real, unchanged data.
    assert report.pooled_fp_rate <= 0.10
    # Sensitivity: planted changes are recovered across families.
    assert report.pooled_recall >= 0.80
    # Calibration pairs exist and carry both classes.
    assert report.reliability.count > 0
    assert report.reliability.positives > 0


def test_render_report_is_deterministic_and_labelled():
    a = render_report(run_registry(n=800, trials=4, seed=0, committed_only=True))
    b = render_report(run_registry(n=800, trials=4, seed=0, committed_only=True))
    assert a == b
    assert "injected-label" in a.lower()
    assert "Reliability" in a


def test_cli_defaults_to_committed_only():
    # The shipped entrypoint must default to the CI-reproducible committed
    # report; local (git-ignored) datasets are opt-in via --include-local so
    # the default output is byte-identical whether or not taxi is prepped.
    from dvi.benchmark.real_eval.__main__ import _parse_args

    assert _parse_args([]).include_local is False
    assert _parse_args(["--include-local"]).include_local is True


def test_cli_default_report_covers_committed_datasets_only():
    from dvi.benchmark.real_eval.__main__ import build_report

    out = build_report([], n=800, trials=4, seed=0)
    assert "| adult |" in out
    assert "| diamonds |" in out
    assert "| online_retail |" in out
    # Local datasets are never folded into the default report.
    assert "nyc_taxi" not in out
